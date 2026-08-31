"""Typed event payloads, draft values, and accepted ledger records."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Final, Literal, cast

from pydantic import Field, ValidationError

from yoetz.domain.findings import (
    CheckVerdict,
    Finding,
    ResponseDisposition,
    SemanticProvenance,
    WaiverScope,
    finding_from_json,
    finding_to_json,
    semantic_provenance_from_json,
    semantic_provenance_to_json,
)
from yoetz.domain.receipts import ReceiptConclusion
from yoetz.domain.values import (
    ActionId,
    Actor,
    ActorId,
    ActorType,
    ClaimId,
    EventId,
    EvidenceId,
    FindingId,
    Frontier,
    JsonObject,
    JsonValue,
    ObjectId,
    ObligationId,
    ReceiptId,
    RequestId,
    ResultId,
    SessionId,
    SubjectStateRef,
    TaskId,
    Timestamp,
    WriterId,
    action_id,
    actor_id,
    claim_id,
    event_id,
    evidence_id,
    finding_id,
    freeze_json,
    frontier_from_json,
    object_id,
    obligation_id,
    receipt_id,
    request_id,
    result_id,
    session_id,
    task_id,
    timestamp_from_string,
    validate_commitment,
    validate_sha256_digest,
    writer_id,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.canonical import entry_digest as compute_entry_digest
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    Coverage,
    EvidenceImmutability,
    PublicationChannel,
    coverage_from_json,
    coverage_to_json,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    CheckPolicyExecutionModel,
    CheckScopeModel,
    ClientKind,
    IntegrationKind,
    ReceiptRedactionProfile,
    SemanticReason,
    SemanticStatus,
    validate_semantic_provenance_binding,
)

__all__ = [
    "EVENT_FAMILIES",
    "MAX_ALTERNATIVES",
    "MAX_CAUSAL_PARENTS",
    "MAX_LABEL_BYTES",
    "MAX_REASON_BYTES",
    "MAX_REF_LIST",
    "MAX_REQUESTED_ITEMS",
    "MAX_TEXT_BYTES",
    "OBSERVATION_COORDINATOR_ACTOR_ID",
    "PAYLOAD_TYPES",
    "SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSIONS",
    "EVIDENCE_TYPED_SCHEMA_VERSION",
    "AcceptedEvent",
    "ActionKind",
    "ActionRecordedPayload",
    "AssignmentRecordedPayload",
    "CheckMode",
    "CheckRecordedPayload",
    "ClaimKind",
    "ClaimRecordedPayload",
    "ClientKind",
    "DecisionRecordedPayload",
    "EventDraft",
    "EventPayload",
    "EventSchema",
    "OCCURRED_AT_DRAFT_DESCRIPTION",
    "EvidenceKind",
    "EvidenceContentAvailability",
    "EvidenceDigestBinding",
    "EvidenceDigestProvenance",
    "EvidenceDigestSubject",
    "EvidenceRecordedPayload",
    "FindingRecordedPayload",
    "IntegrationKind",
    "LedgerChain",
    "LedgerRecord",
    "is_observation_authored",
    "is_observation_authorship",
    "ObligationChange",
    "ObligationChangeKind",
    "NoObligationsReason",
    "NoObligationsReasonMismatch",
    "ObligationPublishedPayload",
    "ObligationResolutionMismatch",
    "ObligationStatus",
    "obligation_meaning_field_diffs",
    "public_error_for_obligation_resolution_mismatch",
    "public_error_for_no_obligations_reason_mismatch",
    "PayloadRef",
    "PlanPublishedPayload",
    "PlanRevisedPayload",
    "PolicyVersion",
    "ProjectionLocator",
    "ReceiptRecordedPayload",
    "RedactionMethod",
    "RedactionReasonCategory",
    "RedactionRecordedPayload",
    "RedactionState",
    "RequestedItem",
    "RequestedItemKind",
    "ResponseRecordedPayload",
    "ResultOutcome",
    "ResultRecordedPayload",
    "RuntimeProfile",
    "SessionOpenedPayload",
    "SessionResumedPayload",
    "UnknownEvent",
    "WritePolicy",
    "WriterChain",
    "accepted_record_digest_preimage",
    "accepted_record_to_json",
    "decode_payload",
    "encode_payload",
    "media_type_for",
    "normalize_payload_json",
]

OBSERVATION_COORDINATOR_ACTOR_ID: Final = "yoetz:observation-coordinator"

SCHEMA_VERSION: Final = "1.0.0"
EVIDENCE_SCHEMA_VERSION: Final = "1.2.0"
EVIDENCE_TYPED_SCHEMA_VERSION: Final = "1.1.0"
EVIDENCE_SCHEMA_VERSIONS: Final = (
    SCHEMA_VERSION,
    EVIDENCE_TYPED_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
)
MAX_TEXT_BYTES: Final = 8_192
MAX_REASON_BYTES: Final = 4_096
MAX_LABEL_BYTES: Final = 256
MAX_REF_LIST: Final = 64
MAX_CAUSAL_PARENTS: Final = 32
MAX_REQUESTED_ITEMS: Final = 64
MAX_ALTERNATIVES: Final = 16

_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_MAX_SQLITE_INTEGER: Final = 9_223_372_036_854_775_807
_MAX_OBJECT_PLAINTEXT_BYTES: Final = 4_194_304
_MAX_FINDINGS_LIMIT: Final = 10
_SCHEMA_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_SEMVER_RE: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    re.ASCII,
)
_MEDIA_TYPE_RE: Final = re.compile(
    r"^application/vnd\.yoetz\.[a-z][a-z0-9_]{0,63}\+json$",
    re.ASCII,
)

EVENT_FAMILIES: Final = (
    "session_opened",
    "session_resumed",
    "plan_published",
    "obligation_published",
    "assignment_recorded",
    "decision_recorded",
    "action_recorded",
    "result_recorded",
    "evidence_recorded",
    "claim_recorded",
    "plan_revised",
    "finding_recorded",
    "response_recorded",
    "redaction_recorded",
    "check_recorded",
    "receipt_recorded",
)


class RuntimeProfile(str, Enum):  # noqa: UP042 - exact wire enum base
    STRICT_LOCAL = "strict-local"
    LOCAL_OPENAI = "local-openai"
    TEST_FAKE = "test-fake"
    RELEASE_PROBE = "release-probe"


class RequestedItemKind(str, Enum):  # noqa: UP042 - exact wire enum base
    URL = "url"
    FILE = "file"
    COMMAND = "command"
    CHANGE = "change"
    SOURCE = "source"


class ObligationStatus(str, Enum):  # noqa: UP042 - exact wire enum base
    OPEN = "open"
    RESOLVED = "resolved"


class NoObligationsReason(str, Enum):  # noqa: UP042 - exact wire enum base
    NO_MATERIAL_CHANGE = "no_material_change"
    SINGLE_ATOMIC_CHANGE = "single_atomic_change"
    EXPLORATORY_SCOPE_UNKNOWN = "exploratory_scope_unknown"


class WritePolicy(str, Enum):  # noqa: UP042 - exact wire enum base
    READ_ONLY = "read_only"
    WRITES_ALLOWED = "writes_allowed"


class ActionKind(str, Enum):  # noqa: UP042 - exact wire enum base
    COMMAND = "command"
    EDIT = "edit"
    RESEARCH = "research"
    REVIEW = "review"
    OTHER = "other"


class ResultOutcome(str, Enum):  # noqa: UP042 - exact wire enum base
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):  # noqa: UP042 - exact wire enum base
    ARTIFACT = "artifact"
    COMMAND_OUTPUT = "command_output"
    TEST_RESULT = "test_result"
    RESEARCH_SOURCE = "research_source"
    IMPORT_REPORT = "import_report"
    OTHER = "other"


class EvidenceDigestSubject(str, Enum):  # noqa: UP042 - exact wire enum base
    APPROVED_CHECK_RECEIPT = "approved_check_receipt"
    ARTIFACT_BYTES = "artifact_bytes"
    BOUNDED_EXCERPT = "bounded_excerpt"
    COMMAND_STDOUT = "command_stdout"
    IMPORT_REPORT = "import_report"
    SOURCE_DIFF = "source_diff"
    STATIC_ANALYSIS_REPORT = "static_analysis_report"
    TEST_REPORT = "test_report"
    TEST_STDOUT = "test_stdout"


class EvidenceContentAvailability(str, Enum):  # noqa: UP042 - exact wire enum base
    CAPTURED = "captured"
    DIGEST_ONLY = "digest_only"
    WITHHELD = "withheld"


class EvidenceDigestProvenance(str, Enum):  # noqa: UP042 - exact wire enum base
    APPROVED_CHECK = "approved_check"
    CALLER_ASSERTED = "caller_asserted"
    IMPORT_OBSERVED = "import_observed"
    OBSERVATION_CAPTURED = "observation_captured"


class ClaimKind(str, Enum):  # noqa: UP042 - exact wire enum base
    COMPLETION = "completion"
    MATERIAL = "material"


class ObligationChangeKind(str, Enum):  # noqa: UP042 - exact wire enum base
    SUPERSEDED = "superseded"
    WAIVED = "waived"
    CARRIED = "carried"


class RedactionMethod(str, Enum):  # noqa: UP042 - exact wire enum base
    LOGICAL_REDACTION = "logical_redaction"
    OBJECT_DELETION = "object_deletion"


class RedactionReasonCategory(str, Enum):  # noqa: UP042 - exact wire enum base
    SECRET = "secret"
    PRIVACY = "privacy"
    RETENTION = "retention"
    LEGAL = "legal"
    OTHER = "other"


class CheckMode(str, Enum):  # noqa: UP042 - exact wire enum base
    DETERMINISTIC_ONLY = "deterministic_only"
    SEMANTIC_IF_CONFIGURED = "semantic_if_configured"
    SEMANTIC_REQUIRED = "semantic_required"


class RedactionState(str, Enum):  # noqa: UP042 - exact wire enum base
    PRESENT = "present"
    LOGICALLY_REDACTED = "logically_redacted"
    KEY_UNAVAILABLE = "key_unavailable"
    ERASED_CLAIMED = "erased_claimed"


def _bounded_text(value: object, maximum: int, *, minimum: int = 0) -> str:
    if type(value) is not str or not minimum <= len(value) <= maximum:
        raise ProtocolValueError("event_text_out_of_bounds")
    freeze_json(value)
    return str.__getitem__(value, slice(None))


def _exact_enum[T: Enum](value: object, enum_type: type[T]) -> T:
    if type(value) is not enum_type:
        raise ProtocolValueError("invalid_event_enum")
    return cast(T, value)


def _enum_from_json[T: Enum](value: object, enum_type: type[T]) -> T:
    if type(value) is not str:
        raise ProtocolValueError("invalid_event_enum")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_event_enum") from exc


def _bounded_integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolValueError("event_integer_out_of_range")
    return value


def _tuple(value: object, minimum: int, maximum: int) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise ProtocolValueError("invalid_event_value_type")
    items = cast(tuple[object, ...], value)
    if not minimum <= len(items) <= maximum:
        raise ProtocolValueError("invalid_event_value_type")
    return items


def _validate_ascii_sorted_unique(values: tuple[str, ...], *, field: str | None = None) -> None:
    """Enforce the canonical set form, naming the owning field so a rejection is actionable.

    ``field`` is a frozen payload field name from this module, never caller data. A non-ASCII
    member is its own reason: reporting it as unsorted sent agents hunting for an ordering bug
    that was not there.
    """

    previous: bytes | None = None
    for value in values:
        try:
            encoded = value.encode("ascii", errors="strict")
        except UnicodeEncodeError as exc:
            raise ProtocolValueError("set_member_not_ascii", field=field) from exc
        if previous is not None:
            if encoded == previous:
                raise ProtocolValueError("duplicate_set_member", field=field)
            if encoded < previous:
                raise ProtocolValueError("unsorted_set_field", field=field)
        previous = encoded


def _id_tuple[T](
    value: object,
    constructor: Callable[[object], T],
    *,
    minimum: int = 0,
    maximum: int = MAX_REF_LIST,
    field: str | None = None,
) -> tuple[T, ...]:
    raw = _tuple(value, minimum, maximum)
    validated = tuple(constructor(item) for item in raw)
    _validate_ascii_sorted_unique(cast(tuple[str, ...], validated), field=field)
    return validated


def _text_tuple(
    value: object,
    maximum_items: int,
    maximum_text: int,
    *,
    unique: bool = False,
) -> tuple[str, ...]:
    raw = _tuple(value, 0, maximum_items)
    result = tuple(_bounded_text(item, maximum_text) for item in raw)
    if unique and len(result) != len(set(result)):
        raise ProtocolValueError("duplicate_set_member")
    return result


def _evidence_result_ref(value: object) -> EvidenceId | ResultId:
    if type(value) is not str:
        raise ProtocolValueError("invalid_event_value_type")
    if value.startswith("evd_"):
        return evidence_id(value)
    if value.startswith("res_"):
        return result_id(value)
    raise ProtocolValueError("invalid_event_value_type")


def _evidence_result_tuple(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = MAX_REF_LIST,
    field: str | None = None,
) -> tuple[EvidenceId | ResultId, ...]:
    raw = _tuple(value, minimum, maximum)
    result = tuple(_evidence_result_ref(item) for item in raw)
    _validate_ascii_sorted_unique(cast(tuple[str, ...], result), field=field)
    return result


def _claim_supporting_ref(value: object) -> EvidenceId | ResultId | ObligationId:
    if type(value) is not str:
        raise ProtocolValueError("invalid_event_value_type")
    if value.startswith("evd_"):
        return evidence_id(value)
    if value.startswith("res_"):
        return result_id(value)
    if value.startswith("obl_"):
        return obligation_id(value)
    raise ProtocolValueError("invalid_event_value_type")


def _claim_dispute_ref(value: object) -> ClaimId | EventId:
    if type(value) is not str:
        raise ProtocolValueError("invalid_event_value_type")
    if value.startswith("clm_"):
        return claim_id(value)
    if value.startswith("evt_"):
        return event_id(value)
    raise ProtocolValueError("invalid_event_value_type")


def _subject_state(value: object) -> SubjectStateRef:
    if type(value) is not SubjectStateRef:
        raise ProtocolValueError("invalid_event_value_type")
    return value


def _frontier(value: object) -> Frontier:
    if type(value) is not Frontier:
        raise ProtocolValueError("invalid_frontier")
    return value


def _timestamp(value: object) -> Timestamp:
    if type(value) is not Timestamp:
        raise ProtocolValueError("invalid_timestamp")
    return value


@dataclass(frozen=True, slots=True)
class RequestedItem:
    item_kind: RequestedItemKind
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_kind", _exact_enum(self.item_kind, RequestedItemKind))
        object.__setattr__(self, "value", _bounded_text(self.value, 1_024))


@dataclass(frozen=True, slots=True)
class ObligationChange:
    obligation_id: ObligationId
    change: ObligationChangeKind
    reason: str | None = None
    replacement_obligation_ids: tuple[ObligationId, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", obligation_id(self.obligation_id))
        change = _exact_enum(self.change, ObligationChangeKind)
        object.__setattr__(self, "change", change)
        if self.reason is not None:
            object.__setattr__(
                self, "reason", _bounded_text(self.reason, MAX_REASON_BYTES, minimum=1)
            )
        replacements = _id_tuple(
            self.replacement_obligation_ids,
            obligation_id,
            maximum=8,
            field="replacement_obligation_ids",
        )
        object.__setattr__(self, "replacement_obligation_ids", replacements)
        if change in {ObligationChangeKind.SUPERSEDED, ObligationChangeKind.WAIVED}:
            if self.reason is None:
                raise ProtocolValueError("obligation_change_invalid")
        if change is not ObligationChangeKind.SUPERSEDED and replacements:
            raise ProtocolValueError("obligation_change_invalid")


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    policy_id: str
    policy_version: str

    def __post_init__(self) -> None:
        policy_id_value = _bounded_text(self.policy_id, 128, minimum=1)
        if policy_id_value not in {"research-evidence", "work-integrity"}:
            raise ProtocolValueError("invalid_event_value_type")
        if type(self.policy_version) is not str or self.policy_version != "0.1.0":
            raise ProtocolValueError("invalid_event_value_type")
        object.__setattr__(self, "policy_id", policy_id_value)
        object.__setattr__(self, "policy_version", "0.1.0")


@dataclass(frozen=True, slots=True)
class EventSchema:
    name: str
    version: str

    def __post_init__(self) -> None:
        name = _bounded_text(self.name, 64, minimum=1)
        version = _bounded_text(self.version, 64, minimum=5)
        if _SCHEMA_NAME_RE.fullmatch(name) is None or _SEMVER_RE.fullmatch(version) is None:
            raise ProtocolValueError("invalid_event_schema")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, slots=True)
class WriterChain:
    writer_id: WriterId
    sequence: int
    previous_entry_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "writer_id", writer_id(self.writer_id))
        if type(self.sequence) is not int or not 1 <= self.sequence <= _MAX_SQLITE_INTEGER:
            raise ProtocolValueError("invalid_chain")
        if self.sequence == 1:
            if (
                type(self.previous_entry_digest) is not str
                or self.previous_entry_digest != "genesis"
            ):
                raise ProtocolValueError("invalid_chain")
        else:
            validate_sha256_digest(self.previous_entry_digest)


@dataclass(frozen=True, slots=True)
class LedgerChain:
    ingestion_sequence: int
    previous_entry_digest: str
    accepted_at: Timestamp

    def __post_init__(self) -> None:
        if (
            type(self.ingestion_sequence) is not int
            or not 1 <= self.ingestion_sequence <= _MAX_SQLITE_INTEGER
        ):
            raise ProtocolValueError("invalid_chain")
        if self.ingestion_sequence == 1:
            if (
                type(self.previous_entry_digest) is not str
                or self.previous_entry_digest != "genesis"
            ):
                raise ProtocolValueError("invalid_chain")
        else:
            validate_sha256_digest(self.previous_entry_digest)
        _timestamp(self.accepted_at)


@dataclass(frozen=True, slots=True)
class PayloadRef:
    object_id: ObjectId
    media_type: str
    plaintext_size: int
    commitment: str
    encryption_format: Literal["yoetz-object/1"] = "yoetz-object/1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", object_id(self.object_id))
        if type(self.media_type) is not str or not 1 <= len(self.media_type) <= 91:
            raise ProtocolValueError("invalid_payload_ref")
        if _MEDIA_TYPE_RE.fullmatch(self.media_type) is None:
            raise ProtocolValueError("invalid_payload_ref")
        if (
            type(self.plaintext_size) is not int
            or not 0 <= self.plaintext_size <= _MAX_OBJECT_PLAINTEXT_BYTES
        ):
            raise ProtocolValueError("invalid_payload_ref")
        validate_commitment(self.commitment)
        if type(self.encryption_format) is not str or self.encryption_format != "yoetz-object/1":
            raise ProtocolValueError("invalid_payload_ref")


def _locator_key_kind(schema: EventSchema) -> str:
    if schema.name not in EVENT_FAMILIES:
        return "none"
    if schema.version != SCHEMA_VERSION and not (
        schema.name == "evidence_recorded" and schema.version in EVIDENCE_SCHEMA_VERSIONS
    ):
        return "none"
    schema_name = schema.name
    if schema_name in {"plan_published", "plan_revised"}:
        return "plan"
    if schema_name in {"assignment_recorded", "decision_recorded", "check_recorded"}:
        return "event"
    if schema_name == "obligation_published":
        return "obligation"
    if schema_name == "action_recorded":
        return "action"
    if schema_name == "result_recorded":
        return "result"
    if schema_name == "evidence_recorded":
        return "evidence"
    if schema_name == "claim_recorded":
        return "claim"
    if schema_name in {"finding_recorded", "response_recorded"}:
        return "finding"
    return "none"


@dataclass(frozen=True, slots=True)
class ProjectionLocator:
    schema: EventSchema
    logical_key: str | None
    canonical_payload_digest: str
    redaction_target_event_ids: tuple[EventId, ...] = ()
    redaction_target_object_ids: tuple[ObjectId, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema) is not EventSchema:
            raise ProtocolValueError("invalid_projection_locator")
        validate_sha256_digest(self.canonical_payload_digest)
        try:
            raw_events = _tuple(self.redaction_target_event_ids, 0, MAX_REF_LIST)
            raw_objects = _tuple(self.redaction_target_object_ids, 0, MAX_REF_LIST)
        except ProtocolValueError as exc:
            raise ProtocolValueError("invalid_projection_locator") from exc
        events = tuple(event_id(item) for item in raw_events)
        objects = tuple(object_id(item) for item in raw_objects)
        _validate_ascii_sorted_unique(
            cast(tuple[str, ...], events), field="redaction_target_event_ids"
        )
        _validate_ascii_sorted_unique(
            cast(tuple[str, ...], objects), field="redaction_target_object_ids"
        )
        object.__setattr__(self, "redaction_target_event_ids", events)
        object.__setattr__(self, "redaction_target_object_ids", objects)
        key_kind = _locator_key_kind(self.schema)
        if key_kind == "none":
            if self.logical_key is not None:
                raise ProtocolValueError("invalid_projection_locator")
        else:
            if type(self.logical_key) is not str:
                raise ProtocolValueError("invalid_projection_locator")
            if key_kind == "plan":
                if not self.logical_key.isascii() or not self.logical_key.isdecimal():
                    raise ProtocolValueError("invalid_projection_locator")
                parsed_key = int(self.logical_key)
                if not 1 <= parsed_key <= _MAX_SAFE_INTEGER or str(parsed_key) != self.logical_key:
                    raise ProtocolValueError("invalid_projection_locator")
            else:
                constructors: Mapping[str, Callable[[object], object]] = {
                    "event": event_id,
                    "obligation": obligation_id,
                    "action": action_id,
                    "result": result_id,
                    "evidence": evidence_id,
                    "claim": claim_id,
                    "finding": finding_id,
                }
                constructors[key_kind](self.logical_key)
        if self.schema == EventSchema("redaction_recorded", SCHEMA_VERSION):
            if not events and not objects:
                raise ProtocolValueError("invalid_projection_locator")
        elif events or objects:
            raise ProtocolValueError("invalid_projection_locator")


@dataclass(frozen=True, slots=True)
class SessionOpenedPayload:
    task_title: str
    client_kind: ClientKind
    client_version: str
    integration: IntegrationKind
    profile: RuntimeProfile
    external_ref: str | None = None
    workspace_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "task_title", _bounded_text(self.task_title, MAX_TEXT_BYTES, minimum=1)
        )
        object.__setattr__(self, "client_kind", _exact_enum(self.client_kind, ClientKind))
        object.__setattr__(
            self,
            "client_version",
            _bounded_text(self.client_version, MAX_LABEL_BYTES, minimum=1),
        )
        object.__setattr__(self, "integration", _exact_enum(self.integration, IntegrationKind))
        object.__setattr__(self, "profile", _exact_enum(self.profile, RuntimeProfile))
        if self.external_ref is not None:
            object.__setattr__(
                self,
                "external_ref",
                _bounded_text(self.external_ref, MAX_TEXT_BYTES, minimum=1),
            )
        if self.workspace_ref is not None:
            object.__setattr__(
                self,
                "workspace_ref",
                _bounded_text(self.workspace_ref, MAX_TEXT_BYTES, minimum=1),
            )


@dataclass(frozen=True, slots=True)
class SessionResumedPayload:
    client_kind: ClientKind
    client_version: str
    integration: IntegrationKind
    profile: RuntimeProfile
    resumed_frontier: Frontier

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_kind", _exact_enum(self.client_kind, ClientKind))
        object.__setattr__(
            self,
            "client_version",
            _bounded_text(self.client_version, MAX_LABEL_BYTES, minimum=1),
        )
        object.__setattr__(self, "integration", _exact_enum(self.integration, IntegrationKind))
        object.__setattr__(self, "profile", _exact_enum(self.profile, RuntimeProfile))
        object.__setattr__(self, "resumed_frontier", _frontier(self.resumed_frontier))


@dataclass(frozen=True, slots=True)
class PlanPublishedPayload:
    plan_version: int
    summary: str
    obligation_refs: tuple[ObligationId, ...]
    scope_exclusions: tuple[str, ...] = ()
    no_obligations_reason: NoObligationsReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "plan_version",
            _bounded_integer(self.plan_version, 1, _MAX_SAFE_INTEGER),
        )
        object.__setattr__(self, "summary", _bounded_text(self.summary, MAX_TEXT_BYTES))
        object.__setattr__(
            self,
            "obligation_refs",
            _id_tuple(self.obligation_refs, obligation_id, field="obligation_refs"),
        )
        object.__setattr__(
            self,
            "scope_exclusions",
            _text_tuple(self.scope_exclusions, MAX_ALTERNATIVES, MAX_LABEL_BYTES),
        )
        if self.no_obligations_reason is not None:
            object.__setattr__(
                self,
                "no_obligations_reason",
                _exact_enum(self.no_obligations_reason, NoObligationsReason),
            )


# Schema-owned obligation meaning fields that must repeat byte-for-byte on resolution.
# status and resolution_evidence_refs are intentionally excluded: they are the transition.
_OBLIGATION_MEANING_FIELD_NAMES: Final = (
    "acceptance_criteria",
    "description",
    "evidence_expectation",
    "obligation_id",
    "requested_items",
    "source_refs",
)
_OBLIGATION_RESOLUTION_INVARIANTS: Final = frozenset(
    {
        "meaning_fields_must_repeat",
        "open_to_resolved_only",
    }
)


@dataclass(frozen=True, slots=True)
class ObligationResolutionMismatch(ValueError):
    """Typed rejection when an obligation_published transition is not a valid resolution.

    Carries only allowlisted schema field names and a fixed invariant code — never submitted
    values — so dry-run and durable append can project the same bounded public error.
    """

    differing_fields: tuple[str, ...]
    invariant: str
    event_id: EventId | None = None

    def __post_init__(self) -> None:
        if type(self.differing_fields) is not tuple:
            raise ProtocolValueError("invalid_event_value_type")
        ordered = tuple(sorted(self.differing_fields, key=str.encode))
        if len(ordered) != len(set(ordered)):
            raise ProtocolValueError("duplicate_set_member")
        allowed = frozenset((*_OBLIGATION_MEANING_FIELD_NAMES, "status"))
        if any(type(name) is not str or name not in allowed for name in ordered):
            raise ProtocolValueError("invalid_event_value_type")
        if (
            type(self.invariant) is not str
            or self.invariant not in _OBLIGATION_RESOLUTION_INVARIANTS
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if self.event_id is not None:
            object.__setattr__(self, "event_id", event_id(self.event_id))
        object.__setattr__(self, "differing_fields", ordered)
        ValueError.__init__(self, "obligation_resolution_mismatch")

    @property
    def reason_code(self) -> str:
        return "obligation_resolution_mismatch"


_MAX_EVENTS_PER_BATCH_FOR_POINTER: Final = 100


@dataclass(frozen=True, slots=True)
class NoObligationsReasonMismatch(ValueError):
    """Typed rejection for a no-obligations reason on non-empty or unreadable scope.

    The exception carries only the accepted event identity. The caller-supplied summary, revision
    reason, and obligation identifiers never enter the public error, so dry-run and durable append
    can return the same bounded diagnostic without reflecting submitted content.
    """

    event_id: EventId | None = None

    def __post_init__(self) -> None:
        if self.event_id is not None:
            object.__setattr__(self, "event_id", event_id(self.event_id))
        ValueError.__init__(self, "no_obligations_reason_conflict")

    @property
    def reason_code(self) -> str:
        return "no_obligations_reason_conflict"


def public_error_for_no_obligations_reason_mismatch(
    mismatch: NoObligationsReasonMismatch,
    *,
    event_index: int | None = None,
) -> PublicOperationError:
    """Project the typed declaration contradiction identically on every publication path."""

    if type(mismatch) is not NoObligationsReasonMismatch:
        raise TypeError("no_obligations_reason_mismatch_wrong_type")
    details: dict[str, str] = {"reason_code": mismatch.reason_code}
    if type(event_index) is int and 0 <= event_index < _MAX_EVENTS_PER_BATCH_FOR_POINTER:
        details["field"] = f"/event_drafts/{event_index}/payload/no_obligations_reason"
    return PublicOperationError(
        PublicErrorCode.EVENT_INVALID,
        (
            "The event batch is invalid. no_obligations_reason requires a readable effective "
            "current plan with zero obligation_refs. Remove no_obligations_reason or revise the "
            "plan declaration before retrying."
        ),
        False,
        safe_details=details,
    )


def public_error_for_obligation_resolution_mismatch(
    mismatch: ObligationResolutionMismatch,
    *,
    event_index: int | None = None,
) -> PublicOperationError:
    """Project one typed obligation-resolution rejection to the shared public error contract.

    Dry-run, in-memory durable append, and SQLite durable append must use this helper so the
    agent always sees the same reason, pointer, invariant, and allowlisted field names.
    """

    if type(mismatch) is not ObligationResolutionMismatch:
        raise TypeError("obligation_resolution_mismatch_wrong_type")
    fields = ", ".join(mismatch.differing_fields) if mismatch.differing_fields else "status"
    message = (
        "The event batch is invalid. Obligation resolution requires invariant "
        f"{mismatch.invariant}; mismatched fields: {fields}. Repeat every meaning field "
        "from the open obligation byte-for-byte; only status and resolution_evidence_refs "
        "may change. See yoetz://guidance/publication-policy.md section obligation-resolution "
        "and the publish_work examples entry for obligation_published resolution. "
        "Correct the event payload before retrying."
    )
    details: dict[str, str] = {"reason_code": "obligation_resolution_mismatch"}
    if type(event_index) is int and 0 <= event_index < _MAX_EVENTS_PER_BATCH_FOR_POINTER:
        details["field"] = f"/event_drafts/{event_index}/payload"
    return PublicOperationError(
        PublicErrorCode.EVENT_INVALID,
        message,
        False,
        safe_details=details,
    )


def obligation_meaning_field_diffs(
    previous: ObligationPublishedPayload,
    nxt: ObligationPublishedPayload,
) -> tuple[str, ...]:
    """Return allowlisted meaning-field names that differ between two obligation payloads.

    Comparison normalizes away ``status`` and ``resolution_evidence_refs`` so callers can
    decide whether a candidate is a pure open→resolved transition.
    """

    if (
        type(previous) is not ObligationPublishedPayload
        or type(nxt) is not ObligationPublishedPayload
    ):
        raise ProtocolValueError("invalid_event_value_type")
    diffs: list[str] = []
    if previous.obligation_id != nxt.obligation_id:
        diffs.append("obligation_id")
    if previous.description != nxt.description:
        diffs.append("description")
    if previous.evidence_expectation != nxt.evidence_expectation:
        diffs.append("evidence_expectation")
    if previous.acceptance_criteria != nxt.acceptance_criteria:
        diffs.append("acceptance_criteria")
    if previous.requested_items != nxt.requested_items:
        diffs.append("requested_items")
    if previous.source_refs != nxt.source_refs:
        diffs.append("source_refs")
    return tuple(sorted(diffs, key=str.encode))


@dataclass(frozen=True, slots=True)
class ObligationPublishedPayload:
    obligation_id: ObligationId
    description: str
    evidence_expectation: str
    status: ObligationStatus
    acceptance_criteria: str | None = None
    requested_items: tuple[RequestedItem, ...] = ()
    source_refs: tuple[EventId, ...] = ()
    resolution_evidence_refs: tuple[EvidenceId | ResultId, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", obligation_id(self.obligation_id))
        object.__setattr__(self, "description", _bounded_text(self.description, MAX_TEXT_BYTES))
        object.__setattr__(
            self,
            "evidence_expectation",
            _bounded_text(self.evidence_expectation, MAX_TEXT_BYTES),
        )
        status = _exact_enum(self.status, ObligationStatus)
        object.__setattr__(self, "status", status)
        if self.acceptance_criteria is not None:
            object.__setattr__(
                self,
                "acceptance_criteria",
                _bounded_text(self.acceptance_criteria, MAX_TEXT_BYTES),
            )
        requested = _tuple(self.requested_items, 0, MAX_REQUESTED_ITEMS)
        if any(type(item) is not RequestedItem for item in requested):
            raise ProtocolValueError("invalid_event_value_type")
        object.__setattr__(self, "requested_items", cast(tuple[RequestedItem, ...], requested))
        object.__setattr__(
            self,
            "source_refs",
            _id_tuple(self.source_refs, event_id, field="source_refs"),
        )
        minimum = 1 if status is ObligationStatus.RESOLVED else 0
        resolution_refs = _evidence_result_tuple(
            self.resolution_evidence_refs,
            minimum=minimum,
            field="resolution_evidence_refs",
        )
        object.__setattr__(self, "resolution_evidence_refs", resolution_refs)
        if status is ObligationStatus.OPEN and resolution_refs:
            raise ProtocolValueError("obligation_resolution_invalid")


@dataclass(frozen=True, slots=True)
class AssignmentRecordedPayload:
    assignee_actor_id: ActorId
    obligation_ids: tuple[ObligationId, ...]
    scope_description: str
    write_policy: WritePolicy | None = None
    handoff_of: EventId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignee_actor_id", actor_id(self.assignee_actor_id))
        object.__setattr__(
            self,
            "obligation_ids",
            _id_tuple(
                self.obligation_ids,
                obligation_id,
                minimum=1,
                field="obligation_ids",
            ),
        )
        object.__setattr__(
            self,
            "scope_description",
            _bounded_text(self.scope_description, MAX_TEXT_BYTES),
        )
        if self.write_policy is not None:
            object.__setattr__(
                self,
                "write_policy",
                _exact_enum(self.write_policy, WritePolicy),
            )
        if self.handoff_of is not None:
            object.__setattr__(self, "handoff_of", event_id(self.handoff_of))


@dataclass(frozen=True, slots=True)
class DecisionRecordedPayload:
    statement: str
    rationale: str
    authority: ActorId
    alternatives: tuple[str, ...] = ()
    affected_obligation_ids: tuple[ObligationId, ...] = ()
    supersedes_event_id: EventId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", _bounded_text(self.statement, MAX_TEXT_BYTES))
        object.__setattr__(
            self,
            "rationale",
            _bounded_text(self.rationale, MAX_TEXT_BYTES, minimum=1),
        )
        object.__setattr__(self, "authority", actor_id(self.authority))
        object.__setattr__(
            self,
            "alternatives",
            _text_tuple(self.alternatives, MAX_ALTERNATIVES, MAX_TEXT_BYTES),
        )
        object.__setattr__(
            self,
            "affected_obligation_ids",
            _id_tuple(self.affected_obligation_ids, obligation_id, field="affected_obligation_ids"),
        )
        if self.supersedes_event_id is not None:
            object.__setattr__(
                self,
                "supersedes_event_id",
                event_id(self.supersedes_event_id),
            )


@dataclass(frozen=True, slots=True)
class ActionRecordedPayload:
    action_id: ActionId
    action_kind: ActionKind
    description: str
    command: str | None = None
    subject_state: SubjectStateRef | None = None
    obligation_refs: tuple[ObligationId, ...] = ()
    attempted_items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", action_id(self.action_id))
        kind = _exact_enum(self.action_kind, ActionKind)
        object.__setattr__(self, "action_kind", kind)
        object.__setattr__(self, "description", _bounded_text(self.description, MAX_TEXT_BYTES))
        if self.command is not None:
            object.__setattr__(self, "command", _bounded_text(self.command, MAX_TEXT_BYTES))
        if kind is ActionKind.COMMAND and self.command is None:
            raise ProtocolValueError("invalid_event_value_type")
        if self.subject_state is not None:
            object.__setattr__(self, "subject_state", _subject_state(self.subject_state))
        object.__setattr__(
            self,
            "obligation_refs",
            _id_tuple(self.obligation_refs, obligation_id, field="obligation_refs"),
        )
        object.__setattr__(
            self,
            "attempted_items",
            _text_tuple(
                self.attempted_items,
                MAX_REQUESTED_ITEMS,
                1_024,
                unique=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class ResultRecordedPayload:
    result_id: ResultId
    action_id: ActionId
    outcome: ResultOutcome
    exit_status: int | None = None
    summary: str | None = None
    subject_state: SubjectStateRef | None = None
    evidence_refs: tuple[EvidenceId, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_id", result_id(self.result_id))
        object.__setattr__(self, "action_id", action_id(self.action_id))
        object.__setattr__(self, "outcome", _exact_enum(self.outcome, ResultOutcome))
        if self.exit_status is not None:
            object.__setattr__(
                self,
                "exit_status",
                _bounded_integer(self.exit_status, -(2**31), 2**31 - 1),
            )
        if self.summary is not None:
            object.__setattr__(self, "summary", _bounded_text(self.summary, MAX_TEXT_BYTES))
        if self.subject_state is not None:
            object.__setattr__(self, "subject_state", _subject_state(self.subject_state))
        object.__setattr__(
            self,
            "evidence_refs",
            _id_tuple(self.evidence_refs, evidence_id, field="evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceDigestBinding:
    subject: EvidenceDigestSubject
    content_availability: EvidenceContentAvailability
    byte_count: int
    provenance: EvidenceDigestProvenance
    approval_commitment: str | None = None
    approved_check_result_digest: str | None = None

    def __post_init__(self) -> None:
        subject = _exact_enum(self.subject, EvidenceDigestSubject)
        availability = _exact_enum(self.content_availability, EvidenceContentAvailability)
        provenance = _exact_enum(self.provenance, EvidenceDigestProvenance)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "content_availability", availability)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(
            self, "byte_count", _bounded_integer(self.byte_count, 0, _MAX_SAFE_INTEGER)
        )
        if self.approval_commitment is not None:
            validate_sha256_digest(self.approval_commitment)
        if self.approved_check_result_digest is not None:
            validate_sha256_digest(self.approved_check_result_digest)
        approved = provenance is EvidenceDigestProvenance.APPROVED_CHECK
        if approved is not (
            self.approval_commitment is not None and self.approved_check_result_digest is not None
        ):
            raise ProtocolValueError("evidence_digest_provenance_invalid")
        if (
            subject is EvidenceDigestSubject.APPROVED_CHECK_RECEIPT
            and provenance is not EvidenceDigestProvenance.APPROVED_CHECK
        ):
            raise ProtocolValueError("evidence_digest_provenance_invalid")
        if (
            subject is EvidenceDigestSubject.IMPORT_REPORT
            and provenance is not EvidenceDigestProvenance.IMPORT_OBSERVED
        ):
            raise ProtocolValueError("evidence_digest_provenance_invalid")


_EVIDENCE_SUBJECTS_BY_KIND: Final[Mapping[EvidenceKind, frozenset[EvidenceDigestSubject]]] = (
    MappingProxyType(
        {
            EvidenceKind.ARTIFACT: frozenset(
                {
                    EvidenceDigestSubject.ARTIFACT_BYTES,
                    EvidenceDigestSubject.BOUNDED_EXCERPT,
                    EvidenceDigestSubject.SOURCE_DIFF,
                }
            ),
            EvidenceKind.COMMAND_OUTPUT: frozenset(
                {
                    EvidenceDigestSubject.APPROVED_CHECK_RECEIPT,
                    EvidenceDigestSubject.COMMAND_STDOUT,
                    EvidenceDigestSubject.STATIC_ANALYSIS_REPORT,
                    EvidenceDigestSubject.TEST_REPORT,
                    EvidenceDigestSubject.TEST_STDOUT,
                }
            ),
            EvidenceKind.TEST_RESULT: frozenset(
                {
                    EvidenceDigestSubject.APPROVED_CHECK_RECEIPT,
                    EvidenceDigestSubject.STATIC_ANALYSIS_REPORT,
                    EvidenceDigestSubject.TEST_REPORT,
                    EvidenceDigestSubject.TEST_STDOUT,
                }
            ),
            EvidenceKind.RESEARCH_SOURCE: frozenset(
                {
                    EvidenceDigestSubject.ARTIFACT_BYTES,
                    EvidenceDigestSubject.BOUNDED_EXCERPT,
                }
            ),
            EvidenceKind.IMPORT_REPORT: frozenset({EvidenceDigestSubject.IMPORT_REPORT}),
            EvidenceKind.OTHER: frozenset({EvidenceDigestSubject.BOUNDED_EXCERPT}),
        }
    )
)


@dataclass(frozen=True, slots=True)
class EvidenceRecordedPayload:
    evidence_id: EvidenceId
    evidence_kind: EvidenceKind
    strength: EvidenceImmutability
    observed_at: Timestamp
    reference: str | None = None
    captured_object_id: ObjectId | None = None
    content_digest: str | None = None
    description: str | None = None
    subject_state: SubjectStateRef | None = None
    digest_binding: EvidenceDigestBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", evidence_id(self.evidence_id))
        kind = _exact_enum(self.evidence_kind, EvidenceKind)
        strength = _exact_enum(self.strength, EvidenceImmutability)
        object.__setattr__(self, "evidence_kind", kind)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at))
        if self.reference is not None:
            object.__setattr__(self, "reference", _bounded_text(self.reference, 2_048))
        if self.captured_object_id is not None:
            object.__setattr__(
                self,
                "captured_object_id",
                object_id(self.captured_object_id),
            )
        if self.content_digest is not None:
            validate_sha256_digest(self.content_digest)
        if self.description is not None:
            object.__setattr__(
                self,
                "description",
                _bounded_text(self.description, MAX_TEXT_BYTES),
            )
        if self.subject_state is not None:
            object.__setattr__(self, "subject_state", _subject_state(self.subject_state))
        if self.digest_binding is not None:
            if type(self.digest_binding) is not EvidenceDigestBinding:
                raise ProtocolValueError("invalid_event_value_type")
            if self.content_digest is None:
                raise ProtocolValueError("evidence_digest_binding_invalid")
            if self.digest_binding.subject not in _EVIDENCE_SUBJECTS_BY_KIND[kind]:
                raise ProtocolValueError("evidence_digest_subject_incompatible")
            captured = (
                self.digest_binding.content_availability is EvidenceContentAvailability.CAPTURED
            )
            if captured is not (self.captured_object_id is not None):
                raise ProtocolValueError("evidence_digest_availability_invalid")

        supported = True
        if strength is EvidenceImmutability.MUTABLE_REFERENCE:
            supported = self.reference is not None
        elif strength is EvidenceImmutability.METADATA_ONLY:
            supported = self.description is not None or self.reference is not None
        elif strength is EvidenceImmutability.CONTENT_DIGEST:
            supported = self.content_digest is not None
        elif strength is EvidenceImmutability.IMMUTABLE_SNAPSHOT:
            supported = self.captured_object_id is not None and self.content_digest is not None
        elif strength is EvidenceImmutability.INDEPENDENTLY_REPRODUCED:
            supported = (
                self.captured_object_id is not None
                and self.content_digest is not None
                and self.subject_state is not None
            )
        if not supported:
            raise ProtocolValueError("evidence_strength_unsupported")
        if kind is EvidenceKind.IMPORT_REPORT and (
            strength is not EvidenceImmutability.IMMUTABLE_SNAPSHOT
            or self.captured_object_id is None
            or self.content_digest is None
        ):
            raise ProtocolValueError("import_report_invalid")


@dataclass(frozen=True, slots=True)
class ClaimRecordedPayload:
    claim_id: ClaimId
    claim_kind: ClaimKind
    statement: str
    supporting_refs: tuple[EvidenceId | ResultId | ObligationId, ...]
    subject_state: SubjectStateRef | None = None
    obligation_refs: tuple[ObligationId, ...] = ()
    disputes_refs: tuple[ClaimId | EventId, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", claim_id(self.claim_id))
        object.__setattr__(self, "claim_kind", _exact_enum(self.claim_kind, ClaimKind))
        object.__setattr__(self, "statement", _bounded_text(self.statement, MAX_TEXT_BYTES))
        supporting_raw = _tuple(self.supporting_refs, 0, MAX_REF_LIST)
        supporting = tuple(_claim_supporting_ref(item) for item in supporting_raw)
        _validate_ascii_sorted_unique(cast(tuple[str, ...], supporting), field="supporting_refs")
        object.__setattr__(self, "supporting_refs", supporting)
        if self.subject_state is not None:
            object.__setattr__(self, "subject_state", _subject_state(self.subject_state))
        object.__setattr__(
            self,
            "obligation_refs",
            _id_tuple(self.obligation_refs, obligation_id, field="obligation_refs"),
        )
        disputes_raw = _tuple(self.disputes_refs, 0, MAX_ALTERNATIVES)
        disputes = tuple(_claim_dispute_ref(item) for item in disputes_raw)
        _validate_ascii_sorted_unique(cast(tuple[str, ...], disputes), field="disputes_refs")
        object.__setattr__(self, "disputes_refs", disputes)


@dataclass(frozen=True, slots=True)
class PlanRevisedPayload:
    plan_version: int
    supersedes_plan_version: int
    reason: str
    summary: str
    obligation_changes: tuple[ObligationChange, ...]
    no_obligations_reason: NoObligationsReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "plan_version",
            _bounded_integer(self.plan_version, 2, _MAX_SAFE_INTEGER),
        )
        object.__setattr__(
            self,
            "supersedes_plan_version",
            _bounded_integer(self.supersedes_plan_version, 1, _MAX_SAFE_INTEGER),
        )
        object.__setattr__(self, "reason", _bounded_text(self.reason, MAX_REASON_BYTES))
        object.__setattr__(self, "summary", _bounded_text(self.summary, MAX_TEXT_BYTES))
        changes = _tuple(self.obligation_changes, 0, MAX_REF_LIST)
        if any(type(change) is not ObligationChange for change in changes):
            raise ProtocolValueError("invalid_event_value_type")
        typed_changes = cast(tuple[ObligationChange, ...], changes)
        if len(typed_changes) != len(set(typed_changes)):
            raise ProtocolValueError("duplicate_set_member")
        object.__setattr__(self, "obligation_changes", typed_changes)
        if self.no_obligations_reason is not None:
            object.__setattr__(
                self,
                "no_obligations_reason",
                _exact_enum(self.no_obligations_reason, NoObligationsReason),
            )


FindingRecordedPayload = Finding


@dataclass(frozen=True, slots=True)
class ResponseRecordedPayload:
    finding_id: FindingId
    finding_frontier: Frontier
    disposition: ResponseDisposition
    reason: str | None = None
    waiver_scope: WaiverScope | None = None
    waiver_expiry: Timestamp | None = None
    evidence_refs: tuple[EvidenceId | ResultId, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", finding_id(self.finding_id))
        object.__setattr__(self, "finding_frontier", _frontier(self.finding_frontier))
        disposition = _exact_enum(self.disposition, ResponseDisposition)
        object.__setattr__(self, "disposition", disposition)
        if self.reason is not None:
            object.__setattr__(
                self,
                "reason",
                _bounded_text(self.reason, MAX_REASON_BYTES, minimum=1),
            )
        if self.waiver_scope is not None:
            object.__setattr__(
                self,
                "waiver_scope",
                _exact_enum(self.waiver_scope, WaiverScope),
            )
        if self.waiver_expiry is not None:
            object.__setattr__(self, "waiver_expiry", _timestamp(self.waiver_expiry))
        object.__setattr__(
            self,
            "evidence_refs",
            _evidence_result_tuple(self.evidence_refs, field="evidence_refs"),
        )
        if disposition in {
            ResponseDisposition.PROVENANCE_DISPUTED,
            ResponseDisposition.REJECTED,
            ResponseDisposition.WAIVED,
        }:
            if self.reason is None:
                raise ProtocolValueError("response_fields_invalid")
        if disposition is ResponseDisposition.WAIVED:
            if self.waiver_scope is None:
                raise ProtocolValueError("response_fields_invalid")
        elif self.waiver_scope is not None or self.waiver_expiry is not None:
            raise ProtocolValueError("response_fields_invalid")


@dataclass(frozen=True, slots=True)
class RedactionRecordedPayload:
    target_event_ids: tuple[EventId, ...]
    target_object_ids: tuple[ObjectId, ...]
    method: RedactionMethod
    reason_category: RedactionReasonCategory
    authority: ActorId
    remaining_gap: str

    def __post_init__(self) -> None:
        events = _id_tuple(self.target_event_ids, event_id, field="target_event_ids")
        objects = _id_tuple(self.target_object_ids, object_id, field="target_object_ids")
        if not events and not objects:
            raise ProtocolValueError("redaction_target_required")
        object.__setattr__(self, "target_event_ids", events)
        object.__setattr__(self, "target_object_ids", objects)
        object.__setattr__(self, "method", _exact_enum(self.method, RedactionMethod))
        object.__setattr__(
            self,
            "reason_category",
            _exact_enum(self.reason_category, RedactionReasonCategory),
        )
        object.__setattr__(self, "authority", actor_id(self.authority))
        object.__setattr__(
            self,
            "remaining_gap",
            _bounded_text(self.remaining_gap, MAX_REASON_BYTES),
        )


_RESEARCH_POLICY: Final = PolicyVersion("research-evidence", "0.1.0")
_WORK_POLICY: Final = PolicyVersion("work-integrity", "0.1.0")
_VALID_POLICY_SELECTIONS: Final = frozenset(
    {
        (_RESEARCH_POLICY,),
        (_WORK_POLICY,),
        (_RESEARCH_POLICY, _WORK_POLICY),
    }
)


@dataclass(frozen=True, slots=True)
class CheckRecordedPayload:
    mode: CheckMode
    policies: tuple[PolicyVersion, ...]
    scope: CheckScopeModel
    policy_executions: tuple[CheckPolicyExecutionModel, ...]
    subject_frontier: Frontier
    verdict: CheckVerdict
    returned_finding_ids: tuple[FindingId, ...]
    suppressed_count: int
    coverage: Coverage
    semantic_status: SemanticStatus
    semantic_reason: SemanticReason
    engine_version: str
    projection_version: str
    semantic_provenance: SemanticProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _exact_enum(self.mode, CheckMode))
        policies_raw = _tuple(self.policies, 1, 2)
        if any(type(policy) is not PolicyVersion for policy in policies_raw):
            raise ProtocolValueError("invalid_event_value_type")
        policies = cast(tuple[PolicyVersion, ...], policies_raw)
        if policies not in _VALID_POLICY_SELECTIONS:
            raise ProtocolValueError("invalid_event_value_type")
        object.__setattr__(self, "policies", policies)
        if type(self.scope) is not CheckScopeModel:
            raise ProtocolValueError("invalid_event_value_type")
        _id_tuple(self.scope.claim_ids, claim_id, field="scope")
        _id_tuple(self.scope.obligation_ids, obligation_id, field="scope")
        executions_raw = _tuple(self.policy_executions, 1, 2)
        if any(type(execution) is not CheckPolicyExecutionModel for execution in executions_raw):
            raise ProtocolValueError("invalid_event_value_type")
        executions = cast(tuple[CheckPolicyExecutionModel, ...], executions_raw)
        if len(executions) != len(set(executions)):
            raise ProtocolValueError("duplicate_set_member")
        execution_identities = tuple(
            (execution.policy_id, execution.policy_version) for execution in executions
        )
        policy_identities = tuple((policy.policy_id, policy.policy_version) for policy in policies)
        if execution_identities != policy_identities:
            raise ProtocolValueError("invalid_event_value_type")
        object.__setattr__(self, "policy_executions", executions)
        object.__setattr__(self, "subject_frontier", _frontier(self.subject_frontier))
        object.__setattr__(self, "verdict", _exact_enum(self.verdict, CheckVerdict))
        object.__setattr__(
            self,
            "returned_finding_ids",
            _id_tuple(
                self.returned_finding_ids,
                finding_id,
                maximum=_MAX_FINDINGS_LIMIT,
                field="returned_finding_ids",
            ),
        )
        object.__setattr__(
            self,
            "suppressed_count",
            _bounded_integer(self.suppressed_count, 0, _MAX_SAFE_INTEGER),
        )
        if type(self.coverage) is not Coverage:
            raise ProtocolValueError("invalid_coverage_value")
        status = _exact_enum(self.semantic_status, SemanticStatus)
        reason = _exact_enum(self.semantic_reason, SemanticReason)
        object.__setattr__(self, "semantic_status", status)
        object.__setattr__(self, "semantic_reason", reason)
        provenance_status: SemanticStatus | None = None
        provenance_reason: SemanticReason | None = None
        if self.semantic_provenance is not None:
            if type(self.semantic_provenance) is not SemanticProvenance:
                raise ProtocolValueError("invalid_semantic_provenance")
            provenance_status = self.semantic_provenance.status
            provenance_reason = self.semantic_provenance.reason
        validate_semantic_provenance_binding(
            status,
            reason,
            provenance_status,
            provenance_reason,
        )
        if type(self.engine_version) is not str or self.engine_version != "0.1.0":
            raise ProtocolValueError("invalid_event_value_type")
        if type(self.projection_version) is not str or self.projection_version != "yoetz/0.1.0":
            raise ProtocolValueError("invalid_event_value_type")


@dataclass(frozen=True, slots=True)
class ReceiptRecordedPayload:
    receipt_id: ReceiptId
    subject_frontier: Frontier
    receipt_digest: str
    receipt_object_id: ObjectId
    conclusion_code: ReceiptConclusion
    redaction_profile: ReceiptRedactionProfile

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", receipt_id(self.receipt_id))
        object.__setattr__(self, "subject_frontier", _frontier(self.subject_frontier))
        validate_sha256_digest(self.receipt_digest)
        object.__setattr__(self, "receipt_object_id", object_id(self.receipt_object_id))
        object.__setattr__(
            self,
            "conclusion_code",
            _exact_enum(self.conclusion_code, ReceiptConclusion),
        )
        object.__setattr__(
            self,
            "redaction_profile",
            _exact_enum(self.redaction_profile, ReceiptRedactionProfile),
        )


type EventPayload = (
    SessionOpenedPayload
    | SessionResumedPayload
    | PlanPublishedPayload
    | ObligationPublishedPayload
    | AssignmentRecordedPayload
    | DecisionRecordedPayload
    | ActionRecordedPayload
    | ResultRecordedPayload
    | EvidenceRecordedPayload
    | ClaimRecordedPayload
    | PlanRevisedPayload
    | Finding
    | ResponseRecordedPayload
    | RedactionRecordedPayload
    | CheckRecordedPayload
    | ReceiptRecordedPayload
)


PAYLOAD_TYPES: Final[Mapping[EventSchema, type[EventPayload]]] = MappingProxyType(
    {
        EventSchema("session_opened", SCHEMA_VERSION): SessionOpenedPayload,
        EventSchema("session_resumed", SCHEMA_VERSION): SessionResumedPayload,
        EventSchema("plan_published", SCHEMA_VERSION): PlanPublishedPayload,
        EventSchema("obligation_published", SCHEMA_VERSION): ObligationPublishedPayload,
        EventSchema("assignment_recorded", SCHEMA_VERSION): AssignmentRecordedPayload,
        EventSchema("decision_recorded", SCHEMA_VERSION): DecisionRecordedPayload,
        EventSchema("action_recorded", SCHEMA_VERSION): ActionRecordedPayload,
        EventSchema("result_recorded", SCHEMA_VERSION): ResultRecordedPayload,
        **{
            EventSchema("evidence_recorded", version): EvidenceRecordedPayload
            for version in EVIDENCE_SCHEMA_VERSIONS
        },
        EventSchema("claim_recorded", SCHEMA_VERSION): ClaimRecordedPayload,
        EventSchema("plan_revised", SCHEMA_VERSION): PlanRevisedPayload,
        EventSchema("finding_recorded", SCHEMA_VERSION): Finding,
        EventSchema("response_recorded", SCHEMA_VERSION): ResponseRecordedPayload,
        EventSchema("redaction_recorded", SCHEMA_VERSION): RedactionRecordedPayload,
        EventSchema("check_recorded", SCHEMA_VERSION): CheckRecordedPayload,
        EventSchema("receipt_recorded", SCHEMA_VERSION): ReceiptRecordedPayload,
    }
)


def _closed_object(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ProtocolValueError("invalid_event_value_type")
    source = cast(Mapping[object, object], value)
    try:
        keys = tuple(source)
    except Exception as exc:
        raise ProtocolValueError("invalid_event_value_type") from exc
    if any(type(key) is not str for key in keys):
        raise ProtocolValueError("invalid_event_value_type")
    string_keys = cast(tuple[str, ...], keys)
    present = frozenset(string_keys)
    if missing := required - present:
        _ = missing
        raise ProtocolValueError("missing_payload_field")
    if present - required - optional:
        raise ProtocolValueError("unknown_payload_field")
    try:
        return cast(Mapping[str, JsonValue], {key: source[key] for key in string_keys})
    except Exception as exc:
        raise ProtocolValueError("invalid_event_value_type") from exc


def _field(source: Mapping[str, JsonValue], key: str) -> JsonValue:
    try:
        return source[key]
    except Exception as exc:
        raise ProtocolValueError("missing_payload_field") from exc


def _optional(source: Mapping[str, JsonValue], key: str) -> JsonValue | None:
    if key not in source:
        return None
    value = _field(source, key)
    if value is None:
        raise ProtocolValueError("invalid_event_value_type")
    return value


def _array(value: object) -> tuple[object, ...]:
    if type(value) is tuple:
        return cast(tuple[object, ...], value)
    if type(value) is list:
        return tuple(cast(list[object], value))
    raise ProtocolValueError("invalid_event_value_type")


def _decode_subject_state(value: object) -> SubjectStateRef:
    source = _closed_object(
        value,
        required=frozenset(),
        optional=frozenset({"tree_digest", "diff_digest", "described_state"}),
    )
    tree_digest = _optional(source, "tree_digest")
    diff_digest = _optional(source, "diff_digest")
    described_state = _optional(source, "described_state")
    return SubjectStateRef(
        tree_digest=cast(str | None, tree_digest),
        diff_digest=cast(str | None, diff_digest),
        described_state=cast(str | None, described_state),
    )


def _encode_subject_state(value: SubjectStateRef) -> JsonObject:
    if type(value) is not SubjectStateRef:
        raise ProtocolValueError("invalid_event_value_type")
    result: dict[str, object] = {}
    if value.tree_digest is not None:
        result["tree_digest"] = value.tree_digest
    if value.diff_digest is not None:
        result["diff_digest"] = value.diff_digest
    if value.described_state is not None:
        result["described_state"] = value.described_state
    return JsonObject(result)


def _decode_digest_binding(value: object) -> EvidenceDigestBinding:
    source = _closed_object(
        value,
        required=frozenset({"subject", "content_availability", "byte_count", "provenance"}),
        optional=frozenset({"approval_commitment", "approved_check_result_digest"}),
    )
    return EvidenceDigestBinding(
        subject=_enum_from_json(_field(source, "subject"), EvidenceDigestSubject),
        content_availability=_enum_from_json(
            _field(source, "content_availability"), EvidenceContentAvailability
        ),
        byte_count=cast(int, _field(source, "byte_count")),
        provenance=_enum_from_json(_field(source, "provenance"), EvidenceDigestProvenance),
        approval_commitment=cast(str | None, _optional(source, "approval_commitment")),
        approved_check_result_digest=cast(
            str | None, _optional(source, "approved_check_result_digest")
        ),
    )


def _encode_digest_binding(value: EvidenceDigestBinding) -> JsonObject:
    if type(value) is not EvidenceDigestBinding:
        raise ProtocolValueError("invalid_event_value_type")
    result: dict[str, object] = {
        "subject": value.subject.value,
        "content_availability": value.content_availability.value,
        "byte_count": value.byte_count,
        "provenance": value.provenance.value,
    }
    _optional_value(result, "approval_commitment", value.approval_commitment)
    _optional_value(result, "approved_check_result_digest", value.approved_check_result_digest)
    return JsonObject(result)


def _decode_requested_item(value: object) -> RequestedItem:
    source = _closed_object(
        value,
        required=frozenset({"item_kind", "value"}),
    )
    return RequestedItem(
        item_kind=_enum_from_json(_field(source, "item_kind"), RequestedItemKind),
        value=cast(str, _field(source, "value")),
    )


def _decode_obligation_change(value: object) -> ObligationChange:
    source = _closed_object(
        value,
        required=frozenset({"obligation_id", "change"}),
        optional=frozenset({"reason", "replacement_obligation_ids"}),
    )
    change = _enum_from_json(_field(source, "change"), ObligationChangeKind)
    replacement_value = _optional(source, "replacement_obligation_ids")
    if change is not ObligationChangeKind.SUPERSEDED and "replacement_obligation_ids" in source:
        raise ProtocolValueError("obligation_change_invalid")
    return ObligationChange(
        obligation_id=obligation_id(_field(source, "obligation_id")),
        change=change,
        reason=cast(str | None, _optional(source, "reason")),
        replacement_obligation_ids=(
            ()
            if replacement_value is None
            else cast(
                tuple[ObligationId, ...],
                tuple(_array(replacement_value)),
            )
        ),
    )


def _decode_policy_version(value: object) -> PolicyVersion:
    source = _closed_object(
        value,
        required=frozenset({"policy_id", "policy_version"}),
    )
    return PolicyVersion(
        policy_id=cast(str, _field(source, "policy_id")),
        policy_version=cast(str, _field(source, "policy_version")),
    )


def _decode_scope(value: object) -> CheckScopeModel:
    source = _closed_object(
        value,
        required=frozenset({"claim_ids", "obligation_ids"}),
    )
    claim_ids = _id_tuple(
        tuple(_array(_field(source, "claim_ids"))),
        claim_id,
        field="scope",
    )
    obligation_ids = _id_tuple(
        tuple(_array(_field(source, "obligation_ids"))),
        obligation_id,
        field="scope",
    )
    try:
        return CheckScopeModel.model_validate(
            {
                "claim_ids": claim_ids,
                "obligation_ids": obligation_ids,
            }
        )
    except ValidationError as exc:
        raise ProtocolValueError("invalid_event_value_type") from exc


def _decode_policy_execution(value: object) -> CheckPolicyExecutionModel:
    source = _closed_object(
        value,
        required=frozenset({"policy_id", "policy_version", "outcome", "reason"}),
    )
    try:
        return CheckPolicyExecutionModel.model_validate(dict(source))
    except ValidationError as exc:
        raise ProtocolValueError("invalid_event_enum") from exc


def _decode_session_opened(source: Mapping[str, JsonValue]) -> SessionOpenedPayload:
    return SessionOpenedPayload(
        task_title=cast(str, _field(source, "task_title")),
        client_kind=_enum_from_json(_field(source, "client_kind"), ClientKind),
        client_version=cast(str, _field(source, "client_version")),
        integration=_enum_from_json(_field(source, "integration"), IntegrationKind),
        profile=_enum_from_json(_field(source, "profile"), RuntimeProfile),
        external_ref=cast(str | None, _optional(source, "external_ref")),
        workspace_ref=cast(str | None, _optional(source, "workspace_ref")),
    )


def _decode_session_resumed(source: Mapping[str, JsonValue]) -> SessionResumedPayload:
    return SessionResumedPayload(
        client_kind=_enum_from_json(_field(source, "client_kind"), ClientKind),
        client_version=cast(str, _field(source, "client_version")),
        integration=_enum_from_json(_field(source, "integration"), IntegrationKind),
        profile=_enum_from_json(_field(source, "profile"), RuntimeProfile),
        resumed_frontier=frontier_from_json(_field(source, "resumed_frontier")),
    )


_PAYLOAD_SHAPES: Final[Mapping[str, tuple[frozenset[str], frozenset[str]]]] = MappingProxyType(
    {
        "session_opened": (
            frozenset({"task_title", "client_kind", "client_version", "integration", "profile"}),
            frozenset({"external_ref", "workspace_ref"}),
        ),
        "session_resumed": (
            frozenset(
                {"client_kind", "client_version", "integration", "profile", "resumed_frontier"}
            ),
            frozenset(),
        ),
        "plan_published": (
            frozenset({"plan_version", "summary", "obligation_refs"}),
            frozenset({"scope_exclusions", "no_obligations_reason"}),
        ),
        "obligation_published": (
            frozenset({"obligation_id", "description", "evidence_expectation", "status"}),
            frozenset(
                {
                    "acceptance_criteria",
                    "requested_items",
                    "source_refs",
                    "resolution_evidence_refs",
                }
            ),
        ),
        "assignment_recorded": (
            frozenset({"assignee_actor_id", "obligation_ids", "scope_description"}),
            frozenset({"write_policy", "handoff_of"}),
        ),
        "decision_recorded": (
            frozenset({"statement", "rationale", "authority"}),
            frozenset({"alternatives", "affected_obligation_ids", "supersedes_event_id"}),
        ),
        "action_recorded": (
            frozenset({"action_id", "action_kind", "description"}),
            frozenset({"command", "subject_state", "obligation_refs", "attempted_items"}),
        ),
        "result_recorded": (
            frozenset({"result_id", "action_id", "outcome"}),
            frozenset({"exit_status", "summary", "subject_state", "evidence_refs"}),
        ),
        "evidence_recorded": (
            frozenset({"evidence_id", "evidence_kind", "strength", "observed_at"}),
            frozenset(
                {
                    "reference",
                    "captured_object_id",
                    "content_digest",
                    "description",
                    "subject_state",
                }
            ),
        ),
        "claim_recorded": (
            frozenset({"claim_id", "claim_kind", "statement", "supporting_refs"}),
            frozenset({"subject_state", "obligation_refs", "disputes_refs"}),
        ),
        "plan_revised": (
            frozenset(
                {
                    "plan_version",
                    "supersedes_plan_version",
                    "reason",
                    "summary",
                    "obligation_changes",
                }
            ),
            frozenset({"no_obligations_reason"}),
        ),
        "response_recorded": (
            frozenset({"finding_id", "finding_frontier", "disposition"}),
            frozenset({"reason", "waiver_scope", "waiver_expiry", "evidence_refs"}),
        ),
        "redaction_recorded": (
            frozenset(
                {
                    "target_event_ids",
                    "target_object_ids",
                    "method",
                    "reason_category",
                    "authority",
                    "remaining_gap",
                }
            ),
            frozenset(),
        ),
        "check_recorded": (
            frozenset(
                {
                    "mode",
                    "policies",
                    "scope",
                    "policy_executions",
                    "subject_frontier",
                    "verdict",
                    "returned_finding_ids",
                    "suppressed_count",
                    "coverage",
                    "semantic_status",
                    "semantic_reason",
                    "engine_version",
                    "projection_version",
                }
            ),
            frozenset({"semantic_provenance"}),
        ),
        "receipt_recorded": (
            frozenset(
                {
                    "receipt_id",
                    "subject_frontier",
                    "receipt_digest",
                    "receipt_object_id",
                    "conclusion_code",
                    "redaction_profile",
                }
            ),
            frozenset(),
        ),
    }
)


def decode_payload(schema: EventSchema, payload: JsonValue) -> EventPayload:
    """Decode one exact known schema pair into its immutable domain payload."""

    if type(schema) is not EventSchema:
        raise ProtocolValueError("invalid_event_schema")
    if schema not in PAYLOAD_TYPES:
        raise ProtocolValueError("unknown_event_schema")
    frozen = freeze_json(payload)
    if schema.name == "finding_recorded":
        return finding_from_json(frozen)
    required, optional = _PAYLOAD_SHAPES[schema.name]
    if schema.name == "evidence_recorded" and schema.version != SCHEMA_VERSION:
        optional = frozenset({*optional, "digest_binding"})
    source = _closed_object(frozen, required=required, optional=optional)

    if schema.name == "session_opened":
        return _decode_session_opened(source)
    if schema.name == "session_resumed":
        return _decode_session_resumed(source)
    if schema.name == "plan_published":
        exclusions = _optional(source, "scope_exclusions")
        no_obligations_reason = _optional(source, "no_obligations_reason")
        return PlanPublishedPayload(
            plan_version=cast(int, _field(source, "plan_version")),
            summary=cast(str, _field(source, "summary")),
            obligation_refs=cast(
                tuple[ObligationId, ...], tuple(_array(_field(source, "obligation_refs")))
            ),
            scope_exclusions=(
                () if exclusions is None else cast(tuple[str, ...], tuple(_array(exclusions)))
            ),
            no_obligations_reason=(
                None
                if no_obligations_reason is None
                else _enum_from_json(no_obligations_reason, NoObligationsReason)
            ),
        )
    if schema.name == "obligation_published":
        status = _enum_from_json(_field(source, "status"), ObligationStatus)
        resolution_refs = _optional(source, "resolution_evidence_refs")
        if status is ObligationStatus.OPEN and "resolution_evidence_refs" in source:
            raise ProtocolValueError("obligation_resolution_invalid")
        requested = _optional(source, "requested_items")
        source_refs = _optional(source, "source_refs")
        return ObligationPublishedPayload(
            obligation_id=obligation_id(_field(source, "obligation_id")),
            description=cast(str, _field(source, "description")),
            evidence_expectation=cast(str, _field(source, "evidence_expectation")),
            status=status,
            acceptance_criteria=cast(str | None, _optional(source, "acceptance_criteria")),
            requested_items=(
                ()
                if requested is None
                else tuple(_decode_requested_item(item) for item in _array(requested))
            ),
            source_refs=(
                () if source_refs is None else cast(tuple[EventId, ...], tuple(_array(source_refs)))
            ),
            resolution_evidence_refs=(
                ()
                if resolution_refs is None
                else cast(
                    tuple[EvidenceId | ResultId, ...],
                    tuple(_array(resolution_refs)),
                )
            ),
        )
    if schema.name == "assignment_recorded":
        obligations = tuple(_array(_field(source, "obligation_ids")))
        write_policy_value = _optional(source, "write_policy")
        handoff = _optional(source, "handoff_of")
        return AssignmentRecordedPayload(
            assignee_actor_id=actor_id(_field(source, "assignee_actor_id")),
            obligation_ids=cast(tuple[ObligationId, ...], obligations),
            scope_description=cast(str, _field(source, "scope_description")),
            write_policy=(
                None
                if write_policy_value is None
                else _enum_from_json(write_policy_value, WritePolicy)
            ),
            handoff_of=None if handoff is None else event_id(handoff),
        )
    if schema.name == "decision_recorded":
        alternatives = _optional(source, "alternatives")
        affected = _optional(source, "affected_obligation_ids")
        supersedes = _optional(source, "supersedes_event_id")
        return DecisionRecordedPayload(
            statement=cast(str, _field(source, "statement")),
            rationale=cast(str, _field(source, "rationale")),
            authority=actor_id(_field(source, "authority")),
            alternatives=(
                () if alternatives is None else cast(tuple[str, ...], tuple(_array(alternatives)))
            ),
            affected_obligation_ids=(
                () if affected is None else cast(tuple[ObligationId, ...], tuple(_array(affected)))
            ),
            supersedes_event_id=None if supersedes is None else event_id(supersedes),
        )
    if schema.name == "action_recorded":
        command = _optional(source, "command")
        state = _optional(source, "subject_state")
        obligations = _optional(source, "obligation_refs")
        attempted = _optional(source, "attempted_items")
        return ActionRecordedPayload(
            action_id=action_id(_field(source, "action_id")),
            action_kind=_enum_from_json(_field(source, "action_kind"), ActionKind),
            description=cast(str, _field(source, "description")),
            command=cast(str | None, command),
            subject_state=None if state is None else _decode_subject_state(state),
            obligation_refs=(
                ()
                if obligations is None
                else cast(tuple[ObligationId, ...], tuple(_array(obligations)))
            ),
            attempted_items=(
                () if attempted is None else cast(tuple[str, ...], tuple(_array(attempted)))
            ),
        )
    if schema.name == "result_recorded":
        state = _optional(source, "subject_state")
        evidence = _optional(source, "evidence_refs")
        return ResultRecordedPayload(
            result_id=result_id(_field(source, "result_id")),
            action_id=action_id(_field(source, "action_id")),
            outcome=_enum_from_json(_field(source, "outcome"), ResultOutcome),
            exit_status=cast(int | None, _optional(source, "exit_status")),
            summary=cast(str | None, _optional(source, "summary")),
            subject_state=None if state is None else _decode_subject_state(state),
            evidence_refs=(
                () if evidence is None else cast(tuple[EvidenceId, ...], tuple(_array(evidence)))
            ),
        )
    if schema.name == "evidence_recorded":
        state = _optional(source, "subject_state")
        raw_binding = _optional(source, "digest_binding")
        content_digest = cast(str | None, _optional(source, "content_digest"))
        if schema.version == SCHEMA_VERSION and raw_binding is not None:
            raise ProtocolValueError("invalid_event_value_type")
        if schema.version != SCHEMA_VERSION and ((content_digest is None) != (raw_binding is None)):
            raise ProtocolValueError("evidence_digest_binding_required")
        decoded = EvidenceRecordedPayload(
            evidence_id=evidence_id(_field(source, "evidence_id")),
            evidence_kind=_enum_from_json(_field(source, "evidence_kind"), EvidenceKind),
            strength=_enum_from_json(_field(source, "strength"), EvidenceImmutability),
            observed_at=timestamp_from_string(_field(source, "observed_at")),
            reference=cast(str | None, _optional(source, "reference")),
            captured_object_id=(
                None
                if _optional(source, "captured_object_id") is None
                else object_id(_field(source, "captured_object_id"))
            ),
            content_digest=content_digest,
            description=cast(str | None, _optional(source, "description")),
            subject_state=None if state is None else _decode_subject_state(state),
            digest_binding=(None if raw_binding is None else _decode_digest_binding(raw_binding)),
        )
        if (
            schema.version == EVIDENCE_TYPED_SCHEMA_VERSION
            and decoded.digest_binding is not None
            and decoded.digest_binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
        ):
            raise ProtocolValueError("evidence_digest_provenance_invalid")
        return decoded
    if schema.name == "claim_recorded":
        state = _optional(source, "subject_state")
        obligations = _optional(source, "obligation_refs")
        disputes = _optional(source, "disputes_refs")
        return ClaimRecordedPayload(
            claim_id=claim_id(_field(source, "claim_id")),
            claim_kind=_enum_from_json(_field(source, "claim_kind"), ClaimKind),
            statement=cast(str, _field(source, "statement")),
            supporting_refs=cast(
                tuple[EvidenceId | ResultId | ObligationId, ...],
                tuple(_array(_field(source, "supporting_refs"))),
            ),
            subject_state=None if state is None else _decode_subject_state(state),
            obligation_refs=(
                ()
                if obligations is None
                else cast(tuple[ObligationId, ...], tuple(_array(obligations)))
            ),
            disputes_refs=(
                ()
                if disputes is None
                else cast(tuple[ClaimId | EventId, ...], tuple(_array(disputes)))
            ),
        )
    if schema.name == "plan_revised":
        no_obligations_reason = _optional(source, "no_obligations_reason")
        return PlanRevisedPayload(
            plan_version=cast(int, _field(source, "plan_version")),
            supersedes_plan_version=cast(int, _field(source, "supersedes_plan_version")),
            reason=cast(str, _field(source, "reason")),
            summary=cast(str, _field(source, "summary")),
            obligation_changes=tuple(
                _decode_obligation_change(item)
                for item in _array(_field(source, "obligation_changes"))
            ),
            no_obligations_reason=(
                None
                if no_obligations_reason is None
                else _enum_from_json(no_obligations_reason, NoObligationsReason)
            ),
        )
    if schema.name == "response_recorded":
        evidence = _optional(source, "evidence_refs")
        waiver_scope_value = _optional(source, "waiver_scope")
        waiver_expiry_value = _optional(source, "waiver_expiry")
        return ResponseRecordedPayload(
            finding_id=finding_id(_field(source, "finding_id")),
            finding_frontier=frontier_from_json(_field(source, "finding_frontier")),
            disposition=_enum_from_json(_field(source, "disposition"), ResponseDisposition),
            reason=cast(str | None, _optional(source, "reason")),
            waiver_scope=(
                None
                if waiver_scope_value is None
                else _enum_from_json(waiver_scope_value, WaiverScope)
            ),
            waiver_expiry=(
                None if waiver_expiry_value is None else timestamp_from_string(waiver_expiry_value)
            ),
            evidence_refs=(
                ()
                if evidence is None
                else cast(tuple[EvidenceId | ResultId, ...], tuple(_array(evidence)))
            ),
        )
    if schema.name == "redaction_recorded":
        return RedactionRecordedPayload(
            target_event_ids=cast(
                tuple[EventId, ...], tuple(_array(_field(source, "target_event_ids")))
            ),
            target_object_ids=cast(
                tuple[ObjectId, ...], tuple(_array(_field(source, "target_object_ids")))
            ),
            method=_enum_from_json(_field(source, "method"), RedactionMethod),
            reason_category=_enum_from_json(
                _field(source, "reason_category"), RedactionReasonCategory
            ),
            authority=actor_id(_field(source, "authority")),
            remaining_gap=cast(str, _field(source, "remaining_gap")),
        )
    if schema.name == "check_recorded":
        provenance_value = _optional(source, "semantic_provenance")
        return CheckRecordedPayload(
            mode=_enum_from_json(_field(source, "mode"), CheckMode),
            policies=tuple(
                _decode_policy_version(item) for item in _array(_field(source, "policies"))
            ),
            scope=_decode_scope(_field(source, "scope")),
            policy_executions=tuple(
                _decode_policy_execution(item)
                for item in _array(_field(source, "policy_executions"))
            ),
            subject_frontier=frontier_from_json(_field(source, "subject_frontier")),
            verdict=_enum_from_json(_field(source, "verdict"), CheckVerdict),
            returned_finding_ids=cast(
                tuple[FindingId, ...],
                tuple(_array(_field(source, "returned_finding_ids"))),
            ),
            suppressed_count=cast(int, _field(source, "suppressed_count")),
            coverage=coverage_from_json(cast(CanonicalJsonValue, _field(source, "coverage"))),
            semantic_status=_enum_from_json(_field(source, "semantic_status"), SemanticStatus),
            semantic_reason=_enum_from_json(_field(source, "semantic_reason"), SemanticReason),
            engine_version=cast(str, _field(source, "engine_version")),
            projection_version=cast(str, _field(source, "projection_version")),
            semantic_provenance=(
                None
                if provenance_value is None
                else semantic_provenance_from_json(provenance_value)
            ),
        )
    if schema.name == "receipt_recorded":
        return ReceiptRecordedPayload(
            receipt_id=receipt_id(_field(source, "receipt_id")),
            subject_frontier=frontier_from_json(_field(source, "subject_frontier")),
            receipt_digest=cast(str, _field(source, "receipt_digest")),
            receipt_object_id=object_id(_field(source, "receipt_object_id")),
            conclusion_code=_enum_from_json(_field(source, "conclusion_code"), ReceiptConclusion),
            redaction_profile=_enum_from_json(
                _field(source, "redaction_profile"), ReceiptRedactionProfile
            ),
        )
    raise ProtocolValueError("unsupported_payload_type")


def _json_object(fields: Mapping[str, object]) -> JsonObject:
    frozen = freeze_json(fields)
    if type(frozen) is not JsonObject:
        raise ProtocolValueError("unsupported_payload_type")
    return frozen


def _optional_value(result: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        result[key] = value


def _optional_tuple(result: dict[str, object], key: str, value: tuple[object, ...]) -> None:
    if value:
        result[key] = value


def encode_payload(payload: EventPayload) -> JsonValue:
    """Encode one exact payload type into its normalized closed frozen JSON object."""

    payload_type = type(payload)
    if payload_type is Finding:
        return finding_to_json(cast(Finding, payload))
    result: dict[str, object]
    if payload_type is SessionOpenedPayload:
        value = cast(SessionOpenedPayload, payload)
        result = {
            "task_title": value.task_title,
            "client_kind": value.client_kind.value,
            "client_version": value.client_version,
            "integration": value.integration.value,
            "profile": value.profile.value,
        }
        _optional_value(result, "external_ref", value.external_ref)
        _optional_value(result, "workspace_ref", value.workspace_ref)
        return _json_object(result)
    if payload_type is SessionResumedPayload:
        value = cast(SessionResumedPayload, payload)
        return _json_object(
            {
                "client_kind": value.client_kind.value,
                "client_version": value.client_version,
                "integration": value.integration.value,
                "profile": value.profile.value,
                "resumed_frontier": value.resumed_frontier.as_wire(),
            }
        )
    if payload_type is PlanPublishedPayload:
        value = cast(PlanPublishedPayload, payload)
        result = {
            "plan_version": value.plan_version,
            "summary": value.summary,
            "obligation_refs": value.obligation_refs,
        }
        _optional_tuple(
            result, "scope_exclusions", cast(tuple[object, ...], value.scope_exclusions)
        )
        _optional_value(
            result,
            "no_obligations_reason",
            None if value.no_obligations_reason is None else value.no_obligations_reason.value,
        )
        return _json_object(result)
    if payload_type is ObligationPublishedPayload:
        value = cast(ObligationPublishedPayload, payload)
        result = {
            "obligation_id": value.obligation_id,
            "description": value.description,
            "evidence_expectation": value.evidence_expectation,
            "status": value.status.value,
        }
        _optional_value(result, "acceptance_criteria", value.acceptance_criteria)
        if value.requested_items:
            result["requested_items"] = tuple(
                {"item_kind": item.item_kind.value, "value": item.value}
                for item in value.requested_items
            )
        _optional_tuple(result, "source_refs", cast(tuple[object, ...], value.source_refs))
        if value.status is ObligationStatus.RESOLVED:
            result["resolution_evidence_refs"] = value.resolution_evidence_refs
        return _json_object(result)
    if payload_type is AssignmentRecordedPayload:
        value = cast(AssignmentRecordedPayload, payload)
        result = {
            "assignee_actor_id": value.assignee_actor_id,
            "obligation_ids": value.obligation_ids,
            "scope_description": value.scope_description,
        }
        _optional_value(
            result,
            "write_policy",
            None if value.write_policy is None else value.write_policy.value,
        )
        _optional_value(result, "handoff_of", value.handoff_of)
        return _json_object(result)
    if payload_type is DecisionRecordedPayload:
        value = cast(DecisionRecordedPayload, payload)
        result = {
            "statement": value.statement,
            "rationale": value.rationale,
            "authority": value.authority,
        }
        _optional_tuple(result, "alternatives", cast(tuple[object, ...], value.alternatives))
        _optional_tuple(
            result,
            "affected_obligation_ids",
            cast(tuple[object, ...], value.affected_obligation_ids),
        )
        _optional_value(result, "supersedes_event_id", value.supersedes_event_id)
        return _json_object(result)
    if payload_type is ActionRecordedPayload:
        value = cast(ActionRecordedPayload, payload)
        result = {
            "action_id": value.action_id,
            "action_kind": value.action_kind.value,
            "description": value.description,
        }
        _optional_value(result, "command", value.command)
        _optional_value(
            result,
            "subject_state",
            None if value.subject_state is None else _encode_subject_state(value.subject_state),
        )
        _optional_tuple(result, "obligation_refs", cast(tuple[object, ...], value.obligation_refs))
        _optional_tuple(result, "attempted_items", cast(tuple[object, ...], value.attempted_items))
        return _json_object(result)
    if payload_type is ResultRecordedPayload:
        value = cast(ResultRecordedPayload, payload)
        result = {
            "result_id": value.result_id,
            "action_id": value.action_id,
            "outcome": value.outcome.value,
        }
        _optional_value(result, "exit_status", value.exit_status)
        _optional_value(result, "summary", value.summary)
        _optional_value(
            result,
            "subject_state",
            None if value.subject_state is None else _encode_subject_state(value.subject_state),
        )
        _optional_tuple(result, "evidence_refs", cast(tuple[object, ...], value.evidence_refs))
        return _json_object(result)
    if payload_type is EvidenceRecordedPayload:
        value = cast(EvidenceRecordedPayload, payload)
        result = {
            "evidence_id": value.evidence_id,
            "evidence_kind": value.evidence_kind.value,
            "strength": value.strength.value,
            "observed_at": value.observed_at.wire,
        }
        _optional_value(result, "reference", value.reference)
        _optional_value(result, "captured_object_id", value.captured_object_id)
        _optional_value(result, "content_digest", value.content_digest)
        _optional_value(result, "description", value.description)
        _optional_value(
            result,
            "subject_state",
            None if value.subject_state is None else _encode_subject_state(value.subject_state),
        )
        _optional_value(
            result,
            "digest_binding",
            None if value.digest_binding is None else _encode_digest_binding(value.digest_binding),
        )
        return _json_object(result)
    if payload_type is ClaimRecordedPayload:
        value = cast(ClaimRecordedPayload, payload)
        result = {
            "claim_id": value.claim_id,
            "claim_kind": value.claim_kind.value,
            "statement": value.statement,
            "supporting_refs": value.supporting_refs,
        }
        _optional_value(
            result,
            "subject_state",
            None if value.subject_state is None else _encode_subject_state(value.subject_state),
        )
        _optional_tuple(result, "obligation_refs", cast(tuple[object, ...], value.obligation_refs))
        _optional_tuple(result, "disputes_refs", cast(tuple[object, ...], value.disputes_refs))
        return _json_object(result)
    if payload_type is PlanRevisedPayload:
        value = cast(PlanRevisedPayload, payload)
        changes: list[object] = []
        for change in value.obligation_changes:
            item: dict[str, object] = {
                "obligation_id": change.obligation_id,
                "change": change.change.value,
            }
            _optional_value(item, "reason", change.reason)
            _optional_tuple(
                item,
                "replacement_obligation_ids",
                cast(tuple[object, ...], change.replacement_obligation_ids),
            )
            changes.append(item)
        result = {
            "plan_version": value.plan_version,
            "supersedes_plan_version": value.supersedes_plan_version,
            "reason": value.reason,
            "summary": value.summary,
            "obligation_changes": tuple(changes),
        }
        _optional_value(
            result,
            "no_obligations_reason",
            None if value.no_obligations_reason is None else value.no_obligations_reason.value,
        )
        return _json_object(result)
    if payload_type is ResponseRecordedPayload:
        value = cast(ResponseRecordedPayload, payload)
        result = {
            "finding_id": value.finding_id,
            "finding_frontier": value.finding_frontier.as_wire(),
            "disposition": value.disposition.value,
        }
        _optional_value(result, "reason", value.reason)
        _optional_value(
            result,
            "waiver_scope",
            None if value.waiver_scope is None else value.waiver_scope.value,
        )
        _optional_value(
            result,
            "waiver_expiry",
            None if value.waiver_expiry is None else value.waiver_expiry.wire,
        )
        _optional_tuple(result, "evidence_refs", cast(tuple[object, ...], value.evidence_refs))
        return _json_object(result)
    if payload_type is RedactionRecordedPayload:
        value = cast(RedactionRecordedPayload, payload)
        return _json_object(
            {
                "target_event_ids": value.target_event_ids,
                "target_object_ids": value.target_object_ids,
                "method": value.method.value,
                "reason_category": value.reason_category.value,
                "authority": value.authority,
                "remaining_gap": value.remaining_gap,
            }
        )
    if payload_type is CheckRecordedPayload:
        value = cast(CheckRecordedPayload, payload)
        result = {
            "mode": value.mode.value,
            "policies": tuple(
                {"policy_id": policy.policy_id, "policy_version": policy.policy_version}
                for policy in value.policies
            ),
            "scope": value.scope.model_dump(mode="json"),
            "policy_executions": tuple(
                execution.model_dump(mode="json") for execution in value.policy_executions
            ),
            "subject_frontier": value.subject_frontier.as_wire(),
            "verdict": value.verdict.value,
            "returned_finding_ids": value.returned_finding_ids,
            "suppressed_count": value.suppressed_count,
            "coverage": coverage_to_json(value.coverage),
            "semantic_status": value.semantic_status.value,
            "semantic_reason": value.semantic_reason.value,
            "engine_version": value.engine_version,
            "projection_version": value.projection_version,
        }
        _optional_value(
            result,
            "semantic_provenance",
            None
            if value.semantic_provenance is None
            else semantic_provenance_to_json(value.semantic_provenance),
        )
        return _json_object(result)
    if payload_type is ReceiptRecordedPayload:
        value = cast(ReceiptRecordedPayload, payload)
        return _json_object(
            {
                "receipt_id": value.receipt_id,
                "subject_frontier": value.subject_frontier.as_wire(),
                "receipt_digest": value.receipt_digest,
                "receipt_object_id": value.receipt_object_id,
                "conclusion_code": value.conclusion_code.value,
                "redaction_profile": value.redaction_profile.value,
            }
        )
    raise ProtocolValueError("unsupported_payload_type")


def normalize_payload_json(schema: EventSchema, value: JsonValue) -> JsonValue:
    return encode_payload(decode_payload(schema, value))


def media_type_for(schema_name: str) -> str:
    name = _bounded_text(schema_name, 64, minimum=1)
    if _SCHEMA_NAME_RE.fullmatch(name) is None:
        raise ProtocolValueError("invalid_event_schema")
    return f"application/vnd.yoetz.{name}+json"


def _expected_payload_type(schema: EventSchema) -> type[EventPayload] | None:
    return PAYLOAD_TYPES.get(schema)


def _validate_evidence_schema_payload(
    schema: EventSchema,
    payload: EventPayload | None,
) -> None:
    if type(payload) is not EvidenceRecordedPayload:
        return
    evidence = payload
    if schema == EventSchema("evidence_recorded", SCHEMA_VERSION):
        if evidence.digest_binding is not None:
            raise ProtocolValueError("invalid_event_value_type")
        return
    if (
        schema.name == "evidence_recorded"
        and schema.version != SCHEMA_VERSION
        and ((evidence.content_digest is None) != (evidence.digest_binding is None))
    ):
        raise ProtocolValueError("evidence_digest_binding_required")
    if (
        schema.version == EVIDENCE_TYPED_SCHEMA_VERSION
        and evidence.digest_binding is not None
        and evidence.digest_binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
    ):
        raise ProtocolValueError("evidence_digest_provenance_invalid")


def _validate_envelope_ref_mirrors(
    schema: EventSchema,
    payload: EventPayload | None,
    artifact_refs: tuple[ObjectId, ...],
    evidence_refs: tuple[EvidenceId | ResultId, ...],
    locator: ProjectionLocator | None = None,
) -> None:
    # The mismatched envelope field name is the whole repair, and it is a frozen schema name, not
    # a caller-supplied key. Carrying it lets the application layer point at the exact list instead
    # of leaving the caller to re-derive which of the two reference lists broke the mirror.
    if schema.name == "result_recorded":
        if (
            payload is not None
            and evidence_refs != cast(ResultRecordedPayload, payload).evidence_refs
        ):
            raise ProtocolValueError("ref_mirror_mismatch", field="evidence_refs")
    elif schema.name == "response_recorded":
        if (
            payload is not None
            and evidence_refs != cast(ResponseRecordedPayload, payload).evidence_refs
        ):
            raise ProtocolValueError("ref_mirror_mismatch", field="evidence_refs")

    if schema.name == "evidence_recorded":
        if payload is None:
            if len(artifact_refs) > 1:
                raise ProtocolValueError("ref_mirror_mismatch", field="artifact_refs")
        else:
            captured = cast(EvidenceRecordedPayload, payload).captured_object_id
            expected = () if captured is None else (captured,)
            if artifact_refs != expected:
                raise ProtocolValueError("ref_mirror_mismatch", field="artifact_refs")
    elif schema.name == "redaction_recorded":
        if payload is not None:
            expected = cast(RedactionRecordedPayload, payload).target_object_ids
        elif locator is not None:
            expected = locator.redaction_target_object_ids
        else:
            expected = ()
        if artifact_refs != expected:
            raise ProtocolValueError("ref_mirror_mismatch", field="artifact_refs")
    elif schema.name == "receipt_recorded":
        if payload is None:
            if len(artifact_refs) != 1:
                raise ProtocolValueError("ref_mirror_mismatch", field="artifact_refs")
        elif artifact_refs != (cast(ReceiptRecordedPayload, payload).receipt_object_id,):
            raise ProtocolValueError("ref_mirror_mismatch", field="artifact_refs")


# Public draft-schema description for caller-asserted event time. Owned here so schema generation
# and hand-reviewed draft envelopes stay aligned; receipt freshness is frontier-bound, not wall-clock.
OCCURRED_AT_DRAFT_DESCRIPTION: Final = (
    "Caller-asserted event time (RFC 3339 UTC, millisecond precision). Use the best real time "
    "available; do not copy illustrative example timestamps. If the exact time is unknown, use an "
    "honest bounded approximation and treat it as a claim. Ledger order uses ingestion sequence; "
    "receipt freshness is frontier-bound. Service accepted_at is independent acceptance metadata, "
    "not a sort, filter, or freshness key."
)


@dataclass(frozen=True, slots=True)
class EventDraft:
    event_id: EventId
    schema: EventSchema
    occurred_at: Annotated[Timestamp, Field(description=OCCURRED_AT_DRAFT_DESCRIPTION)]
    causal_parents: tuple[EventId, ...]
    payload: EventPayload | JsonValue
    artifact_refs: tuple[ObjectId, ...]
    evidence_refs: tuple[EvidenceId | ResultId, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", event_id(self.event_id))
        if type(self.schema) is not EventSchema:
            raise ProtocolValueError("invalid_event_schema")
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at))
        object.__setattr__(
            self,
            "causal_parents",
            _id_tuple(
                self.causal_parents,
                event_id,
                maximum=MAX_CAUSAL_PARENTS,
            ),
        )
        object.__setattr__(
            self,
            "artifact_refs",
            _id_tuple(self.artifact_refs, object_id),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _evidence_result_tuple(self.evidence_refs),
        )

        payload_type = _expected_payload_type(self.schema)
        if payload_type is None:
            object.__setattr__(self, "payload", freeze_json(self.payload))
            return
        if type(self.payload) is not payload_type:
            raise ProtocolValueError("invalid_event_value_type")
        typed_payload = cast(EventPayload, self.payload)
        _validate_evidence_schema_payload(self.schema, typed_payload)
        _validate_envelope_ref_mirrors(
            self.schema,
            typed_payload,
            self.artifact_refs,
            self.evidence_refs,
        )


def _expected_locator_key(payload: EventPayload, envelope_event_id: EventId) -> str | None:
    payload_type = type(payload)
    if payload_type is PlanPublishedPayload:
        return str(cast(PlanPublishedPayload, payload).plan_version)
    if payload_type is PlanRevisedPayload:
        return str(cast(PlanRevisedPayload, payload).plan_version)
    if payload_type is ObligationPublishedPayload:
        return cast(ObligationPublishedPayload, payload).obligation_id
    if payload_type in {AssignmentRecordedPayload, DecisionRecordedPayload, CheckRecordedPayload}:
        return envelope_event_id
    if payload_type is ActionRecordedPayload:
        return cast(ActionRecordedPayload, payload).action_id
    if payload_type is ResultRecordedPayload:
        return cast(ResultRecordedPayload, payload).result_id
    if payload_type is EvidenceRecordedPayload:
        return cast(EvidenceRecordedPayload, payload).evidence_id
    if payload_type is ClaimRecordedPayload:
        return cast(ClaimRecordedPayload, payload).claim_id
    if payload_type is Finding:
        return cast(Finding, payload).finding_id
    if payload_type is ResponseRecordedPayload:
        return cast(ResponseRecordedPayload, payload).finding_id
    return None


def _expected_locator_targets(
    payload: EventPayload,
) -> tuple[tuple[EventId, ...], tuple[ObjectId, ...]]:
    if type(payload) is RedactionRecordedPayload:
        redaction = payload
        return redaction.target_event_ids, redaction.target_object_ids
    return (), ()


def _validate_projection_locator(
    schema: EventSchema,
    event: EventId,
    payload: EventPayload | None,
    locator: ProjectionLocator,
) -> None:
    if type(locator) is not ProjectionLocator or locator.schema != schema:
        raise ProtocolValueError("invalid_projection_locator")
    if payload is None:
        if schema.name in {"assignment_recorded", "decision_recorded", "check_recorded"} and (
            locator.logical_key != event
        ):
            raise ProtocolValueError("invalid_projection_locator")
        return
    expected_key = _expected_locator_key(payload, event)
    expected_event_targets, expected_object_targets = _expected_locator_targets(payload)
    if (
        locator.logical_key != expected_key
        or locator.redaction_target_event_ids != expected_event_targets
        or locator.redaction_target_object_ids != expected_object_targets
        or locator.canonical_payload_digest != canonical_digest(encode_payload(payload))
    ):
        raise ProtocolValueError("invalid_projection_locator")


def _validate_record_common(record: AcceptedEvent | UnknownEvent) -> None:
    object.__setattr__(record, "event_id", event_id(record.event_id))
    object.__setattr__(record, "task_id", task_id(record.task_id))
    object.__setattr__(record, "session_id", session_id(record.session_id))
    if type(record.schema) is not EventSchema:
        raise ProtocolValueError("accepted_record_shape_invalid")
    if type(record.author) is not Actor:
        raise ProtocolValueError("accepted_record_shape_invalid")
    if type(record.writer) is not WriterChain or type(record.ledger) is not LedgerChain:
        raise ProtocolValueError("accepted_record_shape_invalid")
    object.__setattr__(record, "operation_id", request_id(record.operation_id))
    object.__setattr__(record, "occurred_at", _timestamp(record.occurred_at))
    object.__setattr__(
        record,
        "causal_parents",
        _id_tuple(
            record.causal_parents,
            event_id,
            maximum=MAX_CAUSAL_PARENTS,
        ),
    )
    channel = _exact_enum(record.publication_channel, PublicationChannel)
    object.__setattr__(record, "publication_channel", channel)
    if type(record.coverage) is not Coverage:
        raise ProtocolValueError("invalid_coverage_value")
    if record.coverage.publication_channels != (channel,):
        raise ProtocolValueError("invalid_coverage_value")
    if type(record.payload_ref) is not PayloadRef:
        raise ProtocolValueError("invalid_payload_ref")
    if record.payload_ref.media_type != media_type_for(record.schema.name):
        raise ProtocolValueError("invalid_payload_ref")
    object.__setattr__(record, "redaction", _exact_enum(record.redaction, RedactionState))
    object.__setattr__(
        record,
        "artifact_refs",
        _id_tuple(record.artifact_refs, object_id),
    )
    object.__setattr__(
        record,
        "evidence_refs",
        _evidence_result_tuple(record.evidence_refs),
    )
    validate_sha256_digest(record.entry_digest)
    if type(record.projection_locator) is not ProjectionLocator:
        raise ProtocolValueError("invalid_projection_locator")


@dataclass(frozen=True, slots=True)
class AcceptedEvent:
    protocol: Literal["yoetz.event"] = field(init=False, default="yoetz.event")
    protocol_version: Literal["0.1"] = field(init=False, default="0.1")
    event_id: EventId
    task_id: TaskId
    session_id: SessionId
    schema: EventSchema
    author: Actor
    writer: WriterChain
    ledger: LedgerChain
    operation_id: RequestId
    occurred_at: Timestamp
    causal_parents: tuple[EventId, ...]
    publication_channel: PublicationChannel
    coverage: Coverage
    payload_ref: PayloadRef
    redaction: RedactionState
    artifact_refs: tuple[ObjectId, ...]
    evidence_refs: tuple[EvidenceId | ResultId, ...]
    entry_digest: str
    payload: EventPayload | None
    projection_locator: ProjectionLocator

    def __post_init__(self) -> None:
        _validate_record_common(self)
        payload_type = _expected_payload_type(self.schema)
        if payload_type is None:
            raise ProtocolValueError("invalid_event_schema")
        if self.payload is not None and type(self.payload) is not payload_type:
            raise ProtocolValueError("invalid_event_value_type")
        if self.redaction is not RedactionState.PRESENT and self.payload is not None:
            raise ProtocolValueError("payload_redaction_mismatch")
        typed_payload = self.payload
        _validate_evidence_schema_payload(self.schema, typed_payload)
        _validate_projection_locator(
            self.schema,
            self.event_id,
            typed_payload,
            self.projection_locator,
        )
        _validate_envelope_ref_mirrors(
            self.schema,
            typed_payload,
            self.artifact_refs,
            self.evidence_refs,
            self.projection_locator,
        )
        if type(typed_payload) is EvidenceRecordedPayload:
            evidence = typed_payload
            if evidence.evidence_kind is EvidenceKind.IMPORT_REPORT and (
                self.author.actor_type is not ActorType.IMPORTER
                or self.publication_channel is not PublicationChannel.CODEX_JSONL_IMPORT
            ):
                raise ProtocolValueError("import_report_invalid")
            binding = evidence.digest_binding
            if binding is not None:
                if binding.provenance is EvidenceDigestProvenance.APPROVED_CHECK and (
                    self.author.actor_type is not ActorType.YOETZ_ENGINE
                    or self.author.assurance is not AuthorshipAssurance.SERVICE_AUTHENTICATED
                    or self.publication_channel is not PublicationChannel.ENGINE_DERIVED
                ):
                    raise ProtocolValueError("evidence_digest_provenance_invalid")
                if binding.provenance is EvidenceDigestProvenance.IMPORT_OBSERVED and (
                    self.author.actor_type is not ActorType.IMPORTER
                    or self.publication_channel is not PublicationChannel.CODEX_JSONL_IMPORT
                ):
                    raise ProtocolValueError("evidence_digest_provenance_invalid")
                if binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED and (
                    self.author.actor_type is not ActorType.HARNESS
                    or self.author.assurance is not AuthorshipAssurance.HARNESS_OBSERVED
                    or self.publication_channel is not PublicationChannel.HOOK_OBSERVED
                    or evidence.strength is not EvidenceImmutability.IMMUTABLE_SNAPSHOT
                    or binding.content_availability is not EvidenceContentAvailability.CAPTURED
                    or self.coverage.artifact_observation
                    is not ArtifactObservation.CONTENT_CAPTURED
                    or self.coverage.evidence_immutability
                    is not EvidenceImmutability.IMMUTABLE_SNAPSHOT
                ):
                    raise ProtocolValueError("evidence_digest_provenance_invalid")
        if compute_entry_digest(accepted_record_digest_preimage(self)) != self.entry_digest:
            raise ProtocolValueError("entry_digest_mismatch")


@dataclass(frozen=True, slots=True)
class UnknownEvent:
    protocol: Literal["yoetz.event"] = field(init=False, default="yoetz.event")
    protocol_version: Literal["0.1"] = field(init=False, default="0.1")
    event_id: EventId
    task_id: TaskId
    session_id: SessionId
    schema: EventSchema
    author: Actor
    writer: WriterChain
    ledger: LedgerChain
    operation_id: RequestId
    occurred_at: Timestamp
    causal_parents: tuple[EventId, ...]
    publication_channel: PublicationChannel
    coverage: Coverage
    payload_ref: PayloadRef
    redaction: RedactionState
    artifact_refs: tuple[ObjectId, ...]
    evidence_refs: tuple[EvidenceId | ResultId, ...]
    entry_digest: str
    payload: JsonValue | None
    projection_locator: ProjectionLocator
    canonical_payload_digest: str
    projection_status: Literal["unknown_unprojected"] = field(
        init=False,
        default="unknown_unprojected",
    )

    def __post_init__(self) -> None:
        _validate_record_common(self)
        if _expected_payload_type(self.schema) is not None:
            raise ProtocolValueError("invalid_event_schema")
        validate_sha256_digest(self.canonical_payload_digest)
        if (
            self.projection_locator.schema != self.schema
            or self.projection_locator.logical_key is not None
            or self.projection_locator.redaction_target_event_ids
            or self.projection_locator.redaction_target_object_ids
            or self.projection_locator.canonical_payload_digest != self.canonical_payload_digest
        ):
            raise ProtocolValueError("invalid_projection_locator")
        if self.redaction is not RedactionState.PRESENT and self.payload is not None:
            raise ProtocolValueError("payload_redaction_mismatch")
        if self.payload is not None:
            frozen = freeze_json(self.payload)
            object.__setattr__(self, "payload", frozen)
            if canonical_digest(frozen) != self.canonical_payload_digest:
                raise ProtocolValueError("invalid_projection_locator")
        if compute_entry_digest(accepted_record_digest_preimage(self)) != self.entry_digest:
            raise ProtocolValueError("entry_digest_mismatch")


type LedgerRecord = AcceptedEvent | UnknownEvent


def is_observation_authorship(author: Actor, publication_channel: PublicationChannel) -> bool:
    """Recognize only service-stamped observation coordinator authorship.

    Two of these four facts are caller-supplied through ``publish_work`` (``actor_id`` and
    ``actor_type``); ``assurance`` and ``publication_channel`` are service-derived and are what
    make the pair unforgeable. Every caller must test all four, so the predicate lives here once
    rather than being restated per call site.
    """

    return (
        str(author.actor_id) == OBSERVATION_COORDINATOR_ACTOR_ID
        and author.actor_type is ActorType.HARNESS
        and author.assurance is AuthorshipAssurance.HARNESS_OBSERVED
        and publication_channel
        in {PublicationChannel.HOOK_OBSERVED, PublicationChannel.ENGINE_DERIVED}
    )


def is_observation_authored(record: LedgerRecord) -> bool:
    """Recognize only service-stamped observation coordinator records."""

    return is_observation_authorship(record.author, record.publication_channel)


def accepted_record_to_json(record: LedgerRecord) -> JsonObject:
    """Serialize the exact nineteen-field accepted-event storage/wire record."""

    if type(record) is not AcceptedEvent and type(record) is not UnknownEvent:
        raise ProtocolValueError("accepted_record_shape_invalid")
    return JsonObject(
        {
            "protocol": record.protocol,
            "protocol_version": record.protocol_version,
            "event_id": record.event_id,
            "task_id": record.task_id,
            "session_id": record.session_id,
            "schema": {
                "name": record.schema.name,
                "version": record.schema.version,
            },
            "author": {
                "actor_id": record.author.actor_id,
                "actor_type": record.author.actor_type.value,
                "assurance": record.author.assurance.value,
            },
            "writer": {
                "writer_id": record.writer.writer_id,
                "sequence": str(record.writer.sequence),
                "previous_entry_digest": record.writer.previous_entry_digest,
            },
            "ledger": {
                "ingestion_sequence": str(record.ledger.ingestion_sequence),
                "previous_entry_digest": record.ledger.previous_entry_digest,
                "accepted_at": record.ledger.accepted_at.wire,
            },
            "operation_id": record.operation_id,
            "occurred_at": record.occurred_at.wire,
            "causal_parents": record.causal_parents,
            "publication_channel": record.publication_channel.value,
            "coverage": coverage_to_json(record.coverage),
            "payload_ref": {
                "object_id": record.payload_ref.object_id,
                "media_type": record.payload_ref.media_type,
                "plaintext_size": record.payload_ref.plaintext_size,
                "commitment": record.payload_ref.commitment,
                "encryption_format": record.payload_ref.encryption_format,
            },
            "redaction": record.redaction.value,
            "artifact_refs": record.artifact_refs,
            "evidence_refs": record.evidence_refs,
            "entry_digest": record.entry_digest,
        }
    )


def accepted_record_digest_preimage(record: LedgerRecord) -> JsonObject:
    """Return the exact accepted record with only its top-level digest removed."""

    full = accepted_record_to_json(record)
    return JsonObject(tuple((key, value) for key, value in full.items() if key != "entry_digest"))
