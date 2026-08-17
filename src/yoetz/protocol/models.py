"""Shared protocol constants, strict boundary models, and public envelopes."""

from __future__ import annotations

import re
import types
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, Literal, Union, cast, get_args, get_origin

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    RootModel,
    Tag,
    field_validator,
    model_validator,
)

from yoetz.protocol.canonical import JsonValue, parse_canonical_integer_string
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode
from yoetz.protocol.ids import IdKind, validate_actor_id, validate_id

__all__ = [
    "GENESIS_PREDECESSOR_DIGEST",
    "MAX_CANONICAL_REQUEST_BYTES",
    "MAX_EVENTS_PER_BATCH",
    "MAX_FINDINGS_DEFAULT",
    "MAX_FINDINGS_LIMIT",
    "MAX_INTERNAL_PROJECTABLE_RESULT_BYTES",
    "MAX_OBJECT_PLAINTEXT_BYTES",
    "MAX_REVIEW_ASSESSMENTS",
    "MAX_REVIEW_CHALLENGES",
    "MAX_REVIEW_CHANGE_OBSERVATIONS",
    "MAX_REVIEW_EXCERPTS",
    "MAX_REVIEW_OMISSIONS",
    "MAX_REVIEW_TEXT_BYTES",
    "MAX_REVIEW_TIMELINE_ITEMS",
    "MAX_SEMANTIC_CASE_BYTES",
    "MAX_SEMANTIC_ITEM_BYTES",
    "MAX_PROJECTED_RESULT_BYTES",
    "MAX_PROJECTION_CONTENT_LEAVES",
    "MAX_PROJECTION_POINTER_BYTES",
    "MAX_REASON_BYTES",
    "PROTOCOL_VERSION",
    "ActorAssertionModel",
    "ActorType",
    "CheckRequest",
    "CheckRequestModel",
    "CheckResult",
    "CheckResultModel",
    "CheckScopeModel",
    "ClientInfoModel",
    "ClientKind",
    "CoverageModel",
    "DataCategory",
    "FrontierModel",
    "IntegrationKind",
    "JsonValue",
    "OmittedContentModel",
    "OperationFailureModel",
    "PrivacyProjectionModel",
    "PublicEnvelopeModel",
    "PublicErrorModel",
    "PublicRequestModel",
    "PublicResultModel",
    "PublicationChannel",
    "PublishWorkAcceptedMinimalEventModel",
    "PublishWorkAcceptedProjectionUnavailableModel",
    "PublishWorkRequest",
    "PublishWorkRequestModel",
    "PublishWorkResult",
    "PublishWorkResultModel",
    "ReceiptFormat",
    "ReceiptInclude",
    "ReceiptRedactionProfile",
    "ReceiptRequest",
    "ReceiptRequestModel",
    "ReceiptResult",
    "ReceiptResultModel",
    "REGISTERED_GUIDANCE_URIS",
    "ReadGuidanceRequest",
    "ReadGuidanceRequestModel",
    "ReadGuidanceResult",
    "ReadGuidanceResultModel",
    "RespondRequest",
    "RespondRequestModel",
    "RespondResult",
    "RespondResultModel",
    "ProviderChallengeModel",
    "ProviderJudgmentChallengesModel",
    "ProviderJudgmentEnvelopeModel",
    "ProviderJudgmentInsufficientModel",
    "ProviderJudgmentModel",
    "ProviderJudgmentNoDiscrepancyModel",
    "SemanticReason",
    "SemanticStatus",
    "StartRequest",
    "StartRequestModel",
    "StartResult",
    "StartResultModel",
    "StatusRequest",
    "StatusRequestModel",
    "StatusResult",
    "StatusResultModel",
    "SubjectStateRefModel",
    "VALID_SEMANTIC_REASONS",
    "classify_result_leaf",
    "public_model_to_wire",
    "validate_semantic_outcome",
    "validate_semantic_provenance_binding",
]

PROTOCOL_VERSION: Final = "0.1"
MAX_EVENTS_PER_BATCH: Final = 100
MAX_CANONICAL_REQUEST_BYTES: Final = 1_048_576
MAX_FINDINGS_DEFAULT: Final = 3
MAX_FINDINGS_LIMIT: Final = 10
MAX_REASON_BYTES: Final = 4_096
MAX_OBJECT_PLAINTEXT_BYTES: Final = 4_194_304
MAX_SEMANTIC_ITEM_BYTES: Final = 16_384
MAX_SEMANTIC_CASE_BYTES: Final = 262_144
MAX_REVIEW_TEXT_BYTES: Final = 4_096
MAX_REVIEW_TIMELINE_ITEMS: Final = 64
MAX_REVIEW_ASSESSMENTS: Final = 64
MAX_REVIEW_CHANGE_OBSERVATIONS: Final = 32
MAX_REVIEW_EXCERPTS: Final = 16
MAX_REVIEW_OMISSIONS: Final = 64
MAX_REVIEW_CHALLENGES: Final = 3
# JSON Schema ``maxLength`` counts Unicode code points, while the durable semantic contract
# bounds review prose by UTF-8 bytes. Four bytes is the largest UTF-8 encoding of one valid
# Unicode scalar value, so this conservative provider-facing limit guarantees that every string
# admitted by the machine-enforced schema also fits the 4 KiB domain boundary.
MAX_PROVIDER_REVIEW_TEXT_CHARS: Final = MAX_REVIEW_TEXT_BYTES // 4
GENESIS_PREDECESSOR_DIGEST: Final = "genesis"
MAX_PROJECTION_CONTENT_LEAVES: Final = 512
MAX_PROJECTION_POINTER_BYTES: Final = 256
MAX_INTERNAL_PROJECTABLE_RESULT_BYTES: Final = 524_288
MAX_PROJECTED_RESULT_BYTES: Final = 1_048_576


class ActorType(str, Enum):  # noqa: UP042 - exact public wire enum base
    HUMAN = "human"
    HARNESS = "harness"
    LOGICAL_AGENT = "logical_agent"
    MODEL_BACKED_WORKER = "model_backed_worker"
    DELEGATED_SUBAGENT = "delegated_subagent"
    YOETZ_ENGINE = "yoetz_engine"
    IMPORTER = "importer"


class DataCategory(str, Enum):  # noqa: UP042 - exact public wire enum base
    BOUNDED_STRUCTURAL_METADATA = "bounded_structural_metadata"
    DECLARED_FILE_TYPE = "declared_file_type"
    TASK_DESCRIPTION = "task_description"
    CLAIM_TEXT = "claim_text"
    OBLIGATION_TEXT = "obligation_text"
    DECISION_EXCERPT = "decision_excerpt"
    EVIDENCE_EXCERPT = "evidence_excerpt"
    FINDING_SUMMARY = "finding_summary"
    COMMAND_METADATA = "command_metadata"
    DIFF_METADATA = "diff_metadata"
    REPOSITORY_EXCERPT = "repository_excerpt"
    TRANSCRIPT_EXCERPT = "transcript_excerpt"
    DIAGNOSTIC_METADATA = "diagnostic_metadata"


class ClientKind(str, Enum):  # noqa: UP042 - exact public wire enum base
    CODEX_CLI = "codex_cli"
    COOPERATIVE_AGENT = "cooperative_agent"
    YOETZ_CLI = "yoetz_cli"
    TEST_CLIENT = "test_client"
    IMPORTER = "importer"


class IntegrationKind(str, Enum):  # noqa: UP042 - exact public wire enum base
    COOPERATIVE_MCP = "cooperative_mcp"
    LOCAL_CLI = "local_cli"
    CODEX_JSONL_IMPORT = "codex_jsonl_import"


class ReceiptFormat(str, Enum):  # noqa: UP042 - exact public wire enum base
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"


class ReceiptInclude(str, Enum):  # noqa: UP042 - exact public wire enum base
    SUMMARY = "summary"
    STANDARD = "standard"
    FULL = "full"


class ReceiptRedactionProfile(str, Enum):  # noqa: UP042 - exact public wire enum base
    FULL_LOCAL = "full_local"
    DEFAULT_LOCAL_EXPORT = "default_local_export"
    REDACTED_SHARE = "redacted_share"


class SemanticStatus(str, Enum):  # noqa: UP042 - exact public wire enum base
    NOT_REQUESTED = "not_requested"
    NOT_CONFIGURED = "not_configured"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    BLOCKED_FORBIDDEN_DATA = "blocked_forbidden_data"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    AWAITING_HUMAN = "awaiting_human"
    HUMAN_DENIED = "human_denied"
    APPROVAL_EXPIRED = "approval_expired"
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    LATE = "late"
    STALE = "stale"
    FAILED = "failed"


class SemanticReason(str, Enum):  # noqa: UP042 - exact public wire enum base
    DETERMINISTIC_MODE = "deterministic_mode"
    NO_MATERIAL_SEMANTIC_CASE = "no_material_semantic_case"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    LOCAL_MODEL_NOT_CONFIGURED = "local_model_not_configured"
    NETWORK_EGRESS_DENIED = "network_egress_denied"
    ROUTE_SEMANTIC_CEILING = "route_semantic_ceiling"
    CHANNEL_DISABLED = "channel_disabled"
    PROVIDER_BINDING_NOT_AUTHORIZED = "provider_binding_not_authorized"
    SCOPE_NOT_AUTHORIZED = "scope_not_authorized"
    CONTENT_CATEGORY_NOT_AUTHORIZED = "content_category_not_authorized"
    POLICY_GENERATION_REVOKED = "policy_generation_revoked"
    NEVER_SEND_DETECTED = "never_send_detected"
    SECRET_DETECTED = "secret_detected"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    HUMAN_DENIED = "human_denied"
    HUMAN_APPROVAL_EXPIRED = "human_approval_expired"
    SEMANTIC_COMPLETED = "semantic_completed"
    PROVIDER_REFUSED = "provider_refused"
    PROVIDER_TIMEOUT = "provider_timeout"
    RESPONSE_SCHEMA_INVALID = "response_schema_invalid"
    RESPONSE_CONTENT_INVALID = "response_content_invalid"
    SEMANTIC_JUDGMENT_REJECTED = "semantic_judgment_rejected"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    ENDPOINT_PROFILE_UNAVAILABLE = "endpoint_profile_unavailable"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_QUOTA_EXHAUSTED = "provider_quota_exhausted"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"
    AUDIT_RESERVATION_UNAVAILABLE = "audit_reservation_unavailable"
    RECEIPT_PERSISTENCE_UNKNOWN = "receipt_persistence_unknown"
    DEADLINE_AUTHORITY_LOST = "deadline_authority_lost"
    LEASE_AUTHORITY_LOST = "lease_authority_lost"
    FRONTIER_CHANGED = "frontier_changed"
    DEPENDENCY_CHANGED = "dependency_changed"
    COORDINATOR_FAILURE = "coordinator_failure"


VALID_SEMANTIC_REASONS: Final[Mapping[SemanticStatus, frozenset[SemanticReason]]] = (
    MappingProxyType(
        {
            SemanticStatus.NOT_REQUESTED: frozenset(
                {SemanticReason.DETERMINISTIC_MODE, SemanticReason.NO_MATERIAL_SEMANTIC_CASE}
            ),
            SemanticStatus.NOT_CONFIGURED: frozenset(
                {SemanticReason.PROVIDER_NOT_CONFIGURED, SemanticReason.LOCAL_MODEL_NOT_CONFIGURED}
            ),
            SemanticStatus.BLOCKED_BY_POLICY: frozenset(
                {
                    SemanticReason.NETWORK_EGRESS_DENIED,
                    SemanticReason.ROUTE_SEMANTIC_CEILING,
                    SemanticReason.CHANNEL_DISABLED,
                    SemanticReason.PROVIDER_BINDING_NOT_AUTHORIZED,
                    SemanticReason.SCOPE_NOT_AUTHORIZED,
                    SemanticReason.CONTENT_CATEGORY_NOT_AUTHORIZED,
                    SemanticReason.POLICY_GENERATION_REVOKED,
                }
            ),
            SemanticStatus.BLOCKED_FORBIDDEN_DATA: frozenset(
                {SemanticReason.NEVER_SEND_DETECTED, SemanticReason.SECRET_DETECTED}
            ),
            SemanticStatus.CLASSIFICATION_UNCERTAIN: frozenset(
                {SemanticReason.CLASSIFICATION_UNCERTAIN}
            ),
            SemanticStatus.AWAITING_HUMAN: frozenset({SemanticReason.HUMAN_APPROVAL_REQUIRED}),
            SemanticStatus.HUMAN_DENIED: frozenset({SemanticReason.HUMAN_DENIED}),
            SemanticStatus.APPROVAL_EXPIRED: frozenset({SemanticReason.HUMAN_APPROVAL_EXPIRED}),
            SemanticStatus.SUCCEEDED: frozenset({SemanticReason.SEMANTIC_COMPLETED}),
            SemanticStatus.REFUSED: frozenset({SemanticReason.PROVIDER_REFUSED}),
            SemanticStatus.TIMEOUT: frozenset({SemanticReason.PROVIDER_TIMEOUT}),
            SemanticStatus.INVALID: frozenset(
                {
                    SemanticReason.RESPONSE_SCHEMA_INVALID,
                    SemanticReason.RESPONSE_CONTENT_INVALID,
                    SemanticReason.SEMANTIC_JUDGMENT_REJECTED,
                }
            ),
            SemanticStatus.UNAVAILABLE: frozenset(
                {
                    SemanticReason.CREDENTIAL_UNAVAILABLE,
                    SemanticReason.ENDPOINT_PROFILE_UNAVAILABLE,
                    SemanticReason.TRANSPORT_UNAVAILABLE,
                    SemanticReason.PROVIDER_RATE_LIMITED,
                    SemanticReason.PROVIDER_QUOTA_EXHAUSTED,
                    SemanticReason.RETRY_BUDGET_EXHAUSTED,
                    SemanticReason.AUDIT_RESERVATION_UNAVAILABLE,
                    SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN,
                }
            ),
            SemanticStatus.LATE: frozenset(
                {SemanticReason.DEADLINE_AUTHORITY_LOST, SemanticReason.LEASE_AUTHORITY_LOST}
            ),
            SemanticStatus.STALE: frozenset(
                {SemanticReason.FRONTIER_CHANGED, SemanticReason.DEPENDENCY_CHANGED}
            ),
            SemanticStatus.FAILED: frozenset({SemanticReason.COORDINATOR_FAILURE}),
        }
    )
)


_PREDISPATCH_SEMANTIC_STATUSES: Final[frozenset[SemanticStatus]] = frozenset(
    {
        SemanticStatus.NOT_REQUESTED,
        SemanticStatus.NOT_CONFIGURED,
        SemanticStatus.BLOCKED_BY_POLICY,
        SemanticStatus.BLOCKED_FORBIDDEN_DATA,
        SemanticStatus.CLASSIFICATION_UNCERTAIN,
        SemanticStatus.AWAITING_HUMAN,
        SemanticStatus.HUMAN_DENIED,
        SemanticStatus.APPROVAL_EXPIRED,
    }
)
_REQUIRED_SEMANTIC_PROVENANCE_STATUSES: Final[frozenset[SemanticStatus]] = frozenset(
    {
        SemanticStatus.SUCCEEDED,
        SemanticStatus.REFUSED,
        SemanticStatus.TIMEOUT,
        SemanticStatus.INVALID,
        SemanticStatus.LATE,
        SemanticStatus.STALE,
    }
)
_REQUIRED_UNAVAILABLE_PROVENANCE_REASONS: Final[frozenset[SemanticReason]] = frozenset(
    {
        SemanticReason.TRANSPORT_UNAVAILABLE,
        SemanticReason.PROVIDER_RATE_LIMITED,
        SemanticReason.PROVIDER_QUOTA_EXHAUSTED,
    }
)
_FORBIDDEN_UNAVAILABLE_PROVENANCE_REASONS: Final[frozenset[SemanticReason]] = frozenset(
    {
        SemanticReason.CREDENTIAL_UNAVAILABLE,
        SemanticReason.ENDPOINT_PROFILE_UNAVAILABLE,
        SemanticReason.RETRY_BUDGET_EXHAUSTED,
        SemanticReason.AUDIT_RESERVATION_UNAVAILABLE,
        SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN,
    }
)


def validate_semantic_outcome(status: SemanticStatus, reason: SemanticReason) -> None:
    """Validate one exact semantic status/reason pair without coercion."""

    if type(status) is not SemanticStatus or type(reason) is not SemanticReason:
        raise ProtocolValueError("invalid_semantic_outcome_type")
    if reason not in VALID_SEMANTIC_REASONS[status]:
        raise ProtocolValueError("invalid_semantic_status_reason_pair")


def validate_semantic_provenance_binding(
    status: SemanticStatus,
    reason: SemanticReason,
    provenance_status: SemanticStatus | None,
    provenance_reason: SemanticReason | None,
) -> None:
    """Validate the closed provenance-presence and final-attempt identity partition."""

    validate_semantic_outcome(status, reason)

    if provenance_status is None and provenance_reason is None:
        provenance_present = False
    elif type(provenance_status) is SemanticStatus and type(provenance_reason) is SemanticReason:
        provenance_present = True
    else:
        raise ProtocolValueError("invalid_semantic_provenance")

    if provenance_present and (provenance_status is not status or provenance_reason is not reason):
        raise ProtocolValueError("invalid_semantic_provenance")

    if status in _PREDISPATCH_SEMANTIC_STATUSES:
        provenance_required = False
    elif status in _REQUIRED_SEMANTIC_PROVENANCE_STATUSES:
        provenance_required = True
    elif status is SemanticStatus.UNAVAILABLE:
        if reason in _REQUIRED_UNAVAILABLE_PROVENANCE_REASONS:
            provenance_required = True
        elif reason in _FORBIDDEN_UNAVAILABLE_PROVENANCE_REASONS:
            provenance_required = False
        else:  # pragma: no cover - guarded by validate_semantic_outcome
            raise ProtocolValueError("invalid_semantic_provenance")
    elif status is SemanticStatus.FAILED:
        return
    else:  # pragma: no cover - the enum and status/reason registry are closed
        raise ProtocolValueError("invalid_semantic_provenance")

    if provenance_present is not provenance_required:
        raise ProtocolValueError("invalid_semantic_provenance")


