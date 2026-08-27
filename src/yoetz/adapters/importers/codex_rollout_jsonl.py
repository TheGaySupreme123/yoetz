"""Bounded parser for exact Codex session-rollout JSONL profiles.

The ``codex exec --json`` grammar lives in ``codex_jsonl.py`` and is a different
surface. This module admits ``rollout-*.jsonl`` lines of the form
``{"timestamp","ordinal"?,"type","payload"}``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, NoReturn, cast

from yoetz.adapters.importers.codex_jsonl import (
    CodexCapabilityProfile,
    CodexParsedRecord,
    CodexParseResult,
    CodexSourceLine,
)
from yoetz.adapters.integrations.codex_capability_cells import CODEX_ROLLOUT_HISTORY_MODES
from yoetz.domain.values import JsonObject, JsonValue, freeze_json
from yoetz.observability.privacy import redact_sensitive_content
from yoetz.ports.importer import ImportLineStatus
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "CODEX_ROLLOUT_MAPPING_VERSION",
    "SUPPORTED_ROLLOUT_PROFILES",
    "parse_codex_rollout_jsonl",
    "parse_codex_rollout_jsonl_from_offset",
    "profile_for_rollout_version",
    "split_codex_rollout_jsonl_chunk",
]

CODEX_ROLLOUT_MAPPING_VERSION: Final = "codex-rollout-jsonl/1.0.0"
_MAX_SOURCE_BYTES: Final = 4_194_304
_MAX_LINE_BYTES: Final = 1_048_576
_MAX_LINES: Final = 20_000
_MAX_JSON_DEPTH: Final = 64

_WRAPPER_TYPES: Final = (
    "compacted",
    "event_msg",
    "inter_agent_communication",
    "inter_agent_communication_metadata",
    "realtime_item",
    "response_item",
    "security_risk_score",
    "session_meta",
    "turn_context",
    "world_state",
)
_ITEM_TYPES: Final = (
    "agent_message",
    "agent_reasoning",
    "compaction",
    "context_compaction",
    "custom_tool_call",
    "custom_tool_call_output",
    "function_call",
    "function_call_output",
    "item_completed",
    "local_shell_call",
    "message",
    "reasoning",
    "user_message",
    "web_search_call",
)


def _profile_digest() -> str:
    return canonical_digest(
        {
            "cli_version": "0.148.0",
            "item_types": _ITEM_TYPES,
            "history_modes": CODEX_ROLLOUT_HISTORY_MODES,
            "max_line_bytes": _MAX_LINE_BYTES,
            "max_lines": _MAX_LINES,
            "max_source_bytes": _MAX_SOURCE_BYTES,
            "profile_id": "codex-rollout-jsonl/0.148.0/v1",
            "wrapper_types": _WRAPPER_TYPES,
        }
    )


_BASELINE_PROFILE = CodexCapabilityProfile(
    "0.148.0",
    "codex-rollout-jsonl/0.148.0/v1",
    _profile_digest(),
    _WRAPPER_TYPES,
    _ITEM_TYPES,
)
SUPPORTED_ROLLOUT_PROFILES: Final[Mapping[str, CodexCapabilityProfile]] = MappingProxyType(
    {_BASELINE_PROFILE.cli_version: _BASELINE_PROFILE}
)


def profile_for_rollout_version(version: str) -> CodexCapabilityProfile:
    """Return one exact rollout profile; never infer a version range."""

    if type(version) is not str or not version.isascii():
        raise ValueError("unsupported_codex_profile")
    try:
        return SUPPORTED_ROLLOUT_PROFILES[version]
    except KeyError as exc:
        raise ValueError("unsupported_codex_profile") from exc


def split_codex_rollout_jsonl_chunk(
    source: bytes,
    profile: CodexCapabilityProfile,
    *,
    start_ordinal: int = 1,
) -> tuple[CodexSourceLine, ...]:
    """Split a byte chunk into source lines with chunk-relative byte offsets."""

    if type(start_ordinal) is not int or start_ordinal < 1:
        raise ValueError("codex_source_invalid")
    if (
        type(profile) is not CodexCapabilityProfile
        or SUPPORTED_ROLLOUT_PROFILES.get(profile.cli_version) != profile
    ):
        raise ValueError("unsupported_codex_profile")
    if type(source) is not bytes:
        raise ValueError("codex_source_invalid")
    if len(source) > profile.max_source_bytes:
        raise ValueError("import_source_limit_exceeded")
    if not source:
        return ()
    lines: list[CodexSourceLine] = []
    start = 0
    ordinal = start_ordinal
    while start < len(source):
        newline = source.find(b"\n", start)
        terminated = newline >= 0
        end = newline + 1 if terminated else len(source)
        if ordinal > profile.max_lines:
            raise ValueError("import_line_limit_exceeded")
        content_end = end - 1 if terminated else end
        content = bytes(source[start:content_end])
        if content.endswith(b"\r"):
            content = content[:-1]
        lines.append(CodexSourceLine(ordinal, start, end, content, terminated))
        ordinal += 1
        start = end
    return tuple(lines)


def parse_codex_rollout_jsonl(
    source: bytes,
    profile: CodexCapabilityProfile,
    *,
    require_admission: bool = True,
) -> CodexParseResult:
    """Split and validate exact rollout bytes without IO or source-bearing diagnostics."""

    return parse_codex_rollout_jsonl_from_offset(
        source, profile, start_ordinal=1, require_admission=require_admission
    )


def parse_codex_rollout_jsonl_from_offset(
    source: bytes,
    profile: CodexCapabilityProfile,
    *,
    start_ordinal: int = 1,
    require_admission: bool = False,
) -> CodexParseResult:
    """Parse a rollout JSONL chunk. Unterminated tails are retained by callers."""

    if (
        type(profile) is not CodexCapabilityProfile
        or SUPPORTED_ROLLOUT_PROFILES.get(profile.cli_version) != profile
    ):
        raise ValueError("unsupported_codex_profile")
    if type(require_admission) is not bool:
        raise ValueError("codex_source_invalid")
    lines = split_codex_rollout_jsonl_chunk(source, profile, start_ordinal=start_ordinal)
    records: list[CodexParsedRecord] = []
    statuses: list[ImportLineStatus] = []
    reasons: list[str | None] = []
    stream_gaps: set[str] = set()
    admitted = not require_admission
    refused = False
    for line in lines:
        if refused:
            statuses.append(ImportLineStatus.UNSUPPORTED)
            reasons.append("unsupported_codex_profile")
            continue
        if len(line.content) > profile.max_line_bytes:
            statuses.append(ImportLineStatus.OVERSIZED)
            reasons.append("line_oversized")
            if not admitted:
                refused = True
                stream_gaps.add("unsupported_codex_profile")
            continue
        redacted, _detected = redact_sensitive_content(line.content)
        try:
            value = _parse_json_line(redacted)
        except TypeError, ValueError, UnicodeError:
            statuses.append(ImportLineStatus.MALFORMED)
            reason = "malformed_line"
            if line.ordinal == lines[-1].ordinal and not line.terminated:
                reason = "truncated_final_line"
                stream_gaps.add(reason)
            reasons.append(reason)
            if not admitted and line.terminated:
                refused = True
                stream_gaps.add("unsupported_codex_profile")
            continue
        if not admitted:
            admitted, admission_reason = _admit_session_meta(value, profile)
            if not admitted:
                refused = True
                stream_gaps.add("unsupported_codex_profile")
                statuses.append(ImportLineStatus.UNSUPPORTED)
                reasons.append(admission_reason)
                continue
        status, item_type, reason = _validate_wrapper(value)
        statuses.append(status)
        reasons.append(reason)
        wrapper_type = value.get("type")
        if status is ImportLineStatus.MAPPED and type(wrapper_type) is str:
            try:
                frozen = freeze_json(cast(JsonValue, value))
            except ProtocolValueError:
                statuses[-1] = ImportLineStatus.UNSUPPORTED
                reasons[-1] = "json_profile_unsupported"
                continue
            if type(frozen) is not JsonObject:
                statuses[-1] = ImportLineStatus.UNSUPPORTED
                reasons[-1] = "json_profile_unsupported"
                continue
            records.append(
                CodexParsedRecord(
                    line.ordinal,
                    line.byte_start,
                    line.byte_end,
                    wrapper_type,
                    item_type,
                    frozen,
                )
            )
    if lines and not lines[-1].terminated:
        stream_gaps.add("final_newline_absent")
    return CodexParseResult(
        profile,
        lines,
        tuple(records),
        tuple(statuses),
        tuple(reasons),
        tuple(sorted(stream_gaps, key=str.encode)),
    )


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("nonfinite_number")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_object_key")
        result[key] = value
    return result


def _validate_json_tree(value: object, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("nesting_too_deep")
    if type(value) is str:
        value.encode("utf-8", errors="strict")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite_number")
    elif type(value) is list:
        for item in cast(list[object], value):
            _validate_json_tree(item, depth + 1)
    elif type(value) is dict:
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise ValueError("json_object_invalid")
            key.encode("utf-8", errors="strict")
            _validate_json_tree(item, depth + 1)
    elif value is not None and type(value) not in {bool, int}:
        raise ValueError("json_value_invalid")


def _parse_json_line(content: bytes) -> dict[str, object]:
    if not content or b"\x00" in content or content.startswith(b"\xef\xbb\xbf"):
        raise ValueError("malformed_json")
    try:
        text = content.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("malformed_json") from exc
    _validate_json_tree(parsed)
    if type(parsed) is not dict:
        raise ValueError("top_level_not_object")
    return cast(dict[str, object], parsed)


def _admit_session_meta(
    value: dict[str, object], profile: CodexCapabilityProfile
) -> tuple[bool, str]:
    if value.get("type") != "session_meta":
        return False, "unsupported_codex_profile"
    payload = value.get("payload")
    if type(payload) is not dict:
        return False, "unsupported_codex_profile"
    cli_version = cast(dict[str, object], payload).get("cli_version")
    if type(cli_version) is not str:
        return False, "unsupported_codex_profile"
    admitted = SUPPORTED_ROLLOUT_PROFILES.get(cli_version)
    if admitted != profile:
        return False, "unsupported_codex_profile"
    history_mode = cast(dict[str, object], payload).get("history_mode")
    if type(history_mode) is not str or history_mode not in CODEX_ROLLOUT_HISTORY_MODES:
        return False, "unsupported_codex_profile"
    return True, "session_meta"


def _item_type_of(value: dict[str, object]) -> str | None:
    payload = value.get("payload")
    if type(payload) is not dict:
        return None
    body = cast(dict[str, object], payload)
    inner = body.get("item")
    if type(inner) is dict:
        inner_type = cast(dict[str, object], inner).get("type")
        if type(inner_type) is str:
            return inner_type
    payload_type = body.get("type")
    if type(payload_type) is str:
        return payload_type
    return None


def _validate_wrapper(value: dict[str, object]) -> tuple[ImportLineStatus, str | None, str | None]:
    wrapper_type = value.get("type")
    if type(wrapper_type) is not str:
        return ImportLineStatus.UNSUPPORTED, None, "wrapper_shape_unsupported"
    if wrapper_type not in _WRAPPER_TYPES:
        return ImportLineStatus.UNKNOWN, None, "unknown_wrapper_type"
    payload = value.get("payload")
    if type(payload) is not dict:
        return ImportLineStatus.UNSUPPORTED, None, "wrapper_shape_unsupported"
    if "timestamp" in value and type(value.get("timestamp")) is not str:
        return ImportLineStatus.UNSUPPORTED, None, "wrapper_shape_unsupported"
    if "ordinal" in value and type(value.get("ordinal")) is not int:
        return ImportLineStatus.UNSUPPORTED, None, "wrapper_shape_unsupported"
    item_type = _item_type_of(value)
    if item_type is not None and item_type not in _ITEM_TYPES:
        return ImportLineStatus.UNKNOWN, item_type, "unknown_item_type"
    return ImportLineStatus.MAPPED, item_type, None
