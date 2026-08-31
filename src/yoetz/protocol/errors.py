"""Public error vocabulary and bounded protocol-value failures."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import cast

__all__ = [
    "PROTOCOL_REASON_CODES",
    "SAFE_DETAIL_KEYS",
    "ProtocolValueError",
    "PublicErrorCode",
    "PublicOperationError",
    "SafeDetailValue",
    "normalize_safe_details",
]


class PublicErrorCode(str, Enum):  # noqa: UP042 - the wire contract requires these exact bases
    INVALID_REQUEST = "INVALID_REQUEST"
    PROTOCOL_VERSION_UNSUPPORTED = "PROTOCOL_VERSION_UNSUPPORTED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_CONFLICT = "SESSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REQUEST_IDENTITY_CONFLICT = "REQUEST_IDENTITY_CONFLICT"
    OPERATION_PENDING = "OPERATION_PENDING"
    FRONTIER_CONFLICT = "FRONTIER_CONFLICT"
    EVENT_INVALID = "EVENT_INVALID"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    BUNDLE_BUSY = "BUNDLE_BUSY"
    STORAGE_UNSAFE = "STORAGE_UNSAFE"
    STORAGE_CORRUPT = "STORAGE_CORRUPT"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    VAULT_LOCKED = "VAULT_LOCKED"
    PRIVACY_AUTHORITY_REQUIRED = "PRIVACY_AUTHORITY_REQUIRED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_REFUSED = "PROVIDER_REFUSED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    SEMANTIC_RESULT_INVALID = "SEMANTIC_RESULT_INVALID"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


type SafeDetailValue = str | int


_PROTOCOL_REASON_CODE_VALUES: tuple[str, ...] = (
    "accepted_but_unresponsive",
    "accepted_record_shape_invalid",
    "actor_id_malformed",
    "actor_id_not_generated",
    "byte_order_mark_forbidden",
    "claim_revision_invalid",
    "claim_revision_mismatch",
    "commitment_only_object_kind",
    "dependency_changed",
    "duplicate_object_key",
    "duplicate_set_member",
    "empty_check_types",
    "empty_publication_channels",
    "empty_subject_state",
    "endpoint_unsafe",
    "engine_family_wrong_author",
    "entry_digest_mismatch",
    "event_family_not_admitted",
    "event_integer_out_of_range",
    "event_text_out_of_bounds",
    "evidence_digest_availability_invalid",
    "evidence_digest_binding_invalid",
    "evidence_digest_binding_required",
    "evidence_digest_provenance_invalid",
    "evidence_digest_subject_incompatible",
    "evidence_strength_unsupported",
    "expected_frontier_required",
    "finding_json_shape_invalid",
    "finding_priority_mismatch",
    "float_forbidden",
    "frame_invalid",
    "frame_too_large",
    "frontier_changed",
    "frontier_digest_mismatch",
    "id_malformed_uuid",
    "id_not_ascii",
    "id_uuid_not_version_4",
    "id_uuid_wrong_variant",
    "id_wrong_length",
    "id_wrong_prefix",
    "id_wrong_type",
    "import_publication_authority_required",
    "import_report_invalid",
    "input_not_bytes",
    "integer_out_of_safe_range",
    "integer_out_of_sqlite_range",
    "internal_error",
    "invalid_actor_type",
    "invalid_approved_check",
    "invalid_approved_check_policy",
    "invalid_chain",
    "invalid_check_types",
    "invalid_commitment",
    "invalid_cost_fields",
    "invalid_coverage_value",
    "invalid_digest",
    "invalid_duration",
    "invalid_event_enum",
    "invalid_event_schema",
    "invalid_event_value_type",
    "invalid_finding_kind",
    "invalid_finding_origin",
    "invalid_finding_policy_identity",
    "invalid_finding_provenance",
    "invalid_finding_subject_refs",
    "invalid_frontier",
    "invalid_json_pointer",
    "invalid_known_gap",
    "invalid_payload_ref",
    "invalid_projection_locator",
    "invalid_publication_channels",
    "invalid_ranked_findings",
    "invalid_receipt_conclusion",
    "invalid_receipt_document",
    "invalid_receipt_gap",
    "invalid_receipt_obligation",
    "invalid_receipt_redaction",
    "invalid_receipt_response",
    "invalid_receipt_section",
    "invalid_receipt_section_order",
    "invalid_receipt_version_slice",
    "invalid_sampling_params",
    "invalid_semantic_dispatch_kind",
    "invalid_semantic_failure_class",
    "invalid_semantic_outcome_type",
    "invalid_semantic_provenance",
    "invalid_semantic_status_reason_pair",
    "invalid_subject_state",
    "invalid_timestamp",
    "invalid_token_usage",
    "invalid_utf8",
    "ledger_assigned_field_in_request_identity",
    "lone_surrogate",
    "malformed_json",
    "method_forbidden",
    "missing_payload_field",
    "nesting_too_deep",
    "no_obligations_reason_conflict",
    "noncanonical_integer_string",
    "not_an_accepted_envelope",
    "nul_byte_forbidden",
    "object_key_not_string",
    "obligation_change_invalid",
    "obligation_resolution_invalid",
    "obligation_resolution_mismatch",
    "operation_recovery_unavailable",
    "ownership_contended",
    "payload_redaction_mismatch",
    "peer_untrusted",
    "plan_version_conflict",
    "privacy_projection_unavailable",
    "privacy_receipt_not_durable",
    "protocol_mismatch",
    "provider_attempt_provenance_is_not_final",
    "public_error_invalid_correlation_id",
    "public_error_invalid_message",
    "public_error_missing_correlation_id",
    "read_projection_failed",
    "receipt_coverage_mismatch",
    "receipt_gap_not_in_coverage",
    "receipt_json_projection_blocked",
    "receipt_json_shape_invalid",
    "redaction_target_required",
    "ref_mirror_mismatch",
    "request_identity_conflict",
    "request_timeout",
    "response_fields_invalid",
    "response_projection_failed",
    "schema_artifact_role_invalid",
    "schema_artifact_role_mismatch",
    "schema_bytes_invalid",
    "schema_catalog_incomplete",
    "schema_digest_mismatch",
    "schema_draft_unsupported",
    "schema_duplicate_identity",
    "schema_id_mismatch",
    "schema_instance_invalid",
    "schema_kind_mismatch",
    "schema_manifest_duplicate_path",
    "schema_manifest_invalid",
    "schema_manifest_member_mismatch",
    "schema_manifest_missing",
    "schema_name_invalid",
    "schema_not_found",
    "schema_path_unsafe",
    "schema_reference_unresolved",
    "schema_version_mismatch",
    "semantic_provenance_json_shape_invalid",
    "service_draining",
    "service_generation_changed",
    "service_incompatible",
    "service_unavailable",
    "session_superseded",
    "set_member_not_ascii",
    "timestamp_not_utc",
    "timestamp_out_of_range",
    "timestamp_submillisecond_precision",
    "timestamp_timezone_missing",
    "unknown_event_schema",
    "unknown_payload_field",
    "unsorted_set_field",
    "unsupported_json_type",
    "unsupported_payload_type",
    "workspace_task_exists",
)

_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
assert len(_PROTOCOL_REASON_CODE_VALUES) == 164
assert len(_PROTOCOL_REASON_CODE_VALUES) == len(set(_PROTOCOL_REASON_CODE_VALUES))
assert _PROTOCOL_REASON_CODE_VALUES == tuple(sorted(_PROTOCOL_REASON_CODE_VALUES, key=str.encode))
assert all(_REASON_CODE_PATTERN.fullmatch(value) for value in _PROTOCOL_REASON_CODE_VALUES)

PROTOCOL_REASON_CODES: frozenset[str] = frozenset(_PROTOCOL_REASON_CODE_VALUES)

SAFE_DETAIL_KEYS: tuple[str, ...] = (
    "actual_version",
    "authorize_command",
    "availability",
    "availability_inherited",
    "availability_request_id",
    "component",
    "continuation",
    "count",
    "expected_version",
    "field",
    "head_digest",
    "host_profile",
    "limit",
    "method",
    "operation",
    "pending_ttl_seconds",
    "phase",
    "prepare_command",
    "quarantine_code",
    "reason_code",
    "replay_request_id",
    "retry_after_ms",
    "review_command",
    "route_profile",
    "schema_name",
    "sequence",
    "session_id",
    "state",
    "status",
    "task_id",
    "view",
    "writer_id",
)

_INTEGER_DETAIL_KEYS = frozenset(
    {"count", "limit", "pending_ttl_seconds", "retry_after_ms", "sequence"}
)
_BOOLEAN_DETAIL_KEYS = frozenset({"availability_inherited"})
# Closed token sets for the MCP bridge's host-binding availability facts (issue #469). The
# binding identity is the bridge's host and route profile; `availability` names the one latched
# state a later request identity may inherit.
# `continuation` and the exact command literals are the typed initialization-required handoff
# (issue #512): every value is a repository constant, so nothing caller-derived can ride these
# keys onto the wire.
_TOKEN_DETAIL_VALUES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "authorize_command": frozenset({"yoetz consent authorize"}),
        "availability": frozenset({"terminal_unavailable"}),
        "continuation": frozenset({"vault_initialization_required"}),
        "host_profile": frozenset({"generic", "cursor"}),
        "prepare_command": frozenset({"yoetz consent prepare vault_initialize"}),
        "review_command": frozenset({"yoetz consent review"}),
        "route_profile": frozenset({"policy", "strict"}),
    }
)
_HEAD_DIGEST_PATTERN = re.compile(r"^(?:genesis|sha256:[0-9a-f]{64})$", re.ASCII)
_ENUM_DETAIL_KEYS = frozenset(
    {"component", "method", "operation", "phase", "state", "status", "view"}
)
_QUARANTINE_CODES = frozenset(
    {
        "operation_event_range_mismatch",
        "operation_kind_state_contradiction",
        "operation_lease_shape_invalid",
        "operation_result_digest_mismatch",
        "operation_resume_object_invalid",
    }
)
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_LOWER_SNAKE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$", re.ASCII)
_SCHEMA_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", re.ASCII)
_CORRELATION_ID_PATTERN = re.compile(
    r"^err_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_REQUEST_ID_DETAIL_PATTERN = re.compile(
    r"^req_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_PROTOCOL_ID_DETAIL_PATTERNS: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {
        "availability_request_id": _REQUEST_ID_DETAIL_PATTERN,
        "replay_request_id": _REQUEST_ID_DETAIL_PATTERN,
        "session_id": re.compile(
            r"^ses_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.ASCII,
        ),
        "task_id": re.compile(
            r"^tsk_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.ASCII,
        ),
        "writer_id": re.compile(
            r"^wri_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.ASCII,
        ),
    }
)
_EMPTY_SAFE_DETAILS: Mapping[str, SafeDetailValue] = MappingProxyType({})


class ProtocolValueError(ValueError):
    """A bounded internal protocol-value failure."""

    __slots__ = ("field", "reason_code")

    reason_code: str
    field: str | None

    def __init__(self, reason_code: str, *, field: str | None = None) -> None:
        if type(reason_code) is not str or reason_code not in PROTOCOL_REASON_CODES:
            raise ValueError("unregistered_protocol_reason_code")
        if field is not None and type(field) is not str:
            raise ValueError("unregistered_protocol_reason_code")
        self.reason_code = reason_code
        # A hint naming the owning payload field, never a value. Callers that turn this into a
        # public error location must still check it against their own frozen allowlist, so a
        # caller-derived string can never reach a public pointer through this attribute.
        self.field = field
        super().__init__(reason_code)


def _valid_correlation_id(value: object) -> bool:
    return type(value) is str and _CORRELATION_ID_PATTERN.fullmatch(value) is not None


def _valid_json_pointer(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(encoded) > 256 or any(byte < 0x20 or byte > 0x7E for byte in encoded):
        return False
    if not value:
        return True
    if not value.startswith("/"):
        return False
    position = 0
    while True:
        position = value.find("~", position)
        if position < 0:
            return True
        if position + 1 >= len(value) or value[position + 1] not in {"0", "1"}:
            return False
        position += 2


def _normalize_detail(key: str, value: object) -> SafeDetailValue | None:
    if key in _INTEGER_DETAIL_KEYS:
        if type(value) is int and 0 <= value <= _MAX_SAFE_INTEGER:
            return value
        return None
    if key in _BOOLEAN_DETAIL_KEYS:
        return value if type(value) is bool else None
    tokens = _TOKEN_DETAIL_VALUES.get(key)
    if tokens is not None:
        return value if type(value) is str and value in tokens else None
    if key == "reason_code":
        if type(value) is str and value in PROTOCOL_REASON_CODES:
            return value
        return None
    if key == "quarantine_code":
        if type(value) is str and value in _QUARANTINE_CODES:
            return value
        return None
    if key in _ENUM_DETAIL_KEYS:
        if issubclass(type(value), Enum):
            enum_value: object = cast(Enum, value).value
            if type(enum_value) is str and _LOWER_SNAKE_PATTERN.fullmatch(enum_value) is not None:
                return enum_value
        return None
    if key in {"actual_version", "expected_version"}:
        if type(value) is str and _VERSION_PATTERN.fullmatch(value) is not None:
            return value
        return None
    if key == "schema_name":
        if (
            type(value) is str
            and len(value) <= 128
            and _SCHEMA_NAME_PATTERN.fullmatch(value) is not None
        ):
            return value
        return None
    if key == "head_digest":
        if type(value) is str and _HEAD_DIGEST_PATTERN.fullmatch(value) is not None:
            return value
        return None
    if key == "field" and _valid_json_pointer(value):
        return cast(str, value)
    pattern = _PROTOCOL_ID_DETAIL_PATTERNS.get(key)
    if pattern is not None:
        if type(value) is str and pattern.fullmatch(value) is not None:
            return value
        return None
    return None


def normalize_safe_details(value: object) -> Mapping[str, SafeDetailValue]:
    """Return an immutable, bounded allowlisted detail mapping."""

    try:
        is_mapping = issubclass(type(value), Mapping)
    except BaseException:
        return _EMPTY_SAFE_DETAILS
    if not is_mapping:
        return _EMPTY_SAFE_DETAILS
    source = cast(Mapping[object, object], value)
    normalized: dict[str, SafeDetailValue] = {}
    for key in SAFE_DETAIL_KEYS:
        try:
            candidate: object = source[key]
            accepted = _normalize_detail(key, candidate)
        except BaseException:
            continue
        if accepted is not None:
            normalized[key] = accepted
    if not normalized:
        return _EMPTY_SAFE_DETAILS
    return MappingProxyType(normalized)


def _validate_message(value: object) -> str:
    if type(value) is not str:
        raise ProtocolValueError("public_error_invalid_message")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ProtocolValueError("public_error_invalid_message") from exc
    if not 1 <= len(encoded) <= 4096:
        raise ProtocolValueError("public_error_invalid_message")
    if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in value):
        raise ProtocolValueError("public_error_invalid_message")
    return value


def _validate_public_error_code(value: object) -> PublicErrorCode:
    if type(value) is not PublicErrorCode:
        raise TypeError("public_error_code_wrong_type")
    return value


@dataclass(frozen=True, slots=True, init=False)
class PublicOperationError(Exception):
    """A deterministic public operation failure with bounded safe details."""

    code: PublicErrorCode
    message: str
    retryable: bool
    correlation_id: str | None
    safe_details: Mapping[str, SafeDetailValue]

    def __init__(
        self,
        code: PublicErrorCode,
        message: str,
        retryable: bool,
        correlation_id: str | None = None,
        safe_details: object | None = None,
    ) -> None:
        validated_code = _validate_public_error_code(code)
        validated_message = _validate_message(message)
        if type(retryable) is not bool:
            raise TypeError("public_error_retryable_wrong_type")
        if correlation_id is not None and not _valid_correlation_id(correlation_id):
            raise ProtocolValueError("public_error_invalid_correlation_id")
        normalized_details = normalize_safe_details(safe_details)
        object.__setattr__(self, "code", validated_code)
        object.__setattr__(self, "message", validated_message)
        object.__setattr__(self, "retryable", retryable)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "safe_details", normalized_details)
        Exception.__init__(self, validated_message)

    def bind_correlation_id(self, value: str) -> PublicOperationError:
        if not _valid_correlation_id(value):
            raise ProtocolValueError("public_error_invalid_correlation_id")
        if self.correlation_id == value:
            return self
        if self.correlation_id is not None:
            raise ProtocolValueError("public_error_invalid_correlation_id")
        # Copy stored fields verbatim instead of re-running __init__: normalization is not
        # idempotent (enum-keyed details were already collapsed to plain strings, which the
        # enum branch would reject), and binding must change only the correlation ID.
        bound = PublicOperationError.__new__(PublicOperationError)
        object.__setattr__(bound, "code", self.code)
        object.__setattr__(bound, "message", self.message)
        object.__setattr__(bound, "retryable", self.retryable)
        object.__setattr__(bound, "correlation_id", value)
        object.__setattr__(bound, "safe_details", self.safe_details)
        Exception.__init__(bound, self.message)
        return bound

    def as_public_dict(self) -> dict[str, object]:
        if self.correlation_id is None:
            raise ProtocolValueError("public_error_missing_correlation_id")
        result: dict[str, object] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }
        if self.safe_details:
            result["safe_details"] = dict(self.safe_details)
        return result
