"""Incremental Codex session-stream observer (selective secondary source)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from yoetz.adapters.importers.codex_jsonl import (
    CodexCapabilityProfile,
    CodexParsedRecord,
)
from yoetz.adapters.importers.codex_rollout_jsonl import (
    ROLLOUT_MAX_LINE_BYTES,
    SUPPORTED_ROLLOUT_PROFILES,
    parse_codex_rollout_jsonl_from_offset,
    profile_for_rollout_id,
    profile_for_rollout_version,
    rollout_admission_provenance,
)
from yoetz.adapters.integrations.codex_lifecycle import load_mapping
from yoetz.adapters.integrations.observation_admission import (
    AdmissionPlan,
    build_routine_read_summary,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    YOETZ_TOOL_NAMES,
    LocalObservationStore,
    self_observation_deliverable,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    stream_line_commitment,
)
from yoetz.domain.observation_budget import ObservationMode
from yoetz.domain.observation_selection import (
    OBSERVATION_CLASSIFICATION_VERSION,
    ObservationClassification,
    ObservationContentRole,
    classify_observation,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, timestamp_from_datetime
from yoetz.ports.importer import ImportLineStatus
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "STREAM_ADMISSION_STATES",
    "CodexSessionStreamLocator",
    "PERIODIC_RECONCILE_SECONDS",
    "SessionStreamAdvance",
    "SessionStreamReader",
    "default_stream_profile",
    "envelope_from_stream_record",
    "reconcile_session_stream",
    "resolve_codex_home",
    "should_trigger_stream_reconcile",
    "stream_admission",
    "stream_profile_from_id",
    "structural_from_stream_record",
]

# Closed stream-admission states (issue #656). ``structurally_supported`` is an exact certified
# profile with every read line understood; ``partially_understood`` is the compatibility profile
# or any admitted profile with unknown/incompatible lines; ``incompatible`` is a refused header
# or a surface the hook pass cannot read; ``unadmitted`` is a stream whose header has not been
# read yet. None of these is host support: parser admission certifies nothing about hooks or MCP.
STREAM_ADMISSION_STATES: Final = (
    "incompatible",
    "partially_understood",
    "structurally_supported",
    "unadmitted",
)
# Closed per-line reason tokens the reader surfaces beside the admission state so a partial
# stream names the affected family (wrapper vs. nested item vs. shape) without echoing the
# unknown type text itself.
_ADMISSION_REASON_TOKENS: Final = frozenset(
    {
        "json_profile_unsupported",
        "line_oversized",
        "malformed_line",
        "truncated_final_line",
        "unknown_item_type",
        "unknown_wrapper_type",
        "unsupported_codex_profile",
        "wrapper_shape_unsupported",
    }
)

_MAX_READ_CHUNK: Final = 262_144
_EMPTY_COMMITMENT: Final = "hmac-sha256:" + ("0" * 64)
_MAX_SESSION_WALK: Final = 4_096
_MAX_CANONICAL_INTEGER: Final = (1 << 53) - 1
PERIODIC_RECONCILE_SECONDS: Final = 30.0
_MATERIAL_HOOK_EVENTS: Final = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "Stop",
        "SessionEnd",
        "SessionStart",
        "SubagentStart",
        "SubagentStop",
    }
)
_SUBAGENT_ACTIVITY_START_KINDS: Final = frozenset({"start", "started"})
_SUBAGENT_ACTIVITY_STOP_KINDS: Final = frozenset(
    {"cancelled", "completed", "failed", "interrupted", "stop", "stopped"}
)
_SUBAGENT_ACTIVITY_KINDS: Final = _SUBAGENT_ACTIVITY_START_KINDS | _SUBAGENT_ACTIVITY_STOP_KINDS


_JSONL_SUFFIXES: Final = (".jsonl", ".jsonl.zst")
# Union vocabularies are only a fallback for callers that map a record without naming the
# profile that admitted it; the reader always passes the exact admitted profile.
_ROLLOUT_ITEM_TYPES: Final = frozenset(
    item for profile in SUPPORTED_ROLLOUT_PROFILES.values() for item in profile.item_types
)
_ROLLOUT_WRAPPER_TYPES: Final = frozenset(
    wrapper for profile in SUPPORTED_ROLLOUT_PROFILES.values() for wrapper in profile.wrapper_types
)
_OVERSIZED_PARTIAL_PREFIX: Final = b"\x00yoetz-oversized-line/v1\x00"
_OVERSIZED_PARTIAL_DOMAIN: Final = b"yoetz/observation-stream-oversized-state/v1\x00"
_OVERSIZED_LINE_DOMAIN: Final = b"yoetz/observation-stream-oversized-line/v1\x00"
# The local stream call map predates selection and stores only tool names.  A
# candidate marker is kept in the same generation-fenced map so a shell call
# classified from its pre-event arguments can be paired with a later output
# without persisting the command itself.  The separator cannot occur in a
# token accepted by this adapter and is decoded before the value reaches an
# observation envelope.
_CALL_SELECTION_SEPARATOR: Final = "\x1f"
_CALL_SELECTION_MARKER: Final = "routine"
_ROUTINE_OUTCOME_FAILURE_REASONS: Final = frozenset(
    {"failure", "denied", "cancelled", "partial", "unknown"}
)
_OBSERVATION_GAP_CODES: Final = frozenset(code.value for code in ObservationGapCode)


@dataclass(frozen=True, slots=True)
class _OversizedLineState:
    line_start: int
    prefix_digest: str


def _encode_oversized_partial(
    *,
    line_start: int,
    prefix_commitment: str,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> bytes:
    digest = prefix_commitment.removeprefix("hmac-sha256:")
    if (
        type(line_start) is not int
        or line_start < 0
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("session_stream_partial_invalid")
    body = f"{line_start}:{digest}".encode("ascii")
    context = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + body
    )
    tag = hmac.new(key_material, _OVERSIZED_PARTIAL_DOMAIN + context, hashlib.sha256).hexdigest()
    return _OVERSIZED_PARTIAL_PREFIX + tag.encode("ascii") + b":" + body


def _decode_oversized_partial(
    value: bytes,
    *,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> _OversizedLineState | None:
    if not value.startswith(_OVERSIZED_PARTIAL_PREFIX):
        return None
    try:
        tag, line_start_raw, prefix_digest_raw = value[len(_OVERSIZED_PARTIAL_PREFIX) :].split(b":")
        line_start = int(line_start_raw.decode("ascii"))
        prefix_digest = prefix_digest_raw.decode("ascii")
    except UnicodeError, ValueError:
        raise ValueError("session_stream_partial_invalid") from None
    body = line_start_raw + b":" + prefix_digest_raw
    context = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + body
    )
    expected_tag = (
        hmac.new(key_material, _OVERSIZED_PARTIAL_DOMAIN + context, hashlib.sha256)
        .hexdigest()
        .encode("ascii")
    )
    if (
        line_start < 0
        or line_start_raw != str(line_start).encode("ascii")
        or len(prefix_digest) != 64
        or any(char not in "0123456789abcdef" for char in prefix_digest)
        or not hmac.compare_digest(tag, expected_tag)
    ):
        raise ValueError("session_stream_partial_invalid")
    return _OversizedLineState(line_start, prefix_digest)


def _oversized_line_commitment(
    *,
    state: _OversizedLineState,
    byte_end: int,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> str:
    body = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + f"{state.line_start}:{byte_end}:{state.prefix_digest}".encode("ascii")
    )
    digest = hmac.new(key_material, _OVERSIZED_LINE_DOMAIN + body, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def default_stream_profile() -> CodexCapabilityProfile:
    """Return the baseline rollout profile.

    Session-stream reconciliation does not parse under this profile by default: the exact
    ``cli_version`` in each stream's session header selects the admitted profile, and the reader
    remembers that selection per source generation. The baseline exists for callers and tests
    that need one concrete supported profile.
    """

    try:
        return profile_for_rollout_version("0.148.0")
    except ValueError:
        return next(iter(SUPPORTED_ROLLOUT_PROFILES.values()))


def stream_profile_from_id(profile_id: str | None) -> CodexCapabilityProfile | None:
    """Resolve a persisted profile id (exact or compatible); unknown ids resolve to ``None``."""

    if profile_id is None:
        return None
    try:
        return profile_for_rollout_id(profile_id)
    except ValueError:
        return None


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _source_file_identity(facts: os.stat_result, key_material: bytes) -> str:
    """Return a private, path-free identity for one opened stream generation."""

    def bounded(value: int) -> int | str:
        if -_MAX_CANONICAL_INTEGER <= value <= _MAX_CANONICAL_INTEGER:
            return value
        return f"hex:{value:x}"

    payload = canonical_encode(
        JsonObject(
            {
                "device": bounded(facts.st_dev),
                "inode": bounded(facts.st_ino),
            }
        )
    )
    return "hmac-sha256:" + hmac.new(key_material, payload, hashlib.sha256).hexdigest()


def _token(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > 128:
        return None
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-"
    if any(ch not in allowed for ch in value) or value[0] in "._:/+-":
        return None
    return value


def _consistent_alias_token(
    body: Mapping[str, JsonValue], names: tuple[str, ...]
) -> tuple[str | None, bool]:
    """Return one bounded alias value only when every supplied spelling agrees."""

    values: list[str] = []
    supplied = False
    for name in names:
        if name not in body:
            continue
        supplied = True
        raw = body.get(name)
        if raw is None:
            continue
        token = _token(raw)
        if token is None:
            return None, supplied
        values.append(token)
    if any(value != values[0] for value in values[1:]):
        return None, supplied
    return (values[0] if values else None), supplied


def resolve_codex_home(
    explicit: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the selected owner-private Codex home (never disclosed by callers)."""

    if explicit is not None:
        return Path(explicit).expanduser()
    environ = os.environ if env is None else env
    for key in ("CODEX_HOME", "CODEX_TESTING_HOME"):
        raw = environ.get(key)
        if type(raw) is str and raw.strip():
            return Path(raw).expanduser()
    return Path.home() / ".codex"


