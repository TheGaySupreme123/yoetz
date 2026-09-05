"""Live Codex observation values, cursors, status, and advice snapshots."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType, NotImplementedType
from typing import Final, Literal, cast

from yoetz.domain.observation_profiles import (
    is_content_capture_profile,
    validate_content_capture_profile,
)
from yoetz.domain.values import (
    FindingId,
    JsonObject,
    JsonValue,
    Timestamp,
    finding_id,
    object_id,
    session_id,
    timestamp_from_string,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import Coverage, coverage_from_json, coverage_to_json
from yoetz.protocol.errors import PROTOCOL_REASON_CODES, ProtocolValueError

__all__ = [
    "AdviceItem",
    "AdviceSnapshot",
    "OBSERVATION_BACKPRESSURE_REASON",
    "OBSERVATION_HOOK_COMMITMENT_DOMAIN",
    "OBSERVATION_STREAM_LINE_DOMAIN",
    "OBSERVATION_WORKSPACE_DOMAIN",
    "ObservationControlCommand",
    "ObservationContentChunk",
    "ObservationContentManifest",
    "ObservationContentKind",
    "ObservationCursor",
    "ObservationEnvelope",
    "ObservationGapCode",
    "ObservationIngestDisposition",
    "ObservationIngestRequest",
    "ObservationIngestResult",
    "ObservationLifecycle",
    "ObservationInspectionSnapshot",
    "ObservationRevokeCommand",
    "ObservationSource",
    "ObservationStatus",
    "ObservationStatusQuery",
    "advice_item_from_json",
    "advice_item_to_json",
    "advice_snapshot_from_json",
    "advice_snapshot_to_json",
    "hook_source_commitment",
    "observation_control_command_from_json",
    "observation_control_command_to_json",
    "observation_content_chunk_from_json",
    "observation_content_chunk_to_json",
    "observation_cursor_from_json",
    "observation_cursor_to_json",
    "observation_earns_hook_observed",
    "observation_envelope_from_json",
    "observation_envelope_to_json",
    "observation_ingest_request_from_json",
    "observation_ingest_request_to_json",
    "observation_ingest_result_from_json",
    "observation_ingest_result_to_json",
    "observation_revoke_command_from_json",
    "observation_revoke_command_to_json",
    "observation_status_from_json",
    "observation_status_query_from_json",
    "observation_status_query_to_json",
    "observation_status_to_json",
    "stream_line_commitment",
    "workspace_commitment_from_path",
    "is_content_capture_profile",
    "validate_content_capture_profile",
]

_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_MAX_STRUCTURAL_BYTES: Final = 65_536
_MAX_CONTENT_REFS: Final = 16
_MAX_GAP_CODES: Final = 64
_MAX_UNSUPPORTED_EVENTS: Final = 64
_MAX_RANKED_FINDINGS: Final = 64
_MAX_SOURCE_COVERAGE: Final = 8
_MAX_ADVICE_SUMMARY: Final = 160
_MAX_ADVICE_DETAIL: Final = 240
_MAX_EVIDENCE_REFS: Final = 16
_MAX_CONTENT_CHUNKS: Final = 16
_MAX_CONTENT_CHUNK_BYTES: Final = 524_288
# Base64 plus structural framing must stay below the existing 1 MiB ordinary
# control-frame ceiling. Larger logical content is sent over multiple requests
# and assembled from independently encrypted chunk objects.
_MAX_CONTENT_TOTAL_BYTES: Final = 700_000

_TOKEN_RE: Final = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/+-]{0,127}$", re.ASCII)
_GAP_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_ADVICE_TEXT_RE: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 .,:;'_+/-]{0,239}$",
    re.ASCII,
)
_ADVICE_ORIGINS: Final = frozenset({"deterministic", "semantic_model_derived"})
_MEDIA_TYPE_RE: Final = re.compile(
    r"^[a-z][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$",
    re.ASCII,
)

OBSERVATION_WORKSPACE_DOMAIN: Final = b"yoetz/observation-workspace/v1\x00"
OBSERVATION_STREAM_LINE_DOMAIN: Final = b"yoetz/observation-stream-line/v1\x00"
OBSERVATION_HOOK_COMMITMENT_DOMAIN: Final = b"yoetz/observation-hook-commitment/v1\x00"

_STRUCTURAL_KEYS: Final = frozenset(
    {
        "tool_name",
        "action",
        "exit_status",
        "correlation_id",
        "changed_paths_digest",
        "result_status",
        "permission_decision",
        "subagent_id",
        "claim_kind",
        "event_ordinal",
        "duration_ms",
        "attempt",
        "success",
        "denied",
        "truncated",
        "source_lag_ms",
        "hook_name",
        "stream_kind",
        "command_digest",
        "argv_digest",
        "cwd_commitment",
        "file_count",
        "bytes_touched",
        "tool_call_id",
        "parent_tool_call_id",
        "permission_kind",
        "decision_reason_code",
        "mapping_hint",
        "capability_profile_id",
        "codex_version",
        "cursor_version",
        "model_id",
        "model_effort",
        "pairing_mode",
        "correlation_kind",
        "generation_id",
    }
)
_STRUCTURAL_TOKEN_KEYS: Final = frozenset(
    {
        "cursor_version",
        "model_id",
        "model_effort",
        "pairing_mode",
        "correlation_kind",
        "generation_id",
    }
)

_PROSE_KEYS: Final = frozenset(
    {
        "transcript",
        "reasoning",
        "content_full",
        "content",
        "message",
        "output",
        "stderr",
        "stdout",
        "prompt",
        "hidden_reasoning",
        "thinking",
        "raw_text",
        "body",
        "text",
        "command",
        "argv",
        "cwd",
        "path",
        "paths",
        "working_directory",
    }
)


class ObservationSource(str, Enum):  # noqa: UP042 - exact durable wire enum
    CLAUDE_HOOK = "claude_hook"
    CODEX_HOOK = "codex_hook"
    CODEX_SESSION_STREAM = "codex_session_stream"
    CURSOR_HOOK = "cursor_hook"


class ObservationLifecycle(str, Enum):  # noqa: UP042 - exact durable wire enum
    ACTIVE = "active"
    DEGRADED = "degraded"
    STALE = "stale"
    STOPPED = "stopped"


# Retryable ingest-rejection reason for designed back-pressure (#351): the
# observation ledger deferred the append behind an ADR-022 check-acquisition or
# frozen-case barrier (or equivalent transient bundle/frontier contention). It
# is deliberately not an ObservationGapCode — a deferral is expected
# coordination, never a current coverage gap, and must not project into
# observation status, advice, coverage, or receipt inputs. The durable outbox
# simply keeps the row pending and retries after the barrier clears.
OBSERVATION_BACKPRESSURE_REASON: Final = "operation_pending"


class ObservationGapCode(str, Enum):  # noqa: UP042 - exact durable wire enum
    UNPAIRED_EVENT = "unpaired_event"
    UNSUPPORTED_EVENT = "unsupported_event"
    UNSUPPORTED_FORMAT = "unsupported_format"
    TRUNCATED_PAYLOAD = "truncated_payload"
    SERVICE_UNAVAILABLE = "service_unavailable"
    VAULT_LOCKED = "vault_locked"
    LEDGER_REJECTED = "ledger_rejected"
    DEDUP_CONFLICT = "dedup_conflict"
    CURSOR_STALE = "cursor_stale"
    CONSENT_MISSING = "consent_missing"
    CONSENT_REVOKED = "consent_revoked"
    SOURCE_LAG = "source_lag"
    MAPPING_MISSING = "mapping_missing"
    SESSION_SUPERSEDED = "session_superseded"
    OUTBOX_OVERFLOW = "outbox_overflow"
    OUTBOX_QUARANTINED = "outbox_quarantined"
    OBSERVATION_STORAGE_CORRUPT = "observation_storage_corrupt"
    QUARANTINE_DETAIL_EVICTED = "quarantine_detail_evicted"
    CONTENT_CAPTURE_UNAVAILABLE = "content_capture_unavailable"
    CONTENT_CAPTURE_PROFILE_MISMATCH = "content_capture_profile_mismatch"
    CONTENT_UNSELECTED = "content_unselected"
    CONTENT_REDACTED = "content_redacted"
    POLICY_UNTRUSTED = "policy_untrusted"
    VERIFICATION_STALE = "verification_stale"
    NETWORK_CHECK_UNSUPPORTED = "network_check_unsupported"


class ObservationContentKind(str, Enum):  # noqa: UP042 - exact durable wire enum
    VISIBLE_USER_MESSAGE = "visible_user_message"
    VISIBLE_ASSISTANT_MESSAGE = "visible_assistant_message"
    VISIBLE_SUBAGENT_MESSAGE = "visible_subagent_message"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"
    CHANGED_FILE = "changed_file"
    WORKSPACE_DIFF = "workspace_diff"
    APPROVED_CHECK_OUTPUT = "approved_check_output"
    UNSUPPORTED_VISIBLE_PAYLOAD = "unsupported_visible_payload"
    WORKSPACE_LOCATOR = "workspace_locator"


class ObservationIngestDisposition(str, Enum):  # noqa: UP042 - exact durable wire enum
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


def _invalid(reason: str = "invalid_event_value_type") -> ProtocolValueError:
    if reason not in PROTOCOL_REASON_CODES:
        reason = "invalid_event_value_type"
    return ProtocolValueError(reason)


def _token(value: object) -> str:
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        raise _invalid()
    return value


def _gap_code(value: object) -> str:
    if type(value) is ObservationGapCode:
        return value.value
    if type(value) is not str or _GAP_RE.fullmatch(value) is None:
        raise _invalid("invalid_known_gap")
    return value


def _nonnegative(value: object, *, maximum: int = _MAX_SAFE_INTEGER) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _invalid()
    return value


def _positive(value: object, *, maximum: int = _MAX_SAFE_INTEGER) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _invalid()
    return value


def _exact_tuple(value: object, *, maximum: int) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise _invalid()
    result = cast(tuple[object, ...], value)
    if len(result) > maximum:
        raise _invalid()
    return result


def _sorted_unique_tokens(value: object, *, maximum: int) -> tuple[str, ...]:
    raw = _exact_tuple(value, maximum=maximum)
    result: list[str] = []
    previous: str | None = None
    for item in raw:
        member = _token(item)
        if previous is not None and member.encode("ascii") <= previous.encode("ascii"):
            raise _invalid("duplicate_set_member" if member == previous else "unsorted_set_field")
        result.append(member)
        previous = member
    return tuple(result)


def _sorted_unique_gap_codes(value: object, *, maximum: int = _MAX_GAP_CODES) -> tuple[str, ...]:
    raw = _exact_tuple(value, maximum=maximum)
    result: list[str] = []
    previous: str | None = None
    for item in raw:
        member = _gap_code(item)
        if previous is not None and member.encode("ascii") <= previous.encode("ascii"):
            raise _invalid("duplicate_set_member" if member == previous else "unsorted_set_field")
        result.append(member)
        previous = member
    return tuple(result)


def _looks_like_path(value: str) -> bool:
    if "\x00" in value or "\r" in value or "\n" in value:
        return True
    if value.startswith(("/", "\\", "./", "../", "~/", "~\\")):
        return True
    if len(value) >= 3 and value[1] == ":" and value[0].isalpha() and value[2] in {"/", "\\"}:
        return True
    return False


def _reject_path_like(value: object) -> None:
    if type(value) is str:
        if _looks_like_path(value):
            raise _invalid()
        return
    if type(value) is tuple:
        for item in cast(tuple[object, ...], value):
            _reject_path_like(item)
        return
    if type(value) is JsonObject:
        for item in value.values():
            _reject_path_like(item)


def _structural_payload(value: object) -> JsonObject:
    if type(value) is not JsonObject:
        try:
            payload = JsonObject(value)
        except ProtocolValueError as exc:
            raise _invalid() from exc
    else:
        payload = value
    if any(key in _PROSE_KEYS for key in payload):
        raise _invalid("unknown_payload_field")
    if any(key not in _STRUCTURAL_KEYS for key in payload):
        raise _invalid("unknown_payload_field")
    for key, item in payload.items():
        if key.endswith(("_path", "_paths", "_cwd", "_directory")):
            raise _invalid("unknown_payload_field")
        if key in _STRUCTURAL_TOKEN_KEYS:
            _token(item)
        _reject_path_like(item)
    encoded = canonical_encode(payload)
    if len(encoded) > _MAX_STRUCTURAL_BYTES:
        raise _invalid()
    return payload


def _content_object_refs(value: object) -> tuple[str, ...]:
    raw = _exact_tuple(value, maximum=_MAX_CONTENT_REFS)
    result: list[str] = []
    previous: str | None = None
    for item in raw:
        if type(item) is not str:
            raise _invalid()
        if item.startswith("hmac-sha256:"):
            member = validate_commitment(item)
        elif item.startswith("sha256:"):
            member = validate_sha256_digest(item)
        else:
            # Object IDs use the ordinary obj_ prefix; commitments stay path-free.
            if not item.isascii() or not 1 <= len(item) <= 128 or _looks_like_path(item):
                raise _invalid()
            member = item
        if previous is not None and member.encode("ascii") <= previous.encode("ascii"):
            raise _invalid("duplicate_set_member" if member == previous else "unsorted_set_field")
        result.append(member)
        previous = member
    return tuple(result)


def _ranked_finding_ids(value: object) -> tuple[FindingId, ...]:
    raw = _exact_tuple(value, maximum=_MAX_RANKED_FINDINGS)
    result: list[FindingId] = []
    seen: set[str] = set()
    for item in raw:
        member = finding_id(item)
        if member in seen:
            raise _invalid("duplicate_set_member")
        seen.add(member)
        result.append(member)
    return tuple(result)


def _advice_text(value: object, *, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise _invalid()
    if _ADVICE_TEXT_RE.fullmatch(value) is None or _looks_like_path(value):
        raise _invalid()
    lowered = value.lower()
    for banned in ("secret", "password", "token=", "api_key", "bearer ", "/users/", "c:\\"):
        if banned in lowered:
            raise _invalid()
    return value


def _evidence_refs(value: object) -> tuple[str, ...]:
    raw = _exact_tuple(value, maximum=_MAX_EVIDENCE_REFS)
    result: list[str] = []
    previous: str | None = None
    for item in raw:
        member = _token(item)
        if previous is not None and member.encode("ascii") <= previous.encode("ascii"):
            raise _invalid("duplicate_set_member" if member == previous else "unsorted_set_field")
        result.append(member)
        previous = member
    return tuple(result)


def _advice_origin(value: object) -> Literal["deterministic", "semantic_model_derived"]:
    if type(value) is not str or value not in _ADVICE_ORIGINS:
        raise _invalid("invalid_event_enum")
    return cast(Literal["deterministic", "semantic_model_derived"], value)


def _source_coverage(value: object) -> Mapping[ObservationSource, bool]:
    if not isinstance(value, Mapping):
        raise _invalid()
    mapping = cast(Mapping[object, object], value)
    if len(mapping) > _MAX_SOURCE_COVERAGE:
        raise _invalid()
    result: dict[ObservationSource, bool] = {}
    for raw_key, raw_present in mapping.items():
        if type(raw_key) is ObservationSource:
            key = raw_key
        elif type(raw_key) is str:
            try:
                key = ObservationSource(raw_key)
            except ValueError as exc:
                raise _invalid("invalid_event_enum") from exc
        else:
            raise _invalid("invalid_event_enum")
        if type(raw_present) is not bool:
            raise _invalid()
        if key in result:
            raise _invalid("duplicate_set_member")
        result[key] = raw_present
    return MappingProxyType(result)


def workspace_commitment_from_path(key_material: bytes, path: str) -> str:
    """Return an HMAC-SHA-256 commitment for exact filesystem-encoded path bytes.

    The commitment never embeds the raw path. Callers must supply exact key material
    (for example ``K_lookup`` / commitment MAC bytes). Empty or control-bearing paths
    are rejected before hashing.
    """

    if type(key_material) is not bytes or not 16 <= len(key_material) <= 64:
        raise _invalid("invalid_commitment")
    if type(path) is not str or not path or "\x00" in path:
        raise _invalid()
    digest = hmac.new(
        key_material,
        OBSERVATION_WORKSPACE_DOMAIN + os.fsencode(path),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def stream_line_commitment(key_material: bytes, content: bytes) -> str:
    """Keyed HMAC over one session-stream line body (never labeled as plain sha256)."""

    if type(key_material) is not bytes or not 16 <= len(key_material) <= 64:
        raise _invalid("invalid_commitment")
    if type(content) is not bytes:
        raise _invalid()
    digest = hmac.new(
        key_material,
        OBSERVATION_STREAM_LINE_DOMAIN + content,
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def hook_source_commitment(key_material: bytes, source_identity: str) -> str:
    """Keyed HMAC over a hook source-identity token for cursor last-commitment."""

    if type(key_material) is not bytes or not 16 <= len(key_material) <= 64:
        raise _invalid("invalid_commitment")
    if type(source_identity) is not str or not source_identity or "\x00" in source_identity:
        raise _invalid()
    digest = hmac.new(
        key_material,
        OBSERVATION_HOOK_COMMITMENT_DOMAIN + source_identity.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


@dataclass(frozen=True, slots=True)
class ObservationContentChunk:
    """Bounded ephemeral plaintext accepted only by ready-service ingest.

    Chunks are never written to the local structural outbox. They exist long
    enough for the ready service to assemble and encrypt a captured-content
    object, then the mutable byte buffer is discarded by the caller.
    """

    content_kind: ObservationContentKind
    correlation_identity: str
    source_commitment: str
    media_type: str
    part_index: int
    part_count: int
    content: bytes
    redacted: bool = False

    def __post_init__(self) -> None:
        if type(self.content_kind) is not ObservationContentKind:
            raise _invalid("invalid_event_enum")
        object.__setattr__(self, "correlation_identity", _token(self.correlation_identity))
        validate_commitment(self.source_commitment)
        if (
            type(self.media_type) is not str
            or len(self.media_type) > 128
            or _MEDIA_TYPE_RE.fullmatch(self.media_type) is None
        ):
            raise _invalid()
        object.__setattr__(self, "part_index", _nonnegative(self.part_index, maximum=15))
        object.__setattr__(self, "part_count", _positive(self.part_count, maximum=16))
        if self.part_index >= self.part_count:
            raise _invalid()
        if (
            type(self.content) is not bytes
            or not self.content
            or len(self.content) > _MAX_CONTENT_CHUNK_BYTES
            or type(self.redacted) is not bool
        ):
            raise _invalid()

    def __repr__(self) -> str:
        return (
            "ObservationContentChunk("
            f"kind={self.content_kind.value!r}, "
            f"correlation_identity={self.correlation_identity!r}, "
            f"part={self.part_index + 1}/{self.part_count}, "
            f"bytes={len(self.content)}, redacted={self.redacted})"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ObservationContentManifest:
    """Trusted-local descriptor for one encrypted observation-content object.

    ``content_digest`` and ``content_bytes`` describe the secret-scanned bytes inside the
    encrypted manifest, not the encrypted envelope or its structural wrapper. Historical rows
    created before that binding was stored leave both values ``None`` and cannot earn captured
    evidence provenance until the service re-observes and backfills them. ``envelope_digest`` may
    be ``None`` only for lookup-only recovery of an orphaned manifest whose object inventory row
    is unavailable; such a descriptor cannot earn captured coverage.
    """

    object_id: str
    envelope_digest: str | None
    content_kind: ObservationContentKind
    part_index: int
    part_count: int
    redacted: bool
    content_digest: str | None = None
    content_bytes: int | None = None
    correlation_identity: str | None = None
    source_commitment: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", object_id(self.object_id))
        if self.envelope_digest is not None:
            validate_sha256_digest(self.envelope_digest)
        if type(self.content_kind) is not ObservationContentKind:
            raise _invalid("invalid_event_enum")
        object.__setattr__(self, "part_index", _nonnegative(self.part_index, maximum=15))
        object.__setattr__(self, "part_count", _positive(self.part_count, maximum=16))
        if self.part_index >= self.part_count or type(self.redacted) is not bool:
            raise _invalid()
        if (self.content_digest is None) != (self.content_bytes is None):
            raise _invalid()
        if self.content_digest is not None:
            validate_sha256_digest(self.content_digest)
            object.__setattr__(
                self,
                "content_bytes",
                _positive(self.content_bytes, maximum=_MAX_CONTENT_CHUNK_BYTES),
            )
        if self.correlation_identity is not None:
            object.__setattr__(
                self,
                "correlation_identity",
                _token(self.correlation_identity),
            )
        if self.source_commitment is not None:
            validate_commitment(self.source_commitment)


@dataclass(frozen=True, slots=True)
class ObservationInspectionSnapshot:
    """Immutable object bindings for one current changed-path inspection snapshot."""

    snapshot_id: str
    yoetz_session_id: str
    subject_state_digest: str
    changed_paths_digest: str
    facts_object_id: str | None
    facts_content_digest: str | None
    facts_content_bytes: int | None
    excerpt_object_id: str | None
    excerpt_content_digest: str | None
    excerpt_content_bytes: int | None
    excerpt_redacted: bool
    excerpt_truncated: bool
    recorded_at: Timestamp

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _token(self.snapshot_id))
        object.__setattr__(self, "yoetz_session_id", session_id(self.yoetz_session_id))
        validate_sha256_digest(self.subject_state_digest)
        validate_sha256_digest(self.changed_paths_digest)
        if type(self.recorded_at) is not Timestamp:
            raise _invalid("invalid_timestamp")
        if type(self.excerpt_redacted) is not bool or type(self.excerpt_truncated) is not bool:
            raise _invalid()
        if self.excerpt_object_id is None and (self.excerpt_redacted or self.excerpt_truncated):
            raise _invalid()
        for object_value, digest_value, byte_value in (
            (self.facts_object_id, self.facts_content_digest, self.facts_content_bytes),
            (self.excerpt_object_id, self.excerpt_content_digest, self.excerpt_content_bytes),
        ):
            if (object_value is None, digest_value is None, byte_value is None) not in {
                (True, True, True),
                (False, False, False),
            }:
                raise _invalid()
            if object_value is not None:
                object_id(object_value)
                validate_sha256_digest(cast(str, digest_value))
                _positive(byte_value, maximum=4_194_304)


def _content_chunks(value: object) -> tuple[ObservationContentChunk, ...]:
    raw = _exact_tuple(value, maximum=_MAX_CONTENT_CHUNKS)
    chunks: list[ObservationContentChunk] = []
    total = 0
    groups: dict[tuple[str, str, str, str], tuple[int, set[int]]] = {}
    for item in raw:
        if type(item) is not ObservationContentChunk:
            raise _invalid()
        chunks.append(item)
        total += len(item.content)
        key = (
            item.content_kind.value,
            item.correlation_identity,
            item.source_commitment,
            item.media_type,
        )
        expected, indexes = groups.setdefault(key, (item.part_count, set()))
        if expected != item.part_count or item.part_index in indexes:
            raise _invalid()
        indexes.add(item.part_index)
    if total > _MAX_CONTENT_TOTAL_BYTES:
        raise _invalid()
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class ObservationCursor:
    source_generation: int
    byte_position: int
    event_position: int
    last_source_commitment: str
    mapping_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_generation", _positive(self.source_generation))
        object.__setattr__(self, "byte_position", _nonnegative(self.byte_position))
        object.__setattr__(self, "event_position", _nonnegative(self.event_position))
        validate_commitment(self.last_source_commitment)
        object.__setattr__(self, "mapping_version", _token(self.mapping_version))

    def _compare_key(self) -> tuple[int, int, int]:
        return (self.source_generation, self.byte_position, self.event_position)

    def __lt__(self, other: object) -> bool | NotImplementedType:
        if type(other) is not ObservationCursor:
            return NotImplemented
        return self._compare_key() < other._compare_key()

    def __le__(self, other: object) -> bool | NotImplementedType:
        if type(other) is not ObservationCursor:
            return NotImplemented
        return self._compare_key() <= other._compare_key()

    def is_stale_relative_to(self, other: ObservationCursor) -> bool:
        if type(other) is not ObservationCursor:
            raise _invalid()
        if self.source_generation < other.source_generation:
            return True
        if self.source_generation > other.source_generation:
            return False
        return (self.byte_position, self.event_position) < (
            other.byte_position,
            other.event_position,
        )


@dataclass(frozen=True, slots=True, repr=False)
class ObservationEnvelope:
    session_commitment: str
    event_kind: str
    source_identity: str
    source: ObservationSource
    cursor: ObservationCursor
    receipt_time: Timestamp
    structural_payload: JsonObject
    content_object_refs: tuple[str, ...]
    gap_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_commitment(self.session_commitment)
        object.__setattr__(self, "event_kind", _token(self.event_kind))
        object.__setattr__(self, "source_identity", _token(self.source_identity))
        if type(self.source) is not ObservationSource:
            raise _invalid("invalid_event_enum")
        if type(self.cursor) is not ObservationCursor:
            raise _invalid()
        if type(self.receipt_time) is not Timestamp:
            raise _invalid("invalid_timestamp")
        object.__setattr__(self, "structural_payload", _structural_payload(self.structural_payload))
        object.__setattr__(
            self, "content_object_refs", _content_object_refs(self.content_object_refs)
        )
        object.__setattr__(self, "gap_codes", _sorted_unique_gap_codes(self.gap_codes))

    def __repr__(self) -> str:
        return (
            "ObservationEnvelope("
            f"source={self.source.value!r}, "
            f"event_kind={self.event_kind!r}, "
            f"source_identity={self.source_identity!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class AdviceItem:
    """Bounded durable advice value safe for status, observe status, and hooks."""

    finding_id: FindingId
    rule_code: str
    priority: int
    summary: str
    detail: str
    recommended_next_action: str
    evidence_refs: tuple[str, ...]
    coverage: Coverage
    freshness_frontier: str
    origin: Literal["deterministic", "semantic_model_derived"] = "deterministic"
    condition_identity: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", finding_id(self.finding_id))
        object.__setattr__(self, "rule_code", _token(self.rule_code))
        object.__setattr__(self, "priority", _nonnegative(self.priority, maximum=1_000))
        object.__setattr__(self, "summary", _advice_text(self.summary, maximum=_MAX_ADVICE_SUMMARY))
        object.__setattr__(self, "detail", _advice_text(self.detail, maximum=_MAX_ADVICE_DETAIL))
        object.__setattr__(self, "recommended_next_action", _token(self.recommended_next_action))
        object.__setattr__(self, "evidence_refs", _evidence_refs(self.evidence_refs))
        if type(self.coverage) is not Coverage:
            raise _invalid("invalid_coverage_value")
        object.__setattr__(self, "freshness_frontier", _token(self.freshness_frontier))
        object.__setattr__(self, "origin", _advice_origin(self.origin))
        if self.condition_identity is not None:
            object.__setattr__(self, "condition_identity", _token(self.condition_identity))


def _ranked_advice_items(value: object) -> tuple[AdviceItem, ...]:
    raw = _exact_tuple(value, maximum=_MAX_RANKED_FINDINGS)
    result: list[AdviceItem] = []
    seen: set[str] = set()
    for item in raw:
        if type(item) is not AdviceItem:
            raise _invalid()
        key = str(item.finding_id)
        if key in seen:
            raise _invalid("duplicate_set_member")
        seen.add(key)
        result.append(item)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AdviceSnapshot:
    ranked_finding_ids: tuple[FindingId, ...]
    evidence_basis_digest: str
    confidence_coverage: Coverage
    recommended_next_action: str
    freshness_frontier: str
    suppression_identity: str
    ranked_items: tuple[AdviceItem, ...] = ()

    def __post_init__(self) -> None:
        items = _ranked_advice_items(self.ranked_items)
        if items:
            derived = tuple(item.finding_id for item in items)
            if self.ranked_finding_ids and _ranked_finding_ids(self.ranked_finding_ids) != derived:
                raise _invalid()
            object.__setattr__(self, "ranked_items", items)
            object.__setattr__(self, "ranked_finding_ids", derived)
        else:
            object.__setattr__(
                self, "ranked_finding_ids", _ranked_finding_ids(self.ranked_finding_ids)
            )
            object.__setattr__(self, "ranked_items", ())
        validate_sha256_digest(self.evidence_basis_digest)
        if type(self.confidence_coverage) is not Coverage:
            raise _invalid("invalid_coverage_value")
        object.__setattr__(self, "recommended_next_action", _token(self.recommended_next_action))
        object.__setattr__(self, "freshness_frontier", _token(self.freshness_frontier))
        object.__setattr__(self, "suppression_identity", _token(self.suppression_identity))


@dataclass(frozen=True, slots=True)
class ObservationStatus:
    lifecycle: ObservationLifecycle
    workspace_commitment: str
    source_coverage: Mapping[ObservationSource, bool]
    last_observation_receipt_time: Timestamp | None
    lag_events: int
    gaps: tuple[str, ...]
    unsupported_events: tuple[str, ...]
    advice_frontier: str | None

    def __post_init__(self) -> None:
        if type(self.lifecycle) is not ObservationLifecycle:
            raise _invalid("invalid_event_enum")
        validate_commitment(self.workspace_commitment)
        object.__setattr__(self, "source_coverage", _source_coverage(self.source_coverage))
        if (
            self.last_observation_receipt_time is not None
            and type(self.last_observation_receipt_time) is not Timestamp
        ):
            raise _invalid("invalid_timestamp")
        object.__setattr__(self, "lag_events", _nonnegative(self.lag_events))
        object.__setattr__(self, "gaps", _sorted_unique_gap_codes(self.gaps))
        object.__setattr__(
            self,
            "unsupported_events",
            _sorted_unique_tokens(self.unsupported_events, maximum=_MAX_UNSUPPORTED_EVENTS),
        )
        if self.advice_frontier is not None:
            object.__setattr__(self, "advice_frontier", _token(self.advice_frontier))


@dataclass(frozen=True, slots=True)
class ObservationIngestResult:
    disposition: ObservationIngestDisposition
    reason: str | None
    advanced_cursor: ObservationCursor | None

    def __post_init__(self) -> None:
        if type(self.disposition) is not ObservationIngestDisposition:
            raise _invalid("invalid_event_enum")
        if self.disposition is ObservationIngestDisposition.ACCEPTED:
            if self.reason is not None:
                raise _invalid()
            if type(self.advanced_cursor) is not ObservationCursor:
                raise _invalid()
        elif self.disposition is ObservationIngestDisposition.DUPLICATE:
            if self.reason is not None:
                object.__setattr__(self, "reason", _token(self.reason))
            if (
                self.advanced_cursor is not None
                and type(self.advanced_cursor) is not ObservationCursor
            ):
                raise _invalid()
        else:
            if self.reason is None:
                raise _invalid()
            object.__setattr__(self, "reason", _token(self.reason))
            if (
                self.advanced_cursor is not None
                and type(self.advanced_cursor) is not ObservationCursor
            ):
                raise _invalid()


@dataclass(frozen=True, slots=True)
class ObservationIngestRequest:
    """Redacted local-control ingest body: session routing, envelope, and bounded chunks.

    Callers MUST NOT supply Yoetz task/session/writer IDs. The service coordinator
    resolves those from the validated lifecycle mapping. Native-host chunks also
    carry the exact user-enabled content profile; profile-less requests preserve
    the historical structural-only path.
    """

    codex_session_id: str
    envelope: ObservationEnvelope
    content_chunks: tuple[ObservationContentChunk, ...] = ()
    # Native Claude/Cursor content is admitted only with the exact profile
    # explicitly enabled by the user.  Codex's historical session-stream
    # content remains profile-less for backward compatibility.
    content_capture_profile: str | None = None

    def __post_init__(self) -> None:
        if type(self.codex_session_id) is not str or not self.codex_session_id:
            raise _invalid()
        if (
            "\x00" in self.codex_session_id
            or "/" in self.codex_session_id
            or "\\" in self.codex_session_id
        ):
            raise _invalid()
        if len(self.codex_session_id) > 128:
            raise _invalid()
        if type(self.envelope) is not ObservationEnvelope:
            raise _invalid()
        object.__setattr__(self, "content_chunks", _content_chunks(self.content_chunks))
        if self.content_capture_profile is not None:
            try:
                validate_content_capture_profile(self.content_capture_profile)
            except ProtocolValueError as exc:
                raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class ObservationStatusQuery:
    workspace_commitment: str

    def __post_init__(self) -> None:
        validate_commitment(self.workspace_commitment)


@dataclass(frozen=True, slots=True)
class ObservationControlCommand:
    workspace_commitment: str

    def __post_init__(self) -> None:
        validate_commitment(self.workspace_commitment)


@dataclass(frozen=True, slots=True)
class ObservationRevokeCommand:
    workspace_commitment: str
    retain_evidence: Literal[True] = True

    def __post_init__(self) -> None:
        validate_commitment(self.workspace_commitment)
        if self.retain_evidence is not True:
            raise _invalid()


def observation_earns_hook_observed(status: ObservationStatus, has_real_evidence: bool) -> bool:
    """Return True only for active lifecycle with real observation evidence."""

    if type(status) is not ObservationStatus:
        raise _invalid()
    if type(has_real_evidence) is not bool:
        raise _invalid()
    return status.lifecycle is ObservationLifecycle.ACTIVE and has_real_evidence is True


def observation_cursor_to_json(value: ObservationCursor) -> JsonObject:
    return JsonObject(
        {
            "source_generation": value.source_generation,
            "byte_position": value.byte_position,
            "event_position": value.event_position,
            "last_source_commitment": value.last_source_commitment,
            "mapping_version": value.mapping_version,
        }
    )


def observation_cursor_from_json(value: JsonValue) -> ObservationCursor:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = (
        "source_generation",
        "byte_position",
        "event_position",
        "last_source_commitment",
        "mapping_version",
    )
    if set(source) != set(required):
        raise _invalid()
    return ObservationCursor(
        source_generation=cast(int, source["source_generation"]),
        byte_position=cast(int, source["byte_position"]),
        event_position=cast(int, source["event_position"]),
        last_source_commitment=cast(str, source["last_source_commitment"]),
        mapping_version=cast(str, source["mapping_version"]),
    )


def observation_envelope_to_json(value: ObservationEnvelope) -> JsonObject:
    return JsonObject(
        {
            "session_commitment": value.session_commitment,
            "event_kind": value.event_kind,
            "source_identity": value.source_identity,
            "source": value.source.value,
            "cursor": observation_cursor_to_json(value.cursor),
            "receipt_time": value.receipt_time.wire,
            "structural_payload": value.structural_payload,
            "content_object_refs": value.content_object_refs,
            "gap_codes": value.gap_codes,
        }
    )


def observation_envelope_from_json(value: JsonValue) -> ObservationEnvelope:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = (
        "session_commitment",
        "event_kind",
        "source_identity",
        "source",
        "cursor",
        "receipt_time",
        "structural_payload",
        "content_object_refs",
        "gap_codes",
    )
    if set(source) != set(required):
        raise _invalid()
    try:
        observation_source = ObservationSource(cast(str, source["source"]))
    except ValueError as exc:
        raise _invalid("invalid_event_enum") from exc
    return ObservationEnvelope(
        session_commitment=cast(str, source["session_commitment"]),
        event_kind=cast(str, source["event_kind"]),
        source_identity=cast(str, source["source_identity"]),
        source=observation_source,
        cursor=observation_cursor_from_json(source["cursor"]),
        receipt_time=timestamp_from_string(source["receipt_time"]),
        structural_payload=_structural_payload(source["structural_payload"]),
        content_object_refs=_content_object_refs(source["content_object_refs"]),
        gap_codes=_sorted_unique_gap_codes(source["gap_codes"]),
    )


def advice_item_to_json(value: AdviceItem) -> JsonObject:
    return JsonObject(
        {
            "finding_id": str(value.finding_id),
            "rule_code": value.rule_code,
            "priority": value.priority,
            "summary": value.summary,
            "detail": value.detail,
            "recommended_next_action": value.recommended_next_action,
            "evidence_refs": value.evidence_refs,
            "coverage": JsonObject(coverage_to_json(value.coverage)),
            "freshness_frontier": value.freshness_frontier,
            "origin": value.origin,
            "condition_identity": value.condition_identity,
        }
    )


def advice_item_from_json(value: JsonValue) -> AdviceItem:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = (
        "finding_id",
        "rule_code",
        "priority",
        "summary",
        "detail",
        "recommended_next_action",
        "evidence_refs",
        "coverage",
        "freshness_frontier",
        "origin",
    )
    source_keys = set(source)
    if source_keys != set(required) and source_keys != {*required, "condition_identity"}:
        raise _invalid()
    refs = source["evidence_refs"]
    return AdviceItem(
        finding_id=finding_id(source["finding_id"]),
        rule_code=cast(str, source["rule_code"]),
        priority=cast(int, source["priority"]),
        summary=cast(str, source["summary"]),
        detail=cast(str, source["detail"]),
        recommended_next_action=cast(str, source["recommended_next_action"]),
        evidence_refs=_evidence_refs(refs),
        coverage=coverage_from_json(source["coverage"]),
        freshness_frontier=cast(str, source["freshness_frontier"]),
        origin=_advice_origin(source["origin"]),
        condition_identity=(
            cast(str, source["condition_identity"]) if "condition_identity" in source else None
        ),
    )


def advice_snapshot_to_json(value: AdviceSnapshot) -> JsonObject:
    return JsonObject(
        {
            "ranked_finding_ids": tuple(str(item) for item in value.ranked_finding_ids),
            "ranked_items": tuple(advice_item_to_json(item) for item in value.ranked_items),
            "evidence_basis_digest": value.evidence_basis_digest,
            "confidence_coverage": JsonObject(coverage_to_json(value.confidence_coverage)),
            "recommended_next_action": value.recommended_next_action,
            "freshness_frontier": value.freshness_frontier,
            "suppression_identity": value.suppression_identity,
        }
    )


def advice_snapshot_from_json(value: JsonValue) -> AdviceSnapshot:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = {
        "ranked_finding_ids",
        "evidence_basis_digest",
        "confidence_coverage",
        "recommended_next_action",
        "freshness_frontier",
        "suppression_identity",
    }
    optional = {"ranked_items"}
    if not required.issubset(set(source)) or set(source) - required - optional:
        raise _invalid()
    items_raw = source.get("ranked_items", ())
    if type(items_raw) is not tuple:
        raise _invalid()
    ids_raw = source["ranked_finding_ids"]
    items = tuple(advice_item_from_json(item) for item in cast(tuple[JsonValue, ...], items_raw))
    return AdviceSnapshot(
        ranked_finding_ids=_ranked_finding_ids(ids_raw),
        evidence_basis_digest=cast(str, source["evidence_basis_digest"]),
        confidence_coverage=coverage_from_json(source["confidence_coverage"]),
        recommended_next_action=cast(str, source["recommended_next_action"]),
        freshness_frontier=cast(str, source["freshness_frontier"]),
        suppression_identity=cast(str, source["suppression_identity"]),
        ranked_items=items,
    )


def observation_status_to_json(value: ObservationStatus) -> JsonObject:
    coverage = {
        key.value: present
        for key, present in sorted(value.source_coverage.items(), key=lambda item: item[0].value)
    }
    payload: dict[str, JsonValue] = {
        "lifecycle": value.lifecycle.value,
        "workspace_commitment": value.workspace_commitment,
        "source_coverage": JsonObject(coverage),
        "last_observation_receipt_time": (
            None
            if value.last_observation_receipt_time is None
            else value.last_observation_receipt_time.wire
        ),
        "lag_events": value.lag_events,
        "gaps": value.gaps,
        "unsupported_events": value.unsupported_events,
        "advice_frontier": value.advice_frontier,
    }
    return JsonObject(payload)


def observation_status_from_json(value: JsonValue) -> ObservationStatus:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = (
        "lifecycle",
        "workspace_commitment",
        "source_coverage",
        "last_observation_receipt_time",
        "lag_events",
        "gaps",
        "unsupported_events",
        "advice_frontier",
    )
    if set(source) != set(required):
        raise _invalid()
    try:
        lifecycle = ObservationLifecycle(cast(str, source["lifecycle"]))
    except ValueError as exc:
        raise _invalid("invalid_event_enum") from exc
    coverage_raw = source["source_coverage"]
    if type(coverage_raw) is not JsonObject:
        raise _invalid()
    coverage = {
        ObservationSource(key): cast(bool, present) for key, present in coverage_raw.items()
    }
    receipt = source["last_observation_receipt_time"]
    return ObservationStatus(
        lifecycle=lifecycle,
        workspace_commitment=cast(str, source["workspace_commitment"]),
        source_coverage=coverage,
        last_observation_receipt_time=(None if receipt is None else timestamp_from_string(receipt)),
        lag_events=cast(int, source["lag_events"]),
        gaps=_sorted_unique_gap_codes(source["gaps"]),
        unsupported_events=_sorted_unique_tokens(
            source["unsupported_events"], maximum=_MAX_UNSUPPORTED_EVENTS
        ),
        advice_frontier=cast(str | None, source["advice_frontier"]),
    )


def observation_ingest_request_to_json(value: ObservationIngestRequest) -> JsonObject:
    payload: dict[str, JsonValue] = {
        "codex_session_id": value.codex_session_id,
        "envelope": observation_envelope_to_json(value.envelope),
    }
    if value.content_chunks:
        payload["content_chunks"] = tuple(
            observation_content_chunk_to_json(item) for item in value.content_chunks
        )
    if value.content_capture_profile is not None:
        payload["content_capture_profile"] = value.content_capture_profile
    return JsonObject(payload)


def observation_ingest_request_from_json(value: JsonValue) -> ObservationIngestRequest:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    if not {"codex_session_id", "envelope"}.issubset(source) or set(source) - {
        "codex_session_id",
        "envelope",
        "content_chunks",
        "content_capture_profile",
    }:
        raise _invalid()
    envelope_raw = source["envelope"]
    if type(envelope_raw) is not JsonObject:
        if isinstance(envelope_raw, Mapping):
            envelope_raw = JsonObject(cast(Mapping[str, JsonValue], envelope_raw))
        else:
            raise _invalid()
    chunks_raw = source.get("content_chunks", ())
    if type(chunks_raw) is not tuple:
        raise _invalid()
    return ObservationIngestRequest(
        codex_session_id=cast(str, source["codex_session_id"]),
        envelope=observation_envelope_from_json(envelope_raw),
        content_chunks=tuple(
            observation_content_chunk_from_json(item)
            for item in cast(tuple[JsonValue, ...], chunks_raw)
        ),
        content_capture_profile=(
            None
            if source.get("content_capture_profile") is None
            else cast(str, source["content_capture_profile"])
        ),
    )


def observation_content_chunk_to_json(value: ObservationContentChunk) -> JsonObject:
    if type(value) is not ObservationContentChunk:
        raise _invalid()
    return JsonObject(
        {
            "content_kind": value.content_kind.value,
            "correlation_identity": value.correlation_identity,
            "source_commitment": value.source_commitment,
            "media_type": value.media_type,
            "part_index": value.part_index,
            "part_count": value.part_count,
            "content_b64": base64.b64encode(value.content).decode("ascii"),
            "redacted": value.redacted,
        }
    )


def observation_content_chunk_from_json(value: JsonValue) -> ObservationContentChunk:
    if type(value) is not JsonObject:
        if isinstance(value, Mapping):
            value = JsonObject(cast(Mapping[str, JsonValue], value))
        else:
            raise _invalid()
    required = {
        "content_kind",
        "correlation_identity",
        "source_commitment",
        "media_type",
        "part_index",
        "part_count",
        "content_b64",
        "redacted",
    }
    if set(value) != required:
        raise _invalid()
    encoded = value["content_b64"]
    if type(encoded) is not str or len(encoded) > ((_MAX_CONTENT_CHUNK_BYTES + 2) // 3) * 4:
        raise _invalid()
    try:
        content = base64.b64decode(encoded, validate=True)
        kind = ObservationContentKind(cast(str, value["content_kind"]))
    except ValueError as exc:
        raise _invalid() from exc
    return ObservationContentChunk(
        content_kind=kind,
        correlation_identity=cast(str, value["correlation_identity"]),
        source_commitment=cast(str, value["source_commitment"]),
        media_type=cast(str, value["media_type"]),
        part_index=cast(int, value["part_index"]),
        part_count=cast(int, value["part_count"]),
        content=content,
        redacted=cast(bool, value["redacted"]),
    )


def observation_ingest_result_to_json(value: ObservationIngestResult) -> JsonObject:
    return JsonObject(
        {
            "disposition": value.disposition.value,
            "reason": value.reason,
            "advanced_cursor": (
                None
                if value.advanced_cursor is None
                else observation_cursor_to_json(value.advanced_cursor)
            ),
        }
    )


def observation_ingest_result_from_json(value: JsonValue) -> ObservationIngestResult:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    required = ("disposition", "reason", "advanced_cursor")
    if set(source) != set(required):
        raise _invalid()
    try:
        disposition = ObservationIngestDisposition(cast(str, source["disposition"]))
    except ValueError as exc:
        raise _invalid("invalid_event_enum") from exc
    cursor_value = source["advanced_cursor"]
    return ObservationIngestResult(
        disposition=disposition,
        reason=cast(str | None, source["reason"]),
        advanced_cursor=(
            None if cursor_value is None else observation_cursor_from_json(cursor_value)
        ),
    )


def observation_status_query_to_json(value: ObservationStatusQuery) -> JsonObject:
    return JsonObject({"workspace_commitment": value.workspace_commitment})


def observation_status_query_from_json(value: JsonValue) -> ObservationStatusQuery:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    if set(source) != {"workspace_commitment"}:
        raise _invalid()
    return ObservationStatusQuery(workspace_commitment=cast(str, source["workspace_commitment"]))


def observation_control_command_to_json(value: ObservationControlCommand) -> JsonObject:
    return JsonObject({"workspace_commitment": value.workspace_commitment})


def observation_control_command_from_json(value: JsonValue) -> ObservationControlCommand:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    if set(source) != {"workspace_commitment"}:
        raise _invalid()
    return ObservationControlCommand(workspace_commitment=cast(str, source["workspace_commitment"]))


def observation_revoke_command_to_json(value: ObservationRevokeCommand) -> JsonObject:
    return JsonObject(
        {
            "workspace_commitment": value.workspace_commitment,
            "retain_evidence": True,
        }
    )


def observation_revoke_command_from_json(value: JsonValue) -> ObservationRevokeCommand:
    if type(value) is not JsonObject:
        raise _invalid()
    source = value
    if set(source) != {"workspace_commitment", "retain_evidence"}:
        raise _invalid()
    return ObservationRevokeCommand(
        workspace_commitment=cast(str, source["workspace_commitment"]),
        retain_evidence=cast(Literal[True], source["retain_evidence"]),
    )