_ORDINARY_CONFIG: Final = ConfigDict(
    extra="forbid", frozen=True, strict=True, validate_default=True
)
_ROOT_CONFIG: Final = ConfigDict(frozen=True, strict=True, validate_default=True)
_DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_COMMITMENT_PATTERN: Final = re.compile(r"^hmac-sha256:[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_PATTERN: Final = re.compile(
    r"^(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$",
    re.ASCII,
)
_JSON_POINTER_PATTERN: Final = re.compile(r"^(?:/(?:[^~/\x00-\x1f\x7f]|~[01])*)+$")
_CANONICAL_PAGE_LIMITS: Final = frozenset(str(value) for value in range(1, 101))
_MAX_FINDING_LITERALS: Final = frozenset(str(value) for value in range(1, 11))


def _enum_from_wire[T: Enum](value: object, enum_type: type[T]) -> T:
    if type(value) is not str:
        raise ValueError("wire_enum_wrong_type")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError("wire_enum_invalid") from exc


def _actor_type_from_wire(value: object) -> ActorType:
    return _enum_from_wire(value, ActorType)


def _client_kind_from_wire(value: object) -> ClientKind:
    return _enum_from_wire(value, ClientKind)


def _integration_kind_from_wire(value: object) -> IntegrationKind:
    return _enum_from_wire(value, IntegrationKind)


def _public_error_code_from_wire(value: object) -> PublicErrorCode:
    return _enum_from_wire(value, PublicErrorCode)


def _publication_channel_from_wire(value: object) -> PublicationChannel:
    return _enum_from_wire(value, PublicationChannel)


def _authorship_assurance_from_wire(value: object) -> AuthorshipAssurance:
    return _enum_from_wire(value, AuthorshipAssurance)


def _artifact_observation_from_wire(value: object) -> ArtifactObservation:
    return _enum_from_wire(value, ArtifactObservation)


def _evidence_immutability_from_wire(value: object) -> EvidenceImmutability:
    return _enum_from_wire(value, EvidenceImmutability)


def _ledger_freshness_from_wire(value: object) -> LedgerFreshness:
    return _enum_from_wire(value, LedgerFreshness)


def _check_type_from_wire(value: object) -> CheckType:
    return _enum_from_wire(value, CheckType)


def _data_category_from_wire(value: object) -> DataCategory:
    return _enum_from_wire(value, DataCategory)


def _receipt_format_from_wire(value: object) -> ReceiptFormat:
    return _enum_from_wire(value, ReceiptFormat)


def _receipt_include_from_wire(value: object) -> ReceiptInclude:
    return _enum_from_wire(value, ReceiptInclude)


def _receipt_redaction_profile_from_wire(value: object) -> ReceiptRedactionProfile:
    return _enum_from_wire(value, ReceiptRedactionProfile)


def _semantic_status_from_wire(value: object) -> SemanticStatus:
    return _enum_from_wire(value, SemanticStatus)


def _semantic_reason_from_wire(value: object) -> SemanticReason:
    return _enum_from_wire(value, SemanticReason)


def _validate_id_wire(kind: IdKind, value: object) -> str:
    return validate_id(kind, value)


def _request_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.REQUEST, value)


def _session_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.SESSION, value)


def _writer_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.WRITER, value)


def _claim_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.CLAIM, value)


def _obligation_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.OBLIGATION, value)


def _task_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.TASK, value)


def _finding_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.FINDING, value)


def _evidence_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.EVIDENCE, value)


def _result_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.RESULT, value)


def _event_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.EVENT, value)


def _object_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.OBJECT, value)


def _receipt_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.RECEIPT, value)


def _evidence_or_result_id_wire(value: object) -> str:
    try:
        return _evidence_id_wire(value)
    except ProtocolValueError:
        return _result_id_wire(value)


def _correlation_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.CORRELATION, value)


def _privacy_policy_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.PRIVACY_POLICY, value)


def _privacy_proposal_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.PRIVACY_PROPOSAL, value)


def _egress_receipt_id_wire(value: object) -> str:
    return _validate_id_wire(IdKind.EGRESS_RECEIPT, value)


def _actor_assertion_id_wire(value: object) -> str:
    return validate_actor_id(value)


def _canonical_uint_wire(value: object) -> str:
    if type(value) is not str:
        raise ProtocolValueError("noncanonical_integer_string")
    parse_canonical_integer_string(value)
    return value


def _canonical_positive_uint_wire(value: object) -> str:
    validated = _canonical_uint_wire(value)
    if validated == "0":
        raise ProtocolValueError("noncanonical_integer_string")
    return validated


def _canonical_page_limit_wire(value: object) -> str:
    if type(value) is not str or value not in _CANONICAL_PAGE_LIMITS:
        raise ProtocolValueError("noncanonical_integer_string")
    return value


def _digest_wire(value: object) -> str:
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_digest")
    return value


def _genesis_or_digest_wire(value: object) -> str:
    if value == GENESIS_PREDECESSOR_DIGEST and type(value) is str:
        return value
    return _digest_wire(value)


def _commitment_wire(value: object) -> str:
    if type(value) is not str or _COMMITMENT_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_commitment")
    return value


def _timestamp_wire(value: object) -> str:
    if type(value) is not str or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ProtocolValueError("invalid_timestamp") from exc
    return value


def _json_pointer_wire(value: object) -> str:
    if type(value) is not str:
        raise ProtocolValueError("invalid_json_pointer")
    if unicodedata.normalize("NFC", value) != value:
        raise ProtocolValueError("invalid_json_pointer")
    try:
        pointer_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ProtocolValueError("invalid_json_pointer") from exc
    if pointer_size > MAX_PROJECTION_POINTER_BYTES:
        raise ProtocolValueError("invalid_json_pointer")
    if _JSON_POINTER_PATTERN.fullmatch(value) is None:
        raise ProtocolValueError("invalid_json_pointer")
    return value


ActorTypeWire = Annotated[ActorType, BeforeValidator(_actor_type_from_wire)]
ClientKindWire = Annotated[ClientKind, BeforeValidator(_client_kind_from_wire)]
IntegrationKindWire = Annotated[IntegrationKind, BeforeValidator(_integration_kind_from_wire)]
PublicErrorCodeWire = Annotated[PublicErrorCode, BeforeValidator(_public_error_code_from_wire)]
PublicationChannelWire = Annotated[
    PublicationChannel, BeforeValidator(_publication_channel_from_wire)
]
AuthorshipAssuranceWire = Annotated[
    AuthorshipAssurance, BeforeValidator(_authorship_assurance_from_wire)
]
ArtifactObservationWire = Annotated[
    ArtifactObservation, BeforeValidator(_artifact_observation_from_wire)
]
EvidenceImmutabilityWire = Annotated[
    EvidenceImmutability, BeforeValidator(_evidence_immutability_from_wire)
]
LedgerFreshnessWire = Annotated[LedgerFreshness, BeforeValidator(_ledger_freshness_from_wire)]
CheckTypeWire = Annotated[CheckType, BeforeValidator(_check_type_from_wire)]
DataCategoryWire = Annotated[DataCategory, BeforeValidator(_data_category_from_wire)]
ReceiptFormatWire = Annotated[ReceiptFormat, BeforeValidator(_receipt_format_from_wire)]
ReceiptIncludeWire = Annotated[ReceiptInclude, BeforeValidator(_receipt_include_from_wire)]
ReceiptRedactionProfileWire = Annotated[
    ReceiptRedactionProfile, BeforeValidator(_receipt_redaction_profile_from_wire)
]
SemanticStatusWire = Annotated[SemanticStatus, BeforeValidator(_semantic_status_from_wire)]
SemanticReasonWire = Annotated[SemanticReason, BeforeValidator(_semantic_reason_from_wire)]

ActorAssertionIdWire = Annotated[str, BeforeValidator(_actor_assertion_id_wire)]
RequestIdWire = Annotated[str, BeforeValidator(_request_id_wire)]
SessionIdWire = Annotated[str, BeforeValidator(_session_id_wire)]
WriterIdWire = Annotated[str, BeforeValidator(_writer_id_wire)]
ClaimIdWire = Annotated[str, BeforeValidator(_claim_id_wire)]
ObligationIdWire = Annotated[str, BeforeValidator(_obligation_id_wire)]
TaskIdWire = Annotated[str, BeforeValidator(_task_id_wire)]
FindingIdWire = Annotated[str, BeforeValidator(_finding_id_wire)]
EvidenceIdWire = Annotated[str, BeforeValidator(_evidence_id_wire)]
ResultIdWire = Annotated[str, BeforeValidator(_result_id_wire)]
EventIdWire = Annotated[str, BeforeValidator(_event_id_wire)]
ObjectIdWire = Annotated[str, BeforeValidator(_object_id_wire)]
ReceiptIdWire = Annotated[str, BeforeValidator(_receipt_id_wire)]
EvidenceOrResultIdWire = Annotated[str, BeforeValidator(_evidence_or_result_id_wire)]
CorrelationIdWire = Annotated[str, BeforeValidator(_correlation_id_wire)]
PrivacyPolicyIdWire = Annotated[str, BeforeValidator(_privacy_policy_id_wire)]
PrivacyProposalIdWire = Annotated[str, BeforeValidator(_privacy_proposal_id_wire)]
EgressReceiptIdWire = Annotated[str, BeforeValidator(_egress_receipt_id_wire)]
CanonicalUInt64Wire = Annotated[str, BeforeValidator(_canonical_uint_wire)]
CanonicalPositiveUInt64Wire = Annotated[str, BeforeValidator(_canonical_positive_uint_wire)]
CanonicalPageLimitWire = Annotated[str, BeforeValidator(_canonical_page_limit_wire)]
Sha256Digest = Annotated[str, BeforeValidator(_digest_wire)]
GenesisOrSha256Digest = Annotated[str, BeforeValidator(_genesis_or_digest_wire)]
HmacSha256Commitment = Annotated[str, BeforeValidator(_commitment_wire)]
TimestampWire = Annotated[str, BeforeValidator(_timestamp_wire)]
JsonPointer = Annotated[str, BeforeValidator(_json_pointer_wire)]
String1To256 = Annotated[str, Field(min_length=1, max_length=256)]
String1To4096 = Annotated[str, Field(min_length=1, max_length=4096)]
String1To8192 = Annotated[str, Field(min_length=1, max_length=8192)]
String1To32768 = Annotated[str, Field(min_length=1, max_length=32768)]
CursorWire = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[A-Za-z0-9_-]+$")]
SchemaNameWire = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SubjectIdWire = Annotated[
    str,
    Field(
        pattern=r"^(act|clm|evd|evt|fnd|obl|res)_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
CodeWire = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,127}$", max_length=128)]
VersionWire = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,63}$")
]
ReceiptVersionIdentityWire = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[0-9A-Za-z][0-9A-Za-z._/+:-]*$"),
]
ReceiptSchemaCounterVersionWire = Annotated[
    str, Field(min_length=1, max_length=19, pattern=r"^[1-9][0-9]*$")
]
ReceiptPolicyIdWire = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$",
    ),
]
ReceiptSchemaIdWire = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[a-z][a-z0-9]*(?:[-_/][a-z0-9.]+)*$",
    ),
]
SemverWire = Annotated[
    str,
    Field(
        max_length=32,
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$",
    ),
]
ProfileIdWire = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._/-]{0,127}$"),
]
AsciiString1To160 = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[ -~]+$")]

type FreshnessWire = Literal[
    "current", "partial", "redacted_gap", "stale_after_material_change", "unknown"
]
type FindingKindWire = Literal[
    "action_without_result",
    "claim_without_admissible_evidence",
    "completion_with_open_obligations",
    "contradictory_claims_unresolved",
    "diff_does_not_match_account",
    "evidence_does_not_support_claim",
    "failed_work_omitted",
    "ledger_stale_or_incomplete",
    "material_limitation_omitted",
    "questionable_finding_rejection",
    "requested_item_never_attempted",
    "result_without_action",
    "stale_evidence_for_changed_state",
    "weak_or_stale_response",
]

type SafeDetailPrimitive = (
    bool
    | Annotated[int, Field(ge=-(2**53 - 1), le=2**53 - 1)]
    | Annotated[str, Field(max_length=4096)]
)
type SafeDetailArray = Annotated[
    tuple[SafeDetailPrimitive, ...], Field(min_length=0, max_length=32)
]
type SafeDetailValueModel = SafeDetailPrimitive | SafeDetailArray
type SafeDetailObject = Annotated[
    dict[
        Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$", max_length=64)],
        SafeDetailValueModel,
    ],
    Field(min_length=0, max_length=32),
]
type SafeDetailValues = Annotated[
    tuple[SafeDetailValueModel, ...], Field(min_length=0, max_length=32)
]