@dataclass(frozen=True, slots=True, repr=False)
class CodexSessionStreamLocator:
    """Locate a Codex session JSONL path under one selected Codex home.

    Resolved paths are for local use only: never persist or emit them.
    """

    codex_home: Path

    def __repr__(self) -> str:
        return "CodexSessionStreamLocator(codex_home=<redacted>)"

    @property
    def session_root(self) -> Path:
        return self.codex_home / "sessions"

    def resolve(
        self,
        *,
        session_id: str,
        hook_provided_path: str | None = None,
    ) -> Path | None:
        """Return a validated session file or ``None`` when unsafe/ambiguous/absent."""

        token = _token(session_id)
        if token is None:
            return None
        home = self._validated_home()
        if home is None:
            return None
        if hook_provided_path is not None:
            candidate = self._validate_candidate(
                Path(hook_provided_path), home=home, session_id=token
            )
            if candidate is None:
                return None
            if candidate.name.lower().endswith(".jsonl.zst"):
                uncompressed = self._validate_candidate(
                    candidate.with_suffix(""), home=home, session_id=token
                )
                if uncompressed is not None:
                    return uncompressed
            return candidate
        return self._exact_session_match(home=home, session_id=token)

    def _validated_home(self) -> Path | None:
        try:
            home = self.codex_home.expanduser()
            if home.is_symlink() or not home.is_dir():
                return None
            resolved = home.resolve(strict=True)
        except OSError:
            return None
        if not self._owner_safe(resolved):
            return None
        return resolved

    def _exact_session_match(self, *, home: Path, session_id: str) -> Path | None:
        root = home / "sessions"
        try:
            if root.is_symlink() or not root.is_dir():
                return None
            root_resolved = root.resolve(strict=True)
        except OSError:
            return None
        if not self._is_beneath(root_resolved, home):
            return None
        uncompressed_matches: list[Path] = []
        compressed_matches: list[Path] = []
        walked = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
                walked += 1
                if walked > _MAX_SESSION_WALK:
                    return None
                # Never descend through symlinked directories.
                dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
                for name in filenames:
                    if session_id not in name:
                        continue
                    candidate = Path(dirpath) / name
                    validated = self._validate_candidate(
                        candidate, home=home, session_id=session_id
                    )
                    if validated is not None:
                        target = (
                            compressed_matches
                            if validated.name.lower().endswith(".jsonl.zst")
                            else uncompressed_matches
                        )
                        target.append(validated)
        except OSError:
            return None
        if len(uncompressed_matches) == 1:
            return uncompressed_matches[0]
        if uncompressed_matches or len(compressed_matches) != 1:
            return None
        return compressed_matches[0]

    def _validate_candidate(self, candidate: Path, *, home: Path, session_id: str) -> Path | None:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return None
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        if not self._is_beneath(resolved, home / "sessions"):
            return None
        if not self._is_beneath(resolved, home):
            return None
        if session_id not in resolved.name:
            return None
        if not self._owner_safe(resolved):
            return None
        lower_name = resolved.name.lower()
        if not any(lower_name.endswith(suffix) for suffix in _JSONL_SUFFIXES):
            return None
        compressed = lower_name.endswith(".jsonl.zst")
        if compressed:
            return resolved
        try:
            with resolved.open("rb") as handle:
                head = handle.read(1)
        except OSError:
            return None
        if head not in {b"{", b"", b"\n"}:
            return None
        return resolved

    @staticmethod
    def _is_beneath(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root.resolve(strict=False))
        except OSError, ValueError:
            return False
        return True

    @staticmethod
    def _owner_safe(path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return False
        uid = getattr(os, "getuid", None)
        if callable(uid):
            return int(stat.st_uid) == cast(Callable[[], int], uid)()
        return True


def _normalize_tool_name(value: object) -> str | None:
    token = _token(value)
    if token is None:
        return None
    if token.startswith("mcp__yoetz__") and token in YOETZ_TOOL_NAMES:
        return token
    if token in YOETZ_TOOL_NAMES:
        return token
    return token


def _structural_body(record: CodexParsedRecord) -> JsonObject | None:
    payload = record.value.get("payload")
    if isinstance(payload, JsonObject):
        inner = payload.get("item")
        if isinstance(inner, JsonObject):
            return inner
        return payload
    item = record.value.get("item")
    if isinstance(item, JsonObject):
        return item
    return None


def _decode_stream_call_tool(value: object) -> tuple[str | None, bool]:
    """Decode a bounded call-map value and its service-derived candidate bit.

    Older state files contain a plain tool name.  Such values remain usable,
    but cannot prove that a shell call was read-only because the original
    arguments were intentionally never retained.  Only the marker written by
    this adapter can carry that fact across reconcile passes.
    """

    if type(value) is not str or not value:
        return None, False
    if _CALL_SELECTION_SEPARATOR not in value:
        return (value if _token(value) is not None else None), False
    tool_name, marker = value.rsplit(_CALL_SELECTION_SEPARATOR, 1)
    if marker != _CALL_SELECTION_MARKER or _token(tool_name) is None:
        return None, False
    return tool_name, True


def _encode_stream_call_tool(tool_name: str, *, routine_candidate: bool) -> str:
    """Encode a tool name plus a non-content selection fact for the call map."""

    if _token(tool_name) is None:
        raise ValueError("stream_call_tool_invalid")
    if not routine_candidate:
        return tool_name
    return f"{tool_name}{_CALL_SELECTION_SEPARATOR}{_CALL_SELECTION_MARKER}"


def _stream_classification_payload(
    record: CodexParsedRecord, structural: Mapping[str, JsonValue]
) -> Mapping[str, JsonValue]:
    """Build the ephemeral classifier input for one parsed stream record.

    Rollout arguments/results are already redacted by the importer.  Arguments
    are parsed only for the closed routine-read classifier and never copied to
    a structural envelope, state file, outbox row, or summary member.
    """

    payload: dict[str, JsonValue] = dict(structural)
    tool_name = structural.get("tool_name")
    if type(tool_name) is str:
        payload["tool_name"] = tool_name
    body = _structural_body(record)
    if body is None or structural.get("action") not in {"function_call", "custom_tool_call"}:
        return payload

    raw_arguments: object = body.get("arguments")
    if raw_arguments is None:
        raw_arguments = body.get("input")
    if raw_arguments is None:
        raw_arguments = body.get("tool_input")
    if type(raw_arguments) is str:
        try:
            parsed = strict_json_parse(raw_arguments.encode("utf-8"))
        except ProtocolValueError, TypeError, ValueError, UnicodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            payload["tool_input"] = cast(JsonObject, parsed)
    elif isinstance(raw_arguments, Mapping):
        payload["tool_input"] = raw_arguments
    return payload


def _stream_record_classification(
    record: CodexParsedRecord, structural: Mapping[str, JsonValue]
) -> ObservationClassification | None:
    """Classify only tool-call phases; lifecycle/visible rows stay protected."""

    phase = _stream_phase(structural)
    if phase not in {"PreToolUse", "PostToolUse"}:
        return None
    return classify_observation(_stream_classification_payload(record, structural), phase)


def _carry_stream_candidate(
    classification: ObservationClassification,
    *,
    routine_candidate: bool,
) -> ObservationClassification:
    """Carry a pre-event candidate to a later output after pairing.

    The post classifier still owns outcome truth.  The persisted candidate is
    only the result of an earlier classifier call bound to this call id and
    source generation; it cannot turn a failure, partial, denial, or unknown
    output into a successful summary.
    """

    if not routine_candidate or classification.routine_candidate:
        return classification
    reasons = list(classification.reason_tokens)
    if "routine_candidate" not in reasons:
        reasons.append("routine_candidate")
    proven = "success" in reasons and not set(reasons).intersection(
        _ROUTINE_OUTCOME_FAILURE_REASONS
    )
    if proven and "routine_success" not in reasons:
        reasons.append("routine_success")
    return ObservationClassification(
        protected=not proven,
        routine_candidate=True,
        proven_routine_success=proven,
        content_role=(ObservationContentRole.NONE if proven else ObservationContentRole.BOTH),
        reason_tokens=tuple(reasons),
        version=OBSERVATION_CLASSIFICATION_VERSION,
    )


def _selection_envelope(
    envelope: ObservationEnvelope,
    classification: ObservationClassification | None,
    *,
    focused: bool,
    routed: bool,
) -> ObservationEnvelope:
    """Adapt a stream tool row to the hook-shaped summary phase contract."""

    if not routed or classification is None or not classification.routine_candidate:
        return envelope
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    action = structural.get("action")
    if action in {"function_call", "custom_tool_call"}:
        if envelope.event_kind in {"response_item", "event_msg"}:
            return replace(
                envelope,
                event_kind="PreToolUse",
                structural_payload=JsonObject(
                    {
                        **dict(structural),
                        "action": "routine_read" if focused else "function_call",
                    }
                ),
            )
    if (
        action in {"function_call_output", "custom_tool_call_output"}
        and classification.proven_routine_success
        and envelope.event_kind in {"response_item", "event_msg"}
    ):
        return replace(
            envelope,
            event_kind="PostToolUse",
            structural_payload=JsonObject(
                {
                    **dict(structural),
                    "action": "routine_read" if focused else "function_call_output",
                }
            ),
        )
    return envelope


def _stream_read_protection_envelope(
    envelope: ObservationEnvelope,
    classification: ObservationClassification | None,
) -> ObservationEnvelope:
    """Normalize a candidate to the read-protection API's phase contract.

    Protection is independent of Focused/Detailed selection and of a live
    route.  The local store recognizes shell reads only through its
    service-owned ``routine_read`` marker, so failed and unknown outputs must
    use the same normalized probe before they are admitted individually.
    """

    if classification is None or not classification.routine_candidate:
        return envelope
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    action = structural.get("action")
    if action in {"function_call", "custom_tool_call"} and envelope.event_kind in {
        "response_item",
        "event_msg",
        "PreToolUse",
    }:
        return replace(
            envelope,
            event_kind="PreToolUse",
            structural_payload=JsonObject({**dict(structural), "action": "routine_read"}),
        )
    if action in {"function_call_output", "custom_tool_call_output"} and envelope.event_kind in {
        "response_item",
        "event_msg",
        "PostToolUse",
        "PostToolUseFailure",
    }:
        return replace(
            envelope,
            event_kind="PostToolUse",
            structural_payload=JsonObject({**dict(structural), "action": "routine_read"}),
        )
    return envelope


def _stream_read_protection_probe(
    store: LocalObservationStore,
    workspace_commitment: str,
    session_commitment: str,
    envelope: ObservationEnvelope,
    classification: ObservationClassification | None,
) -> tuple[ObservationEnvelope | None, str | None]:
    """Reserve an explicitly protected logical read, if one matches.

    The probe remains separate from the stamped delivery envelope because the
    local store validates the service-owned ``routine_read`` marker while
    ``evidence_linked_read`` is the materialization marker.
    """

    probe = _stream_read_protection_envelope(envelope, classification)
    if probe is envelope:
        return None, None
    reader = getattr(store, "read_is_protected", None)
    if not callable(reader):
        return None, None
    try:
        protected = reader(workspace_commitment, session_commitment, probe)
    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
        return None, None
    if protected is not True:
        return None, None
    reference_reader = getattr(store, "read_protection_reference", None)
    if not callable(reference_reader):
        return probe, None
    try:
        reference = reference_reader(workspace_commitment, session_commitment, probe)
    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
        reference = None
    return probe, reference if type(reference) is str else None


def _stamp_stream_read_protection(
    envelope: ObservationEnvelope,
    reference: str | None,
) -> ObservationEnvelope:
    """Mark a protected stream phase as individually retained evidence."""

    fields: dict[str, JsonValue] = {
        **dict(cast(Mapping[str, JsonValue], envelope.structural_payload)),
        "action": "evidence_linked_read",
    }
    if reference is not None:
        fields["protection_reference"] = reference
    return replace(envelope, structural_payload=JsonObject(fields))


def _restore_stream_individual(envelope: ObservationEnvelope) -> ObservationEnvelope:
    """Restore a selected stream row when it is delivered individually."""

    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    action = structural.get("action")
    if action == "routine_read":
        if envelope.event_kind == "PostToolUse":
            return replace(
                envelope,
                structural_payload=JsonObject(
                    {**dict(structural), "action": "function_call_output"}
                ),
            )
        if envelope.event_kind in {"response_item", "event_msg", "PreToolUse"}:
            return replace(
                envelope,
                event_kind="PreToolUse",
                structural_payload=JsonObject({**dict(structural), "action": "function_call"}),
            )
        return envelope
    if action not in {"function_call", "custom_tool_call"}:
        return envelope
    if envelope.event_kind not in {"response_item", "event_msg", "PreToolUse"}:
        return envelope
    return replace(envelope, event_kind="PreToolUse")


def _restore_stream_individual_deliveries(plan: AdmissionPlan) -> AdmissionPlan:
    """Keep failed/incomplete stream attempts individually materializable."""

    if not plan.deliveries:
        return plan
    return replace(
        plan,
        deliveries=tuple(
            (host_session, _restore_stream_individual(envelope))
            for host_session, envelope in plan.deliveries
        ),
    )


def structural_from_stream_record(
    record: CodexParsedRecord,
    *,
    profile: CodexCapabilityProfile | None = None,
) -> tuple[JsonObject, tuple[str, ...]]:
    """Map a parsed stream record to allowlisted structural fields + opaque gaps.

    ``profile`` is the exact profile that admitted the record; without it the union of every
    supported vocabulary decides which ``type`` tokens are semantic rather than tool names.
    """

    item_types = _ROLLOUT_ITEM_TYPES if profile is None else frozenset(profile.item_types)
    known_wrappers = _ROLLOUT_WRAPPER_TYPES if profile is None else frozenset(profile.wrapper_types)
    gaps: set[str] = set()
    fields: dict[str, JsonValue] = {"stream_kind": record.wrapper_type}
    item_type = record.item_type
    if item_type is not None:
        token = _token(item_type)
        if token is not None:
            fields["action"] = token
        else:
            gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    body = _structural_body(record)
    if body is not None:
        tool = _normalize_tool_name(body.get("tool")) or _normalize_tool_name(body.get("name"))
        type_token = _token(body.get("type"))
        if tool is None and type_token is not None and type_token not in item_types:
            tool = _normalize_tool_name(type_token)
        if tool is not None:
            fields["tool_name"] = tool
        status = _token(body.get("status")) or _token(body.get("result_status"))
        if status is not None:
            fields["result_status"] = status
        exit_code = body.get("exit_code")
        if "exit_code" in body:
            if type(exit_code) is int and -1 <= exit_code <= 255:
                fields["exit_status"] = exit_code
            else:
                gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
        # ``SubAgentActivity.id`` identifies the rollout item itself, not the
        # parent tool call.  Keep it out of the parent correlation family; only
        # an explicit call alias may identify that parent.
        if item_type != "SubAgentActivity":
            call_id = _token(body.get("id")) or _token(body.get("call_id"))
            if call_id is not None:
                fields["tool_call_id"] = call_id
        # Codex rollout records use the same child identifiers as its native
        # hook profile.  Preserve only the bounded structural pair; task
        # creation and acceptance stay service-owned.  Some historical stream
        # records use ``agent_id`` for the child, so normalize that alias to
        # the canonical ``subagent_id`` field.
        subagent_id, _subagent_aliases_supplied = _consistent_alias_token(
            body, ("subagent_id", "agent_id", "agent_thread_id")
        )
        if subagent_id is not None:
            fields["subagent_id"] = subagent_id
        if item_type == "SubAgentActivity":
            parent_tool_call_id, _parent_aliases_supplied = _consistent_alias_token(
                body, ("parent_tool_call_id", "tool_call_id", "tool_use_id")
            )
            if parent_tool_call_id is not None:
                fields["parent_tool_call_id"] = parent_tool_call_id
            # The generic ``tool_call_id`` spelling is accepted above only as
            # an explicit parent alias for this item family.  Never leave a
            # conflicting/invalid value in the generic field where the domain
            # normalizer could reinterpret it as a parent call.
            fields.pop("tool_call_id", None)
            activity_kind = _token(body.get("kind"))
            if activity_kind not in _SUBAGENT_ACTIVITY_KINDS:
                gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    if record.wrapper_type not in known_wrappers:
        gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    return JsonObject(fields), tuple(sorted(gaps, key=str.encode))


def envelope_from_stream_record(
    record: CodexParsedRecord,
    *,
    session_commitment: str,
    cursor: ObservationCursor,
    profile: CodexCapabilityProfile | None = None,
) -> ObservationEnvelope:
    structural, gaps = structural_from_stream_record(record, profile=profile)
    host_ids: dict[str, JsonValue] = {}
    body = _structural_body(record)
    if body is not None:
        for key in ("id", "call_id", "tool_call_id", "event_id"):
            token = _token(body.get(key))
            if token is not None:
                host_ids[key] = token
        subagent_id, _subagent_aliases_supplied = _consistent_alias_token(
            body, ("subagent_id", "agent_id", "agent_thread_id")
        )
        if subagent_id is not None:
            host_ids["subagent_id"] = subagent_id
        if record.item_type == "SubAgentActivity":
            parent_tool_call_id, _parent_aliases_supplied = _consistent_alias_token(
                body, ("parent_tool_call_id", "tool_call_id", "tool_use_id")
            )
            if parent_tool_call_id is not None:
                host_ids["parent_tool_call_id"] = parent_tool_call_id
    event_id = _token(record.value.get("event_id")) or _token(record.value.get("id"))
    if event_id is not None:
        host_ids.setdefault("event_id", event_id)
    identity = (
        "stream:"
        + canonical_digest(
            JsonObject(
                {
                    "byte_end": record.byte_end,
                    "source_generation": cursor.source_generation,
                    "host_ids": JsonObject(host_ids),
                    "ordinal": record.line_ordinal,
                    "structural": structural,
                    "wrapper": record.wrapper_type,
                }
            )
        ).removeprefix("sha256:")[:48]
    )
    event_kind = _token(record.wrapper_type) or "unsupported_event"
    if record.item_type == "SubAgentActivity" and body is not None:
        activity_kind = _token(body.get("kind"))
        if activity_kind in _SUBAGENT_ACTIVITY_START_KINDS:
            event_kind = "SubagentStart"
        elif activity_kind in _SUBAGENT_ACTIVITY_STOP_KINDS:
            event_kind = "SubagentStop"
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind=event_kind,
        source_identity=identity,
        source=ObservationSource.CODEX_SESSION_STREAM,
        cursor=cursor,
        receipt_time=_now(),
        structural_payload=structural,
        content_object_refs=(),
        gap_codes=gaps,
    )


def _opaque_stream_envelope(
    *,
    session_commitment: str,
    cursor: ObservationCursor,
) -> ObservationEnvelope:
    """Retain one unsupported complete line without interpreting its payload."""

    identity = (
        "stream:"
        + canonical_digest(
            JsonObject(
                {
                    "byte_end": cursor.byte_position,
                    "line_commitment": cursor.last_source_commitment,
                    "ordinal": cursor.event_position,
                    "source_generation": cursor.source_generation,
                    "wrapper": "unsupported_event",
                }
            )
        ).removeprefix("sha256:")[:48]
    )
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind="unsupported_event",
        source_identity=identity,
        source=ObservationSource.CODEX_SESSION_STREAM,
        cursor=cursor,
        receipt_time=_now(),
        structural_payload=JsonObject({}),
        content_object_refs=(),
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )


@dataclass(frozen=True, slots=True)
class SessionStreamAdvance:
    envelopes: tuple[ObservationEnvelope, ...]
    cursor: ObservationCursor
    partial_line: bytes
    gaps: tuple[str, ...]
    restarted: bool
    truncated: bool
    rotated: bool
    # Closed parser reason tokens for the terminated lines this advance did not map (sorted,
    # unique). Never the offending type text: a partial stream names its affected family only.
    reason_codes: tuple[str, ...] = ()
    # Per-envelope classifier results are ephemeral adapter facts.  They are
    # kept parallel to ``envelopes`` so a function-call's original arguments
    # can be classified before structural mapping discards them.
    classifications: tuple[ObservationClassification | None, ...] = ()