def _normalize_safe_detail_item(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


def _normalize_safe_details_wire(value: object) -> object:
    if type(value) is list:
        return tuple(_normalize_safe_detail_item(item) for item in cast(list[object], value))
    if type(value) is dict:
        source = cast(dict[object, object], value)
        return {key: _normalize_safe_detail_item(item) for key, item in source.items()}
    return value


type SafeDetails = Annotated[
    SafeDetailObject | SafeDetailValues,
    BeforeValidator(_normalize_safe_details_wire),
]


def _annotation_accepts_tuple(annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin is tuple:
        return True
    if origin is types.UnionType or origin is Union:
        return any(_annotation_accepts_tuple(argument) for argument in get_args(annotation))
    return False


class _ClosedModel(BaseModel):
    model_config = _ORDINARY_CONFIG

    optional_non_null_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _adapt_json_arrays_and_reject_forbidden_nulls(cls, value: object) -> object:
        if isinstance(value, Mapping):
            source = cast(Mapping[object, object], value)
            for field_name in cls.optional_non_null_fields:
                if field_name in source and source[field_name] is None:
                    raise ValueError("optional_field_must_not_be_null")
            adapted: dict[object, object] | None = None
            for field_name, field in cls.model_fields.items():
                raw = source.get(field_name)
                if type(raw) is list and _annotation_accepts_tuple(field.annotation):
                    if adapted is None:
                        adapted = dict(source)
                    adapted[field_name] = tuple(cast(list[object], raw))
            if adapted is not None:
                return adapted
        return cast(object, value)


def _strip_optional_non_null_fields(
    model: BaseModel, dumped: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Omit unset optional non-null fields recursively from one public model tree."""

    root: object = getattr(model, "root", None)
    if isinstance(root, BaseModel):
        return _strip_optional_non_null_fields(root, dumped)
    declared: object = getattr(type(model), "optional_non_null_fields", None)
    optional: frozenset[str] = (
        cast(frozenset[str], declared) if isinstance(declared, frozenset) else frozenset()
    )
    field_names_by_dump_key = {
        (field.serialization_alias or field.alias or field_name): field_name
        for field_name, field in type(model).model_fields.items()
    }
    result: dict[str, JsonValue] = {}
    for key, value in dumped.items():
        field_name = field_names_by_dump_key.get(key, key)
        if field_name in optional and value is None:
            continue
        attribute: object = getattr(model, field_name)
        if isinstance(attribute, BaseModel) and isinstance(value, Mapping):
            result[key] = _strip_optional_non_null_fields(attribute, value)
        elif (
            isinstance(attribute, Sequence)
            and not isinstance(attribute, (str, bytes))
            and isinstance(value, list)
        ):
            children: list[JsonValue] = []
            for child, child_dump in zip(cast(Sequence[object], attribute), value, strict=True):
                if isinstance(child, BaseModel) and isinstance(child_dump, Mapping):
                    children.append(_strip_optional_non_null_fields(child, child_dump))
                else:
                    children.append(child_dump)
            result[key] = children
        else:
            result[key] = value
    return result


class PublicEnvelopeModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]


class ActorAssertionModel(_ClosedModel):
    optional_non_null_fields = frozenset({"asserted_by", "display_name"})

    actor_id: ActorAssertionIdWire
    actor_type: ActorTypeWire
    asserted_by: String1To256 | None = None
    display_name: String1To256 | None = None


class ClientInfoModel(_ClosedModel):
    kind: ClientKindWire
    version: String1To256
    integration: IntegrationKindWire


class PublicRequestModel(PublicEnvelopeModel):
    request_id: RequestIdWire
    actor: ActorAssertionModel
    client: ClientInfoModel


class PublicResultModel[T](RootModel[T]):
    model_config = _ROOT_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _require_exact_boolean_discriminator(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("result_root_not_mapping")
        source = cast(Mapping[object, object], value)
        if type(source.get("ok")) is not bool:
            raise ValueError("result_ok_wrong_type")
        return cast(object, value)


class FrontierModel(_ClosedModel):
    sequence: CanonicalUInt64Wire
    head_digest: GenesisOrSha256Digest

    @model_validator(mode="after")
    def _validate_genesis_identity(self) -> FrontierModel:
        if (self.sequence == "0") is not (self.head_digest == GENESIS_PREDECESSOR_DIGEST):
            raise ProtocolValueError("invalid_frontier")
        return self


class CoverageModel(_ClosedModel):
    publication_channels: tuple[PublicationChannelWire, ...]
    authorship_assurance: AuthorshipAssuranceWire
    artifact_observation: ArtifactObservationWire
    evidence_immutability: EvidenceImmutabilityWire
    ledger_freshness: LedgerFreshnessWire
    check_types: tuple[CheckTypeWire, ...]
    known_gaps: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...]

    @model_validator(mode="after")
    def _validate_coverage_lattice(self) -> CoverageModel:
        Coverage(
            publication_channels=self.publication_channels,
            authorship_assurance=self.authorship_assurance,
            artifact_observation=self.artifact_observation,
            evidence_immutability=self.evidence_immutability,
            ledger_freshness=self.ledger_freshness,
            check_types=self.check_types,
            known_gaps=self.known_gaps,
        )
        return self


class SubjectStateRefModel(_ClosedModel):
    optional_non_null_fields = frozenset({"tree_digest", "diff_digest", "described_state"})

    tree_digest: Sha256Digest | None = None
    diff_digest: Sha256Digest | None = None
    described_state: String1To256 | None = None

    @model_validator(mode="after")
    def _require_one_state_member(self) -> SubjectStateRefModel:
        if self.tree_digest is None and self.diff_digest is None and self.described_state is None:
            raise ProtocolValueError("empty_subject_state")
        return self


class PublicErrorModel(_ClosedModel):
    optional_non_null_fields = frozenset({"safe_details"})

    code: PublicErrorCodeWire
    message: String1To4096
    retryable: bool
    correlation_id: CorrelationIdWire
    safe_details: SafeDetails | None = None


class OmittedContentModel(_ClosedModel):
    omitted: Literal[True]
    category: DataCategoryWire
    reason: Literal["local_disclosure_not_authorized", "never_send_redacted"]


class PrivacyProjectionModel(_ClosedModel):
    sink: Literal["agent_context", "local_human_view"]
    local_disclosure_receipt_id: EgressReceiptIdWire
    policy_id: PrivacyPolicyIdWire
    policy_version: CanonicalPositiveUInt64Wire
    policy_digest: Sha256Digest
    included_categories: tuple[DataCategoryWire, ...]
    blocked_categories: tuple[DataCategoryWire, ...]
    omitted_pointers: tuple[JsonPointer, ...]
    projection_commitment: HmacSha256Commitment

    @model_validator(mode="after")
    def _validate_sorted_unique_projection_sets(self) -> PrivacyProjectionModel:
        for values in (self.included_categories, self.blocked_categories):
            if len(values) > len(DataCategory):
                raise ValueError("projection_category_limit")
            encoded = tuple(value.value.encode("ascii") for value in values)
            if encoded != tuple(sorted(set(encoded))):
                raise ValueError("projection_categories_not_canonical")
        if len(self.omitted_pointers) > MAX_PROJECTION_CONTENT_LEAVES:
            raise ValueError("projection_pointer_limit")
        encoded_pointers = tuple(pointer.encode("utf-8") for pointer in self.omitted_pointers)
        if encoded_pointers != tuple(sorted(set(encoded_pointers))):
            raise ValueError("projection_pointers_not_canonical")
        return self


class OperationFailureModel(PublicEnvelopeModel):
    ok: Literal[False]
    error: PublicErrorModel
    request_id: RequestIdWire | None = None


class CheckScopeModel(_ClosedModel):
    claim_ids: tuple[ClaimIdWire, ...]
    obligation_ids: tuple[ObligationIdWire, ...]

    @model_validator(mode="after")
    def _validate_scope_sets(self) -> CheckScopeModel:
        if len(self.claim_ids) > 64 or len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("invalid_claim_ids")
        if len(self.obligation_ids) > 64 or len(set(self.obligation_ids)) != len(
            self.obligation_ids
        ):
            raise ValueError("invalid_obligation_ids")
        return self


def _validate_model_against_schema(model: BaseModel, schema_name: str) -> None:
    from pydantic import ValidationError as PydanticValidationError

    from yoetz.protocol.schemas import SchemaInstanceInvalid, validate_schema_instance

    raw_dump = model.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
        exclude_none=False,
    )
    dumped = _strip_optional_non_null_fields(model, cast(Mapping[str, JsonValue], raw_dump))
    try:
        validate_schema_instance(schema_name, "1.0.0", cast(JsonValue, dumped))
    except SchemaInstanceInvalid as exc:
        # Re-raise with JSON Schema path(s) so MCP safe_details can name corrective fields.
        # Root-level object rules (dependentRequired, if/then) arrive as location_reasons with
        # closed reason tokens; nested field failures use absolute_path.
        if exc.location_reasons:
            raise PydanticValidationError.from_exception_data(
                type(model).__name__,
                [
                    {
                        "type": "value_error",
                        "loc": tuple(path),
                        "input": None,
                        "ctx": {"error": ValueError(reason)},
                    }
                    for path, reason in exc.location_reasons
                ],
            ) from None
        if exc.absolute_path:
            # The reason token is drawn from a closed set inside the validator, and the family,
            # its schema version, and the count beside it are frozen schema content, so MCP can
            # name the class of the mistake rather than collapsing every nested failure to one
            # generic token (issue #240). The version rides along because a family may have
            # several admitted versions, and naming the wrong one hands back the wrong contract
            # (issue #239).
            raise PydanticValidationError.from_exception_data(
                type(model).__name__,
                [
                    {
                        "type": "value_error",
                        "loc": tuple(exc.absolute_path),
                        "input": None,
                        "ctx": {
                            "error": ValueError(exc.reason or "schema_instance_invalid"),
                            "schema_name": exc.family,
                            "schema_version": exc.family_version,
                            "count": exc.unknown_count or None,
                            # Frozen schema vocabulary, never a caller-invented key: the validator
                            # admits it only when it byte-equals a catalogued payload property
                            # name, so MCP can name the field's one legal owner (issue #266).
                            "misplaced_field": exc.misplaced_field,
                        },
                    }
                ],
            ) from None
        raise ValueError("schema_instance_invalid") from None


class StartRequestModel(PublicRequestModel):
    optional_non_null_fields = frozenset({"session_id", "external_ref", "workspace_ref"})

    mode: Literal["attach", "create", "create_or_attach"]
    task_title: String1To8192
    requested_view: Literal["compact"]
    session_id: SessionIdWire | None = None
    external_ref: String1To8192 | None = None
    workspace_ref: String1To8192 | None = None

    @model_validator(mode="after")
    def _validate_start_request(self) -> StartRequestModel:
        _validate_model_against_schema(self, "start-request")
        return self


class PublishWorkRequestModel(PublicRequestModel):
    optional_non_null_fields = frozenset({"dry_run"})

    session_id: SessionIdWire
    writer_id: WriterIdWire
    expected_frontier: FrontierModel | None
    event_drafts: tuple[JsonValue, ...]
    # When true, validate the batch and return a non-evidential preview without appending.
    dry_run: bool | None = None

    @model_validator(mode="after")
    def _validate_publish_work_request(self) -> PublishWorkRequestModel:
        _validate_model_against_schema(self, "publish-work-request")
        return self


class CheckRequestModel(PublicRequestModel):
    optional_non_null_fields = frozenset({"mode", "scope", "max_findings", "policy_packs"})

    session_id: SessionIdWire
    writer_id: WriterIdWire
    expected_frontier: FrontierModel
    # Omitted mode resolves via VerificationPolicy.default_check_mode in the application facade.
    mode: Literal["deterministic_only", "semantic_if_configured", "semantic_required"] | None = None
    scope: CheckScopeModel | None = None
    max_findings: Literal["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"] | None = None
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...] | None = (
        None
    )

    @model_validator(mode="after")
    def _validate_check_request(self) -> CheckRequestModel:
        _validate_model_against_schema(self, "check-request")
        return self


class RespondRequestModel(PublicRequestModel):
    optional_non_null_fields = frozenset(
        {"reason", "waiver_scope", "waiver_expiry", "evidence_refs"}
    )

    session_id: SessionIdWire
    writer_id: WriterIdWire
    expected_frontier: FrontierModel
    finding_id: FindingIdWire
    finding_frontier: FrontierModel
    disposition: Literal["acknowledged", "provenance_disputed", "rejected", "waived"]
    reason: String1To4096 | None = None
    waiver_scope: Literal["finding_only"] | None = None
    waiver_expiry: TimestampWire | None = None
    evidence_refs: tuple[EvidenceOrResultIdWire, ...] | None = None

    @model_validator(mode="after")
    def _validate_respond_request(self) -> RespondRequestModel:
        _validate_model_against_schema(self, "respond-request")
        return self


class StatusAssignmentFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"actor_id", "include_resolved"})

    actor_id: ActorAssertionIdWire | None = None
    include_resolved: bool | None = None


class StatusCandidateFindingsFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"priority"})

    priority: Annotated[int, Field(ge=1, le=3)] | None = None


class StatusEvidenceFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"freshness", "include_unavailable", "strength"})

    freshness: (
        Literal["current", "partial", "redacted_gap", "stale_after_material_change", "unknown"]
        | None
    ) = None
    include_unavailable: bool | None = None
    strength: (
        Literal[
            "content_digest",
            "immutable_snapshot",
            "independently_reproduced",
            "metadata_only",
            "mutable_reference",
        ]
        | None
    ) = None


class StatusFindingsFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"disposition", "include_resolved", "origin", "priority"})

    disposition: (
        Literal["acknowledged", "none", "provenance_disputed", "rejected", "waived"] | None
    ) = None
    include_resolved: bool | None = None
    origin: Literal["deterministic", "semantic_model_derived"] | None = None
    priority: Annotated[int, Field(ge=1, le=3)] | None = None


class StatusHistoryFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"actor_id", "after_sequence", "schema_name"})

    actor_id: ActorAssertionIdWire | None = None
    after_sequence: CanonicalUInt64Wire | None = None
    schema_name: SchemaNameWire | None = None


class StatusObligationsFilterModel(_ClosedModel):
    optional_non_null_fields = frozenset({"actor_id", "include_resolved", "status"})

    actor_id: ActorAssertionIdWire | None = None
    include_resolved: bool | None = None
    status: Literal["open", "resolved"] | None = None


class StatusOperationFilterModel(_ClosedModel):
    """Keys a status read at one prior operation identity for the authenticated writer."""

    operation_request_id: RequestIdWire


type StatusFilter = (
    StatusAssignmentFilterModel
    | StatusCandidateFindingsFilterModel
    | StatusEvidenceFilterModel
    | StatusFindingsFilterModel
    | StatusHistoryFilterModel
    | StatusObligationsFilterModel
    | StatusOperationFilterModel
)

_STATUS_FILTER_BY_VIEW: Final[Mapping[str, type[_ClosedModel]]] = MappingProxyType(
    {
        "assignment": StatusAssignmentFilterModel,
        "candidate_findings": StatusCandidateFindingsFilterModel,
        "evidence": StatusEvidenceFilterModel,
        "findings": StatusFindingsFilterModel,
        "history": StatusHistoryFilterModel,
        "obligations": StatusObligationsFilterModel,
        "operation": StatusOperationFilterModel,
    }
)


class StatusRequestModel(PublicRequestModel):
    optional_non_null_fields = frozenset({"filter"})

    session_id: SessionIdWire
    writer_id: WriterIdWire
    view: Literal[
        "advice",
        "assignment",
        "candidate_findings",
        "compact",
        "evidence",
        "findings",
        "history",
        "obligations",
        "operation",
        "versions",
    ]
    limit: CanonicalPageLimitWire
    filter: StatusFilter | None = None
    at_frontier: CanonicalUInt64Wire | None = None
    cursor: CursorWire | None = None

    @model_validator(mode="before")
    @classmethod
    def _route_status_filter(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        source = cast(Mapping[object, object], value)
        raw_filter = source.get("filter")
        raw_view = source.get("view")
        if raw_filter is None or type(raw_view) is not str:
            return cast(object, value)
        filter_type = _STATUS_FILTER_BY_VIEW.get(raw_view)
        if filter_type is None:
            return cast(object, value)
        routed = dict(source)
        routed["filter"] = filter_type.model_validate(raw_filter)
        return routed

    @model_validator(mode="after")
    def _validate_status_request(self) -> StatusRequestModel:
        _validate_model_against_schema(self, "status-request")
        return self


class ReceiptRequestModel(PublicRequestModel):
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    expected_frontier: FrontierModel
    format: ReceiptFormatWire
    include: ReceiptIncludeWire
    redaction_profile: ReceiptRedactionProfileWire

    @model_validator(mode="after")
    def _validate_receipt_request(self) -> ReceiptRequestModel:
        _validate_model_against_schema(self, "receipt-request")
        return self


REGISTERED_GUIDANCE_URIS: Final[tuple[str, ...]] = (
    "yoetz://guidance/agent-instructions.md",
    "yoetz://guidance/workflow.md",
    "yoetz://guidance/publication-policy.md",
    "yoetz://guidance/coverage-and-receipts.md",
    "yoetz://guidance/request-templates.md",
)
type GuidanceResourceUri = Literal[
    "yoetz://guidance/agent-instructions.md",
    "yoetz://guidance/workflow.md",
    "yoetz://guidance/publication-policy.md",
    "yoetz://guidance/coverage-and-receipts.md",
    "yoetz://guidance/request-templates.md",
]
_MAX_GUIDANCE_DOCUMENT_CHARS: Final = 65_536


class ReadGuidanceRequestModel(_ClosedModel):
    """Request to read one registered Yoetz guidance document as tool text."""

    uri: GuidanceResourceUri = Field(
        description="One registered URI such as yoetz://guidance/workflow.md."
    )

    @model_validator(mode="after")
    def _validate_read_guidance_request(self) -> ReadGuidanceRequestModel:
        _validate_model_against_schema(self, "read-guidance-request")
        return self


class ReadGuidanceSuccessModel(_ClosedModel):
    """Registered guidance document returned as tool text."""

    ok: Literal[True]
    uri: GuidanceResourceUri
    media_type: Literal["text/markdown"]
    byte_count: Annotated[int, Field(ge=0, le=_MAX_GUIDANCE_DOCUMENT_CHARS)]
    text: Annotated[str, Field(min_length=0, max_length=_MAX_GUIDANCE_DOCUMENT_CHARS)]

    @model_validator(mode="after")
    def _validate_read_guidance_success(self) -> ReadGuidanceSuccessModel:
        encoded = self.text.encode("utf-8")
        if len(encoded) != self.byte_count:
            raise ValueError("guidance_byte_count_mismatch")
        _validate_model_against_schema(self, "read-guidance-result")
        return self


type ReadGuidanceResultBranch = Annotated[
    ReadGuidanceSuccessModel | OperationFailureModel, Field(discriminator="ok")
]


class ReadGuidanceResultModel(PublicResultModel[ReadGuidanceResultBranch]):
    pass


def _require_unique(values: tuple[object, ...], *, limit: int) -> None:
    if len(values) > limit or len(set(values)) != len(values):
        raise ValueError("array_not_unique_or_bounded")


def _require_review_text_utf8_bytes(value: str) -> str:
    """Bound review prose by UTF-8 byte length (matches domain ReviewerChallenge)."""

    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("provider_review_text_invalid") from exc
    if not 1 <= len(encoded) <= MAX_REVIEW_TEXT_BYTES:
        raise ValueError("provider_review_text_invalid")
    return value


type ReviewerNextStepWire = Literal[
    "act",
    "provide_evidence",
    "revise_claim",
    "dispute_with_evidence",
    "state_unresolved_limitation",
]

ProviderReviewTextWire = Annotated[
    str,
    Field(min_length=1, max_length=MAX_PROVIDER_REVIEW_TEXT_CHARS),
]


class ProviderChallengeModel(_ClosedModel):
    """One provider-facing reviewer challenge; owns the constrained-output shape."""

    finding_kind: FindingKindWire
    summary: ProviderReviewTextWire
    cited_refs: Annotated[
        tuple[SubjectIdWire, ...],
        Field(min_length=1, max_length=16, json_schema_extra={"uniqueItems": True}),
    ]
    discrepancy: ProviderReviewTextWire
    alternative_interpretation: ProviderReviewTextWire
    message_to_main_agent: ProviderReviewTextWire
    requested_next_step: ReviewerNextStepWire
    uncertainty: ProviderReviewTextWire

    @model_validator(mode="after")
    def _validate_challenge_invariants(self) -> ProviderChallengeModel:
        _require_unique(self.cited_refs, limit=16)
        for field_name in (
            "summary",
            "discrepancy",
            "alternative_interpretation",
            "message_to_main_agent",
            "uncertainty",
        ):
            _require_review_text_utf8_bytes(getattr(self, field_name))
        return self


class ProviderJudgmentNoDiscrepancyModel(_ClosedModel):
    conclusion: Literal["no_material_discrepancy"]
    reviewer_challenges: Annotated[
        tuple[ProviderChallengeModel, ...], Field(min_length=0, max_length=0)
    ]


class ProviderJudgmentChallengesModel(_ClosedModel):
    conclusion: Literal["challenges_returned"]
    reviewer_challenges: Annotated[
        tuple[ProviderChallengeModel, ...],
        Field(min_length=1, max_length=MAX_REVIEW_CHALLENGES),
    ]


class ProviderJudgmentInsufficientModel(_ClosedModel):
    conclusion: Literal["insufficient_packet"]
    reviewer_challenges: Annotated[
        tuple[ProviderChallengeModel, ...], Field(min_length=0, max_length=0)
    ]


type ProviderJudgmentModel = (
    ProviderJudgmentNoDiscrepancyModel
    | ProviderJudgmentChallengesModel
    | ProviderJudgmentInsufficientModel
)


class ProviderJudgmentEnvelopeModel(_ClosedModel):
    """Root wrapper that carries the judgment union one level below the schema root.

    Constrained-output (``strict: true``) schemas must have an object at the root; a root-level
    ``anyOf`` is rejected by the provider before generation starts. Nesting the union under a
    single required property keeps one owning contract for generation and consumption while
    still expressing the conclusion/challenge coupling through explicit union branches.
    """

    judgment: ProviderJudgmentModel


class StartCompactViewModel(_ClosedModel):
    # Start reuses the same compact projection as status. A redacted/unreadable current plan
    # cannot honestly produce a numeric count, so attach preserves null instead of inventing zero.
    open_obligation_count: CanonicalUInt64Wire | None
    unanswered_finding_count: CanonicalUInt64Wire
    receipt_blocking_finding_count: CanonicalUInt64Wire | None
    ledger_freshness: Literal[
        "current", "partial", "redacted_gap", "stale_after_material_change", "unknown"
    ]
    coverage: CoverageModel
    gaps: tuple[CodeWire, ...]
    current_plan_event_id: EventIdWire | None = None

    @model_validator(mode="after")
    def _validate_start_compact_sets(self) -> StartCompactViewModel:
        _require_unique(self.gaps, limit=64)
        legacy_unknown = "legacy_receipt_blocking_count_unknown" in self.gaps
        if (self.receipt_blocking_finding_count is None) != legacy_unknown:
            raise ValueError("start_compact_receipt_blocking_count_mismatch")
        return self


class StartVersionSliceModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    engine_version: VersionWire
    projection_version: VersionWire
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...]

    @model_validator(mode="after")
    def _validate_start_version_packs(self) -> StartVersionSliceModel:
        _require_unique(self.policy_packs, limit=2)
        return self


class StartNextRequestActorTemplateModel(_ClosedModel):
    """Caller-owned actor fields that must be filled before publication."""

    actor_id: Literal[""]
    actor_type: Literal[""]


class StartNextRequestClientTemplateModel(_ClosedModel):
    """Caller-owned client fields that must be filled before publication."""

    kind: Literal[""]
    version: Literal[""]
    integration: Literal[""]


class StartNextRequestEventSchemaTemplateModel(_ClosedModel):
    name: Literal["plan_published", "obligation_published"]
    version: Literal["1.0.0"]


class StartNextRequestPlanPayloadTemplateModel(_ClosedModel):
    plan_version: Literal[1]
    summary: Literal[""]
    obligation_refs: tuple[Literal[""], ...]

    @model_validator(mode="after")
    def _validate_plan_template(self) -> StartNextRequestPlanPayloadTemplateModel:
        if self.obligation_refs != ("",):
            raise ValueError("invalid_start_next_request_template")
        return self


class StartNextRequestObligationPayloadTemplateModel(_ClosedModel):
    obligation_id: Literal[""]
    description: Literal[""]
    acceptance_criteria: Literal[""]
    evidence_expectation: Literal[""]
    status: Literal["open"]


class StartNextRequestEventDraftTemplateModel(_ClosedModel):
    event_id: Literal[""]
    event_schema: StartNextRequestEventSchemaTemplateModel = Field(alias="schema")
    occurred_at: Literal[""]
    causal_parents: tuple[Literal[""], ...]
    payload: (
        StartNextRequestPlanPayloadTemplateModel | StartNextRequestObligationPayloadTemplateModel
    )
    artifact_refs: tuple[Literal[""], ...]
    evidence_refs: tuple[Literal[""], ...]

    @model_validator(mode="after")
    def _validate_event_template(self) -> StartNextRequestEventDraftTemplateModel:
        if self.causal_parents or self.artifact_refs or self.evidence_refs:
            raise ValueError("invalid_start_next_request_template")
        expected_name = (
            "plan_published"
            if type(self.payload) is StartNextRequestPlanPayloadTemplateModel
            else "obligation_published"
        )
        if self.event_schema.name != expected_name:
            raise ValueError("invalid_start_next_request_template")
        return self


class StartPublishWorkRequestTemplateModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: Literal[""]
    actor: StartNextRequestActorTemplateModel
    client: StartNextRequestClientTemplateModel
    session_id: SessionIdWire
    writer_id: WriterIdWire
    expected_frontier: FrontierModel
    event_drafts: tuple[StartNextRequestEventDraftTemplateModel, ...]

    @model_validator(mode="after")
    def _validate_event_pair(self) -> StartPublishWorkRequestTemplateModel:
        if (
            len(self.event_drafts) != 2
            or self.event_drafts[0].event_schema.name != "plan_published"
            or self.event_drafts[1].event_schema.name != "obligation_published"
        ):
            raise ValueError("invalid_start_next_request_template")
        return self


class StartNextRequestTemplateModel(_ClosedModel):
    """Non-evidential, projection-only authoring scaffold for the first publication."""

    evidential: Literal[False]
    operation: Literal["publish_work"]
    arguments: StartPublishWorkRequestTemplateModel


class StartSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    outcome: Literal["attached", "created", "replayed"]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    frontier: FrontierModel
    compact: StartCompactViewModel
    versions: StartVersionSliceModel
    next_request_template: StartNextRequestTemplateModel
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="after")
    def _validate_start_success(self) -> StartSuccessModel:
        request = self.next_request_template.arguments
        if (
            request.protocol_version != self.protocol_version
            or request.schema_version != self.schema_version
            or request.session_id != self.session_id
            or request.writer_id != self.writer_id
            or request.expected_frontier != self.frontier
        ):
            raise ValueError("start_next_request_binding_mismatch")
        _validate_model_against_schema(self, "start-result")
        return self


type StartResultBranch = Annotated[
    StartSuccessModel | OperationFailureModel, Field(discriminator="ok")
]


class StartResultModel(PublicResultModel[StartResultBranch]):
    pass


_PUBLISH_SUMMARY_CATEGORY: Final[Mapping[tuple[str, str], DataCategory]] = MappingProxyType(
    {
        ("plan_published", "1.0.0"): DataCategory.TASK_DESCRIPTION,
        ("plan_revised", "1.0.0"): DataCategory.TASK_DESCRIPTION,
        ("obligation_published", "1.0.0"): DataCategory.TASK_DESCRIPTION,
        ("decision_recorded", "1.0.0"): DataCategory.DECISION_EXCERPT,
        ("action_recorded", "1.0.0"): DataCategory.COMMAND_METADATA,
        ("result_recorded", "1.0.0"): DataCategory.COMMAND_METADATA,
        ("evidence_recorded", "1.0.0"): DataCategory.EVIDENCE_EXCERPT,
        ("claim_recorded", "1.0.0"): DataCategory.FINDING_SUMMARY,
        ("response_recorded", "1.0.0"): DataCategory.FINDING_SUMMARY,
        ("finding_recorded", "1.0.0"): DataCategory.FINDING_SUMMARY,
    }
)
_PUBLISH_FIXED_SUMMARY: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {
        ("session_opened", "1.0.0"): "session_opened",
        ("session_resumed", "1.0.0"): "session_resumed",
        ("assignment_recorded", "1.0.0"): "assignment_recorded",
        ("redaction_recorded", "1.0.0"): "redaction_recorded",
        ("check_recorded", "1.0.0"): "check_recorded",
        ("receipt_recorded", "1.0.0"): "receipt_recorded",
    }
)


class PublishWorkAcceptedEventModel(_ClosedModel):
    optional_non_null_fields = frozenset({"summary"})

    event_id: EventIdWire
    schema_name: SchemaNameWire
    schema_version: SemverWire
    writer_sequence: CanonicalUInt64Wire
    ingestion_sequence: CanonicalUInt64Wire
    accepted_at: TimestampWire
    predecessor_digest: GenesisOrSha256Digest
    entry_digest: Sha256Digest
    projection_status: Literal["projected", "unknown_unprojected"]
    summary: String1To8192 | OmittedContentModel | None = None

    @model_validator(mode="after")
    def _validate_summary_identity(self) -> PublishWorkAcceptedEventModel:
        if self.summary is None:
            return self
        identity = (self.schema_name, self.schema_version)
        category = _PUBLISH_SUMMARY_CATEGORY.get(identity)
        if category is not None:
            if (
                isinstance(self.summary, OmittedContentModel)
                and self.summary.category is not category
            ):
                raise ValueError("publish_summary_category_mismatch")
            return self
        fixed = _PUBLISH_FIXED_SUMMARY.get(identity)
        if fixed is not None:
            if type(self.summary) is not str or self.summary != fixed:
                raise ValueError("publish_fixed_summary_mismatch")
            return self
        if type(self.summary) is not str or self.summary != "opaque_unknown":
            raise ValueError("publish_opaque_summary_mismatch")
        return self


class PublishWorkVersionSliceModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    engine_version: VersionWire
    projection_version: VersionWire
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...]

    @model_validator(mode="after")
    def _validate_publish_version_packs(self) -> PublishWorkVersionSliceModel:
        _require_unique(self.policy_packs, limit=2)
        return self


class PublishWorkSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    outcome: Literal["accepted", "replayed"]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    accepted_events: tuple[PublishWorkAcceptedEventModel, ...]
    warning_codes: tuple[Literal["unknown_event_schema_preserved"], ...]
    coverage: CoverageModel
    gaps: tuple[CodeWire, ...]
    versions: PublishWorkVersionSliceModel
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="after")
    def _validate_publish_success(self) -> PublishWorkSuccessModel:
        if not 1 <= len(self.accepted_events) <= MAX_EVENTS_PER_BATCH:
            raise ValueError("accepted_event_count_invalid")
        _require_unique(self.warning_codes, limit=1)
        _require_unique(self.gaps, limit=64)
        _validate_model_against_schema(self, "publish-work-result")
        return self


class PublishWorkAcceptedMinimalEventModel(_ClosedModel):
    """Structural acceptance facts available from the ledger append result alone."""

    event_id: EventIdWire
    entry_digest: Sha256Digest
    ingestion_sequence: CanonicalUInt64Wire


class PublishWorkAcceptedProjectionUnavailableModel(_ClosedModel):
    """Total acceptance when privacy projection cannot shape the full success body.

    Built only from committed ledger facts plus a correlation id for operator diagnostics. It is a
    true success (`ok: true`), not an error bent into one: the write is durable and the caller does
    not need a second `status` call or same-`request_id` replay to learn what landed.
    """

    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    response_completeness: Literal["accepted_projection_unavailable"]
    reason_code: Literal["response_projection_failed"]
    correlation_id: CorrelationIdWire
    outcome: Literal["accepted", "replayed"]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    accepted_events: tuple[PublishWorkAcceptedMinimalEventModel, ...]

    @model_validator(mode="after")
    def _validate_publish_accepted_projection_unavailable(
        self,
    ) -> PublishWorkAcceptedProjectionUnavailableModel:
        if not 1 <= len(self.accepted_events) <= MAX_EVENTS_PER_BATCH:
            raise ValueError("accepted_event_count_invalid")
        _validate_model_against_schema(self, "publish-work-result")
        return self


class PublishWorkDryRunPreviewEventModel(_ClosedModel):
    """Structural preview of one draft that would be accepted on a real publish."""

    event_id: EventIdWire
    schema_name: SchemaNameWire
    schema_version: SemverWire
    causal_parents: tuple[EventIdWire, ...]
    artifact_refs: tuple[ObjectIdWire, ...]
    evidence_refs: tuple[EvidenceOrResultIdWire, ...]

    @model_validator(mode="after")
    def _validate_preview_refs(self) -> PublishWorkDryRunPreviewEventModel:
        _require_unique(self.causal_parents, limit=32)
        _require_unique(self.artifact_refs, limit=64)
        _require_unique(self.evidence_refs, limit=64)
        return self


class PublishWorkDryRunModel(_ClosedModel):
    """Non-evidential validation preview: nothing was appended and nothing is citable.

    Same discipline as ``status view=candidate_findings``: the body is advisory. It is not a
    check, publication, coverage source, or receipt input. ``evidential`` is always false.
    """

    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    outcome: Literal["dry_run"]
    evidential: Literal[False]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    would_accept: tuple[PublishWorkDryRunPreviewEventModel, ...]
    coverage: CoverageModel
    gaps: tuple[CodeWire, ...]

    @model_validator(mode="after")
    def _validate_publish_dry_run(self) -> PublishWorkDryRunModel:
        if not 1 <= len(self.would_accept) <= MAX_EVENTS_PER_BATCH:
            raise ValueError("dry_run_preview_count_invalid")
        if self.subject_frontier != self.result_frontier:
            raise ValueError("dry_run_frontier_moved")
        _require_unique(self.gaps, limit=64)
        _validate_model_against_schema(self, "publish-work-result")
        return self


def _publish_work_result_kind(value: object) -> str:
    """Discriminate the publish result shapes without overloading ``ok`` alone."""

    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        if source.get("ok") is False:
            return "failure"
        if source.get("response_completeness") == "accepted_projection_unavailable":
            return "accepted_projection_unavailable"
        if source.get("outcome") == "dry_run":
            return "dry_run"
        return "success"
    if getattr(value, "ok", None) is False:
        return "failure"
    if getattr(value, "response_completeness", None) == "accepted_projection_unavailable":
        return "accepted_projection_unavailable"
    if getattr(value, "outcome", None) == "dry_run":
        return "dry_run"
    return "success"


type PublishWorkResultBranch = Annotated[
    Annotated[PublishWorkSuccessModel, Tag("success")]
    | Annotated[
        PublishWorkAcceptedProjectionUnavailableModel,
        Tag("accepted_projection_unavailable"),
    ]
    | Annotated[PublishWorkDryRunModel, Tag("dry_run")]
    | Annotated[OperationFailureModel, Tag("failure")],
    Discriminator(_publish_work_result_kind),
]


class PublishWorkResultModel(PublicResultModel[PublishWorkResultBranch]):
    """RootModel wrapper with typed success-field accessors for static checkers.

    Pydantic RootModel delegates attributes to ``root`` at runtime, but pyright does not
    synthesize those members. After the publish result discriminator, call sites that
    hold ``PublishWorkInternalResult | PublishWorkResult`` need these fields to exist on both
    arms. Failure roots raise ``AttributeError`` for success-only names (callers check ``ok``).
    """

    @property
    def ok(self) -> bool:
        return self.root.ok

    @property
    def outcome(self) -> Literal["accepted", "replayed", "dry_run"]:
        root = self.root
        if type(root) is PublishWorkSuccessModel:
            return root.outcome
        if type(root) is PublishWorkAcceptedProjectionUnavailableModel:
            return root.outcome
        if type(root) is PublishWorkDryRunModel:
            return root.outcome
        raise AttributeError("outcome")

    @property
    def subject_frontier(self) -> FrontierModel:
        root = self.root
        if type(root) is PublishWorkSuccessModel:
            return root.subject_frontier
        if type(root) is PublishWorkAcceptedProjectionUnavailableModel:
            return root.subject_frontier
        if type(root) is PublishWorkDryRunModel:
            return root.subject_frontier
        raise AttributeError("subject_frontier")

    @property
    def result_frontier(self) -> FrontierModel:
        root = self.root
        if type(root) is PublishWorkSuccessModel:
            return root.result_frontier
        if type(root) is PublishWorkAcceptedProjectionUnavailableModel:
            return root.result_frontier
        if type(root) is PublishWorkDryRunModel:
            return root.result_frontier
        raise AttributeError("result_frontier")

    @property
    def accepted_events(
        self,
    ) -> (
        tuple[PublishWorkAcceptedEventModel, ...] | tuple[PublishWorkAcceptedMinimalEventModel, ...]
    ):
        root = self.root
        if type(root) is PublishWorkSuccessModel:
            return root.accepted_events
        if type(root) is PublishWorkAcceptedProjectionUnavailableModel:
            return root.accepted_events
        raise AttributeError("accepted_events")


class CheckPolicyExecutionModel(_ClosedModel):
    policy_id: Literal["research-evidence", "work-integrity"]
    policy_version: Literal["0.1.0"]
    outcome: Literal["failed", "run", "skipped"]
    reason: Literal[
        "completed",
        "material_unavailable",
        "not_applicable",
        "policy_failure",
        "scope_excluded",
    ]

    @model_validator(mode="after")
    def _validate_policy_execution_pair(self) -> CheckPolicyExecutionModel:
        allowed: Mapping[str, frozenset[str]] = {
            "run": frozenset({"completed"}),
            "skipped": frozenset({"material_unavailable", "not_applicable", "scope_excluded"}),
            "failed": frozenset({"policy_failure"}),
        }
        if self.reason not in allowed[self.outcome]:
            raise ValueError("policy_execution_pair_invalid")
        return self


class CheckProjectedFindingModel(_ClosedModel):
    finding_id: FindingIdWire
    kind: Literal[
        "action_without_result",
        "claim_without_admissible_evidence",
        "completion_with_open_obligations",
        "contradictory_claims_unresolved",
        "diff_does_not_match_account",
        "evidence_does_not_support_claim",
        "failed_work_omitted",
        "ledger_stale_or_incomplete",
        "material_limitation_omitted",
        "questionable_finding_rejection",
        "requested_item_never_attempted",
        "result_without_action",
        "stale_evidence_for_changed_state",
        "weak_or_stale_response",
    ]
    origin: Literal["deterministic", "semantic_model_derived"]
    priority: Annotated[int, Field(ge=1, le=3)]
    summary: String1To8192 | OmittedContentModel
    detail: String1To8192 | OmittedContentModel
    subject_refs: tuple[SubjectIdWire, ...]
    policy_id: Literal["research-evidence", "work-integrity"]
    policy_version: Literal["0.1.0"]
    subject_frontier: FrontierModel
    coverage: CoverageModel
    provenance: JsonValue | None

    @model_validator(mode="after")
    def _validate_projected_finding(self) -> CheckProjectedFindingModel:
        if not 1 <= len(self.subject_refs) <= 64 or len(set(self.subject_refs)) != len(
            self.subject_refs
        ):
            raise ValueError("finding_subject_refs_invalid")
        for value in (self.summary, self.detail):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.FINDING_SUMMARY
            ):
                raise ValueError("finding_omission_category_invalid")
        if self.origin == "deterministic" and self.provenance is not None:
            raise ValueError("deterministic_finding_provenance_invalid")
        if self.origin == "semantic_model_derived" and self.provenance is None:
            raise ValueError("semantic_finding_provenance_missing")
        return self


class CheckVersionSliceModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    engine_version: VersionWire
    projection_version: VersionWire
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...]

    @model_validator(mode="after")
    def _validate_check_version_packs(self) -> CheckVersionSliceModel:
        if not 1 <= len(self.policy_packs) <= 2:
            raise ValueError("policy_pack_count_invalid")
        _require_unique(self.policy_packs, limit=2)
        return self


def _semantic_provenance_identity(
    value: object,
) -> tuple[SemanticStatus, SemanticReason]:
    """Extract identity fields without invoking methods on caller-owned mappings."""

    if type(value) is not dict:
        raise ProtocolValueError("invalid_semantic_provenance")

    missing = object()
    status_token: object = missing
    reason_token: object = missing
    source = cast(dict[object, object], value)
    for key, item in source.items():
        if type(key) is not str:
            continue
        if key == "status":
            status_token = item
        elif key == "reason":
            reason_token = item

    if type(status_token) is not str or type(reason_token) is not str:
        raise ProtocolValueError("invalid_semantic_provenance")
    try:
        status = SemanticStatus(status_token)
        reason = SemanticReason(reason_token)
    except ValueError as exc:
        raise ProtocolValueError("invalid_semantic_provenance") from exc
    return status, reason


class CheckContinuationModel(_ClosedModel):
    """What the caller must do to resume one suspended check.

    The command is fixed by the service rather than assembled by the caller: an agent told only
    that a human must approve has no supported way to learn *what* to approve, which is what
    drives it to read the catalog database or product source instead.
    """

    optional_non_null_fields = frozenset({"pending_id", "expires_at"})

    kind: Literal["privacy_disclosure_decision", "repository_privacy_setup"]
    pending_id: PrivacyProposalIdWire | None = None
    expires_at: TimestampWire | None = None
    command: tuple[String1To256, ...]
    replay_request_id: RequestIdWire
    instruction: String1To4096

    @model_validator(mode="after")
    def _validate_continuation(self) -> CheckContinuationModel:
        if self.kind == "privacy_disclosure_decision":
            if self.pending_id is None or self.expires_at is None or len(self.command) != 4:
                raise ValueError("check_continuation_disclosure_invalid")
            head = ("yoetz", "privacy", "decide-disclosure")
            if tuple(self.command[:3]) != head or self.command[3] != self.pending_id:
                raise ValueError("check_continuation_command_invalid")
        elif (
            self.pending_id is not None
            or self.expires_at is not None
            or tuple(self.command) != ("yoetz", "--privacy")
        ):
            raise ValueError("check_continuation_repository_setup_invalid")
        return self


class CheckAwaitingHumanModel(_ClosedModel):
    """The nonterminal CHECK branch: suspended on a local disclosure decision.

    Carries no verdict, findings, coverage, or semantic provenance. A completion-grade shape here
    would let a caller conclude from a check that never ran.
    """

    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    state: Literal["awaiting_human"]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    semantic_status: Literal["awaiting_human"]
    semantic_reason: Literal["human_approval_required"]
    continuation: CheckContinuationModel
    versions: CheckVersionSliceModel
    # Every projected success body carries its disclosure projection; the nonterminal branch is
    # projected through the same path and must not be an exception to that invariant.
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="after")
    def _validate_awaiting_human(self) -> CheckAwaitingHumanModel:
        if self.continuation.replay_request_id != self.request_id:
            raise ValueError("check_continuation_request_mismatch")
        _validate_model_against_schema(self, "check-result")
        return self


class CheckSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    state: Literal["complete"]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    verdict: Literal[
        "action_required", "incomplete_check", "insufficient_coverage", "no_issue_detected"
    ]
    findings: tuple[CheckProjectedFindingModel, ...]
    suppressed_count: CanonicalUInt64Wire
    policy_executions: tuple[CheckPolicyExecutionModel, ...]
    semantic_status: SemanticStatusWire
    semantic_reason: SemanticReasonWire
    semantic_provenance: JsonValue | None
    coverage: CoverageModel
    versions: CheckVersionSliceModel
    privacy_projection: PrivacyProjectionModel

    @field_validator("semantic_provenance", mode="before")
    @classmethod
    def _require_safe_semantic_provenance_identity(cls, value: object) -> object:
        if value is not None:
            _semantic_provenance_identity(value)
        return value

    @model_validator(mode="after")
    def _validate_check_success(self) -> CheckSuccessModel:
        if len(self.findings) > MAX_FINDINGS_LIMIT:
            raise ValueError("finding_count_invalid")
        if not 1 <= len(self.policy_executions) <= 2 or len(set(self.policy_executions)) != len(
            self.policy_executions
        ):
            raise ValueError("policy_execution_count_invalid")
        validate_semantic_outcome(self.semantic_status, self.semantic_reason)
        provenance_status: SemanticStatus | None = None
        provenance_reason: SemanticReason | None = None
        if self.semantic_provenance is not None:
            provenance_status, provenance_reason = _semantic_provenance_identity(
                self.semantic_provenance
            )
        validate_semantic_provenance_binding(
            self.semantic_status,
            self.semantic_reason,
            provenance_status,
            provenance_reason,
        )
        _validate_model_against_schema(self, "check-result")
        return self


type CheckSuccessBranch = Annotated[
    CheckSuccessModel | CheckAwaitingHumanModel, Field(discriminator="state")
]
type CheckResultBranch = Annotated[
    CheckSuccessBranch | OperationFailureModel, Field(discriminator="ok")
]


class CheckResultModel(PublicResultModel[CheckResultBranch]):
    pass


class RespondAcceptedEventModel(_ClosedModel):
    event_id: EventIdWire
    writer_sequence: CanonicalUInt64Wire
    ingestion_sequence: CanonicalUInt64Wire
    accepted_at: TimestampWire
    entry_digest: Sha256Digest


class RespondEvidenceSummaryModel(_ClosedModel):
    optional_non_null_fields = frozenset({"description"})

    reference_id: EvidenceOrResultIdWire
    description: String1To8192 | OmittedContentModel | None = None

    @model_validator(mode="after")
    def _validate_evidence_omission(self) -> RespondEvidenceSummaryModel:
        if (
            isinstance(self.description, OmittedContentModel)
            and self.description.category is not DataCategory.EVIDENCE_EXCERPT
        ):
            raise ValueError("evidence_omission_category_invalid")
        return self


class RespondResponseModel(_ClosedModel):
    optional_non_null_fields = frozenset({"reason", "waiver_scope", "waiver_expiry"})

    response_event_id: EventIdWire
    finding_id: FindingIdWire
    finding_frontier: FrontierModel
    disposition: Literal["acknowledged", "provenance_disputed", "rejected", "waived"]
    evidence: tuple[RespondEvidenceSummaryModel, ...]
    reason: String1To4096 | OmittedContentModel | None = None
    waiver_scope: Literal["finding_only"] | None = None
    waiver_expiry: TimestampWire | None = None

    @model_validator(mode="after")
    def _validate_response_fields(self) -> RespondResponseModel:
        if len(self.evidence) > 64:
            raise ValueError("response_evidence_limit")
        if (
            isinstance(self.reason, OmittedContentModel)
            and self.reason.category is not DataCategory.FINDING_SUMMARY
        ):
            raise ValueError("reason_omission_category_invalid")
        if self.disposition == "acknowledged":
            if self.waiver_scope is not None or self.waiver_expiry is not None:
                raise ValueError("response_fields_invalid")
        elif self.disposition in {"provenance_disputed", "rejected"}:
            if (
                self.reason is None
                or self.waiver_scope is not None
                or self.waiver_expiry is not None
            ):
                raise ValueError("response_fields_invalid")
        elif self.reason is None or self.waiver_scope != "finding_only":
            raise ValueError("response_fields_invalid")
        return self


class RespondVersionSliceModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    engine_version: VersionWire
    projection_version: VersionWire
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...]

    @model_validator(mode="after")
    def _validate_respond_version_packs(self) -> RespondVersionSliceModel:
        _require_unique(self.policy_packs, limit=2)
        return self


class RespondSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    accepted_event: RespondAcceptedEventModel
    response: RespondResponseModel
    coverage: CoverageModel
    warning_codes: tuple[Literal["waiver_expired_at_recording"], ...]
    versions: RespondVersionSliceModel
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="after")
    def _validate_respond_success(self) -> RespondSuccessModel:
        _require_unique(self.warning_codes, limit=1)
        _validate_model_against_schema(self, "respond-result")
        return self


type RespondResultBranch = Annotated[
    RespondSuccessModel | OperationFailureModel, Field(discriminator="ok")
]


class RespondResultModel(PublicResultModel[RespondResultBranch]):
    pass


class StatusAssignmentItemModel(_ClosedModel):
    assignment_event_id: EventIdWire
    actor_id: ActorAssertionIdWire
    obligation_ids: tuple[ObligationIdWire, ...]
    scope_refs: tuple[SubjectIdWire, ...]
    resolved: bool

    @model_validator(mode="after")
    def _validate_assignment_sets(self) -> StatusAssignmentItemModel:
        _require_unique(self.obligation_ids, limit=64)
        _require_unique(self.scope_refs, limit=64)
        return self


class StatusAssignmentPageModel(_ClosedModel):
    items: tuple[StatusAssignmentItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_assignment_page(self) -> StatusAssignmentPageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusFindingBasisModel(_ClosedModel):
    rule_id: CodeWire
    observed_fact_codes: tuple[CodeWire, ...]
    observed_refs: tuple[SubjectIdWire, ...]
    required_missing_fact_codes: tuple[CodeWire, ...]
    subject_state_relation: Literal["different", "same", "unknown"]
    frozen_source_availability: Literal["available", "not_recorded", "redacted", "unavailable"]
    coverage_gaps: tuple[CodeWire, ...]
    evidence_refs: tuple[EvidenceOrResultIdWire, ...]

    @model_validator(mode="after")
    def _validate_basis_sets(self) -> StatusFindingBasisModel:
        for values in (
            self.observed_fact_codes,
            self.observed_refs,
            self.required_missing_fact_codes,
            self.coverage_gaps,
            self.evidence_refs,
        ):
            _require_unique(values, limit=64)
        return self


class StatusCandidateFindingItemModel(_ClosedModel):
    kind: FindingKindWire
    origin: Literal["deterministic"]
    priority: Annotated[int, Field(ge=1, le=3)]
    summary: String1To8192 | OmittedContentModel
    detail: String1To8192 | OmittedContentModel
    subject_refs: tuple[SubjectIdWire, ...]
    policy_id: Literal["research-evidence", "work-integrity"]
    policy_version: Literal["0.1.0"]
    subject_frontier: FrontierModel
    coverage: CoverageModel
    basis: StatusFindingBasisModel

    @model_validator(mode="after")
    def _validate_candidate_subjects(self) -> StatusCandidateFindingItemModel:
        if not self.subject_refs:
            raise ValueError("finding_subject_refs_empty")
        _require_unique(self.subject_refs, limit=64)
        for value in (self.summary, self.detail):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.FINDING_SUMMARY
            ):
                raise ValueError("finding_omission_category_invalid")
        return self


class StatusCandidateFindingsPageModel(_ClosedModel):
    items: tuple[StatusCandidateFindingItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_candidate_page(self) -> StatusCandidateFindingsPageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusCompactFindingModel(_ClosedModel):
    finding_id: FindingIdWire
    kind: FindingKindWire
    priority: Annotated[int, Field(ge=1, le=3)]
    summary: String1To8192 | OmittedContentModel
    detail: String1To8192 | OmittedContentModel

    @model_validator(mode="after")
    def _validate_compact_finding_omissions(self) -> StatusCompactFindingModel:
        for value in (self.summary, self.detail):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.FINDING_SUMMARY
            ):
                raise ValueError("finding_omission_category_invalid")
        return self


class StatusCompactObligationModel(_ClosedModel):
    optional_non_null_fields = frozenset({"acceptance_criteria"})

    obligation_id: ObligationIdWire
    description: String1To8192 | OmittedContentModel
    evidence_expectation: String1To8192 | OmittedContentModel
    acceptance_criteria: String1To8192 | OmittedContentModel | None = None

    @model_validator(mode="after")
    def _validate_compact_obligation_omissions(self) -> StatusCompactObligationModel:
        for value in (self.description, self.evidence_expectation, self.acceptance_criteria):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.OBLIGATION_TEXT
            ):
                raise ValueError("obligation_omission_category_invalid")
        return self


class StatusCompactItemModel(_ClosedModel):
    task_id: TaskIdWire
    session_id: SessionIdWire
    task_title: String1To8192 | OmittedContentModel
    current_plan_event_id: EventIdWire | None
    declared_obligation_count: CanonicalUInt64Wire | None
    no_obligations_reason: (
        Literal[
            "no_material_change",
            "single_atomic_change",
            "exploratory_scope_unknown",
        ]
        | None
    )
    open_obligation_count: CanonicalUInt64Wire | None
    unanswered_finding_count: CanonicalUInt64Wire
    receipt_blocking_finding_count: CanonicalUInt64Wire
    open_obligations: tuple[StatusCompactObligationModel, ...]
    unanswered_findings: tuple[StatusCompactFindingModel, ...]
    freshness: FreshnessWire
    coverage: CoverageModel
    gaps: tuple[CodeWire, ...]

    @model_validator(mode="after")
    def _validate_compact_item(self) -> StatusCompactItemModel:
        if (
            isinstance(self.task_title, OmittedContentModel)
            and self.task_title.category is not DataCategory.TASK_DESCRIPTION
        ):
            raise ValueError("task_omission_category_invalid")
        if len(self.open_obligations) > 10 or len(self.unanswered_findings) > 10:
            raise ValueError("compact_item_limit")
        _require_unique(self.gaps, limit=64)
        unknown = self.declared_obligation_count is None or self.open_obligation_count is None
        if unknown and (
            self.declared_obligation_count is not None
            or self.open_obligation_count is not None
            or self.no_obligations_reason is not None
        ):
            raise ValueError("compact_scope_partial_unknown")
        if self.current_plan_event_id is None and (
            self.declared_obligation_count != "0" or self.no_obligations_reason is not None
        ):
            raise ValueError("compact_no_plan_scope_mismatch")
        if not unknown:
            declared = int(cast(str, self.declared_obligation_count))
            opened = int(cast(str, self.open_obligation_count))
            if opened > declared or len(self.open_obligations) > opened:
                raise ValueError("compact_obligation_count_mismatch")
            if self.no_obligations_reason is not None and declared != 0:
                raise ValueError("compact_no_obligations_reason_mismatch")
        if len(self.unanswered_findings) > int(self.unanswered_finding_count):
            raise ValueError("compact_unanswered_finding_count_mismatch")
        return self


class StatusCompactPageModel(_ClosedModel):
    items: tuple[StatusCompactItemModel, ...]
    next_cursor: None

    @model_validator(mode="after")
    def _validate_compact_page(self) -> StatusCompactPageModel:
        if len(self.items) > 1:
            raise ValueError("status_page_limit")
        return self


class StatusStructuralSubjectStateModel(_ClosedModel):
    optional_non_null_fields = frozenset({"tree_digest", "diff_digest"})

    tree_digest: Sha256Digest | None = None
    diff_digest: Sha256Digest | None = None

    @model_validator(mode="after")
    def _validate_structural_subject_state(self) -> StatusStructuralSubjectStateModel:
        if self.tree_digest is None and self.diff_digest is None:
            raise ProtocolValueError("empty_subject_state")
        return self


class StatusEvidenceItemModel(_ClosedModel):
    evidence_id: EvidenceIdWire
    strength: Literal[
        "content_digest",
        "immutable_snapshot",
        "independently_reproduced",
        "metadata_only",
        "mutable_reference",
    ]
    freshness: FreshnessWire
    available: bool
    description: String1To8192 | OmittedContentModel | None
    reference: String1To8192 | OmittedContentModel | None
    captured_object_id: ObjectIdWire | None
    content_digest: Sha256Digest | None
    subject_state: StatusStructuralSubjectStateModel | None

    @model_validator(mode="after")
    def _validate_evidence_item_omissions(self) -> StatusEvidenceItemModel:
        for value in (self.description, self.reference):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.EVIDENCE_EXCERPT
            ):
                raise ValueError("evidence_omission_category_invalid")
        return self


class StatusEvidencePageModel(_ClosedModel):
    items: tuple[StatusEvidenceItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_evidence_page(self) -> StatusEvidencePageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusFindingItemModel(_ClosedModel):
    finding_id: FindingIdWire
    kind: FindingKindWire
    origin: Literal["deterministic", "semantic_model_derived"]
    priority: Annotated[int, Field(ge=1, le=3)]
    summary: String1To8192 | OmittedContentModel
    detail: String1To8192 | OmittedContentModel
    subject_refs: tuple[SubjectIdWire, ...]
    policy_id: Literal["research-evidence", "work-integrity"]
    policy_version: Literal["0.1.0"]
    subject_frontier: FrontierModel
    coverage: CoverageModel
    provenance: JsonValue | None
    disposition: Literal["acknowledged", "none", "provenance_disputed", "rejected", "waived"]
    resolved: bool
    response_event_id: EventIdWire | None
    reason: String1To8192 | OmittedContentModel | None
    waiver_scope: Literal["finding_only"] | None
    waiver_expiry: TimestampWire | None

    @model_validator(mode="after")
    def _validate_finding_item(self) -> StatusFindingItemModel:
        if not self.subject_refs:
            raise ValueError("finding_subject_refs_empty")
        _require_unique(self.subject_refs, limit=64)
        for value in (self.summary, self.detail, self.reason):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.FINDING_SUMMARY
            ):
                raise ValueError("finding_omission_category_invalid")
        if self.disposition == "provenance_disputed" and (
            self.reason is None
            or self.resolved
            or self.waiver_scope is not None
            or self.waiver_expiry is not None
        ):
            raise ValueError("provenance_disputed_finding_state_invalid")
        return self


class StatusFindingsPageModel(_ClosedModel):
    items: tuple[StatusFindingItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_findings_page(self) -> StatusFindingsPageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusHistoryItemModel(_ClosedModel):
    event_id: EventIdWire
    schema_name: SchemaNameWire
    schema_version: SemverWire
    actor_id: ActorAssertionIdWire
    publication_channel: Literal[
        "codex_jsonl_import",
        "cooperative_mcp",
        "engine_derived",
        "hook_observed",
        "human_import",
        "local_cli",
    ]
    ingestion_sequence: CanonicalUInt64Wire
    # Caller-asserted event time; not service acceptance time.
    occurred_at: TimestampWire
    # Trusted-local service acceptance time bound into the entry digest.
    accepted_at: TimestampWire
    # Exact comparison of the two recorded clocks; this does not verify caller time.
    occurred_at_consistency: Annotated[
        Literal[
            "within_forward_skew_allowance",
            "ahead_of_forward_skew_allowance",
        ],
        Field(
            description=(
                "Exact comparison of caller-asserted occurred_at with service accepted_at. "
                "Caller time through five seconds ahead is within_forward_skew_allowance; "
                "larger forward drift is ahead_of_forward_skew_allowance. This does not verify "
                "caller time or affect ingestion-sequence ordering."
            )
        ),
    ]
    projection_status: Literal["projected", "unknown_unprojected"]
    summary_code: Literal[
        "action_recorded",
        "assignment_recorded",
        "check_recorded",
        "claim_recorded",
        "decision_recorded",
        "evidence_recorded",
        "finding_recorded",
        "obligation_published",
        "opaque_unknown",
        "plan_published",
        "plan_revised",
        "receipt_recorded",
        "redaction_recorded",
        "response_recorded",
        "result_recorded",
        "session_opened",
        "session_resumed",
    ]


class StatusHistoryPageModel(_ClosedModel):
    items: tuple[StatusHistoryItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_history_page(self) -> StatusHistoryPageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusImportStatusModel(_ClosedModel):
    pending_count: CanonicalUInt64Wire
    terminal_count: CanonicalUInt64Wire
    phase: (
        Literal[
            "plan_ready",
            "publishing",
            "report_published",
            "report_ready",
            "source_reserved",
            "terminal",
        ]
        | None
    )
    report_evidence_id: EvidenceIdWire | None
    source_identity_digest: Sha256Digest | None


class StatusClosureReadinessModel(_ClosedModel):
    """What currently bounds a completion conclusion, before a check or receipt is spent.

    Advisory and derived: reading it records nothing, creates no verdict, and never strengthens
    coverage. It exists so an agent can see that a check would come back coverage-bounded instead
    of discovering it afterwards from the receipt.
    """

    declared_obligation_count: CanonicalUInt64Wire | None
    no_obligations_reason: (
        Literal[
            "no_material_change",
            "single_atomic_change",
            "exploratory_scope_unknown",
        ]
        | None
    )
    open_obligation_count: CanonicalUInt64Wire | None
    unanswered_finding_count: CanonicalUInt64Wire | None
    receipt_blocking_finding_count: CanonicalUInt64Wire | None
    blocking_conditions: tuple[
        Literal[
            "obligations_open",
            "findings_unanswered",
            "receipt_findings_unresolved",
            "no_plan_published",
            "no_obligations_declared",
            "projection_stale",
            "coverage_gaps_declared",
            "readiness_unknown",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def _validate_closure_readiness(self) -> StatusClosureReadinessModel:
        _require_unique(self.blocking_conditions, limit=8)
        # Absent counts mean the compact singleton could not be read (an unreadable task title
        # omits it). Reporting zero there would assert "nothing is open" from missing data, so
        # unknown must be null and must say so rather than look like a clean record.
        counts = (
            self.declared_obligation_count,
            self.open_obligation_count,
            self.unanswered_finding_count,
            self.receipt_blocking_finding_count,
        )
        unknown = any(value is None for value in counts)
        if unknown != ("readiness_unknown" in self.blocking_conditions):
            raise ValueError("closure_readiness_unknown_mismatch")
        if unknown and any(value is not None for value in counts):
            raise ValueError("closure_readiness_partial_unknown")
        if unknown and self.no_obligations_reason is not None:
            raise ValueError("closure_readiness_unknown_reason")
        if unknown and set(self.blocking_conditions) != {"readiness_unknown"}:
            raise ValueError("closure_readiness_unknown_not_exclusive")
        if not unknown:
            declared = int(cast(str, self.declared_obligation_count))
            opened = int(cast(str, self.open_obligation_count))
            unanswered = int(cast(str, self.unanswered_finding_count))
            receipt_blocking = int(cast(str, self.receipt_blocking_finding_count))
            if opened > declared:
                raise ValueError("closure_readiness_obligation_count_mismatch")
            if self.no_obligations_reason is not None and declared != 0:
                raise ValueError("closure_readiness_no_obligations_reason_mismatch")
            if (opened > 0) != ("obligations_open" in self.blocking_conditions):
                raise ValueError("closure_readiness_open_blocker_mismatch")
            if (unanswered > 0) != ("findings_unanswered" in self.blocking_conditions):
                raise ValueError("closure_readiness_unanswered_blocker_mismatch")
            if (receipt_blocking > 0) != (
                "receipt_findings_unresolved" in self.blocking_conditions
            ):
                raise ValueError("closure_readiness_receipt_blocker_mismatch")
            no_plan = "no_plan_published" in self.blocking_conditions
            expected_empty_scope_blocker = (
                not no_plan and declared == 0 and self.no_obligations_reason is None
            )
            if (
                "no_obligations_declared" in self.blocking_conditions
            ) != expected_empty_scope_blocker:
                raise ValueError("closure_readiness_empty_scope_blocker_mismatch")
            if no_plan and (declared != 0 or self.no_obligations_reason is not None):
                raise ValueError("closure_readiness_no_plan_blocker_mismatch")
        return self


class StatusObligationItemModel(_ClosedModel):
    optional_non_null_fields = frozenset({"acceptance_criteria"})

    obligation_id: ObligationIdWire
    status: Literal["open", "resolved"]
    description: String1To8192 | OmittedContentModel
    evidence_expectation: String1To8192 | OmittedContentModel
    source_refs: tuple[SubjectIdWire, ...]
    assigned_actor_ids: tuple[ActorAssertionIdWire, ...]
    evidence_refs: tuple[EvidenceOrResultIdWire, ...]
    revision_event_id: EventIdWire | None
    acceptance_criteria: String1To8192 | OmittedContentModel | None = None

    @model_validator(mode="after")
    def _validate_obligation_item(self) -> StatusObligationItemModel:
        for values in (self.source_refs, self.assigned_actor_ids, self.evidence_refs):
            _require_unique(values, limit=64)
        for value in (self.description, self.evidence_expectation, self.acceptance_criteria):
            if (
                isinstance(value, OmittedContentModel)
                and value.category is not DataCategory.OBLIGATION_TEXT
            ):
                raise ValueError("obligation_omission_category_invalid")
        return self


class StatusObligationsPageModel(_ClosedModel):
    items: tuple[StatusObligationItemModel, ...]
    next_cursor: CursorWire | None

    @model_validator(mode="after")
    def _validate_obligations_page(self) -> StatusObligationsPageModel:
        if len(self.items) > 100:
            raise ValueError("status_page_limit")
        return self


class StatusAdviceItemModel(_ClosedModel):
    finding_id: FindingIdWire
    rule_code: CodeWire
    priority: Annotated[int, Field(ge=1, le=100)]
    evidence_commitments: tuple[AsciiString1To160, ...]
    coverage: CoverageModel
    freshness_frontier: AsciiString1To160
    verification_state: Literal["current", "stale", "unavailable", "not_required"]
    semantic_state: Literal["ready", "disabled", "unavailable", "failed"]
    recommended_next_action: CodeWire

    @model_validator(mode="after")
    def _validate_advice_item(self) -> StatusAdviceItemModel:
        _require_unique(self.evidence_commitments, limit=16)
        return self


class StatusAdvicePageModel(_ClosedModel):
    projection_format: Literal["yoetz.advice-snapshot/1"]
    items: tuple[StatusAdviceItemModel, ...]
    next_cursor: None

    @model_validator(mode="after")
    def _validate_advice_page(self) -> StatusAdvicePageModel:
        if len(self.items) > 64:
            raise ValueError("status_page_limit")
        return self


class StatusVersionSliceModel(_ClosedModel):
    optional_non_null_fields = frozenset({"route_profile"})

    protocol_version: Literal["0.1"]
    engine_version: VersionWire
    projection_version: VersionWire
    object_format: Literal["yoetz-object/1"]
    storage_schema: CanonicalUInt64Wire
    python_version: VersionWire
    apsw_version: VersionWire
    sqlite_version: VersionWire
    sqlite_source_id: AsciiString1To160
    policy_packs: tuple[Literal["research-evidence/0.1.0", "work-integrity/0.1.0"], ...]
    provider_profiles: tuple[ProfileIdWire, ...]
    route_profile: Literal["policy", "strict"] | None = None

    @model_validator(mode="after")
    def _validate_status_version_sets(self) -> StatusVersionSliceModel:
        _require_unique(self.policy_packs, limit=2)
        _require_unique(self.provider_profiles, limit=16)
        return self


class StatusVersionsPageModel(_ClosedModel):
    items: tuple[StatusVersionSliceModel, ...]
    next_cursor: None

    @model_validator(mode="after")
    def _validate_versions_page(self) -> StatusVersionsPageModel:
        if len(self.items) > 1:
            raise ValueError("status_page_limit")
        return self


class StatusOperationAcceptedEventModel(_ClosedModel):
    event_id: EventIdWire
    entry_digest: Sha256Digest
    ingestion_sequence: CanonicalUInt64Wire
    writer_sequence: CanonicalUInt64Wire
    projection_status: Literal["projected", "unknown_unprojected"]


class StatusOperationPageModel(_ClosedModel):
    """One request-id-keyed operation recovery page for the authenticated writer."""

    operation_request_id: RequestIdWire
    found: bool
    state: Literal["absent", "pending", "complete", "quarantined"]
    operation_kind: Literal["start", "publish_work", "check", "respond", "receipt"] | None = None
    outcome: Literal["accepted", "replayed"] | None = None
    subject_frontier: FrontierModel | None = None
    result_frontier: FrontierModel | None = None
    accepted_events: tuple[StatusOperationAcceptedEventModel, ...] = ()
    # Recovery after lost output or compaction: a pending check suspended on a local disclosure
    # decision returns the same continuation its check result carried. Every other operation
    # state returns null, so its presence is never ambiguous.
    continuation: CheckContinuationModel | None = None
    next_cursor: None = None

    @model_validator(mode="after")
    def _validate_operation_page(self) -> StatusOperationPageModel:
        if (
            self.continuation is not None
            and self.continuation.replay_request_id != self.operation_request_id
        ):
            raise ValueError("status_operation_continuation_request_mismatch")
        if self.continuation is not None and not (
            self.state == "pending" and self.operation_kind == "check"
        ):
            raise ValueError("status_operation_page_invalid")
        if self.state == "absent":
            if (
                self.found
                or self.operation_kind is not None
                or self.outcome is not None
                or self.subject_frontier is not None
                or self.result_frontier is not None
                or self.accepted_events
            ):
                raise ValueError("status_operation_page_invalid")
            return self
        if not self.found or self.operation_kind is None:
            raise ValueError("status_operation_page_invalid")
        if self.state == "complete" and self.operation_kind == "publish_work":
            if (
                self.outcome is None
                or self.subject_frontier is None
                or self.result_frontier is None
                or not self.accepted_events
            ):
                raise ValueError("status_operation_page_invalid")
            if len(self.accepted_events) > MAX_EVENTS_PER_BATCH:
                raise ValueError("status_page_limit")
            return self
        if self.state == "complete":
            # Non-publish completions are reported without append-shaped event detail.
            if (
                self.outcome is not None
                or self.subject_frontier is not None
                or self.result_frontier is not None
                or self.accepted_events
            ):
                raise ValueError("status_operation_page_invalid")
            return self
        if (
            self.outcome is not None
            or self.subject_frontier is not None
            or self.result_frontier is not None
            or self.accepted_events
        ):
            raise ValueError("status_operation_page_invalid")
        return self


type StatusPage = (
    StatusAdvicePageModel
    | StatusAssignmentPageModel
    | StatusCandidateFindingsPageModel
    | StatusCompactPageModel
    | StatusEvidencePageModel
    | StatusFindingsPageModel
    | StatusHistoryPageModel
    | StatusObligationsPageModel
    | StatusOperationPageModel
    | StatusVersionsPageModel
)

_STATUS_PAGE_BY_VIEW: Final[Mapping[str, type[_ClosedModel]]] = MappingProxyType(
    {
        "advice": StatusAdvicePageModel,
        "assignment": StatusAssignmentPageModel,
        "candidate_findings": StatusCandidateFindingsPageModel,
        "compact": StatusCompactPageModel,
        "evidence": StatusEvidencePageModel,
        "findings": StatusFindingsPageModel,
        "history": StatusHistoryPageModel,
        "obligations": StatusObligationsPageModel,
        "operation": StatusOperationPageModel,
        "versions": StatusVersionsPageModel,
    }
)


class StatusSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    task_id: TaskIdWire
    session_id: SessionIdWire
    writer_id: WriterIdWire
    view: Literal[
        "advice",
        "assignment",
        "candidate_findings",
        "compact",
        "evidence",
        "findings",
        "history",
        "obligations",
        "operation",
        "versions",
    ]
    requested_frontier: FrontierModel
    head_frontier: FrontierModel
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    projection_lag: CanonicalUInt64Wire
    projection_version: VersionWire
    rebuild_state: Literal["current", "rebuild_required", "rebuilding"]
    page: StatusPage
    coverage: CoverageModel
    gaps: tuple[CodeWire, ...]
    import_status: StatusImportStatusModel
    closure_readiness: StatusClosureReadinessModel
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="before")
    @classmethod
    def _route_status_page(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        source = cast(Mapping[object, object], value)
        raw_page = source.get("page")
        raw_view = source.get("view")
        if raw_page is None or type(raw_view) is not str:
            return cast(object, value)
        page_type = _STATUS_PAGE_BY_VIEW.get(raw_view)
        if page_type is None:
            return cast(object, value)
        routed = dict(source)
        routed["page"] = page_type.model_validate(raw_page)
        return routed

    @model_validator(mode="after")
    def _validate_status_success(self) -> StatusSuccessModel:
        expected_type = _STATUS_PAGE_BY_VIEW[self.view]
        if type(self.page) is not expected_type:
            raise ValueError("status_view_page_mismatch")
        _require_unique(self.gaps, limit=64)
        _validate_model_against_schema(self, "status-result")
        return self


type StatusResultBranch = Annotated[
    StatusSuccessModel | OperationFailureModel, Field(discriminator="ok")
]


class StatusResultModel(PublicResultModel[StatusResultBranch]):
    pass


class ReceiptPolicyVersionEntryModel(_ClosedModel):
    policy_id: ReceiptPolicyIdWire
    policy_version: ReceiptVersionIdentityWire


class ReceiptSchemaVersionEntryModel(_ClosedModel):
    schema_id: ReceiptSchemaIdWire
    schema_version: ReceiptVersionIdentityWire


class ReceiptVersionSliceModel(_ClosedModel):
    package_name: Literal["yoetz"]
    package_version: ReceiptVersionIdentityWire
    protocol_version: ReceiptVersionIdentityWire
    engine_version: ReceiptVersionIdentityWire
    projection_version: ReceiptVersionIdentityWire
    object_format_version: ReceiptVersionIdentityWire
    catalog_schema_version: ReceiptSchemaCounterVersionWire
    bundle_schema_version: ReceiptSchemaCounterVersionWire
    policy_versions: Annotated[
        tuple[ReceiptPolicyVersionEntryModel, ...], Field(min_length=1, max_length=16)
    ]
    schema_versions: Annotated[
        tuple[ReceiptSchemaVersionEntryModel, ...], Field(min_length=1, max_length=64)
    ]
    resource_manifest_digest: Sha256Digest

    @model_validator(mode="after")
    def _validate_receipt_version_entries(self) -> ReceiptVersionSliceModel:
        policy_keys = tuple(
            f"{entry.policy_id}\x00{entry.policy_version}".encode("ascii")
            for entry in self.policy_versions
        )
        schema_keys = tuple(
            f"{entry.schema_id}\x00{entry.schema_version}".encode("ascii")
            for entry in self.schema_versions
        )
        if policy_keys != tuple(sorted(set(policy_keys))):
            raise ValueError("receipt_policy_versions_not_canonical")
        if schema_keys != tuple(sorted(set(schema_keys))):
            raise ValueError("receipt_schema_versions_not_canonical")
        return self


class ReceiptSuccessModel(_ClosedModel):
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: RequestIdWire
    ok: Literal[True]
    receipt_id: ReceiptIdWire
    task_id: TaskIdWire
    session_id: SessionIdWire
    subject_frontier: FrontierModel
    result_frontier: FrontierModel
    receipt_object_id: ObjectIdWire
    receipt_digest: Sha256Digest
    conclusion: Literal[
        "insufficient_coverage",
        "no_unresolved_deterministic_findings",
        "unresolved_findings_remain",
    ]
    redaction_profile: ReceiptRedactionProfileWire
    format: ReceiptFormatWire
    include: ReceiptIncludeWire
    document: JsonValue | None
    human_text: String1To32768 | OmittedContentModel | None
    coverage: CoverageModel
    suppressed_finding_count: Annotated[int, Field(ge=0, le=2**53 - 1)]
    versions: ReceiptVersionSliceModel
    privacy_projection: PrivacyProjectionModel

    @model_validator(mode="after")
    def _validate_receipt_success(self) -> ReceiptSuccessModel:
        if (
            isinstance(self.human_text, OmittedContentModel)
            and self.human_text.category is not DataCategory.FINDING_SUMMARY
        ):
            raise ValueError("finding_omission_category_invalid")
        if self.format is ReceiptFormat.JSON:
            if self.document is None or self.human_text is not None:
                raise ValueError("receipt_format_body_mismatch")
        elif self.document is not None or self.human_text is None:
            raise ValueError("receipt_format_body_mismatch")
        _validate_model_against_schema(self, "receipt-result")
        return self


type ReceiptResultBranch = Annotated[
    ReceiptSuccessModel | OperationFailureModel, Field(discriminator="ok")
]


class ReceiptResultModel(PublicResultModel[ReceiptResultBranch]):
    pass


StartRequest = StartRequestModel
StartResult = StartResultModel
PublishWorkRequest = PublishWorkRequestModel
PublishWorkResult = PublishWorkResultModel
CheckRequest = CheckRequestModel
CheckResult = CheckResultModel
RespondRequest = RespondRequestModel
RespondResult = RespondResultModel
StatusRequest = StatusRequestModel
StatusResult = StatusResultModel
ReceiptRequest = ReceiptRequestModel
ReceiptResult = ReceiptResultModel
ReadGuidanceRequest = ReadGuidanceRequestModel
ReadGuidanceResult = ReadGuidanceResultModel

_PUBLIC_MODEL_SCHEMA: Final[Mapping[type[object], str]] = MappingProxyType(
    {
        StartRequestModel: "start-request",
        StartResultModel: "start-result",
        PublishWorkRequestModel: "publish-work-request",
        PublishWorkResultModel: "publish-work-result",
        CheckRequestModel: "check-request",
        CheckResultModel: "check-result",
        RespondRequestModel: "respond-request",
        RespondResultModel: "respond-result",
        StatusRequestModel: "status-request",
        StatusResultModel: "status-result",
        ReceiptRequestModel: "receipt-request",
        ReceiptResultModel: "receipt-result",
        ReadGuidanceRequestModel: "read-guidance-request",
        ReadGuidanceResultModel: "read-guidance-result",
    }
)


def public_model_to_wire(model: object) -> dict[str, JsonValue]:
    """Dump one exact public model type and validate it against the local schema catalog."""

    schema_name = _PUBLIC_MODEL_SCHEMA.get(type(model))
    if schema_name is None:
        raise TypeError("public_model_wrong_type")
    public_model = cast(BaseModel, model)
    raw_dump = public_model.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
        exclude_none=False,
    )
    dumped = _strip_optional_non_null_fields(public_model, raw_dump)
    if type(dumped) is not dict:
        raise TypeError("public_model_wrong_type")
    from yoetz.protocol.schemas import validate_schema_instance

    validate_schema_instance(schema_name, "1.0.0", cast(JsonValue, dumped))
    return dict(dumped)


type _LeafClassification = Literal["public_structural"] | DataCategory
type _EventSelector = tuple[str, str] | Literal["<opaque>"]


@dataclass(frozen=True, slots=True)
class _ResultLeafRule:
    method: str
    status_view: str | None
    event_selector: _EventSelector | None
    segments: tuple[str, ...]
    classification: _LeafClassification


_RESULT_METHODS: Final[frozenset[str]] = frozenset(
    {"check", "publish_work", "receipt", "respond", "start", "status"}
)
_STATUS_VIEWS: Final[frozenset[str]] = frozenset(_STATUS_PAGE_BY_VIEW)
_KNOWN_PUBLISH_EVENT_SELECTORS: Final[frozenset[tuple[str, str]]] = frozenset(
    set(_PUBLISH_SUMMARY_CATEGORY) | set(_PUBLISH_FIXED_SUMMARY)
)


def _prefix_leaf_patterns(prefix: str, suffixes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{prefix}/{suffix}" for suffix in suffixes)


_COMMON_SUCCESS_LEAVES: Final = (
    "/ok",
    "/protocol_version",
    "/request_id",
    "/schema_version",
    "/session_id",
    "/task_id",
)
FRONTIER_LEAVES: Final = ("head_digest", "sequence")
_COVERAGE_LEAVES: Final = (
    "artifact_observation",
    "authorship_assurance",
    "check_types/*",
    "evidence_immutability",
    "known_gaps/*",
    "ledger_freshness",
    "publication_channels/*",
)
_PRIVACY_PROJECTION_LEAVES: Final = (
    "blocked_categories/*",
    "included_categories/*",
    "local_disclosure_receipt_id",
    "omitted_pointers/*",
    "policy_digest",
    "policy_id",
    "policy_version",
    "projection_commitment",
    "sink",
)
_OMITTED_CONTENT_LEAVES: Final = ("category", "omitted", "reason")
_BASIC_VERSION_LEAVES: Final = (
    "engine_version",
    "policy_packs/*",
    "projection_version",
    "protocol_version",
)
_SEMANTIC_PROVENANCE_LEAVES: Final = (
    "cost_fields/currency",
    "cost_fields/input_microunits",
    "cost_fields/output_microunits",
    "cost_fields/total_microunits",
    "dispatch_kind",
    "egress_authorization_id",
    "endpoint_profile_id",
    "endpoint_profile_version",
    "failure_class",
    "latency_ms",
    "local_disclosure_reservation_id",
    "model",
    "policy_digest",
    "privacy_policy_digest",
    "privacy_receipt_id",
    "prompt_digest",
    "provider",
    "provider_request_id",
    "reason",
    "request_commitment",
    "sampling_params/max_output_tokens",
    "sampling_params/seed",
    "sampling_params/temperature",
    "sampling_params/top_p",
    "schema_digest",
    "sdk_version",
    "semantic_attempt_id",
    "status",
    "token_usage/input_tokens",
    "token_usage/output_tokens",
    "token_usage/total_tokens",
)
_RECEIPT_VERSION_LEAVES: Final = (
    "bundle_schema_version",
    "catalog_schema_version",
    "engine_version",
    "object_format_version",
    "package_name",
    "package_version",
    "policy_versions/*/policy_id",
    "policy_versions/*/policy_version",
    "projection_version",
    "protocol_version",
    "resource_manifest_digest",
    "schema_versions/*/schema_id",
    "schema_versions/*/schema_version",
)
_RECEIPT_DOCUMENT_VERSION_LEAVES: Final = (
    "bundle_schema_version",
    "catalog_schema_version",
    "engine_version",
    "object_format_version",
    "package_name",
    "package_version",
    "policy_versions/*/policy_id",
    "policy_versions/*/policy_version",
    "projection_version",
    "protocol_version",
    "resource_manifest_digest",
    "schema_versions/*/schema_id",
    "schema_versions/*/schema_version",
)

_START_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + ("/outcome", "/writer_id")
    + _prefix_leaf_patterns("/frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/compact/coverage", _COVERAGE_LEAVES)
    + (
        "/compact/current_plan_event_id",
        "/compact/gaps/*",
        "/compact/ledger_freshness",
        "/compact/open_obligation_count",
        "/compact/unanswered_finding_count",
        "/compact/receipt_blocking_finding_count",
    )
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns("/versions", _BASIC_VERSION_LEAVES)
    + (
        "/next_request_template/evidential",
        "/next_request_template/operation",
        "/next_request_template/arguments/protocol_version",
        "/next_request_template/arguments/schema_version",
        "/next_request_template/arguments/request_id",
        "/next_request_template/arguments/actor/actor_id",
        "/next_request_template/arguments/actor/actor_type",
        "/next_request_template/arguments/client/kind",
        "/next_request_template/arguments/client/version",
        "/next_request_template/arguments/client/integration",
        "/next_request_template/arguments/session_id",
        "/next_request_template/arguments/writer_id",
        "/next_request_template/arguments/expected_frontier/sequence",
        "/next_request_template/arguments/expected_frontier/head_digest",
        "/next_request_template/arguments/event_drafts/*/event_id",
        "/next_request_template/arguments/event_drafts/*/schema/name",
        "/next_request_template/arguments/event_drafts/*/schema/version",
        "/next_request_template/arguments/event_drafts/*/occurred_at",
        "/next_request_template/arguments/event_drafts/*/causal_parents/*",
        "/next_request_template/arguments/event_drafts/*/payload/plan_version",
        "/next_request_template/arguments/event_drafts/*/payload/summary",
        "/next_request_template/arguments/event_drafts/*/payload/obligation_refs/*",
        "/next_request_template/arguments/event_drafts/*/payload/obligation_id",
        "/next_request_template/arguments/event_drafts/*/payload/description",
        "/next_request_template/arguments/event_drafts/*/payload/acceptance_criteria",
        "/next_request_template/arguments/event_drafts/*/payload/evidence_expectation",
        "/next_request_template/arguments/event_drafts/*/payload/status",
        "/next_request_template/arguments/event_drafts/*/artifact_refs/*",
        "/next_request_template/arguments/event_drafts/*/evidence_refs/*",
    )
)

_PUBLISH_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + (
        "/correlation_id",
        "/evidential",
        "/gaps/*",
        "/outcome",
        "/reason_code",
        "/response_completeness",
        "/warning_codes/*",
        "/writer_id",
    )
    + _prefix_leaf_patterns("/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/result_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns("/versions", _BASIC_VERSION_LEAVES)
    + _prefix_leaf_patterns(
        "/accepted_events/*",
        (
            "accepted_at",
            "entry_digest",
            "event_id",
            "ingestion_sequence",
            "predecessor_digest",
            "projection_status",
            "schema_name",
            "schema_version",
            "writer_sequence",
        ),
    )
    + _prefix_leaf_patterns(
        "/accepted_events/*/summary",
        _OMITTED_CONTENT_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/would_accept/*",
        (
            "artifact_refs/*",
            "causal_parents/*",
            "event_id",
            "evidence_refs/*",
            "schema_name",
            "schema_version",
        ),
    )
)

_CHECK_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + (
        "/semantic_provenance",
        "/semantic_reason",
        "/semantic_status",
        # Every continuation leaf is service-authored: an opaque proposal id, its expiry, the
        # fixed argv, the request id to replay, and fixed instruction prose. None of it carries
        # case content, so all of it is structural.
        "/state",
        "/continuation/expires_at",
        "/continuation/instruction",
        "/continuation/kind",
        "/continuation/pending_id",
        "/continuation/replay_request_id",
        "/continuation/command/*",
        "/suppressed_count",
        "/verdict",
        "/writer_id",
    )
    + _prefix_leaf_patterns("/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/result_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns("/versions", _BASIC_VERSION_LEAVES)
    + _prefix_leaf_patterns("/semantic_provenance", _SEMANTIC_PROVENANCE_LEAVES)
    + _prefix_leaf_patterns(
        "/policy_executions/*",
        ("outcome", "policy_id", "policy_version", "reason"),
    )
    + _prefix_leaf_patterns(
        "/findings/*",
        (
            "finding_id",
            "kind",
            "origin",
            "policy_id",
            "policy_version",
            "priority",
            "provenance",
            "subject_refs/*",
        ),
    )
    + _prefix_leaf_patterns("/findings/*/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/findings/*/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/findings/*/provenance", _SEMANTIC_PROVENANCE_LEAVES)
    + _prefix_leaf_patterns("/findings/*/summary", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/findings/*/detail", _OMITTED_CONTENT_LEAVES)
)

_RESPOND_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + ("/warning_codes/*", "/writer_id")
    + _prefix_leaf_patterns("/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/result_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns("/versions", _BASIC_VERSION_LEAVES)
    + _prefix_leaf_patterns(
        "/accepted_event",
        ("accepted_at", "entry_digest", "event_id", "ingestion_sequence", "writer_sequence"),
    )
    + _prefix_leaf_patterns(
        "/response",
        ("disposition", "finding_id", "response_event_id", "waiver_expiry", "waiver_scope"),
    )
    + _prefix_leaf_patterns("/response/finding_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/response/evidence/*", ("reference_id",))
    + _prefix_leaf_patterns("/response/reason", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns(
        "/response/evidence/*/description",
        _OMITTED_CONTENT_LEAVES,
    )
)

_STATUS_COMMON_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + (
        "/gaps/*",
        "/projection_lag",
        "/projection_version",
        "/rebuild_state",
        "/view",
        "/writer_id",
    )
    + _prefix_leaf_patterns("/requested_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/head_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/result_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns(
        "/import_status",
        (
            "pending_count",
            "phase",
            "report_evidence_id",
            "source_identity_digest",
            "terminal_count",
        ),
    )
    + _prefix_leaf_patterns(
        "/closure_readiness",
        (
            "blocking_conditions/*",
            "declared_obligation_count",
            "no_obligations_reason",
            "open_obligation_count",
            "unanswered_finding_count",
            "receipt_blocking_finding_count",
        ),
    )
)

_STATUS_ADVICE_STRUCTURAL_POINTERS: Final = (
    (
        "/page/next_cursor",
        "/page/projection_format",
    )
    + _prefix_leaf_patterns(
        "/page/items/*",
        (
            "evidence_commitments/*",
            "finding_id",
            "freshness_frontier",
            "priority",
            "recommended_next_action",
            "rule_code",
            "semantic_state",
            "verification_state",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/coverage", _COVERAGE_LEAVES)
)

_STATUS_ASSIGNMENT_STRUCTURAL_POINTERS: Final = ("/page/next_cursor",) + _prefix_leaf_patterns(
    "/page/items/*",
    ("actor_id", "assignment_event_id", "obligation_ids/*", "resolved", "scope_refs/*"),
)

_STATUS_CANDIDATE_FINDINGS_STRUCTURAL_POINTERS: Final = (
    ("/page/next_cursor",)
    + _prefix_leaf_patterns(
        "/page/items/*",
        ("kind", "origin", "policy_id", "policy_version", "priority", "subject_refs/*"),
    )
    + _prefix_leaf_patterns(
        "/page/items/*/basis",
        (
            "coverage_gaps/*",
            "evidence_refs/*",
            "frozen_source_availability",
            "observed_fact_codes/*",
            "observed_refs/*",
            "required_missing_fact_codes/*",
            "rule_id",
            "subject_state_relation",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/detail", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/summary", _OMITTED_CONTENT_LEAVES)
)

_STATUS_COMPACT_STRUCTURAL_POINTERS: Final = (
    ("/page/next_cursor",)
    + _prefix_leaf_patterns(
        "/page/items/*",
        (
            "current_plan_event_id",
            "declared_obligation_count",
            "freshness",
            "gaps/*",
            "no_obligations_reason",
            "open_obligation_count",
            "receipt_blocking_finding_count",
            "session_id",
            "task_id",
            "unanswered_finding_count",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/task_title", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/open_obligations/*", ("obligation_id",))
    + _prefix_leaf_patterns(
        "/page/items/*/open_obligations/*/acceptance_criteria",
        _OMITTED_CONTENT_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/page/items/*/open_obligations/*/description",
        _OMITTED_CONTENT_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/page/items/*/open_obligations/*/evidence_expectation",
        _OMITTED_CONTENT_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/page/items/*/unanswered_findings/*",
        ("finding_id", "kind", "priority"),
    )
    + _prefix_leaf_patterns(
        "/page/items/*/unanswered_findings/*/detail",
        _OMITTED_CONTENT_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/page/items/*/unanswered_findings/*/summary",
        _OMITTED_CONTENT_LEAVES,
    )
)

_STATUS_EVIDENCE_STRUCTURAL_POINTERS: Final = (
    ("/page/next_cursor",)
    + _prefix_leaf_patterns(
        "/page/items/*",
        (
            "available",
            "captured_object_id",
            "content_digest",
            "evidence_id",
            "freshness",
            "strength",
            "subject_state",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/description", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/reference", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns(
        "/page/items/*/subject_state",
        ("diff_digest", "tree_digest"),
    )
)

_STATUS_FINDINGS_STRUCTURAL_POINTERS: Final = (
    ("/page/next_cursor",)
    + _prefix_leaf_patterns(
        "/page/items/*",
        (
            "disposition",
            "finding_id",
            "kind",
            "origin",
            "policy_id",
            "policy_version",
            "priority",
            "provenance",
            "resolved",
            "response_event_id",
            "subject_refs/*",
            "waiver_expiry",
            "waiver_scope",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/provenance", _SEMANTIC_PROVENANCE_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/detail", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/reason", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/summary", _OMITTED_CONTENT_LEAVES)
)

_STATUS_HISTORY_STRUCTURAL_POINTERS: Final = ("/page/next_cursor",) + _prefix_leaf_patterns(
    "/page/items/*",
    (
        "accepted_at",
        "actor_id",
        "event_id",
        "ingestion_sequence",
        "occurred_at",
        "occurred_at_consistency",
        "projection_status",
        "publication_channel",
        "schema_name",
        "schema_version",
        "summary_code",
    ),
)

_STATUS_OBLIGATIONS_STRUCTURAL_POINTERS: Final = (
    ("/page/next_cursor",)
    + _prefix_leaf_patterns(
        "/page/items/*",
        (
            "assigned_actor_ids/*",
            "evidence_refs/*",
            "obligation_id",
            "revision_event_id",
            "source_refs/*",
            "status",
        ),
    )
    + _prefix_leaf_patterns("/page/items/*/acceptance_criteria", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/description", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns("/page/items/*/evidence_expectation", _OMITTED_CONTENT_LEAVES)
)

_STATUS_OPERATION_STRUCTURAL_POINTERS: Final = (
    (
        "/page/accepted_events/*/entry_digest",
        "/page/accepted_events/*/event_id",
        "/page/accepted_events/*/ingestion_sequence",
        "/page/accepted_events/*/projection_status",
        "/page/accepted_events/*/writer_sequence",
        # Nullable continuation object: a leaf when null, expands when a check is suspended.
        # Every field is service-authored, so all of it is structural.
        "/page/continuation",
        "/page/continuation/command/*",
        "/page/continuation/expires_at",
        "/page/continuation/instruction",
        "/page/continuation/kind",
        "/page/continuation/pending_id",
        "/page/continuation/replay_request_id",
        "/page/found",
        "/page/next_cursor",
        "/page/operation_kind",
        "/page/operation_request_id",
        "/page/outcome",
        # Nullable frontier objects are leaves when null and expand when present.
        "/page/result_frontier",
        "/page/state",
        "/page/subject_frontier",
    )
    + _prefix_leaf_patterns("/page/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/page/result_frontier", FRONTIER_LEAVES)
)

_STATUS_VERSIONS_STRUCTURAL_POINTERS: Final = ("/page/next_cursor",) + _prefix_leaf_patterns(
    "/page/items/*",
    (
        "apsw_version",
        "engine_version",
        "object_format",
        "policy_packs/*",
        "projection_version",
        "protocol_version",
        "provider_profiles/*",
        "python_version",
        "route_profile",
        "sqlite_source_id",
        "sqlite_version",
        "storage_schema",
    ),
)

_RECEIPT_STRUCTURAL_POINTERS: Final = (
    _COMMON_SUCCESS_LEAVES
    + (
        "/conclusion",
        "/document",
        "/format",
        "/include",
        "/receipt_digest",
        "/receipt_id",
        "/receipt_object_id",
        "/redaction_profile",
        "/suppressed_finding_count",
    )
    + _prefix_leaf_patterns("/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/result_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/privacy_projection", _PRIVACY_PROJECTION_LEAVES)
    + _prefix_leaf_patterns("/versions", _RECEIPT_VERSION_LEAVES)
    + _prefix_leaf_patterns("/human_text", _OMITTED_CONTENT_LEAVES)
    + _prefix_leaf_patterns(
        "/document",
        (
            "claim_refs/*",
            "conclusion",
            "evidence_refs/*",
            "generated_at",
            "receipt_id",
            "schema_version",
            "session_id",
            "suppressed_finding_count",
            "task_id",
        ),
    )
    + _prefix_leaf_patterns("/document/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/document/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/document/versions", _RECEIPT_DOCUMENT_VERSION_LEAVES)
    + _prefix_leaf_patterns(
        "/document/findings/*",
        (
            "finding_id",
            "kind",
            "origin",
            "policy_id",
            "policy_version",
            "priority",
            "subject_refs/*",
        ),
    )
    + _prefix_leaf_patterns("/document/findings/*/coverage", _COVERAGE_LEAVES)
    + _prefix_leaf_patterns("/document/findings/*/subject_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns(
        "/document/findings/*/provenance",
        _SEMANTIC_PROVENANCE_LEAVES,
    )
    + _prefix_leaf_patterns(
        "/document/obligations/*",
        ("obligation_id", "source_refs/*", "status"),
    )
    + _prefix_leaf_patterns(
        "/document/responses/*",
        ("disposition", "evidence_refs/*", "finding_id", "waiver_expiry", "waiver_scope"),
    )
    + _prefix_leaf_patterns("/document/responses/*/finding_frontier", FRONTIER_LEAVES)
    + _prefix_leaf_patterns("/document/gaps/*", ("code", "subject_refs/*"))
    + _prefix_leaf_patterns("/document/redactions/*", ("category", "count", "reason"))
    + _prefix_leaf_patterns("/document/sections/*", ("key",))
)

_CHECK_CONTENT_RULES: Final[tuple[tuple[str, DataCategory], ...]] = (
    ("/findings/*/detail", DataCategory.FINDING_SUMMARY),
    ("/findings/*/summary", DataCategory.FINDING_SUMMARY),
)
_RESPOND_CONTENT_RULES: Final[tuple[tuple[str, DataCategory], ...]] = (
    ("/response/evidence/*/description", DataCategory.EVIDENCE_EXCERPT),
    ("/response/reason", DataCategory.FINDING_SUMMARY),
)
_STATUS_CONTENT_RULES: Final[tuple[tuple[str, str, DataCategory], ...]] = (
    ("candidate_findings", "/page/items/*/detail", DataCategory.FINDING_SUMMARY),
    ("candidate_findings", "/page/items/*/summary", DataCategory.FINDING_SUMMARY),
    (
        "compact",
        "/page/items/*/open_obligations/*/acceptance_criteria",
        DataCategory.OBLIGATION_TEXT,
    ),
    (
        "compact",
        "/page/items/*/open_obligations/*/description",
        DataCategory.OBLIGATION_TEXT,
    ),
    (
        "compact",
        "/page/items/*/open_obligations/*/evidence_expectation",
        DataCategory.OBLIGATION_TEXT,
    ),
    ("compact", "/page/items/*/task_title", DataCategory.TASK_DESCRIPTION),
    (
        "compact",
        "/page/items/*/unanswered_findings/*/detail",
        DataCategory.FINDING_SUMMARY,
    ),
    (
        "compact",
        "/page/items/*/unanswered_findings/*/summary",
        DataCategory.FINDING_SUMMARY,
    ),
    ("evidence", "/page/items/*/description", DataCategory.EVIDENCE_EXCERPT),
    ("evidence", "/page/items/*/reference", DataCategory.EVIDENCE_EXCERPT),
    ("findings", "/page/items/*/detail", DataCategory.FINDING_SUMMARY),
    ("findings", "/page/items/*/reason", DataCategory.FINDING_SUMMARY),
    ("findings", "/page/items/*/summary", DataCategory.FINDING_SUMMARY),
    ("obligations", "/page/items/*/acceptance_criteria", DataCategory.OBLIGATION_TEXT),
    ("obligations", "/page/items/*/description", DataCategory.OBLIGATION_TEXT),
    ("obligations", "/page/items/*/evidence_expectation", DataCategory.OBLIGATION_TEXT),
)
_RECEIPT_CONTENT_RULES: Final[tuple[tuple[str, DataCategory], ...]] = (
    ("/document/gaps/*/detail", DataCategory.FINDING_SUMMARY),
    ("/document/findings/*/detail", DataCategory.FINDING_SUMMARY),
    ("/document/findings/*/summary", DataCategory.FINDING_SUMMARY),
    ("/document/obligations/*/summary", DataCategory.OBLIGATION_TEXT),
    ("/document/responses/*/reason", DataCategory.FINDING_SUMMARY),
    ("/document/sections/*/body", DataCategory.FINDING_SUMMARY),
    ("/document/sections/*/coverage_note", DataCategory.FINDING_SUMMARY),
    ("/document/sections/*/items/*", DataCategory.FINDING_SUMMARY),
    ("/document/sections/*/title", DataCategory.FINDING_SUMMARY),
    ("/human_text", DataCategory.FINDING_SUMMARY),
)


def _leaf_rule(
    method: str,
    pointer_pattern: str,
    classification: _LeafClassification,
    *,
    status_view: str | None = None,
    event_selector: _EventSelector | None = None,
) -> _ResultLeafRule:
    return _ResultLeafRule(
        method=method,
        status_view=status_view,
        event_selector=event_selector,
        segments=tuple(pointer_pattern.removeprefix("/").split("/")) if pointer_pattern else (),
        classification=classification,
    )


def _build_result_leaf_rules() -> tuple[_ResultLeafRule, ...]:
    rules: list[_ResultLeafRule] = []

    def add_structural(
        method: str,
        pointers: tuple[str, ...],
        *,
        status_view: str | None = None,
    ) -> None:
        rules.extend(
            _leaf_rule(method, pointer, "public_structural", status_view=status_view)
            for pointer in pointers
        )

    add_structural("start", _START_STRUCTURAL_POINTERS)
    add_structural("publish_work", _PUBLISH_STRUCTURAL_POINTERS)
    add_structural("check", _CHECK_STRUCTURAL_POINTERS)
    add_structural("respond", _RESPOND_STRUCTURAL_POINTERS)
    add_structural("status", _STATUS_COMMON_STRUCTURAL_POINTERS)
    add_structural("status", _STATUS_ADVICE_STRUCTURAL_POINTERS, status_view="advice")
    add_structural(
        "status",
        _STATUS_ASSIGNMENT_STRUCTURAL_POINTERS,
        status_view="assignment",
    )
    add_structural(
        "status",
        _STATUS_CANDIDATE_FINDINGS_STRUCTURAL_POINTERS,
        status_view="candidate_findings",
    )
    add_structural("status", _STATUS_COMPACT_STRUCTURAL_POINTERS, status_view="compact")
    add_structural("status", _STATUS_EVIDENCE_STRUCTURAL_POINTERS, status_view="evidence")
    add_structural("status", _STATUS_FINDINGS_STRUCTURAL_POINTERS, status_view="findings")
    add_structural("status", _STATUS_HISTORY_STRUCTURAL_POINTERS, status_view="history")
    add_structural(
        "status",
        _STATUS_OBLIGATIONS_STRUCTURAL_POINTERS,
        status_view="obligations",
    )
    add_structural(
        "status",
        _STATUS_OPERATION_STRUCTURAL_POINTERS,
        status_view="operation",
    )
    add_structural("status", _STATUS_VERSIONS_STRUCTURAL_POINTERS, status_view="versions")
    add_structural("receipt", _RECEIPT_STRUCTURAL_POINTERS)

    summary_pattern = "/accepted_events/*/summary"
    for selector, category in _PUBLISH_SUMMARY_CATEGORY.items():
        rules.append(
            _leaf_rule(
                "publish_work",
                summary_pattern,
                category,
                event_selector=selector,
            )
        )
    for selector in _PUBLISH_FIXED_SUMMARY:
        rules.append(
            _leaf_rule(
                "publish_work",
                summary_pattern,
                "public_structural",
                event_selector=selector,
            )
        )
    rules.append(
        _leaf_rule(
            "publish_work",
            summary_pattern,
            "public_structural",
            event_selector="<opaque>",
        )
    )

    for pointer, category in _CHECK_CONTENT_RULES:
        rules.append(_leaf_rule("check", pointer, category))
    for pointer, category in _RESPOND_CONTENT_RULES:
        rules.append(_leaf_rule("respond", pointer, category))
    for view, pointer, category in _STATUS_CONTENT_RULES:
        rules.append(_leaf_rule("status", pointer, category, status_view=view))
    for pointer, category in _RECEIPT_CONTENT_RULES:
        rules.append(_leaf_rule("receipt", pointer, category))

    def sort_key(rule: _ResultLeafRule) -> tuple[bytes, bytes, bytes, tuple[bytes, ...]]:
        if isinstance(rule.event_selector, tuple):
            selector = f"{rule.event_selector[0]}@{rule.event_selector[1]}"
        else:
            selector = rule.event_selector or ""
        return (
            rule.method.encode("utf-8"),
            (rule.status_view or "").encode("utf-8"),
            selector.encode("utf-8"),
            tuple(segment.encode("utf-8") for segment in rule.segments),
        )

    result = tuple(sorted(rules, key=sort_key))
    keys: set[tuple[str, str | None, _EventSelector | None, tuple[str, ...]]] = set()
    for rule in result:
        key = (rule.method, rule.status_view, rule.event_selector, rule.segments)
        if key in keys:
            raise RuntimeError("duplicate_result_leaf_rule")
        keys.add(key)
        if rule.method not in _RESULT_METHODS:
            raise RuntimeError("invalid_result_leaf_method")
        if not rule.segments:
            raise RuntimeError("invalid_result_leaf_pattern")
        if rule.status_view is not None and (
            rule.method != "status" or rule.status_view not in _STATUS_VIEWS
        ):
            raise RuntimeError("invalid_result_leaf_view")
        if rule.event_selector is not None and (
            rule.method != "publish_work" or rule.segments != ("accepted_events", "*", "summary")
        ):
            raise RuntimeError("invalid_result_leaf_event_selector")
        if rule.status_view is not None and rule.event_selector is not None:
            raise RuntimeError("invalid_result_leaf_context")
        if any(segment == "**" or ("*" in segment and segment != "*") for segment in rule.segments):
            raise RuntimeError("invalid_result_leaf_pattern")
        if (
            rule.classification != "public_structural"
            and type(rule.classification) is not DataCategory
        ):
            raise RuntimeError("invalid_result_leaf_classification")
    if len(result) != 780:
        raise RuntimeError("incomplete_result_leaf_registry")
    return result


_RESULT_LEAF_RULES: Final[tuple[_ResultLeafRule, ...]] = _build_result_leaf_rules()


def _decode_pointer(pointer: object) -> tuple[str, ...]:
    if type(pointer) is not str or unicodedata.normalize("NFC", pointer) != pointer:
        raise ProtocolValueError("invalid_json_pointer")
    try:
        pointer_size = len(pointer.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ProtocolValueError("invalid_json_pointer") from exc
    if not pointer.startswith("/") or pointer_size > MAX_PROJECTION_POINTER_BYTES:
        raise ProtocolValueError("invalid_json_pointer")
    raw_segments = pointer[1:].split("/")
    decoded: list[str] = []
    for raw in raw_segments:
        index = 0
        while index < len(raw):
            if raw[index] == "~":
                if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                    raise ProtocolValueError("invalid_json_pointer")
                index += 2
            else:
                index += 1
        segment = raw.replace("~1", "/").replace("~0", "~")
        canonical = segment.replace("~", "~0").replace("/", "~1")
        if canonical != raw or unicodedata.normalize("NFC", segment) != segment:
            raise ProtocolValueError("invalid_json_pointer")
        decoded.append(segment)
    return tuple(decoded)


def _traverse_result_leaf(
    result: Mapping[str, JsonValue], segments: tuple[str, ...]
) -> tuple[JsonValue, tuple[bool, ...]]:
    current: JsonValue = result
    array_segments: list[bool] = []
    for segment in segments:
        if isinstance(current, Mapping):
            if segment not in current:
                raise ProtocolValueError("invalid_json_pointer")
            current = current[segment]
            array_segments.append(False)
        elif type(current) is list or type(current) is tuple:
            if not segment.isascii() or not segment.isdecimal():
                raise ProtocolValueError("invalid_json_pointer")
            if segment != "0" and segment.startswith("0"):
                raise ProtocolValueError("invalid_json_pointer")
            index = int(segment)
            sequence = cast(list[JsonValue] | tuple[JsonValue, ...], current)
            if index >= len(sequence):
                raise ProtocolValueError("invalid_json_pointer")
            current = sequence[index]
            array_segments.append(True)
        else:
            raise ProtocolValueError("invalid_json_pointer")
    if type(current) not in {type(None), bool, int, str}:
        raise ProtocolValueError("invalid_json_pointer")
    return current, tuple(array_segments)


def _rule_matches(
    rule: _ResultLeafRule,
    segments: tuple[str, ...],
    array_segments: tuple[bool, ...],
) -> bool:
    if not rule.segments or len(rule.segments) != len(segments):
        return False
    for expected, actual, is_array in zip(rule.segments, segments, array_segments, strict=True):
        if expected == "*":
            if not is_array:
                return False
        elif expected != actual:
            return False
    return True


def _publish_event_selector(
    result: Mapping[str, JsonValue], segments: tuple[str, ...]
) -> _EventSelector | None:
    if len(segments) != 3 or segments[0] != "accepted_events" or segments[2] != "summary":
        return None
    try:
        index = int(segments[1])
        raw_events = result["accepted_events"]
        if type(raw_events) is not list and type(raw_events) is not tuple:
            raise ProtocolValueError("invalid_json_pointer")
        event = cast(list[JsonValue] | tuple[JsonValue, ...], raw_events)[index]
        if not isinstance(event, Mapping):
            raise ProtocolValueError("invalid_json_pointer")
        event_map = cast(Mapping[str, JsonValue], event)
        schema_name = event_map.get("schema_name")
        schema_version = event_map.get("schema_version")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_json_pointer") from exc
    if type(schema_name) is not str or type(schema_version) is not str:
        raise ProtocolValueError("invalid_json_pointer")
    selector = (schema_name, schema_version)
    if selector in _KNOWN_PUBLISH_EVENT_SELECTORS:
        return selector
    if event_map.get("summary") != "opaque_unknown":
        raise ProtocolValueError("invalid_json_pointer")
    return "<opaque>"


def classify_result_leaf(
    method: str,
    validated_result: Mapping[str, JsonValue],
    pointer: str,
) -> Literal["public_structural"] | DataCategory:
    """Classify one leaf of an exact locally validated public success result."""

    if type(method) is not str or method not in _RESULT_METHODS:
        raise ProtocolValueError("invalid_json_pointer")
    if not isinstance(validated_result, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ProtocolValueError("invalid_json_pointer")
    result = validated_result
    if result.get("ok") is not True:
        raise ProtocolValueError("invalid_json_pointer")

    segments = _decode_pointer(pointer)
    leaf_value, array_segments = _traverse_result_leaf(result, segments)
    if method == "receipt" and segments == ("human_text",):
        receipt_format = result.get("format")
        if leaf_value is None:
            if receipt_format != "json":
                raise ProtocolValueError("invalid_json_pointer")
            return "public_structural"
        if type(leaf_value) is not str or receipt_format not in {"markdown", "text"}:
            raise ProtocolValueError("invalid_json_pointer")
    status_view: str | None = None
    if method == "status":
        candidate = result.get("view")
        if type(candidate) is not str or candidate not in _STATUS_VIEWS:
            raise ProtocolValueError("invalid_json_pointer")
        status_view = candidate
    event_selector = _publish_event_selector(result, segments) if method == "publish_work" else None

    contextual = tuple(
        rule
        for rule in _RESULT_LEAF_RULES
        if rule.method == method
        and (rule.status_view is None or rule.status_view == status_view)
        and (rule.event_selector is None or rule.event_selector == event_selector)
    )
    exact = tuple(
        rule
        for rule in contextual
        if "*" not in rule.segments and _rule_matches(rule, segments, array_segments)
    )
    if len(exact) > 1:
        raise ProtocolValueError("invalid_json_pointer")
    if exact:
        return exact[0].classification
    wildcard = tuple(
        rule
        for rule in contextual
        if "*" in rule.segments and _rule_matches(rule, segments, array_segments)
    )
    if len(wildcard) != 1:
        raise ProtocolValueError("invalid_json_pointer")
    return wildcard[0].classification