def stream_admission(
    profile: CodexCapabilityProfile | None,
    gaps: tuple[str, ...],
    reason_codes: tuple[str, ...],
) -> str:
    """Classify one stream's admission from its profile and bounded gaps (issue #656)."""

    if ObservationGapCode.UNSUPPORTED_FORMAT.value in gaps:
        return "incompatible"
    if profile is None:
        return "unadmitted"
    if (
        rollout_admission_provenance(profile) == "structural"
        or ObservationGapCode.UNSUPPORTED_EVENT.value in gaps
        or any(token in _ADMISSION_REASON_TOKENS for token in reason_codes)
    ):
        return "partially_understood"
    return "structurally_supported"


@dataclass
class SessionStreamReader:
    """Incremental JSONL reader with generation-fenced cursor and partial-line hold."""

    session_commitment: str
    # The exact profile the current source generation's header admitted, or ``None`` until the
    # header is read. Never a default: every generation re-selects from its own header.
    profile: CodexCapabilityProfile | None
    cursor: ObservationCursor
    key_material: bytes
    partial_line: bytes = b""
    _inode: int | None = None
    _source_identity: str | None = None
    _size_at_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.partial_line) is not bytes:
            raise ValueError("session_stream_partial_invalid")
        if type(self.key_material) is not bytes or not 16 <= len(self.key_material) <= 64:
            raise ValueError("session_stream_key_invalid")

    @property
    def source_identity(self) -> str | None:
        """Return the private identity of the source inspected by the latest advance."""

        return self._source_identity

    def advance(self, path: Path) -> SessionStreamAdvance:
        gaps: set[str] = set()
        restarted = False
        truncated = False
        rotated = False
        try:
            stat = path.stat()
        except OSError:
            gaps.add(ObservationGapCode.SOURCE_LAG.value)
            return SessionStreamAdvance(
                (), self.cursor, self.partial_line, tuple(sorted(gaps)), False, False, False
            )

        inode = getattr(stat, "st_ino", None)
        source_identity = _source_file_identity(stat, self.key_material)
        size = stat.st_size
        generation = self.cursor.source_generation
        byte_position = self.cursor.byte_position
        event_position = self.cursor.event_position
        last_commitment = self.cursor.last_source_commitment

        if (self._source_identity is not None and source_identity != self._source_identity) or (
            self._inode is not None and inode is not None and inode != self._inode
        ):
            # Rotation: new inode → new generation.
            rotated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            self.profile = None
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif size < byte_position:
            # Truncation / rewrite in place.
            truncated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            self.profile = None
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif event_position > 0 and self.profile is None:
            # An admitted generation whose exact profile is not recorded cannot be parsed
            # under any vocabulary without inferring one. Replay it from the header under a
            # fresh generation instead; earlier bytes are re-read, never skipped.
            restarted = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif byte_position == 0 and event_position == 0 and self.partial_line == b"":
            restarted = generation > 1 or (self._inode is not None)

        oversized_state: _OversizedLineState | None = None
        if self.partial_line.startswith(_OVERSIZED_PARTIAL_PREFIX):
            try:
                oversized_state = _decode_oversized_partial(
                    self.partial_line,
                    session_commitment=self.session_commitment,
                    source_generation=generation,
                    source_identity=source_identity,
                    key_material=self.key_material,
                )
                if (
                    oversized_state is None
                    or oversized_state.line_start > byte_position
                    or byte_position - oversized_state.line_start <= ROLLOUT_MAX_LINE_BYTES
                ):
                    raise ValueError("session_stream_partial_invalid")
            except ValueError:
                # The private continuation marker is authenticated and bound to this session,
                # generation, and source. An invalid/transplanted marker cannot authorize a skip;
                # restart the generation and replay from admission instead.
                restarted = True
                generation += 1
                byte_position = 0
                event_position = 0
                last_commitment = _EMPTY_COMMITMENT
                self.partial_line = b""
                self.profile = None
                oversized_state = None
                gaps.add(ObservationGapCode.CURSOR_STALE.value)
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)

        self._inode = inode if type(inode) is int else self._inode
        self._source_identity = source_identity
        self._size_at_generation = size

        if event_position == 0 and byte_position > 0 and not self.partial_line:
            # Refused admission is durable for this generation: consumed bytes with
            # no admitted event mean the first complete line was rejected, so no
            # later append may materialize without an accepted exact header. Only
            # rotation or truncation (a fresh generation) re-opens admission.
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=0,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
            return SessionStreamAdvance(
                (), cursor, b"", tuple(sorted(gaps, key=str.encode)), restarted, truncated, rotated
            )

        partial_source_bytes = 0 if oversized_state is not None else len(self.partial_line)
        to_read = min(_MAX_READ_CHUNK, max(0, size - (byte_position + partial_source_bytes)))
        if to_read == 0 and not self.partial_line:
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, self.partial_line, tuple(sorted(gaps)), restarted, truncated, rotated
            )

        # A retained partial first line has not established admission. Keep requiring the exact
        # session header until a complete admitted line advances the durable cursor.
        require_admission = event_position == 0
        try:
            with path.open("rb") as handle:
                handle.seek(byte_position + partial_source_bytes)
                chunk = handle.read(to_read)
                data = chunk if oversized_state is not None else self.partial_line + chunk
                if oversized_state is None and b"\n" not in data:
                    # A live JSONL writer can leave a legal line unterminated across hook passes.
                    # Read ahead only when the ordinary chunk contains no delimiter, and never
                    # beyond the profile's admitted line bound plus its terminator. This lets a
                    # previously dropped read-cache tail recover as soon as the newline arrives
                    # without making every routine reconcile scan a full maximum-sized line.
                    unread = max(0, size - (byte_position + len(data)))
                    read_ahead = min(
                        unread,
                        max(0, ROLLOUT_MAX_LINE_BYTES + 1 - len(data)),
                    )
                    if read_ahead:
                        data += handle.read(read_ahead)
        except OSError:
            gaps.add(ObservationGapCode.SOURCE_LAG.value)
            return SessionStreamAdvance(
                (),
                self.cursor,
                self.partial_line,
                tuple(sorted(gaps)),
                restarted,
                truncated,
                rotated,
            )

        if oversized_state is not None:
            newline = data.find(b"\n")
            if newline < 0:
                cursor = ObservationCursor(
                    source_generation=generation,
                    byte_position=byte_position + len(data),
                    event_position=event_position,
                    last_source_commitment=last_commitment,
                    mapping_version=STREAM_MAPPING_VERSION,
                )
                self.cursor = cursor
                self.partial_line = _encode_oversized_partial(
                    line_start=oversized_state.line_start,
                    prefix_commitment=f"hmac-sha256:{oversized_state.prefix_digest}",
                    session_commitment=self.session_commitment,
                    source_generation=generation,
                    source_identity=source_identity,
                    key_material=self.key_material,
                )
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
                if not data and size > byte_position:
                    gaps.add(ObservationGapCode.SOURCE_LAG.value)
                return SessionStreamAdvance(
                    (),
                    cursor,
                    self.partial_line,
                    tuple(sorted(gaps, key=str.encode)),
                    restarted,
                    truncated,
                    rotated,
                )

            byte_end = byte_position + newline + 1
            last_commitment = _oversized_line_commitment(
                state=oversized_state,
                byte_end=byte_end,
                session_commitment=self.session_commitment,
                source_generation=generation,
                source_identity=source_identity,
                key_material=self.key_material,
            )
            if not require_admission:
                event_position += 1
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_end,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            self.partial_line = b""
            if require_admission:
                # The oversized first line never established admission. Leaving
                # the ordinal at zero with consumed bytes keeps this generation
                # durably refused instead of quietly admitting later appends.
                gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
                oversized_envelopes: tuple[ObservationEnvelope, ...] = ()
            else:
                gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
                oversized_envelopes = (
                    _opaque_stream_envelope(
                        session_commitment=self.session_commitment,
                        cursor=cursor,
                    ),
                )
            return SessionStreamAdvance(
                oversized_envelopes,
                cursor,
                b"",
                tuple(sorted(gaps, key=str.encode)),
                restarted,
                truncated,
                rotated,
                classifications=tuple(None for _ in oversized_envelopes),
            )

        if b"\n" not in data and len(data) > ROLLOUT_MAX_LINE_BYTES:
            prefix_commitment = stream_line_commitment(self.key_material, data)
            self.partial_line = _encode_oversized_partial(
                line_start=byte_position,
                prefix_commitment=prefix_commitment,
                session_commitment=self.session_commitment,
                source_generation=generation,
                source_identity=source_identity,
                key_material=self.key_material,
            )
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position + len(data),
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
            return SessionStreamAdvance(
                (),
                cursor,
                self.partial_line,
                tuple(sorted(gaps, key=str.encode)),
                restarted,
                truncated,
                rotated,
            )

        if not data:
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, b"", tuple(sorted(gaps)), restarted, truncated, rotated
            )

        try:
            # Admission always re-selects from the header: the profile is the one the exact
            # ``cli_version`` names, never a caller default or the previous generation's choice.
            parsed = parse_codex_rollout_jsonl_from_offset(
                data,
                None if require_admission else self.profile,
                start_ordinal=1,
                require_admission=require_admission,
            )
        except ValueError:
            gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, self.partial_line, tuple(sorted(gaps)), restarted, truncated, rotated
            )

        if parsed.profile is not None:
            self.profile = parsed.profile
        consumed = 0
        envelopes: list[ObservationEnvelope] = []
        classifications: list[ObservationClassification | None] = []
        reason_codes: set[str] = set()
        hold = b""
        for index, line in enumerate(parsed.lines):
            if not line.terminated:
                hold = line.content
                if "truncated_final_line" in parsed.stream_gaps:
                    gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
                break
            consumed = line.byte_end
            last_commitment = stream_line_commitment(self.key_material, line.content)
            record = next(
                (item for item in parsed.records if item.line_ordinal == line.ordinal),
                None,
            )
            if record is None:
                reason = parsed.reason_codes[index] if index < len(parsed.reason_codes) else None
                status = parsed.statuses[index] if index < len(parsed.statuses) else None
                if reason in _ADMISSION_REASON_TOKENS:
                    reason_codes.add(reason)
                if reason == "unsupported_codex_profile" or (
                    require_admission
                    and index == 0
                    and status in {ImportLineStatus.MALFORMED, ImportLineStatus.OVERSIZED}
                ):
                    # A refused or never-admitted line consumes its bytes but
                    # never advances the event ordinal, so admission stays
                    # required instead of silently lapsing after a rejected
                    # header once event_position moved past zero.
                    gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
                    continue
                event_position += 1
                if index < len(parsed.statuses):
                    gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
                    envelopes.append(
                        _opaque_stream_envelope(
                            session_commitment=self.session_commitment,
                            cursor=ObservationCursor(
                                source_generation=generation,
                                byte_position=byte_position + consumed,
                                event_position=event_position,
                                last_source_commitment=last_commitment,
                                mapping_version=STREAM_MAPPING_VERSION,
                            ),
                        )
                    )
                    classifications.append(None)
                continue
            event_position += 1
            abs_cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position + consumed,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            # Rewrite record ordinal to the absolute event position for identity stability.
            positioned = CodexParsedRecord(
                event_position,
                record.byte_start,
                record.byte_end,
                record.wrapper_type,
                record.item_type,
                record.value,
            )
            envelopes.append(
                envelope_from_stream_record(
                    positioned,
                    session_commitment=self.session_commitment,
                    cursor=abs_cursor,
                    profile=self.profile,
                )
            )
            structural, _structural_gaps = structural_from_stream_record(
                record, profile=self.profile
            )
            classifications.append(_stream_record_classification(record, structural))

        if "unsupported_codex_profile" in parsed.stream_gaps and consumed > 0:
            # A refused chunk holds no tail: the durable refused state is exactly
            # (event_position == 0, consumed bytes, empty partial), and every
            # later line of a refused generation is equally unsupported.
            hold = b""
        new_byte = byte_position + consumed
        self.partial_line = hold
        cursor = ObservationCursor(
            source_generation=generation,
            byte_position=new_byte,
            event_position=event_position,
            last_source_commitment=last_commitment,
            mapping_version=STREAM_MAPPING_VERSION,
        )
        self.cursor = cursor
        for gap in parsed.stream_gaps:
            if gap in {"truncated_final_line", "final_newline_absent"}:
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
            elif gap == "unsupported_codex_profile":
                gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
        return SessionStreamAdvance(
            tuple(envelopes),
            cursor,
            self.partial_line,
            tuple(sorted(gaps, key=str.encode)),
            restarted,
            truncated,
            rotated,
            tuple(sorted(reason_codes, key=str.encode)),
            tuple(classifications),
        )


def should_trigger_stream_reconcile(
    event_name: str,
    *,
    last_reconcile_mono: float | None,
    now_mono: float | None = None,
    session_source: str | None = None,
) -> bool:
    """Decide whether an observe hook should run incremental stream reconciliation."""

    if event_name in {"Stop", "SessionEnd", "PostCompact", "PreCompact"}:
        return True
    if event_name == "SessionStart" and session_source in {"resume", "compact"}:
        return True
    if event_name in _MATERIAL_HOOK_EVENTS:
        return True
    current = time.monotonic() if now_mono is None else now_mono
    if last_reconcile_mono is None:
        return False
    return (current - last_reconcile_mono) >= PERIODIC_RECONCILE_SECONDS


def _stream_phase(structural: Mapping[str, JsonValue]) -> str:
    """Map a rollout tool record to the hook phase the delivery policy speaks."""

    action = structural.get("action")
    if action in {"function_call", "custom_tool_call"}:
        return "PreToolUse"
    if action in {"function_call_output", "custom_tool_call_output"}:
        return "PostToolUse"
    return ""


def _pair_stream_tool_name(
    envelope: ObservationEnvelope,
    call_tools: dict[str, str],
    *,
    routine_candidate: bool = False,
) -> ObservationEnvelope:
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    action = structural.get("action")
    call_id = _token(structural.get("tool_call_id"))
    tool_name = _token(structural.get("tool_name"))
    if call_id is None:
        return envelope
    if action in {"function_call", "custom_tool_call"} and tool_name is not None:
        if call_id not in call_tools and len(call_tools) >= 256:
            call_tools.pop(next(iter(call_tools)))
        call_tools[call_id] = _encode_stream_call_tool(
            tool_name, routine_candidate=routine_candidate
        )
        return envelope
    if action not in {"function_call_output", "custom_tool_call_output"}:
        return envelope
    paired, _candidate = _decode_stream_call_tool(call_tools.get(call_id))
    if paired is None:
        return replace(
            envelope,
            gap_codes=tuple(
                sorted(
                    {*envelope.gap_codes, ObservationGapCode.UNPAIRED_EVENT.value},
                    key=str.encode,
                )
            ),
        )
    gaps = set(envelope.gap_codes)
    if tool_name is not None and tool_name != paired:
        gaps.add(ObservationGapCode.DEDUP_CONFLICT.value)
    return replace(
        envelope,
        structural_payload=JsonObject({**dict(structural), "tool_name": paired}),
        gap_codes=tuple(sorted(gaps, key=str.encode)),
    )


def _stream_selection_context(
    store: LocalObservationStore,
    workspace_commitment: str,
    codex_session_id: str,
    envelope: ObservationEnvelope,
    *,
    mode: ObservationMode,
    classification: ObservationClassification,
) -> tuple[ObservationEnvelope, str]:
    """Attach route-bound selection metadata when the local authority is real.

    A stream may be readable before a Codex session has an active Yoetz route.
    In that case the caller must use individual admission.  Empty fences are
    deliberately not synthesized from workspace/session values.
    """

    try:
        state_root = getattr(store, "_state_root", None)
        mapping = load_mapping(codex_session_id, _state=state_root)
        authority = store.content_capture_authority(workspace_commitment)
        if mapping is None or authority is None or not authority.active:
            return envelope, ""
        epoch = store.selection_epoch(workspace_commitment)
    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
        return envelope, ""
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    annotated = JsonObject(
        {
            **dict(structural),
            "selection_task_id": mapping.yoetz_task_id,
            "selection_session_id": mapping.yoetz_session_id,
            "selection_writer_id": mapping.yoetz_writer_id,
            "selection_authority_generation": authority.generation,
        }
    )
    delegate = structural.get("subagent_id")
    parent_call = structural.get("parent_tool_call_id")
    fence = canonical_digest(
        JsonObject(
            {
                "route": mapping.yoetz_task_id,
                "task": mapping.yoetz_task_id,
                "session": mapping.yoetz_session_id,
                "writer": mapping.yoetz_writer_id,
                "host_session": envelope.session_commitment,
                "source": envelope.source.value,
                "generation": envelope.cursor.source_generation,
                "authority": authority.generation,
                "epoch": epoch,
                "delegate": delegate,
                "parent_call": parent_call,
                "policy": classification.version,
                "classification": classification.version,
                "mode": mode.value,
            }
        )
    )
    return replace(envelope, structural_payload=annotated), fence


def _stream_selection_pressure(
    store: LocalObservationStore,
    workspace_commitment: str,
    session_commitment: str,
) -> object | None:
    """Read the store's pressure decision without making it a stream dependency.

    Older local stores have no pressure projection.  They retain the historic
    individual stream path; once the selection-aware store is present, any
    failed pressure read is treated as a closed safety failure by the caller.
    """

    updater = getattr(store, "update_selection_pressure", None)
    if not callable(updater):
        return None
    try:
        return updater(workspace_commitment, session_commitment)
    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
        return False


def _stream_selection_history_gaps(
    store: LocalObservationStore,
    workspace_commitment: str,
    envelope: ObservationEnvelope,
) -> tuple[str, ...]:
    """Return exact-lane selection loss that must travel with this admission.

    The store can only attribute a prior non-replayable loss after the current
    envelope has the authenticated route fields attached.  Keep this lookup
    optional for older stores and fail closed to the envelope's own gaps when
    the projection is unavailable or malformed.
    """

    lookup = getattr(store, "selection_history_gaps", None)
    if not callable(lookup):
        return ()
    try:
        inherited = cast(tuple[object, ...], lookup(workspace_commitment, envelope))
    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
        return ()
    if type(inherited) is not tuple:
        return ()
    return tuple(gap for gap in inherited if type(gap) is str and gap in _OBSERVATION_GAP_CODES)


def reconcile_session_stream(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    session_commitment: str,
    codex_session_id: str,
    locator: CodexSessionStreamLocator,
    hook_provided_path: str | None = None,
) -> dict[str, JsonValue]:
    """Advance the session stream cursor and ingest envelopes. Path stays local-only."""

    path = locator.resolve(session_id=codex_session_id, hook_provided_path=hook_provided_path)
    if path is None:
        return {
            "accepted": 0,
            "duplicates": 0,
            "gaps": (ObservationGapCode.SOURCE_LAG.value,),
            "resolved": False,
        }
    return reconcile_session_stream_path(
        store,
        workspace_commitment=workspace_commitment,
        session_commitment=session_commitment,
        codex_session_id=codex_session_id,
        path=path,
    )


def reconcile_session_stream_path(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    session_commitment: str,
    codex_session_id: str,
    path: Path,
) -> dict[str, JsonValue]:
    """Reconcile one path under one durable local-store batch."""

    with store.batched(workspace_commitment):
        return _reconcile_session_stream_path(
            store,
            workspace_commitment=workspace_commitment,
            session_commitment=session_commitment,
            codex_session_id=codex_session_id,
            path=path,
        )


def _reconcile_session_stream_path(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    session_commitment: str,
    codex_session_id: str,
    path: Path,
) -> dict[str, JsonValue]:
    """Reconcile a locally selected path with the same durable frontier on every entry point."""

    if path.name.lower().endswith(".jsonl.zst"):
        store.note_coverage_gap(workspace_commitment, ObservationGapCode.UNSUPPORTED_FORMAT.value)
        store.note_stream_reconcile(workspace_commitment)
        return {
            "accepted": 0,
            "duplicates": 0,
            "gaps": (ObservationGapCode.UNSUPPORTED_FORMAT.value,),
            "byte_position": 0,
            "event_position": 0,
            "generation": 1,
            "admission": "incompatible",
            "admission_provenance": None,
            "admission_reasons": (),
            "rotated": False,
            "truncated": False,
            "resolved": True,
        }
    existing = store.get_stream_cursor(workspace_commitment, session_commitment)
    mapping_reset = existing is not None and existing.mapping_version != STREAM_MAPPING_VERSION
    if existing is None or mapping_reset:
        existing = ObservationCursor(
            source_generation=(1 if existing is None else existing.source_generation + 1),
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY_COMMITMENT,
            mapping_version=STREAM_MAPPING_VERSION,
        )
    partial = (
        b"" if mapping_reset else store.get_stream_partial(workspace_commitment, session_commitment)
    )
    call_tools = (
        {}
        if mapping_reset
        else store.stream_call_tools_for_session(
            workspace_commitment,
            session_commitment,
            source_generation=existing.source_generation,
        )
    )
    prior_call_tools = dict(call_tools)
    source_identity = (
        None
        if mapping_reset
        else store.stream_source_identity_for_session(workspace_commitment, session_commitment)
    )
    # The profile persisted with the cursor is the one this generation's header admitted. A
    # missing or no-longer-supported id makes the reader replay from the header (CURSOR_STALE)
    # rather than parse admitted lines under a guessed vocabulary.
    prior_profile_id = (
        None
        if mapping_reset
        else store.stream_profile_for_session(workspace_commitment, session_commitment)
    )
    reader = SessionStreamReader(
        session_commitment=session_commitment,
        profile=stream_profile_from_id(prior_profile_id),
        cursor=existing,
        key_material=store.key_material(),
        partial_line=partial,
        _source_identity=source_identity,
    )
    advance = reader.advance(path)
    if advance.cursor.source_generation != existing.source_generation:
        call_tools.clear()
    accepted = 0
    duplicates = 0
    overflow = False
    delivery_blocked = False
    committed_cursor = existing
    # Source rotation/truncation starts a new selection lane.  Flush the old
    # lane before any new-generation envelope can move the stream frontier.
    if advance.rotated or advance.truncated or advance.restarted:
        try:
            boundary_ok = store.flush_selected_admission(
                workspace_commitment,
                summary_builder=build_routine_read_summary,
                force=True,
                material_boundary=True,
            )
        except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
            boundary_ok = False
        if not boundary_ok:
            delivery_blocked = True

    advance_classifications = advance.classifications
    for index, unpaired_envelope in enumerate(advance.envelopes):
        if delivery_blocked:
            break
        base_classification = (
            advance_classifications[index] if index < len(advance_classifications) else None
        )
        candidate_call_tools = dict(call_tools)
        structural = cast(Mapping[str, JsonValue], unpaired_envelope.structural_payload)
        call_id = _token(structural.get("tool_call_id"))
        _, paired_candidate = _decode_stream_call_tool(
            candidate_call_tools.get(call_id) if call_id is not None else None
        )
        envelope = _pair_stream_tool_name(
            unpaired_envelope,
            candidate_call_tools,
            routine_candidate=(
                False if base_classification is None else base_classification.routine_candidate
            ),
        )
        classification = base_classification
        action = envelope.structural_payload.get("action")
        if action in {"function_call_output", "custom_tool_call_output"}:
            # Pairing is authoritative for both the tool family and the
            # conservative pre-event candidate.  A conflicting or unpaired
            # output must not select itself into a routine summary merely by
            # carrying a read-looking name in its own payload.
            output_structural = dict(envelope.structural_payload)
            if not paired_candidate:
                output_structural.pop("tool_name", None)
            classification = classify_observation(output_structural, "PostToolUse")
            classification = _carry_stream_candidate(
                classification,
                routine_candidate=paired_candidate,
            )

        # A protected input is a subject-state boundary.  Flush any earlier
        # summary before attempting its individual delivery.  Routine pre
        # events are pending identities and remain in the shared buffer.
        # Keep every candidate in the planner until its paired post result is
        # handled.  A failed/unknown post is still a protected individual
        # outcome, but letting the planner flush it preserves the original
        # pending call instead of exposing the summary marker as a pre row.
        optional_routine = classification is not None and classification.routine_candidate
        try:
            flush_ok = store.flush_selected_admission(
                workspace_commitment,
                summary_builder=build_routine_read_summary,
                force=not optional_routine,
                material_boundary=not optional_routine,
            )
        except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
            flush_ok = False
        if not flush_ok:
            delivery_blocked = True
            break

        result = store.ingest(envelope)
        if result.disposition.value not in {"accepted", "duplicate"}:
            delivery_blocked = True
            break
        # Re-read pressure after every committed input: one stream pass can
        # contain enough routine calls to cross a profile boundary.
        current_pressure = _stream_selection_pressure(
            store, workspace_commitment, session_commitment
        )
        if current_pressure is False:
            effective_focused = False
            effective_mode = ObservationMode.FOCUSED
        elif current_pressure is None:
            effective_focused = True
            effective_mode = ObservationMode.FOCUSED
        else:
            raw_mode = getattr(current_pressure, "effective_mode", None)
            effective_mode = (
                raw_mode if isinstance(raw_mode, ObservationMode) else ObservationMode.FOCUSED
            )
            effective_focused = effective_mode is ObservationMode.FOCUSED

        selection_envelope = envelope
        selection_fence = ""
        read_protection_probe: ObservationEnvelope | None = None
        read_protected = False
        admitted = False
        if classification is not None and classification.routine_candidate:
            selection_envelope, selection_fence = _stream_selection_context(
                store,
                workspace_commitment,
                codex_session_id,
                envelope,
                mode=effective_mode,
                classification=classification,
            )
            if selection_fence:
                inherited_gaps = _stream_selection_history_gaps(
                    store,
                    workspace_commitment,
                    selection_envelope,
                )
                if inherited_gaps:
                    selection_envelope = replace(
                        selection_envelope,
                        gap_codes=tuple(
                            sorted(
                                {*selection_envelope.gap_codes, *inherited_gaps},
                                key=str.encode,
                            )
                        ),
                    )
            # Read protection is an explicit retention request and therefore
            # outranks Focused/Detailed selection.  Its attempt identity is
            # source-scoped by the store, so a native hook copy and this
            # session-stream copy intentionally reserve independent slots.
            read_protection_probe, protection_reference = _stream_read_protection_probe(
                store,
                workspace_commitment,
                session_commitment,
                selection_envelope,
                classification,
            )
            read_protected = read_protection_probe is not None
            if read_protected:
                assert read_protection_probe is not None
                selection_envelope = _stamp_stream_read_protection(
                    read_protection_probe,
                    protection_reference,
                )
            # ``routine_read`` is a service-owned summary marker. Keep the
            # original stream shape when the route is absent or Detailed mode
            # is active so individual materialization still produces action and
            # result records without captured content.
            if not read_protected:
                selection_envelope = _selection_envelope(
                    selection_envelope,
                    classification,
                    focused=effective_focused,
                    routed=bool(selection_fence),
                )

        deliverable = self_observation_deliverable(
            _stream_phase(envelope.structural_payload), envelope.structural_payload
        )
        if deliverable:
            try:
                plan = store.prepare_selected_admission(
                    workspace_commitment,
                    codex_session_id,
                    selection_envelope,
                    fence=selection_fence,
                    focused=effective_focused and not read_protected,
                    routine_candidate=(
                        False if classification is None else classification.routine_candidate
                    ),
                    proven_routine_success=(
                        False if classification is None else classification.proven_routine_success
                    ),
                    summary_builder=build_routine_read_summary,
                )
                plan = _restore_stream_individual_deliveries(plan)
                admitted = store.commit_selected_admission(
                    workspace_commitment,
                    plan,
                    incoming=envelope,
                    newly_observed=result.disposition.value == "accepted",
                    replayable=True,
                )
            except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
                admitted = False
            if not admitted:
                # The local envelope is retained, but its source position is
                # replayed until either its individual row or its summary
                # account is durably admitted.
                overflow = True
                delivery_blocked = True
                break
            if (
                admitted
                and read_protected
                and read_protection_probe is not None
                and action in {"function_call_output", "custom_tool_call_output"}
            ):
                consume = getattr(store, "consume_read_protection", None)
                if callable(consume):
                    try:
                        consume(workspace_commitment, session_commitment, read_protection_probe)
                    except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
                        pass
        elif result.disposition.value == "accepted":
            # Self-observation suppression is intentional selection, not a
            # missing observation.  Count each accepted stream input once so
            # status/check accounting can distinguish it from queue loss.
            note_omission = getattr(store, "note_selection_omission", None)
            if callable(note_omission):
                try:
                    note_omission(workspace_commitment)
                except AttributeError, OSError, ProtocolValueError, TypeError, ValueError:
                    # The source cursor must not pass an accepted input when
                    # its intentional-omission accounting could not commit.
                    delivery_blocked = True
                    break
        # Pairing provenance is needed even for Yoetz-owned calls that stay
        # local-only. It is committed with the same stream cursor below.
        call_tools = candidate_call_tools
        # Yoetz-owned calls remain local-only where the self-observation policy
        # says the service already has the authoritative record.
        committed_cursor = envelope.cursor
        if result.disposition.value == "accepted":
            accepted += 1
        else:
            duplicates += 1
    if not overflow and not delivery_blocked:
        committed_cursor = advance.cursor
    source_read_complete = ObservationGapCode.SOURCE_LAG.value not in advance.gaps
    progress_committed = committed_cursor != existing
    if progress_committed or (not overflow and not delivery_blocked and source_read_complete):
        persisted_partial = (
            advance.partial_line
            if not overflow and not delivery_blocked and committed_cursor == advance.cursor
            else (
                partial
                if partial.startswith(_OVERSIZED_PARTIAL_PREFIX) and committed_cursor == existing
                else b""
            )
        )
        persisted_call_tools = call_tools
        same_generation = committed_cursor.source_generation == advance.cursor.source_generation
        persisted_identity = reader.source_identity if same_generation else source_identity
        persisted_profile_id = (
            (None if reader.profile is None else reader.profile.profile_id)
            if same_generation
            else prior_profile_id
        )
    else:
        # No cursor progress means no new source identity or pairing state may
        # commit: the next process must rediscover rotation and replay line 1.
        persisted_partial = partial
        persisted_call_tools = prior_call_tools
        persisted_identity = source_identity
        persisted_profile_id = prior_profile_id
    store.set_stream_reconcile_state(
        workspace_commitment,
        session_commitment,
        cursor=committed_cursor,
        partial=persisted_partial,
        call_tools=persisted_call_tools,
        source_identity=persisted_identity,
        profile_id=persisted_profile_id,
    )
    store.note_stream_reconcile(workspace_commitment)
    gaps = advance.gaps
    for durable_gap in (
        ObservationGapCode.UNSUPPORTED_EVENT.value,
        ObservationGapCode.UNSUPPORTED_FORMAT.value,
    ):
        if durable_gap in gaps:
            store.note_coverage_gap(workspace_commitment, durable_gap)
    if overflow and ObservationGapCode.OUTBOX_OVERFLOW.value not in gaps:
        gaps = (*gaps, ObservationGapCode.OUTBOX_OVERFLOW.value)
    admitted_profile = stream_profile_from_id(persisted_profile_id)
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": gaps,
        "byte_position": committed_cursor.byte_position,
        "event_position": committed_cursor.event_position,
        "generation": committed_cursor.source_generation,
        "profile_id": persisted_profile_id,
        # Structural admission is a parser fact about this pass, never host support (#656).
        "admission": stream_admission(admitted_profile, gaps, advance.reason_codes),
        "admission_provenance": (
            None if admitted_profile is None else rollout_admission_provenance(admitted_profile)
        ),
        "admission_reasons": advance.reason_codes,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
        "resolved": True,
    }
