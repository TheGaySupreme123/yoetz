"""Deterministic in-memory reference implementation of the ledger boundary."""

from __future__ import annotations

import asyncio
import hashlib
import platform
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, cast

from yoetz.domain.events import (
    AcceptedEvent,
    ActionRecordedPayload,
    AssignmentRecordedPayload,
    CheckMode,
    CheckRecordedPayload,
    ClaimRecordedPayload,
    DecisionRecordedPayload,
    EventDraft,
    EventPayload,
    EventSchema,
    EvidenceRecordedPayload,
    FindingRecordedPayload,
    LedgerChain,
    LedgerRecord,
    NoObligationsReasonMismatch,
    ObligationPublishedPayload,
    ObligationResolutionMismatch,
    PayloadRef,
    PlanPublishedPayload,
    PlanRevisedPayload,
    PolicyVersion,
    ProjectionLocator,
    RedactionRecordedPayload,
    RedactionState,
    ResponseRecordedPayload,
    ResultRecordedPayload,
    SessionOpenedPayload,
    UnknownEvent,
    WriterChain,
    encode_payload,
    is_observation_authored,
    is_observation_authorship,
    media_type_for,
    public_error_for_no_obligations_reason_mismatch,
    public_error_for_obligation_resolution_mismatch,
)
from yoetz.domain.findings import (
    RankedFindings,
    SemanticProvenance,
    rank_key,
    semantic_provenance_to_json,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    EventId,
    Frontier,
    JsonValue,
    ObjectId,
    actor_id,
    event_id,
    finding_id,
    object_id,
    occurred_at_consistency,
    request_id,
    session_id,
    task_id,
    timestamp_from_datetime,
    writer_id,
)
from yoetz.kernel.deterministic_checks import (
    CaseAvailabilityFacts,
    DeterministicCase,
    UnavailableCapturedObject,
    build_deterministic_case,
    deterministic_case_from_json,
    deterministic_case_to_json,
)
from yoetz.kernel.finding_resolution import finding_is_resolved
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.projections import (
    PROJECTION_VERSION,
    ProjectionState,
    empty_projection_state,
    projection_digest,
    unanswered_finding_count,
)
from yoetz.kernel.receipt_capacity import (
    ReceiptCoverageCapacityExceeded,
    receipt_blocking_finding_count,
    validate_receipt_coverage_capacity,
)
from yoetz.kernel.reducers import invalidates_recorded_check, replay
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    AcceptedEventSummary,
    AppendCommand,
    AppendEntry,
    AppendResult,
    AppendWarning,
    AssignmentProjectionFilter,
    AttemptOutcome,
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    CheckSuspensionKind,
    CheckVersionSlice,
    EvidenceProjectionFilter,
    FindingProjectionPosition,
    FindingsProjectionFilter,
    FrozenCase,
    HistoryProjectionFilter,
    HistoryProjectionPosition,
    IdProjectionPosition,
    ObligationsProjectionFilter,
    OperationKind,
    OperationLease,
    OperationQuarantineCode,
    OperationRecord,
    OperationState,
    PendingVerdict,
    PendingVerdictKind,
    ProjectionItem,
    ProjectionPage,
    ProjectionQuery,
    ProjectionView,
    SelectedAttempt,
    SemanticAttemptHandle,
    SemanticAttemptRecord,
    SemanticDisclosureWait,
    SemanticJobRecord,
    StoredProjection,
)
from yoetz.ports.objects import (
    ObjectKind,
    ObjectMetadata,
    ObjectRef,
    ObjectSource,
    ObjectStorePort,
)
from yoetz.ports.runtime import OwnershipFence
from yoetz.protocol.canonical import (
    canonical_digest,
    canonical_encode,
    entry_digest,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    Coverage,
    PublicationChannel,
    coverage_for_channel,
    coverage_to_json,
    weakest,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import (
    CheckPolicyExecutionModel,
    CheckScopeModel,
    CoverageModel,
    FrontierModel,
    SemanticReason,
    SemanticStatus,
    StatusAssignmentItemModel,
    StatusCompactFindingModel,
    StatusCompactItemModel,
    StatusCompactObligationModel,
    StatusEvidenceItemModel,
    StatusFindingItemModel,
    StatusHistoryItemModel,
    StatusObligationItemModel,
    StatusResultItemModel,
    StatusStructuralSubjectStateModel,
    StatusVersionSliceModel,
)

__all__ = ["MemoryLedgerAdapter", "MemoryLedgerState", "compact_status_coverage"]

_GENESIS: Final = Frontier.genesis()
type _SummaryCode = Literal[
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


class _ErrorComponent(StrEnum):
    RECEIPT_COVERAGE = "receipt_coverage"


def _error(
    code: PublicErrorCode,
    *,
    retryable: bool = False,
    reason_code: str | None = None,
    quarantine_code: str | None = None,
    sequence: int | None = None,
    head_digest: str | None = None,
    component: _ErrorComponent | None = None,
    count: int | None = None,
    limit: int | None = None,
) -> PublicOperationError:
    details: dict[str, str | int] = {}
    if reason_code is not None:
        details["reason_code"] = reason_code
    if quarantine_code is not None:
        details["quarantine_code"] = quarantine_code
    if sequence is not None:
        details["sequence"] = sequence
    if head_digest is not None:
        details["head_digest"] = head_digest
    if component is not None:
        details["component"] = component
    if count is not None:
        details["count"] = count
    if limit is not None:
        details["limit"] = limit
    return PublicOperationError(code, code.value.lower(), retryable, safe_details=details)


def _frontier_conflict(head: Frontier) -> PublicOperationError:
    """Stale optimistic guard: surface the current head so callers can retry without a status trip."""

    return _error(
        PublicErrorCode.FRONTIER_CONFLICT,
        retryable=True,
        reason_code="frontier_changed",
        sequence=head.sequence,
        head_digest=head.head_digest,
    )


@dataclass(frozen=True, slots=True)
class _WriterState:
    task_id: str
    session_id: str
    next_sequence: int = 1
    head_digest: str = "genesis"


@dataclass(frozen=True, slots=True)
class _AttemptState:
    handle: SemanticAttemptHandle
    state: str
    result_object_ref: ObjectRef | None = None
    terminal_code: SemanticReason | None = None


@dataclass(frozen=True, slots=True)
class _CheckReservation:
    session_id: str
    expires_at: datetime


@dataclass(slots=True)
class MemoryLedgerState:
    """Copy-on-write task state shared by the reference adapters."""

    records: tuple[LedgerRecord, ...] = ()
    operations: dict[tuple[str, str], tuple[OperationRecord, AppendResult | None]] = field(
        default_factory=lambda: {}
    )
    writers: dict[str, _WriterState] = field(default_factory=lambda: {})
    projection: ProjectionState = field(default_factory=empty_projection_state)
    frozen_cases: dict[tuple[str, str], DeterministicCase] = field(default_factory=lambda: {})
    check_results: dict[tuple[str, str], CheckCommitResult] = field(default_factory=lambda: {})
    check_errors: dict[tuple[str, str], PublicOperationError] = field(default_factory=lambda: {})
    jobs: dict[str, SemanticJobRecord] = field(default_factory=lambda: {})
    job_by_case: dict[tuple[str, str, str], str] = field(default_factory=lambda: {})
    attempts: dict[str, _AttemptState] = field(default_factory=lambda: {})
    # Keyed by job_id: at most one disclosure wait per semantic job.
    disclosure_waits: dict[str, SemanticDisclosureWait] = field(default_factory=lambda: {})
    object_refs: dict[str, ObjectRef] = field(default_factory=lambda: {})
    # Transient freeze-acquisition holds: never persisted; a crash mid-freeze must drop them.
    check_reservations: dict[tuple[str, str], _CheckReservation] = field(default_factory=lambda: {})

    def restore_writer(
        self,
        writer: str,
        task: str,
        session: str,
        next_sequence: int,
        head_digest: str,
    ) -> None:
        self.writers[writer] = _WriterState(task, session, next_sequence, head_digest)


def _case_dependency_digest(case: DeterministicCase) -> str:
    return canonical_digest(
        {
            "availability": {
                "captured": tuple(
                    (item.source_event_id, item.object_id)
                    for item in case.availability.unavailable_captured_objects
                ),
                "events": case.availability.unavailable_event_ids,
            },
            "projection": projection_digest(case.projection),
        }
    )


async def _open_exact_object(objects: ObjectStorePort, expected: ObjectRef) -> bytes:
    resolved = await objects.resolve_verified(expected.object_id, expected.envelope_digest)
    if resolved != expected:
        raise ValueError("resume_object_descriptor_invalid")
    return b"".join([chunk async for chunk in objects.open_verified(resolved)])


def _strict_mapping(value: object, *, reason: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(reason)
    mapping = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in mapping):
        raise ValueError(reason)
    return cast(Mapping[str, object], value)


async def load_frozen_case_from_resume(
    objects: ObjectStorePort,
    record: OperationRecord,
    *,
    task: str,
    session: str,
) -> DeterministicCase:
    """Verified-decode the case reachable from the row's sole current pointer."""

    current = record.resume_object_ref
    if current is None or current.metadata.task_id != task:
        raise ValueError("resume_object_binding_invalid")
    raw = await _open_exact_object(objects, current)
    parsed = strict_json_parse(raw)
    if canonical_encode(parsed) != raw:
        raise ValueError("resume_object_noncanonical")
    source = _strict_mapping(parsed, reason="resume_object_shape_invalid")

    if current.metadata.kind is ObjectKind.DETERMINISTIC_RESULT:
        deterministic_result_keys = frozenset(
            {
                "schema_version",
                "request_id",
                "request_digest",
                "task_id",
                "session_id",
                "writer_id",
                "subject_frontier",
                "dependency_digest",
                "text_contract_digest",
                "prior_resume",
                "policy_executions",
                "assessments",
            }
        )
        # A checkpoint without the text-contract stamp predates issue #340. Its case pointer and
        # bindings are still authoritative for reopening the frozen case; only the application's
        # finding-text replay treats the missing stamp as superseded.
        if frozenset(source) not in {
            deterministic_result_keys,
            deterministic_result_keys - {"text_contract_digest"},
        }:
            raise ValueError("deterministic_result_shape_invalid")
        if "text_contract_digest" in source and type(source["text_contract_digest"]) is not str:
            raise ValueError("deterministic_result_shape_invalid")
        if (
            source["schema_version"] != "1.0.0"
            or source["request_id"] != record.operation_id
            or source["request_digest"] != record.request_digest
            or source["task_id"] != task
            or source["session_id"] != session
            or source["writer_id"] != record.writer_id
        ):
            raise ValueError("deterministic_result_binding_invalid")
        pointer = _strict_mapping(
            source["prior_resume"], reason="deterministic_result_pointer_invalid"
        )
        if frozenset(pointer) != frozenset({"object_id", "envelope_digest", "commitment"}) or any(
            type(pointer[key]) is not str for key in pointer
        ):
            raise ValueError("deterministic_result_pointer_invalid")
        prior = await objects.resolve_verified(
            cast(str, pointer["object_id"]), cast(str, pointer["envelope_digest"])
        )
        if (
            prior.metadata.kind is not ObjectKind.CHECK_RESUME
            or prior.metadata.task_id != task
            or prior.commitment != pointer["commitment"]
        ):
            raise ValueError("deterministic_result_pointer_invalid")
        raw = await _open_exact_object(objects, prior)
        parsed = strict_json_parse(raw)
        if canonical_encode(parsed) != raw:
            raise ValueError("resume_object_noncanonical")
        resume_source = _strict_mapping(parsed, reason="resume_object_shape_invalid")
        expected_frontier = source["subject_frontier"]
        expected_dependency = source["dependency_digest"]
    elif current.metadata.kind is ObjectKind.CHECK_RESUME:
        resume_source = source
        expected_frontier = None
        expected_dependency = None
    else:
        raise ValueError("resume_object_kind_invalid")

    if frozenset(resume_source) != frozenset(
        {
            "schema_version",
            "task_id",
            "session_id",
            "writer_id",
            "request_id",
            "request_digest",
            "frontier",
            "dependency_digest",
            "case_digest",
            "case",
        }
    ):
        raise ValueError("resume_object_shape_invalid")
    if (
        resume_source["schema_version"] != "1.0.0"
        or resume_source["task_id"] != task
        or resume_source["session_id"] != session
        or resume_source["writer_id"] != record.writer_id
        or resume_source["request_id"] != record.operation_id
        or resume_source["request_digest"] != record.request_digest
    ):
        raise ValueError("resume_object_binding_invalid")
    case = deterministic_case_from_json(cast(JsonValue, resume_source["case"]))
    case_json = deterministic_case_to_json(case)
    dependency = _case_dependency_digest(case)
    if (
        resume_source["case_digest"] != canonical_digest(case_json)
        or resume_source["frontier"] != case.frontier.as_wire()
        or resume_source["dependency_digest"] != dependency
        or (expected_frontier is not None and expected_frontier != case.frontier.as_wire())
        or (expected_dependency is not None and expected_dependency != dependency)
    ):
        raise ValueError("resume_object_binding_invalid")
    return case


def quarantine_resume_object_invalid(
    record: OperationRecord, *, now: datetime
) -> tuple[OperationRecord, PublicOperationError]:
    """Terminalize one pending check whose durable resume pointer cannot be rehydrated.

    The verdict belongs to this operation id, not the ledger: recovery keeps the event
    chain, projection, and every other operation live, and same-request replay raises
    the stored error instead of latching bundle ``STORAGE_CORRUPT``.
    """

    error = _error(
        PublicErrorCode.STORAGE_CORRUPT,
        quarantine_code=OperationQuarantineCode.OPERATION_RESUME_OBJECT_INVALID.value,
    )
    canonical = canonical_encode(
        {
            "code": error.code.value,
            "message": str(error),
            "retryable": error.retryable,
            "safe_details": dict(error.safe_details),
        }
    )
    quarantined = replace(
        record,
        state=OperationState.QUARANTINED,
        phase=CheckPhase.TERMINAL,
        owner_generation=None,
        lease_owner_id=None,
        lease_generation=None,
        lease_expires_at=None,
        resume_object_ref=None,
        result_canonical=canonical,
        result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        result_locator=None,
        quarantine_code=OperationQuarantineCode.OPERATION_RESUME_OBJECT_INVALID,
        terminal_at=now,
        suspension_kind=None,
    )
    return quarantined, error


def _now(clock: ClockPort | None) -> datetime:
    value = datetime.now(UTC) if clock is None else clock.now_utc()
    if value.tzinfo is None or value.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("clock_value_invalid")
    return value


def _logical_key(payload: EventPayload, event_id: str) -> str | None:
    if type(payload) in {PlanPublishedPayload, PlanRevisedPayload}:
        return str(cast(PlanPublishedPayload | PlanRevisedPayload, payload).plan_version)
    if type(payload) is ObligationPublishedPayload:
        return payload.obligation_id
    if type(payload) in {AssignmentRecordedPayload, DecisionRecordedPayload, CheckRecordedPayload}:
        return event_id
    if type(payload) is ActionRecordedPayload:
        return payload.action_id
    if type(payload) is ResultRecordedPayload:
        return payload.result_id
    if type(payload) is EvidenceRecordedPayload:
        return payload.evidence_id
    if type(payload) is ClaimRecordedPayload:
        return payload.claim_id
    if type(payload) is FindingRecordedPayload:
        return payload.finding_id
    if type(payload) is ResponseRecordedPayload:
        return payload.finding_id
    return None


def _redaction_targets(
    payload: EventPayload,
) -> tuple[tuple[EventId, ...], tuple[ObjectId, ...]]:
    if type(payload) is RedactionRecordedPayload:
        return payload.target_event_ids, payload.target_object_ids
    return (), ()


def _record_preimage(
    command: AppendCommand,
    index: int,
    *,
    ingestion_sequence: int,
    writer_sequence: int,
    previous_ledger_digest: str,
    previous_writer_digest: str,
    accepted_at: datetime,
) -> LedgerRecord:
    item = command.entries[index]
    draft = item.draft
    known_payload = item.projection_status == "projected"
    payload_json: JsonValue = (
        encode_payload(cast(EventPayload, draft.payload))
        if known_payload
        else cast(JsonValue, draft.payload)
    )
    payload_digest = canonical_digest(payload_json)
    logical_key: str | None = None
    target_events: tuple[EventId, ...] = ()
    target_objects: tuple[ObjectId, ...] = ()
    if known_payload:
        typed_payload = cast(EventPayload, draft.payload)
        logical_key = _logical_key(typed_payload, draft.event_id)
        target_events, target_objects = _redaction_targets(typed_payload)
    locator = ProjectionLocator(
        draft.schema,
        logical_key,
        payload_digest,
        target_events,
        target_objects,
    )
    timestamp = timestamp_from_datetime(accepted_at)
    task = task_id(command.task_id)
    session = session_id(command.session_id)
    writer = WriterChain(writer_id(command.writer_id), writer_sequence, previous_writer_digest)
    ledger = LedgerChain(ingestion_sequence, previous_ledger_digest, timestamp)
    operation = request_id(command.operation_id)
    payload_ref = PayloadRef(
        object_id(item.payload_object.object_id),
        item.media_type,
        item.plaintext_size,
        item.payload_commitment,
        item.payload_object.encryption_format,
    )
    # The digest preimage has exactly the structural fields serialized by the
    # domain record, so construct once with a temporary mapping rather than a
    # record carrying an invalid placeholder digest.
    from yoetz.protocol.coverage import coverage_to_json

    preimage = {
        "protocol": "yoetz.event",
        "protocol_version": "0.1",
        "event_id": draft.event_id,
        "task_id": command.task_id,
        "session_id": command.session_id,
        "schema": {"name": draft.schema.name, "version": draft.schema.version},
        "author": {
            "actor_id": item.author.actor_id,
            "actor_type": item.author.actor_type.value,
            "assurance": item.author.assurance.value,
        },
        "writer": {
            "writer_id": command.writer_id,
            "sequence": str(writer_sequence),
            "previous_entry_digest": previous_writer_digest,
        },
        "ledger": {
            "ingestion_sequence": str(ingestion_sequence),
            "previous_entry_digest": previous_ledger_digest,
            "accepted_at": timestamp.wire,
        },
        "operation_id": command.operation_id,
        "occurred_at": draft.occurred_at.wire,
        "causal_parents": draft.causal_parents,
        "publication_channel": item.publication_channel.value,
        "coverage": coverage_to_json(item.coverage),
        "payload_ref": {
            "object_id": item.payload_object.object_id,
            "media_type": item.media_type,
            "plaintext_size": item.plaintext_size,
            "commitment": item.payload_commitment,
            "encryption_format": item.payload_object.encryption_format,
        },
        "redaction": "present",
        "artifact_refs": draft.artifact_refs,
        "evidence_refs": draft.evidence_refs,
    }
    digest = entry_digest(preimage)
    if item.projection_status == "projected":
        record: LedgerRecord = AcceptedEvent(
            event_id=draft.event_id,
            task_id=task,
            session_id=session,
            schema=draft.schema,
            author=item.author,
            writer=writer,
            ledger=ledger,
            operation_id=operation,
            occurred_at=draft.occurred_at,
            causal_parents=draft.causal_parents,
            publication_channel=item.publication_channel,
            coverage=item.coverage,
            payload_ref=payload_ref,
            redaction=RedactionState.PRESENT,
            artifact_refs=draft.artifact_refs,
            evidence_refs=draft.evidence_refs,
            entry_digest=digest,
            payload=cast(EventPayload, draft.payload),
            projection_locator=locator,
        )
    else:
        record = UnknownEvent(
            event_id=draft.event_id,
            task_id=task,
            session_id=session,
            schema=draft.schema,
            author=item.author,
            writer=writer,
            ledger=ledger,
            operation_id=operation,
            occurred_at=draft.occurred_at,
            causal_parents=draft.causal_parents,
            publication_channel=item.publication_channel,
            coverage=item.coverage,
            payload_ref=payload_ref,
            redaction=RedactionState.PRESENT,
            artifact_refs=draft.artifact_refs,
            evidence_refs=draft.evidence_refs,
            entry_digest=digest,
            payload=cast(JsonValue, draft.payload),
            projection_locator=locator,
            canonical_payload_digest=payload_digest,
        )
    return record


def _result_bytes(result: AppendResult) -> bytes:
    value = {
        "accepted": tuple(
            {
                "entry_digest": row.entry_digest,
                "event_id": row.event_id,
                "ingestion_sequence": str(row.ingestion_sequence),
                "projection_status": row.projection_status,
                "writer_sequence": str(row.writer_sequence),
            }
            for row in result.accepted
        ),
        "result_frontier": result.result_frontier.as_wire(),
        "subject_frontier": result.subject_frontier.as_wire(),
        "warnings": tuple(item.value for item in result.warnings),
    }
    return canonical_encode(value)


def build_append_operation_record(
    command: AppendCommand, result: AppendResult, now: datetime
) -> OperationRecord:
    canonical = _result_bytes(result)
    first = result.accepted[0].ingestion_sequence
    last = result.accepted[-1].ingestion_sequence
    from yoetz.ports.ledger import OperationResultLocator

    return OperationRecord(
        writer_id=command.writer_id,
        operation_id=command.operation_id,
        operation_kind=command.operation_kind,
        request_digest=command.request_digest,
        state=OperationState.COMPLETE,
        phase=CheckPhase.TERMINAL,
        owner_generation=None,
        lease_owner_id=None,
        lease_generation=None,
        lease_expires_at=None,
        resume_object_ref=None,
        result_canonical=canonical,
        result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        result_locator=OperationResultLocator(
            first,
            last,
            command.result_object_ref,
            tuple(sorted((row.event_id for row in result.accepted), key=str.encode)),
        ),
        quarantine_code=None,
        terminal_at=now,
    )


def _finding_position(finding: FindingRecordedPayload) -> FindingProjectionPosition:
    key = rank_key(finding)
    return FindingProjectionPosition(
        key[0],
        key[1] == -1,
        -key[2],
        -key[3],
        -key[4],
        -key[5],
        key[6] == -1,
        key[7],
        key[8],
        finding.finding_id,
    )


def _finding_position_key(value: FindingProjectionPosition) -> tuple[object, ...]:
    return (
        value.priority,
        -int(value.actionable),
        -value.artifact_ordinal,
        -value.immutability_ordinal,
        -value.freshness_ordinal,
        -value.authorship_ordinal,
        -int(value.real_check_present),
        value.known_gap_count,
        value.origin_ordinal,
        value.finding_id.encode("ascii"),
    )


def _status_gap_codes(markers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({marker.split(":", 1)[0] for marker in markers}, key=str.encode))


def compact_status_coverage(
    records: tuple[LedgerRecord, ...], projection: ProjectionState
) -> Coverage:
    """Fold the applicable check's coverage into the compact-status baseline.

    The newest record's envelope (often an engine-derived ``receipt_recorded``) hardcodes
    ``check_types=(none,)`` even immediately after a rich check; the check's real coverage
    lives in its payload. The receipt applicability rule decides whether the projected latest
    check still covers this state: it does unless cooperative material work or an observation
    finding superseded it.

    Shared by the memory status view and the durable SQLite ``p1_projection_state`` mirror so
    a restart cannot disagree with the in-process projection about what was checked, and it uses
    the same predicate as the receipt so status and receipts cannot disagree either.
    """

    baseline = records[-1].coverage
    latest = projection.latest_tested_state
    if latest is None:
        return baseline
    check_record = next(
        (record for record in records if record.event_id == latest.source_check_event_id),
        None,
    )
    if check_record is None or type(check_record.payload) is not CheckRecordedPayload:
        return baseline
    if any(
        invalidates_recorded_check(
            record, check_record.ledger.ingestion_sequence, latest.returned_finding_ids
        )
        for record in records
    ):
        return baseline
    return weakest(baseline, check_record.payload.coverage)


def _obligation_item(
    obligation: str,
    record: object,
    assigned_actor_ids: tuple[str, ...],
    attempted_items: frozenset[str],
) -> StatusObligationItemModel:
    from yoetz.kernel.projections import ObligationProjectionRecord

    typed = cast(ObligationProjectionRecord, record)
    assert typed.payload is not None
    requested_items = tuple(
        {"item_kind": item.item_kind.value, "value": item.value}
        for item in typed.payload.requested_items
    )
    unattempted_items = tuple(
        item for item in requested_items if item["value"] not in attempted_items
    )
    values = dict(
        obligation_id=obligation,
        status=typed.payload.status.value,
        description=typed.payload.description,
        evidence_expectation=typed.payload.evidence_expectation,
        source_refs=typed.payload.source_refs,
        assigned_actor_ids=assigned_actor_ids,
        evidence_refs=typed.payload.resolution_evidence_refs,
        requested_items=requested_items,
        unattempted_items=unattempted_items,
        revision_event_id=None,
    )
    if typed.payload.acceptance_criteria is not None:
        values["acceptance_criteria"] = typed.payload.acceptance_criteria
    return StatusObligationItemModel.model_validate(values)


def _compact_obligation_item(obligation: str, record: object) -> StatusCompactObligationModel:
    from yoetz.kernel.projections import ObligationProjectionRecord

    typed = cast(ObligationProjectionRecord, record)
    assert typed.payload is not None
    values = dict(
        obligation_id=obligation,
        description=typed.payload.description,
        evidence_expectation=typed.payload.evidence_expectation,
    )
    if typed.payload.acceptance_criteria is not None:
        values["acceptance_criteria"] = typed.payload.acceptance_criteria
    return StatusCompactObligationModel.model_validate(values)


def _projection_items(
    view: ProjectionView,
    projection: ProjectionState,
    records: tuple[LedgerRecord, ...],
    *,
    task: str,
    session: str,
) -> tuple[ProjectionItem, ...]:
    """Project one immutable state into the exact bounded public row types."""

    if view is ProjectionView.CANDIDATE_FINDINGS:
        return ()
    if view is ProjectionView.HISTORY:
        return tuple(
            StatusHistoryItemModel(
                event_id=row.event_id,
                schema_name=row.schema.name,
                schema_version=row.schema.version,
                actor_id=row.author.actor_id,
                publication_channel=row.publication_channel.value,
                ingestion_sequence=str(row.ledger.ingestion_sequence),
                occurred_at=row.occurred_at.wire,
                accepted_at=row.ledger.accepted_at.wire,
                occurred_at_consistency=occurred_at_consistency(
                    row.occurred_at, row.ledger.accepted_at
                ),
                projection_status=(
                    "unknown_unprojected" if type(row) is UnknownEvent else "projected"
                ),
                summary_code=cast(
                    _SummaryCode,
                    "opaque_unknown" if type(row) is UnknownEvent else row.schema.name,
                ),
            )
            for row in records
            if row.session_id == session
        )
    if view is ProjectionView.ASSIGNMENT:
        handed_off = {
            payload.handoff_of
            for record in projection.assignments.values()
            if (payload := record.payload) is not None and payload.handoff_of is not None
        }
        result: list[ProjectionItem] = []
        for event, record in sorted(
            projection.assignments.items(), key=lambda item: item[0].encode()
        ):
            payload = record.payload
            if payload is None:
                continue
            obligations_resolved = all(
                (target := projection.obligations.get(obligation)) is not None
                and target.payload is not None
                and target.payload.status.value == "resolved"
                for obligation in payload.obligation_ids
            )
            result.append(
                StatusAssignmentItemModel(
                    assignment_event_id=event,
                    actor_id=payload.assignee_actor_id,
                    obligation_ids=payload.obligation_ids,
                    scope_refs=payload.obligation_ids,
                    resolved=event in handed_off or obligations_resolved,
                )
            )
        return tuple(result)
    if view is ProjectionView.OBLIGATIONS:
        attempted_items = frozenset(
            item
            for action in projection.actions.values()
            if action.payload is not None
            for item in action.payload.attempted_items
        )
        actors: dict[str, set[str]] = {}
        for assignment in projection.assignments.values():
            if assignment.payload is None:
                continue
            for obligation in assignment.payload.obligation_ids:
                actors.setdefault(obligation, set()).add(assignment.payload.assignee_actor_id)
        return tuple(
            _obligation_item(
                obligation,
                record,
                tuple(sorted(actors.get(obligation, ()), key=str.encode)),
                attempted_items,
            )
            for obligation, record in sorted(
                projection.obligations.items(), key=lambda item: item[0].encode()
            )
            if record.payload is not None
        )
    if view is ProjectionView.RESULTS:
        return tuple(
            StatusResultItemModel(
                result_id=result,
                source_event_id=record.source_event_id,
                payload_available=record.payload is not None,
                outcome=None if record.payload is None else record.payload.outcome.value,
                action_id=None if record.payload is None else record.payload.action_id,
                evidence_refs=() if record.payload is None else record.payload.evidence_refs,
            )
            for result, record in sorted(
                projection.results.items(), key=lambda item: item[0].encode()
            )
        )
    if view is ProjectionView.EVIDENCE:
        evidence_items: list[ProjectionItem] = []
        for evidence, record in sorted(
            projection.evidence.items(), key=lambda item: item[0].encode()
        ):
            payload = record.payload
            if payload is None:
                continue
            state = payload.subject_state
            subject_state = None
            if state is not None and (
                state.tree_digest is not None or state.diff_digest is not None
            ):
                # Optional non-null digests must be omitted when unset, never passed as null —
                # ``StatusStructuralSubjectStateModel`` rejects explicit null leaves.
                subject_state_values: dict[str, object] = {}
                if state.tree_digest is not None:
                    subject_state_values["tree_digest"] = state.tree_digest
                if state.diff_digest is not None:
                    subject_state_values["diff_digest"] = state.diff_digest
                subject_state = StatusStructuralSubjectStateModel.model_validate(
                    subject_state_values
                )
            freshness = projection.freshness.value
            if not record.object_available and freshness == "current":
                freshness = "redacted_gap"
            evidence_items.append(
                StatusEvidenceItemModel(
                    evidence_id=evidence,
                    strength=payload.strength.value,
                    freshness=freshness,
                    available=record.object_available,
                    description=payload.description,
                    reference=payload.reference,
                    captured_object_id=payload.captured_object_id,
                    content_digest=payload.content_digest,
                    subject_state=subject_state,
                )
            )
        return tuple(evidence_items)
    if view is ProjectionView.FINDINGS:
        finding_items: list[ProjectionItem] = []
        ordered = sorted(
            (
                record.payload
                for record in projection.findings.values()
                if record.payload is not None
            ),
            key=rank_key,
        )
        for finding in ordered:
            response_record = projection.responses.get(finding.finding_id)
            response = None if response_record is None else response_record.payload
            finding_items.append(
                StatusFindingItemModel(
                    finding_id=finding.finding_id,
                    kind=finding.kind.value,
                    origin=finding.origin.value,
                    priority=finding.priority,
                    summary=finding.summary,
                    detail=finding.detail,
                    subject_refs=finding.subject_refs,
                    policy_id=cast(
                        Literal["research-evidence", "work-integrity"], finding.policy_id
                    ),
                    policy_version=cast(Literal["0.1.0"], finding.policy_version),
                    subject_frontier=FrontierModel.model_validate(
                        dict(finding.subject_frontier.as_wire())
                    ),
                    coverage=CoverageModel.model_validate(coverage_to_json(finding.coverage)),
                    provenance=(
                        None
                        if finding.provenance is None
                        else semantic_provenance_to_json(finding.provenance)
                    ),
                    disposition="none" if response is None else response.disposition.value,
                    resolved=finding_is_resolved(projection, finding.finding_id),
                    response_event_id=(
                        None if response_record is None else response_record.source_event_id
                    ),
                    reason=None if response is None else response.reason,
                    waiver_scope=(
                        None
                        if response is None or response.waiver_scope is None
                        else response.waiver_scope.value
                    ),
                    waiver_expiry=(
                        None
                        if response is None or response.waiver_expiry is None
                        else response.waiver_expiry.wire
                    ),
                )
            )
        return tuple(finding_items)
    if view is ProjectionView.COMPACT:
        opened = next(
            (
                row.payload
                for row in records
                if type(row) is AcceptedEvent and type(row.payload) is SessionOpenedPayload
            ),
            None,
        )
        if opened is None:
            return ()
        scope = current_plan_scope(projection.plans, projection.coverage_gaps)
        scope_refs = scope.effective_obligation_refs
        open_obligations = (
            ()
            if scope_refs is None
            else tuple(
                _compact_obligation_item(key, record)
                for key in scope_refs
                if (record := projection.obligations.get(key)) is not None
                and record.payload is not None
                and record.payload.status.value == "open"
            )
        )
        unanswered_findings = tuple(
            StatusCompactFindingModel(
                finding_id=finding.finding_id,
                kind=finding.kind.value,
                priority=finding.priority,
                summary=finding.summary,
                detail=finding.detail,
            )
            for finding in sorted(
                (
                    value.payload
                    for key, value in projection.findings.items()
                    if value.payload is not None and key not in projection.responses
                ),
                key=rank_key,
            )
        )
        unanswered_count = unanswered_finding_count(projection)
        receipt_blocking_count = receipt_blocking_finding_count(projection)
        declared_count = (
            None
            if scope_refs is None
            else (0 if not scope.has_plan else scope.declared_obligation_count)
        )
        open_count = (
            None
            if scope_refs is None
            else sum(
                (record := projection.obligations.get(obligation)) is None
                or record.payload is None
                or record.payload.status.value == "open"
                for obligation in scope_refs
            )
        )
        item_coverage = compact_status_coverage(records, projection)
        # The scalar and the coverage beside it are two derivations of one fact: the projection
        # folds events, the coverage re-folds the applicable check over the newest record's
        # envelope. An import-channel envelope reads `partial` where the projection reads
        # `current`, so reporting the projection scalar raw lets the headline read cleaner than
        # the coverage it summarizes. Report the weaker of the two (issue #307).
        item_freshness = min(projection.freshness, item_coverage.ledger_freshness)
        return (
            StatusCompactItemModel(
                task_id=task,
                session_id=session,
                task_title=opened.task_title,
                current_plan_event_id=scope.current_plan_event_id,
                declared_obligation_count=(None if declared_count is None else str(declared_count)),
                no_obligations_reason=(
                    None
                    if scope.no_obligations_reason is None
                    else scope.no_obligations_reason.value
                ),
                open_obligation_count=None if open_count is None else str(open_count),
                unanswered_finding_count=str(unanswered_count),
                receipt_blocking_finding_count=str(receipt_blocking_count),
                open_obligations=open_obligations[:10],
                unanswered_findings=unanswered_findings[:10],
                freshness=item_freshness.value,
                coverage=CoverageModel.model_validate(coverage_to_json(item_coverage)),
                gaps=_status_gap_codes(projection.coverage_gaps),
            ),
        )
    if view is ProjectionView.VERSIONS:
        return (
            StatusVersionSliceModel(
                protocol_version="0.1",
                engine_version="0.1.0",
                projection_version="0.1.0",
                object_format="yoetz-object/1",
                storage_schema="1",
                python_version=platform.python_version(),
                apsw_version="3.51.0.0",
                sqlite_version="3.51.0",
                sqlite_source_id="runtime-verified-by-connection-gate",
                policy_packs=("research-evidence/0.1.0", "work-integrity/0.1.0"),
                provider_profiles=(),
            ),
        )
    raise _error(PublicErrorCode.INVALID_REQUEST)


class MemoryLedgerAdapter:
    """The deterministic conformance oracle for task-ledger operations."""

    def __init__(
        self,
        *,
        task_id: str,
        ownership_fence: OwnershipFence,
        state: MemoryLedgerState,
        import_state: object,
        transaction_lock: asyncio.Lock,
        clock: ClockPort | None = None,
        ids: IdPort | None = None,
        objects: ObjectStorePort | None = None,
    ) -> None:
        if (
            type(ownership_fence) is not OwnershipFence
            or type(state) is not MemoryLedgerState
            or type(transaction_lock) is not asyncio.Lock
        ):
            raise TypeError("memory_ledger_construction_invalid")
        if ids is None or objects is None:
            raise TypeError("memory_ledger_dependencies_required")
        self._task_id = task_id
        self._fence = ownership_fence
        self._state = state
        self._import_state = import_state
        self._lock = transaction_lock
        self._clock = clock
        self._ids = ids
        self._objects = objects

    def _pending_import(self, session_id: str) -> bool:
        method = getattr(self._import_state, "has_pending_import", None)
        if callable(method):
            return bool(method(session_id))
        pending = cast(tuple[str, ...], getattr(self._import_state, "pending_session_ids", ()))
        return session_id in pending

    def _reservation_valid(self, command: AppendCommand) -> bool:
        method = getattr(self._import_state, "publication_reservation", None)
        if callable(method):
            reservation = method(command.writer_id, command.operation_id)
            if reservation is None:
                return False
            source = getattr(reservation, "source_identity_digest", None)
            ordinal = getattr(reservation, "publication_ordinal", None)
            jobs = cast(Mapping[str, object], getattr(self._import_state, "jobs", {}))
            job = jobs.get(cast(str, source))
            if job is None:
                # The SQLite preparation shim deliberately exposes only the
                # already-verified permanent reservation.
                return type(reservation).__name__ == "_Reservation"
            if (
                getattr(job, "session_id", None) != command.session_id
                or getattr(job, "publishing_writer_id", None) != command.writer_id
            ):
                return False
            event_ids = tuple(item.draft.event_id for item in command.entries)
            batches = cast(
                Mapping[tuple[str, int], object], getattr(self._import_state, "batches", {})
            )
            batch = batches.get((cast(str, source), cast(int, ordinal)))
            if batch is not None:
                return (
                    getattr(batch, "request_id", None) == command.operation_id
                    and getattr(batch, "event_ids", None) == event_ids
                    and getattr(batch, "result", None) is None
                )
            return (
                getattr(job, "report_request_id", None) == command.operation_id
                and len(event_ids) == 1
                and getattr(job, "report_event_id", None) == event_ids[0]
            )
        reservations = getattr(self._import_state, "publication_requests", {})
        return (command.writer_id, command.operation_id) in cast(
            Mapping[tuple[str, str], object], reservations
        )

    def _lease_for(self, record: OperationRecord) -> OperationLease:
        key = (record.writer_id, record.operation_id)
        case = self._state.frozen_cases.get(key)
        if (
            case is None
            or record.owner_generation is None
            or record.lease_owner_id is None
            or record.lease_generation is None
            or record.lease_expires_at is None
        ):
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        return OperationLease(
            record.writer_id,
            record.operation_id,
            next(
                writer.session_id
                for writer_id, writer in self._state.writers.items()
                if writer_id == record.writer_id
            ),
            record.phase,
            record.owner_generation,
            record.lease_owner_id,
            record.lease_generation,
            record.lease_expires_at,
            case.frontier,
            _case_dependency_digest(case),
        )

    def _require_lease(self, lease: OperationLease) -> OperationRecord:
        row = self._state.operations.get((lease.writer_id, lease.operation_id))
        if row is None:
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
        record = row[0]
        if (
            record.state is not OperationState.PENDING
            or self._lease_for(record) != lease
            or lease.owner_generation != str(self._fence.owner_generation)
            or lease.lease_owner_id != self._fence.service_instance_id
            or lease.lease_expires_at <= _now(self._clock)
        ):
            raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
        return record

    def _has_active_frozen_case_unlocked(self, session_id: str) -> bool:
        """True when observation must defer because a check is actively frozen.

        A standing-grant park expires the lease and may never resume, so
        ``suspension_kind=repository_grant`` is not a barrier: observation
        continues and the same request re-acquires the barrier on replay.
        """

        now = _now(self._clock)
        if any(
            reservation.session_id == session_id and reservation.expires_at > now
            for reservation in self._state.check_reservations.values()
        ):
            return True
        return any(
            (writer := self._state.writers.get(case_writer_id)) is not None
            and writer.session_id == session_id
            and (operation := self._state.operations.get((case_writer_id, operation_id)))
            is not None
            and operation[0].state is OperationState.PENDING
            and operation[0].suspension_kind is not CheckSuspensionKind.REPOSITORY_GRANT
            for case_writer_id, operation_id in self._state.frozen_cases
        )

    async def has_active_frozen_case(self, session_id: str) -> bool:
        async with self._lock:
            return self._has_active_frozen_case_unlocked(session_id)

    def _observation_only_since_unlocked(
        self, sequence: int, *, finding_free: bool = False
    ) -> bool:
        """True when every record past ``sequence`` is observation-authored.

        ``finding_free`` additionally rejects a suffix that materialized findings: a caller
        pinned to the older frontier would otherwise leave those findings silently uncovered.
        """

        return all(
            is_observation_authored(record)
            and not (finding_free and record.schema.name == "finding_recorded")
            for record in self._state.records
            if record.ledger.ingestion_sequence > sequence
        )

    def _projection_anchored_unlocked(self, projection: ProjectionState) -> bool:
        """True when ``projection`` is the exact replay of this chain through its frontier."""

        records = tuple(
            row
            for row in self._state.records
            if row.ledger.ingestion_sequence <= projection.frontier
        )
        try:
            return replay(records) == projection
        except ValueError as exc:
            raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc

    def _replace_pending_record(
        self,
        record: OperationRecord,
        *,
        phase: CheckPhase | None = None,
        resume_object_ref: ObjectRef | None = None,
    ) -> tuple[OperationRecord, OperationLease]:
        updated = replace(
            record,
            phase=record.phase if phase is None else phase,
            resume_object_ref=(
                record.resume_object_ref if resume_object_ref is None else resume_object_ref
            ),
            owner_generation=str(self._fence.owner_generation),
            lease_owner_id=self._fence.service_instance_id,
            lease_generation=cast(int, record.lease_generation) + 1,
            lease_expires_at=_now(self._clock) + timedelta(seconds=60),
            suspension_kind=None,
        )
        key = (record.writer_id, record.operation_id)
        self._state.operations[key] = (updated, None)
        return updated, self._lease_for(updated)

    async def append_batch(self, command: AppendCommand) -> AppendResult:
        if command.task_id != self._task_id:
            raise _error(PublicErrorCode.EVENT_INVALID)
        accepted_at = _now(self._clock)
        key = (command.writer_id, command.operation_id)
        importer_authored = any(
            entry.author.actor_type is ActorType.IMPORTER
            or entry.publication_channel is PublicationChannel.CODEX_JSONL_IMPORT
            for entry in command.entries
        )
        observation_authored = all(
            is_observation_authorship(entry.author, entry.publication_channel)
            for entry in command.entries
        )
        async with self._lock:
            prior = self._state.operations.get(key)
            if prior is not None:
                operation, prior_result = prior
                if operation.request_digest != command.request_digest:
                    raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
                if operation.state is OperationState.COMPLETE and prior_result is not None:
                    return replace(prior_result, outcome="replayed")
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

            if observation_authored and self._has_active_frozen_case_unlocked(command.session_id):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)

            if command.operation_kind is OperationKind.RECEIPT and self._pending_import(
                command.session_id
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            if importer_authored and not self._reservation_valid(command):
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            subject = Frontier(self._state.projection.frontier, self._state.projection.head_digest)
            # A receipt's case replays the prefix at its subject frontier, so a finding drained
            # past that frontier would be silently uncovered; receipt appends alone treat a
            # finding-bearing suffix as a real conflict.
            if (
                command.expected_frontier is not None
                and command.expected_frontier != subject.sequence
                and not (
                    command.expected_frontier < subject.sequence
                    and self._observation_only_since_unlocked(
                        command.expected_frontier,
                        finding_free=command.operation_kind is OperationKind.RECEIPT,
                    )
                )
            ):
                raise _frontier_conflict(subject)
            writer = self._state.writers.get(command.writer_id)
            if writer is None:
                writer = _WriterState(command.task_id, command.session_id)
            elif writer.task_id != command.task_id or writer.session_id != command.session_id:
                raise _error(PublicErrorCode.EVENT_INVALID)
            snapshot_records = self._state.records
            seen = {record.event_id for record in snapshot_records}
            prior_batch: set[str] = set()
            new_records: list[LedgerRecord] = []
            summaries: list[AcceptedEventSummary] = []
            previous_ledger = subject.head_digest
            previous_writer = writer.head_digest
            for offset, item in enumerate(command.entries):
                if item.draft.event_id in seen or item.draft.event_id in prior_batch:
                    raise _error(PublicErrorCode.EVENT_INVALID)
                if any(
                    parent not in seen and parent not in prior_batch
                    for parent in item.draft.causal_parents
                ):
                    raise _error(PublicErrorCode.EVENT_INVALID)
                ingestion = subject.sequence + offset + 1
                writer_sequence = writer.next_sequence + offset
                try:
                    record = _record_preimage(
                        command,
                        offset,
                        ingestion_sequence=ingestion,
                        writer_sequence=writer_sequence,
                        previous_ledger_digest=previous_ledger,
                        previous_writer_digest=previous_writer,
                        accepted_at=accepted_at,
                    )
                except ValueError as exc:
                    raise _error(PublicErrorCode.EVENT_INVALID) from exc
                new_records.append(record)
                summaries.append(
                    AcceptedEventSummary(
                        record.event_id,
                        ingestion,
                        writer_sequence,
                        record.entry_digest,
                        item.projection_status,
                    )
                )
                previous_ledger = record.entry_digest
                previous_writer = record.entry_digest
                prior_batch.add(record.event_id)
            proposed = snapshot_records + tuple(new_records)
            try:
                projection = replay(proposed)
            except NoObligationsReasonMismatch as exc:
                draft_index: int | None = None
                if exc.event_id is not None:
                    for index, item in enumerate(command.entries):
                        if item.draft.event_id == exc.event_id:
                            draft_index = index
                            break
                raise public_error_for_no_obligations_reason_mismatch(
                    exc, event_index=draft_index
                ) from exc
            except ObligationResolutionMismatch as exc:
                draft_index = None
                if exc.event_id is not None:
                    for index, item in enumerate(command.entries):
                        if item.draft.event_id == exc.event_id:
                            draft_index = index
                            break
                raise public_error_for_obligation_resolution_mismatch(
                    exc, event_index=draft_index
                ) from exc
            except ValueError as exc:
                raise _error(PublicErrorCode.EVENT_INVALID) from exc
            try:
                validate_receipt_coverage_capacity(projection, proposed)
            except ReceiptCoverageCapacityExceeded as exc:
                raise _error(
                    PublicErrorCode.LIMIT_EXCEEDED,
                    component=_ErrorComponent.RECEIPT_COVERAGE,
                    count=exc.count,
                    limit=exc.limit,
                ) from exc
            result_frontier = Frontier(projection.frontier, projection.head_digest)
            warnings = (
                (AppendWarning.UNKNOWN_EVENT_SCHEMA_PRESERVED,)
                if any(type(record) is UnknownEvent for record in new_records)
                else ()
            )
            result = AppendResult("accepted", tuple(summaries), subject, result_frontier, warnings)
            operation = build_append_operation_record(command, result, accepted_at)
            if command.operation_kind is OperationKind.RECEIPT and self._pending_import(
                command.session_id
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            if importer_authored and not self._reservation_valid(command):
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            self._state.records = proposed
            self._state.projection = projection
            self._state.object_refs = {
                **self._state.object_refs,
                **{
                    entry.payload_object.object_id: entry.payload_object
                    for entry in command.entries
                },
                **(
                    {}
                    if command.result_object_ref is None
                    else {command.result_object_ref.object_id: command.result_object_ref}
                ),
            }
            self._state.writers = {
                **self._state.writers,
                command.writer_id: _WriterState(
                    command.task_id,
                    command.session_id,
                    writer.next_sequence + len(new_records),
                    previous_writer,
                ),
            }
            self._state.operations = {**self._state.operations, key: (operation, result)}
            return result

    async def _iter_events(
        self, session_id: str, *, after: int, through: int | None
    ) -> AsyncIterator[LedgerRecord]:
        if (
            type(after) is not int
            or after < 0
            or (through is not None and (type(through) is not int or through < after))
        ):
            raise _error(PublicErrorCode.INVALID_REQUEST)
        async with self._lock:
            records = self._state.records
            # ``session_id`` is a membership check, not a row filter. The ingestion sequence and
            # the digest chain are task-global, so every session attached to this task reads the
            # whole task chain, exactly as ``load_projection`` and ``query_projection`` do.
            # Filtering rows by session would hand replay — which is genesis-anchored — a
            # mid-chain suffix as soon as a resumed session appended its own event.
            rows = (
                tuple(
                    record
                    for record in records
                    if record.ledger.ingestion_sequence > after
                    and (through is None or record.ledger.ingestion_sequence <= through)
                )
                if any(record.session_id == session_id for record in records)
                else ()
            )
        for record in rows:
            yield record

    def load_events(
        self, session_id: str, *, after: int = 0, through: int | None = None
    ) -> AsyncIterator[LedgerRecord]:
        return self._iter_events(session_id, after=after, through=through)

    async def load_projection(
        self, session_id: str, view: ProjectionView
    ) -> StoredProjection | None:
        async with self._lock:
            records = self._state.records
            if not any(record.session_id == session_id for record in records):
                return None
            projection = self._state.projection
        frontier = Frontier(projection.frontier, projection.head_digest)
        if view is ProjectionView.CANDIDATE_FINDINGS:
            state: ProjectionState | tuple[ProjectionItem, ...] = projection
        else:
            state = _projection_items(
                view, projection, records, task=self._task_id, session=session_id
            )
        return StoredProjection(view, state, frontier, 0, PROJECTION_VERSION, False)

    async def load_frontier(self) -> Frontier:
        """Return the task-ledger frontier without requiring an existing session."""

        async with self._lock:
            return Frontier(self._state.projection.frontier, self._state.projection.head_digest)

    async def load_case_availability(
        self, session_id: str, frontier: Frontier, projection: ProjectionState
    ) -> CaseAvailabilityFacts:
        if (
            frontier.sequence != projection.frontier
            or frontier.head_digest != projection.head_digest
        ):
            raise _frontier_conflict(Frontier(projection.frontier, projection.head_digest))
        async with self._lock:
            live = Frontier(self._state.projection.frontier, self._state.projection.head_digest)
            if not any(row.session_id == session_id for row in self._state.records):
                raise _frontier_conflict(live)
            # A case pinned to a past frontier stays valid while the live chain only extends it
            # with observation-authored, finding-free records. Replaying and comparing the exact
            # prefix is required: its head digest authenticates the ledger prefix, not an arbitrary
            # caller-supplied ProjectionState that happens to repeat that frontier. Anything else
            # is a real conflict.
            if projection != self._state.projection and not (
                projection.frontier < live.sequence
                and self._projection_anchored_unlocked(projection)
                and self._observation_only_since_unlocked(projection.frontier, finding_free=True)
            ):
                raise _frontier_conflict(live)
            by_event = {row.event_id: row for row in self._state.records}
            refs = dict(self._state.object_refs)
            current_records = tuple(
                record
                for collection in (
                    projection.plans,
                    projection.obligations,
                    projection.decisions,
                    projection.assignments,
                    projection.actions,
                    projection.results,
                    projection.evidence,
                    projection.claims,
                    projection.findings,
                    projection.responses,
                )
                for record in collection.values()
                if not record.redacted
            )
        assert self._objects is not None

        async def available(ref: ObjectRef | None) -> bool:
            if ref is None:
                return False
            try:
                async for _ in self._objects.open_verified(ref):
                    pass
            except KeyError, OSError, ValueError:
                return False
            return True

        unavailable_events: set[str] = set()
        for current in current_records:
            accepted = by_event.get(current.source_event_id)
            if accepted is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            if not await available(refs.get(accepted.payload_ref.object_id)):
                unavailable_events.add(current.source_event_id)
        unavailable_captured: set[UnavailableCapturedObject] = set()
        for evidence in projection.evidence.values():
            payload = evidence.payload
            if (
                payload is None
                or payload.captured_object_id is None
                or not evidence.object_available
            ):
                continue
            if not await available(refs.get(payload.captured_object_id)):
                unavailable_captured.add(
                    UnavailableCapturedObject(evidence.source_event_id, payload.captured_object_id)
                )
        return CaseAvailabilityFacts(
            tuple(event_id(value) for value in sorted(unavailable_events, key=str.encode)),
            tuple(
                sorted(
                    unavailable_captured,
                    key=lambda item: (item.source_event_id.encode(), item.object_id.encode()),
                )
            ),
        )

    async def query_projection(self, query: ProjectionQuery) -> ProjectionPage:
        async with self._lock:
            head = Frontier(self._state.projection.frontier, self._state.projection.head_digest)
            records = self._state.records
        if not any(row.session_id == query.session_id for row in records):
            raise _error(PublicErrorCode.SESSION_NOT_FOUND)
        if query.requested_frontier > head or (
            query.expected_projection_version is not None
            and query.expected_projection_version != PROJECTION_VERSION
        ):
            raise _error(PublicErrorCode.INVALID_REQUEST)
        prefix = tuple(
            row
            for row in records
            if row.ledger.ingestion_sequence <= query.requested_frontier.sequence
        )
        effective_projection = replay(prefix)
        effective = Frontier(effective_projection.frontier, effective_projection.head_digest)
        if effective != query.requested_frontier:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        view = ProjectionView(query.view)
        all_items = _projection_items(
            view,
            effective_projection,
            prefix,
            task=self._task_id,
            session=query.session_id,
        )
        filtered_items: list[ProjectionItem] = []
        for item in all_items:
            keep = True
            if type(query.filter) is AssignmentProjectionFilter:
                assert type(item) is StatusAssignmentItemModel
                keep = (
                    query.filter.actor_id is None or item.actor_id == query.filter.actor_id
                ) and (query.filter.include_resolved is True or not item.resolved)
            elif type(query.filter) is ObligationsProjectionFilter:
                assert type(item) is StatusObligationItemModel
                assigned = item.assigned_actor_ids
                keep = (
                    (query.filter.actor_id is None or query.filter.actor_id in assigned)
                    and (query.filter.include_resolved is True or item.status == "open")
                    and (query.filter.status is None or item.status == query.filter.status)
                )
            elif type(query.filter) is FindingsProjectionFilter:
                assert type(item) is StatusFindingItemModel
                keep = (
                    (query.filter.origin is None or item.origin == query.filter.origin)
                    and (query.filter.priority is None or item.priority == query.filter.priority)
                    and (
                        query.filter.disposition is None
                        or item.disposition == query.filter.disposition
                    )
                    and (query.filter.include_resolved is True or not item.resolved)
                )
            elif type(query.filter) is EvidenceProjectionFilter:
                assert type(item) is StatusEvidenceItemModel
                keep = (
                    (query.filter.strength is None or item.strength == query.filter.strength)
                    and (query.filter.freshness is None or item.freshness == query.filter.freshness)
                    and (query.filter.include_unavailable is True or item.available)
                )
            elif type(query.filter) is HistoryProjectionFilter:
                assert type(item) is StatusHistoryItemModel
                keep = (
                    (
                        query.filter.schema_name is None
                        or item.schema_name == query.filter.schema_name
                    )
                    and (query.filter.actor_id is None or item.actor_id == query.filter.actor_id)
                    and (
                        query.filter.after_sequence is None
                        or int(item.ingestion_sequence) > query.filter.after_sequence
                    )
                )
            if keep and type(query.position) is IdProjectionPosition:
                structural_id = (
                    item.assignment_event_id
                    if type(item) is StatusAssignmentItemModel
                    else item.obligation_id
                    if type(item) is StatusObligationItemModel
                    else item.evidence_id
                    if type(item) is StatusEvidenceItemModel
                    else item.result_id
                    if type(item) is StatusResultItemModel
                    else ""
                )
                keep = structural_id.encode() > query.position.last_id.encode()
            elif keep and type(query.position) is HistoryProjectionPosition:
                assert type(item) is StatusHistoryItemModel
                keep = int(item.ingestion_sequence) > query.position.ingestion_sequence
            elif keep and type(query.position) is FindingProjectionPosition:
                assert type(item) is StatusFindingItemModel
                finding_record = effective_projection.findings[finding_id(item.finding_id)]
                assert finding_record.payload is not None
                keep = _finding_position_key(
                    _finding_position(finding_record.payload)
                ) > _finding_position_key(query.position)
            if keep:
                filtered_items.append(item)
        selected = tuple(filtered_items[: query.limit])
        next_position = None
        if selected and len(filtered_items) > len(selected):
            last = selected[-1]
            if type(last) is StatusHistoryItemModel:
                next_position = HistoryProjectionPosition(int(last.ingestion_sequence))
            elif type(last) is StatusFindingItemModel:
                finding_record = effective_projection.findings[finding_id(last.finding_id)]
                assert finding_record.payload is not None
                next_position = _finding_position(finding_record.payload)
            elif type(last) is StatusAssignmentItemModel:
                next_position = IdProjectionPosition(last.assignment_event_id)
            elif type(last) is StatusObligationItemModel:
                next_position = IdProjectionPosition(last.obligation_id)
            elif type(last) is StatusEvidenceItemModel:
                next_position = IdProjectionPosition(last.evidence_id)
            elif type(last) is StatusResultItemModel:
                next_position = IdProjectionPosition(last.result_id)
        status_gaps = _status_gap_codes(effective_projection.coverage_gaps)
        coverage = replace(prefix[-1].coverage, known_gaps=status_gaps)
        return ProjectionPage(
            query.view,
            selected,
            query.requested_frontier,
            head,
            effective,
            head.sequence - effective.sequence,
            PROJECTION_VERSION,
            "current",
            coverage,
            status_gaps,
            next_position,
        )

    async def lookup_operation(self, writer_id: str, operation_id: str) -> OperationRecord | None:
        async with self._lock:
            row = self._state.operations.get((writer_id, operation_id))
        return None if row is None else row[0]

    async def lookup_task_operation(
        self, writer_id: str, operation_id: str
    ) -> OperationRecord | None:
        async with self._lock:
            preferred = self._state.operations.get((writer_id, operation_id))
            if preferred is not None:
                return preferred[0]
            matches = [
                row[0]
                for key, row in self._state.operations.items()
                if key[0] != writer_id and key[1] == operation_id
            ]
        return matches[0] if len(matches) == 1 else None

    async def reclaim_operation(
        self, writer_id: str, operation_id: str, request_digest: str
    ) -> OperationLease | PendingVerdict:
        record = await self.lookup_operation(writer_id, operation_id)
        if record is None:
            return PendingVerdict(PendingVerdictKind.ABSENT, None, None)
        if record.request_digest != request_digest:
            raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
        if record.state is OperationState.COMPLETE:
            return PendingVerdict(PendingVerdictKind.TERMINAL, record, None)
        if record.state is OperationState.QUARANTINED:
            return PendingVerdict(PendingVerdictKind.QUARANTINED, record, None)
        assert record.lease_expires_at is not None
        now = _now(self._clock)
        if (
            record.owner_generation == str(self._fence.owner_generation)
            and record.lease_expires_at > now
        ):
            remaining = max(0, int((record.lease_expires_at - now).total_seconds() * 1000))
            return PendingVerdict(PendingVerdictKind.LIVE, record, remaining)
        writer = self._state.writers.get(writer_id)
        if writer is None:
            raise _error(PublicErrorCode.STORAGE_CORRUPT)
        assert self._objects is not None
        try:
            case = await load_frozen_case_from_resume(
                self._objects,
                record,
                task=self._task_id,
                session=writer.session_id,
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc
        async with self._lock:
            current = self._state.operations.get((writer_id, operation_id))
            if current is None or current[0] != record:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            if self._pending_import(writer.session_id):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            self._state.frozen_cases[(writer_id, operation_id)] = case
            _, lease = self._replace_pending_record(record)
            return lease

    async def freeze_case(
        self,
        session_id: str,
        writer_id: str,
        expected_frontier: int | None,
        request_id: str,
        request_digest: str,
    ) -> FrozenCase | CheckCommitResult:
        key = (writer_id, request_id)
        prior_record: OperationRecord | None = None
        projection: ProjectionState | None = None
        frontier: Frontier | None = None
        records: tuple[LedgerRecord, ...] | None = None
        acquisition_reservation: _CheckReservation | None = None
        async with self._lock:
            prior = self._state.operations.get(key)
            if prior is not None:
                record = prior[0]
                if record.request_digest != request_digest:
                    raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
                if record.state is OperationState.COMPLETE:
                    result = self._state.check_results.get(key)
                    if result is not None:
                        return replace(result, outcome="replayed")
                    stored_error = self._state.check_errors.get(key)
                    if stored_error is not None:
                        raise stored_error
                    raise _error(PublicErrorCode.STORAGE_CORRUPT)
                if record.state is OperationState.QUARANTINED:
                    stored_error = self._state.check_errors.get(key)
                    if stored_error is not None:
                        raise stored_error
                    raise _error(
                        PublicErrorCode.STORAGE_CORRUPT,
                        quarantine_code=(
                            record.quarantine_code.value
                            if record.quarantine_code is not None
                            else None
                        ),
                    )
                writer = self._state.writers.get(writer_id)
                if writer is None or writer.session_id != session_id:
                    raise _error(PublicErrorCode.SESSION_NOT_FOUND)
                assert record.lease_expires_at is not None
                if record.lease_expires_at > _now(self._clock) and record.owner_generation == str(
                    self._fence.owner_generation
                ):
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                if self._pending_import(session_id):
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                prior_record = record
            else:
                if self._pending_import(session_id):
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                projection = self._state.projection
                frontier = Frontier(projection.frontier, projection.head_digest)
                # Observation-only motion past the caller's frontier is tolerated by freezing at
                # the real head: the case then covers the drained records instead of racing them.
                if (
                    expected_frontier is not None
                    and expected_frontier != frontier.sequence
                    and not (
                        expected_frontier < frontier.sequence
                        and self._observation_only_since_unlocked(expected_frontier)
                    )
                ):
                    raise _frontier_conflict(frontier)
                records = self._state.records
                writer = self._state.writers.get(writer_id)
                if writer is None or writer.session_id != session_id:
                    raise _error(PublicErrorCode.SESSION_NOT_FOUND)
                # Arm the observation-append barrier before this lock is released, or the drain
                # that stayed active through acquisition would race the freeze it just tolerated.
                reservation = self._state.check_reservations.get(key)
                if reservation is not None and reservation.expires_at > _now(self._clock):
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                acquisition_reservation = _CheckReservation(
                    session_id, _now(self._clock) + timedelta(seconds=60)
                )
                self._state.check_reservations[key] = acquisition_reservation
        if prior_record is not None:
            assert self._objects is not None
            try:
                case = await load_frozen_case_from_resume(
                    self._objects,
                    prior_record,
                    task=self._task_id,
                    session=session_id,
                )
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc
            async with self._lock:
                current = self._state.operations.get(key)
                if current is None or current[0] != prior_record:
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                if self._pending_import(session_id):
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                self._state.frozen_cases[key] = case
                _, renewed = self._replace_pending_record(prior_record)
                return FrozenCase(case, renewed)
        assert projection is not None and frontier is not None and records is not None
        try:
            availability = await self.load_case_availability(session_id, frontier, projection)
            try:
                case = build_deterministic_case(projection, records, availability)
            except ValueError as exc:
                if str(exc) == "deterministic_case_invalid":
                    raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc
                raise
            dependency = _case_dependency_digest(case)
            case_json = deterministic_case_to_json(case)
            case_bytes = canonical_encode(
                {
                    "schema_version": "1.0.0",
                    "task_id": self._task_id,
                    "case": case_json,
                    "case_digest": canonical_digest(case_json),
                    "dependency_digest": dependency,
                    "frontier": frontier.as_wire(),
                    "request_digest": request_digest,
                    "request_id": request_id,
                    "session_id": session_id,
                    "writer_id": writer_id,
                }
            )
            assert self._objects is not None
            created_at = _now(self._clock)
            staged = await self._objects.stage(
                ObjectSource(data=case_bytes, declared_size=len(case_bytes)),
                ObjectMetadata(
                    ObjectKind.CHECK_RESUME,
                    "application/vnd.yoetz.check-resume+json",
                    self._task_id,
                    created_at,
                ),
            )
            resume_ref = await self._objects.finalize(staged)
            async with self._lock:
                if key in self._state.operations:
                    raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                current = Frontier(
                    self._state.projection.frontier, self._state.projection.head_digest
                )
                if current != frontier or self._pending_import(session_id):
                    raise _frontier_conflict(current)
                operation = OperationRecord(
                    writer_id,
                    request_id,
                    OperationKind.CHECK,
                    request_digest,
                    OperationState.PENDING,
                    CheckPhase.RESERVED,
                    str(self._fence.owner_generation),
                    self._fence.service_instance_id,
                    1,
                    _now(self._clock) + timedelta(seconds=60),
                    resume_ref,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
                self._state.frozen_cases[key] = case
                self._state.operations[key] = (operation, None)
                return FrozenCase(case, self._lease_for(operation))
        finally:
            # The frozen case (registered above on success) carries the barrier from here on. An
            # expired acquisition may have been replaced under the same request key, so release
            # only the exact reservation this invocation installed.
            async with self._lock:
                if self._state.check_reservations.get(key) is acquisition_reservation:
                    self._state.check_reservations.pop(key, None)

    async def advance_check_phase(
        self,
        lease: OperationLease,
        expected_phase: CheckPhase,
        next_phase: CheckPhase,
        durable_object_ref: ObjectRef | None = None,
    ) -> OperationLease:
        edges = {
            (CheckPhase.RESERVED, CheckPhase.LOCAL_READY),
            (CheckPhase.LOCAL_READY, CheckPhase.SEMANTIC_WAIT),
            (CheckPhase.LOCAL_READY, CheckPhase.READY_TO_FINALIZE),
            (CheckPhase.SEMANTIC_WAIT, CheckPhase.READY_TO_FINALIZE),
        }
        if (expected_phase, next_phase) not in edges:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        if expected_phase is CheckPhase.RESERVED:
            if (
                durable_object_ref is None
                or durable_object_ref.metadata.kind is not ObjectKind.DETERMINISTIC_RESULT
                or durable_object_ref.metadata.task_id != self._task_id
                or durable_object_ref.metadata.media_type
                != "application/vnd.yoetz.deterministic-result+json"
            ):
                raise _error(PublicErrorCode.INVALID_REQUEST)
            assert self._objects is not None
            try:
                resolved = await self._objects.resolve_verified(
                    durable_object_ref.object_id,
                    durable_object_ref.envelope_digest,
                )
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise _error(PublicErrorCode.INVALID_REQUEST) from exc
            if resolved != durable_object_ref:
                raise _error(PublicErrorCode.INVALID_REQUEST)
        elif durable_object_ref is not None:
            raise _error(PublicErrorCode.INVALID_REQUEST)
        async with self._lock:
            record = self._require_lease(lease)
            if record.phase is not expected_phase:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            _, replacement = self._replace_pending_record(
                record,
                phase=next_phase,
                resume_object_ref=durable_object_ref,
            )
            return replacement

    async def enqueue_semantic_job(
        self, lease: OperationLease, case_digest: str, case_object_ref: ObjectRef
    ) -> SemanticJobRecord:
        async with self._lock:
            record = self._require_lease(lease)
            if record.phase is not CheckPhase.SEMANTIC_WAIT:
                raise _error(PublicErrorCode.INVALID_REQUEST)
            identity = (lease.writer_id, lease.operation_id, case_digest)
            existing_id = self._state.job_by_case.get(identity)
            if existing_id is not None:
                return self._state.jobs[existing_id]
            assert self._ids is not None
            job_id = self._ids.new(IdKind.SEMANTIC_JOB)
            job = SemanticJobRecord(
                job_id,
                lease.writer_id,
                lease.operation_id,
                case_digest,
                case_object_ref,
                "queued",
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            self._state.jobs[job_id] = job
            self._state.job_by_case[identity] = job_id
            return job

    async def suspend_check_for_repository_grant(self, lease: OperationLease) -> None:
        """Durably mark and expire the lease for a pre-job standing-grant suspension.

        The marker also releases the observation frozen-case barrier for this
        operation: the ceremony may never complete, and same-request replay
        re-installs the barrier when it reclaims the lease.
        """

        async with self._lock:
            record = self._require_lease(lease)
            if record.phase is not CheckPhase.SEMANTIC_WAIT or any(
                job.writer_id == lease.writer_id and job.operation_id == lease.operation_id
                for job in self._state.jobs.values()
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            suspended = replace(
                record,
                lease_expires_at=_now(self._clock),
                suspension_kind=CheckSuspensionKind.REPOSITORY_GRANT,
            )
            self._state.operations[(record.writer_id, record.operation_id)] = (suspended, None)

    async def claim_semantic_job(self, lease: OperationLease, job_id: str) -> SemanticAttemptHandle:
        async with self._lock:
            operation = self._require_lease(lease)
            if operation.phase is not CheckPhase.SEMANTIC_WAIT:
                raise _error(PublicErrorCode.INVALID_REQUEST)
            job = self._state.jobs.get(job_id)
            if (
                job is None
                or job.writer_id != lease.writer_id
                or job.operation_id != lease.operation_id
            ):
                raise _error(PublicErrorCode.INVALID_REQUEST)
            now = _now(self._clock)
            # A live disclosure wait suspends the lease clock. The human may take longer than a
            # lease TTL to answer, and the reclaim path below would expire the started attempt and
            # mint a new provider request id — changing the prepared bytes the human approved and
            # silently orphaning their decision. The bound on this wait is the proposal's own
            # expiry, which the resume path checks before dispatching anything.
            wait = self._state.disclosure_waits.get(job_id)
            if (
                wait is not None
                and wait.state == "awaiting"
                and job.active_attempt_id is not None
                and wait.attempt_id == job.active_attempt_id
            ):
                attempt = self._state.attempts.get(job.active_attempt_id)
                if attempt is not None and attempt.state == "started":
                    self._state.jobs[job_id] = replace(
                        job,
                        lease_owner_id=lease.lease_owner_id,
                        lease_expires_at=min(lease.lease_expires_at, now + timedelta(seconds=60)),
                    )
                    return attempt.handle
            if (
                job.state == "leased"
                and job.lease_expires_at is not None
                and job.lease_expires_at > now
            ):
                # Same owner still holding a live lease: resume the active started attempt.
                # Crash-before-authorization-consumption must not mint a new attempt identity.
                if job.lease_owner_id == lease.lease_owner_id and job.active_attempt_id is not None:
                    attempt = self._state.attempts.get(job.active_attempt_id)
                    if attempt is not None and attempt.state == "started":
                        return attempt.handle
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            if job.state not in {"queued", "leased"}:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            # Expired or reclaimed lease: close the prior started attempt before a new ordinal.
            if (
                job.state == "leased"
                and job.active_attempt_id is not None
                and job.active_attempt_id in self._state.attempts
            ):
                prior = self._state.attempts[job.active_attempt_id]
                if prior.state == "started":
                    self._state.attempts[job.active_attempt_id] = replace(
                        prior,
                        state="expired",
                        terminal_code=SemanticReason.LEASE_AUTHORITY_LOST,
                    )
            ordinal = job.attempt_count + 1
            expiry = min(lease.lease_expires_at, now + timedelta(seconds=60))
            handle = SemanticAttemptHandle(
                job_id,
                self._ids.new(IdKind.SEMANTIC_ATTEMPT),
                ordinal,
                self._ids.new(IdKind.REQUEST),
                lease.writer_id,
                lease.operation_id,
                lease.owner_generation,
                lease.lease_owner_id,
                ordinal,
                expiry,
                lease.frontier,
                lease.dependency_digest,
            )
            self._state.attempts[handle.attempt_id] = _AttemptState(handle, "started")
            self._state.jobs[job_id] = replace(
                job,
                state="leased",
                attempt_count=ordinal,
                active_attempt_id=handle.attempt_id,
                lease_owner_id=lease.lease_owner_id,
                lease_generation=ordinal,
                lease_expires_at=expiry,
            )
            return handle

    async def record_attempt_outcome(
        self,
        handle: SemanticAttemptHandle,
        outcome: AttemptOutcome,
        result_object_ref: ObjectRef | None = None,
        terminal_code: SemanticReason | None = None,
    ) -> None:
        async with self._lock:
            attempt = self._state.attempts.get(handle.attempt_id)
            if attempt is None or attempt.handle != handle or attempt.state != "started":
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            job = self._state.jobs[handle.job_id]
            if outcome is AttemptOutcome.SELECTED:
                raise _error(PublicErrorCode.INVALID_REQUEST)
            if outcome is AttemptOutcome.RESPONSE_DURABLE:
                if result_object_ref is None or terminal_code is not None:
                    raise _error(PublicErrorCode.INVALID_REQUEST)
                self._state.attempts[handle.attempt_id] = replace(
                    attempt, state="response_durable", result_object_ref=result_object_ref
                )
                return
            if terminal_code is None or (
                outcome is AttemptOutcome.EXPIRED and result_object_ref is not None
            ):
                raise _error(PublicErrorCode.INVALID_REQUEST)
            self._state.attempts[handle.attempt_id] = replace(
                attempt,
                state=outcome.value,
                result_object_ref=result_object_ref,
                terminal_code=terminal_code,
            )
            if outcome is AttemptOutcome.FAILED:
                self._state.jobs[handle.job_id] = replace(
                    job,
                    state="failed",
                    active_attempt_id=None,
                    lease_owner_id=None,
                    lease_generation=None,
                    lease_expires_at=None,
                    terminal_code=terminal_code,
                    terminal_at=_now(self._clock),
                )
            else:
                self._state.jobs[handle.job_id] = replace(
                    job,
                    state="queued",
                    active_attempt_id=None,
                    lease_owner_id=None,
                    lease_generation=None,
                    lease_expires_at=None,
                )

    async def fail_semantic_job(
        self,
        lease: OperationLease,
        job_id: str,
        terminal_code: SemanticReason,
    ) -> SemanticJobRecord:
        """Terminally fail a queued job when no physical attempt is active."""

        async with self._lock:
            operation = self._require_lease(lease)
            job = self._state.jobs.get(job_id)
            if (
                operation.phase is not CheckPhase.SEMANTIC_WAIT
                or job is None
                or job.writer_id != lease.writer_id
                or job.operation_id != lease.operation_id
                or job.state != "queued"
                or job.active_attempt_id is not None
                or terminal_code is SemanticReason.SEMANTIC_COMPLETED
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            failed = replace(
                job,
                state="failed",
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                terminal_code=terminal_code,
                terminal_at=_now(self._clock),
            )
            self._state.jobs[job_id] = failed
            return failed

    async def select_attempt(
        self,
        lease: OperationLease,
        handle: SemanticAttemptHandle,
        selected_result_object_ref: ObjectRef,
    ) -> SelectedAttempt:
        async with self._lock:
            operation = self._require_lease(lease)
            attempt = self._state.attempts.get(handle.attempt_id)
            job = self._state.jobs.get(handle.job_id)
            if (
                operation.phase is not CheckPhase.SEMANTIC_WAIT
                or attempt is None
                or attempt.handle != handle
                or attempt.state != "response_durable"
                or attempt.result_object_ref != selected_result_object_ref
                or job is None
                or job.active_attempt_id != handle.attempt_id
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            selected_at = _now(self._clock)
            self._state.attempts[handle.attempt_id] = replace(
                attempt, state="selected", terminal_code=SemanticReason.SEMANTIC_COMPLETED
            )
            self._state.jobs[handle.job_id] = replace(
                job,
                state="succeeded",
                active_attempt_id=None,
                selected_attempt_id=handle.attempt_id,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                selected_result_object_ref=selected_result_object_ref,
                terminal_code=SemanticReason.SEMANTIC_COMPLETED,
                terminal_at=selected_at,
            )
            return SelectedAttempt(
                handle.job_id,
                handle.attempt_id,
                selected_result_object_ref,
                selected_at,
                lease.frontier,
                lease.dependency_digest,
            )

    async def load_semantic_job(
        self, writer_id: str, operation_id: str
    ) -> SemanticJobRecord | None:
        async with self._lock:
            matches = tuple(
                job
                for job in self._state.jobs.values()
                if job.writer_id == writer_id and job.operation_id == operation_id
            )
            if not matches:
                return None
            # One operation binds at most one durable semantic job in practice; if multiple
            # case digests exist (should not), return the most recently enqueued by attempt_count.
            return max(matches, key=lambda item: (item.attempt_count, item.job_id))

    async def list_semantic_attempts(self, job_id: str) -> tuple[SemanticAttemptRecord, ...]:
        async with self._lock:
            if job_id not in self._state.jobs:
                return ()
            rows = [
                SemanticAttemptRecord(
                    attempt.handle.job_id,
                    attempt.handle.attempt_id,
                    attempt.handle.attempt_ordinal,
                    attempt.handle.provider_request_id,
                    cast(
                        Literal[
                            "started",
                            "response_durable",
                            "selected",
                            "failed",
                            "expired",
                            "late",
                        ],
                        attempt.state,
                    ),
                    attempt.terminal_code,
                    attempt.result_object_ref,
                )
                for attempt in self._state.attempts.values()
                if attempt.handle.job_id == job_id
            ]
            rows.sort(key=lambda item: item.attempt_ordinal)
            return tuple(rows)

    async def record_disclosure_wait(
        self,
        handle: SemanticAttemptHandle,
        pending_id: str,
        pending_expires_at: datetime,
    ) -> SemanticDisclosureWait:
        async with self._lock:
            attempt = self._state.attempts.get(handle.attempt_id)
            if attempt is None or attempt.handle != handle or attempt.state != "started":
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            job = self._state.jobs.get(handle.job_id)
            if job is None or job.active_attempt_id != handle.attempt_id:
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            existing = self._state.disclosure_waits.get(handle.job_id)
            if existing is not None:
                # A second proposal on the same job means a fresh physical attempt asked for a
                # fresh decision. Re-recording over a live wait would let one decision authorize
                # bytes it never saw.
                if existing.state == "awaiting" and existing.attempt_id == handle.attempt_id:
                    return existing
                if existing.attempt_id == handle.attempt_id:
                    raise _error(PublicErrorCode.INVALID_REQUEST)
            wait = SemanticDisclosureWait(
                handle.job_id,
                handle.attempt_id,
                handle.writer_id,
                handle.operation_id,
                pending_id,
                pending_expires_at,
                "awaiting",
                None,
            )
            self._state.disclosure_waits[handle.job_id] = wait
            return wait

    async def load_disclosure_wait(
        self, writer_id: str, operation_id: str
    ) -> SemanticDisclosureWait | None:
        async with self._lock:
            matches = tuple(
                wait
                for wait in self._state.disclosure_waits.values()
                if wait.writer_id == writer_id and wait.operation_id == operation_id
            )
            if not matches:
                return None
            live = tuple(wait for wait in matches if wait.state == "awaiting")
            if live:
                return max(live, key=lambda item: item.job_id)
            return max(matches, key=lambda item: item.job_id)

    async def resolve_disclosure_wait(self, job_id: str) -> SemanticDisclosureWait:
        async with self._lock:
            wait = self._state.disclosure_waits.get(job_id)
            if wait is None:
                raise _error(PublicErrorCode.INVALID_REQUEST)
            # One-use. A denial or expiry resumes exactly once; a second attempt to consume the
            # same decision is a protocol error, not a silent replay.
            if wait.state != "awaiting":
                raise _error(PublicErrorCode.INVALID_REQUEST)
            resolved = replace(wait, state="resolved", resolved_at=_now(self._clock))
            self._state.disclosure_waits[job_id] = resolved
            return resolved

    async def renew_leases(self, lease: OperationLease) -> OperationLease:
        async with self._lock:
            record = self._require_lease(lease)
            _, replacement = self._replace_pending_record(record)
            return replacement

    async def commit_check_if_current(
        self,
        frozen: FrozenCase,
        findings: RankedFindings,
        policy_executions: tuple[CheckPolicyExecution, ...],
        semantic_status: SemanticStatus,
        semantic_reason: SemanticReason,
        semantic_provenance: SemanticProvenance | None,
        request_id: str,
        *,
        scope: CheckScopeModel | None = None,
    ) -> CheckCommitResult:
        key = (frozen.lease.writer_id, frozen.lease.operation_id)
        async with self._lock:
            prior_result = self._state.check_results.get(key)
            if prior_result is not None:
                return replace(prior_result, outcome="replayed")
            stored_error = self._state.check_errors.get(key)
            if stored_error is not None:
                raise stored_error
            record = self._require_lease(frozen.lease)
            if (
                record.phase is not CheckPhase.READY_TO_FINALIZE
                or request_id != record.operation_id
            ):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            current = Frontier(self._state.projection.frontier, self._state.projection.head_digest)
            frozen_frontier = frozen.case.frontier
            observation_only_suffix = (
                current.sequence > frozen_frontier.sequence
                and self._observation_only_since_unlocked(frozen_frontier.sequence)
            )
            if (
                current != frozen_frontier and not observation_only_suffix
            ) or self._state.frozen_cases.get(key) != frozen.case:
                failure = _frontier_conflict(current)
                canonical = canonical_encode(
                    {
                        "code": failure.code.value,
                        "reason_code": "frontier_changed",
                        "sequence": current.sequence,
                        "head_digest": current.head_digest,
                    }
                )
                terminal = replace(
                    record,
                    state=OperationState.COMPLETE,
                    phase=CheckPhase.TERMINAL,
                    owner_generation=None,
                    lease_owner_id=None,
                    lease_generation=None,
                    lease_expires_at=None,
                    resume_object_ref=None,
                    result_canonical=canonical,
                    result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
                    result_locator=None,
                    terminal_at=_now(self._clock),
                )
                self._state.operations[key] = (terminal, None)
                self._state.check_errors[key] = failure
                raise failure
            if self._pending_import(frozen.lease.session_id):
                raise _error(PublicErrorCode.OPERATION_PENDING, retryable=True)
            snapshot_records = self._state.records
            snapshot_projection = self._state.projection
            writer = self._state.writers.get(frozen.lease.writer_id)
            if writer is None:
                raise _error(PublicErrorCode.STORAGE_CORRUPT)
            request_digest_value = record.request_digest

        assert self._ids is not None and self._objects is not None
        # A finding that kept a previously recorded ID is already on the ledger; re-emitting it
        # would grow the record per re-check, so the check only cites it in returned_finding_ids.
        event_payloads: list[tuple[EventId, EventPayload]] = [
            (event_id(self._ids.new(IdKind.EVENT)), finding)
            for finding in findings.findings
            if (prior := snapshot_projection.findings.get(finding.finding_id)) is None
            or prior.payload is None
        ]
        check_payload = CheckRecordedPayload(
            mode=(
                CheckMode.DETERMINISTIC_ONLY
                if semantic_status is SemanticStatus.NOT_REQUESTED
                else CheckMode.SEMANTIC_IF_CONFIGURED
            ),
            policies=tuple(
                PolicyVersion(execution.policy_id, execution.policy_version)
                for execution in policy_executions
            ),
            # The normalized request scope is what a later reader needs to know which issues
            # this check could speak to (issue #458); omitted scope is the whole case.
            scope=CheckScopeModel(claim_ids=(), obligation_ids=()) if scope is None else scope,
            policy_executions=tuple(
                CheckPolicyExecutionModel.model_validate(
                    {
                        "policy_id": execution.policy_id,
                        "policy_version": execution.policy_version,
                        "outcome": execution.outcome,
                        "reason": execution.reason,
                    }
                )
                for execution in policy_executions
            ),
            subject_frontier=frozen.case.frontier,
            verdict=findings.verdict,
            # Ranking is by priority/actionability/coverage, not finding ID. The event field
            # is a canonical unique ASCII-ascending set; presentation order stays on the
            # check result's findings tuple.
            returned_finding_ids=tuple(
                sorted((item.finding_id for item in findings.findings), key=str.encode)
            ),
            suppressed_count=findings.suppressed_count,
            coverage=findings.coverage,
            semantic_status=semantic_status,
            semantic_reason=semantic_reason,
            engine_version="0.1.0",
            projection_version=PROJECTION_VERSION,
            semantic_provenance=semantic_provenance,
        )
        event_payloads.append((event_id(self._ids.new(IdKind.EVENT)), check_payload))
        accepted_at = _now(self._clock)
        author = Actor(
            actor_id("yoetz.engine"),
            ActorType.YOETZ_ENGINE,
            AuthorshipAssurance.SERVICE_AUTHENTICATED,
        )
        entries: list[AppendEntry] = []
        for event, payload in event_payloads:
            payload_bytes = canonical_encode(encode_payload(payload))
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for(
                    "finding_recorded"
                    if type(payload) is FindingRecordedPayload
                    else "check_recorded"
                ),
                self._task_id,
                accepted_at,
            )
            staged = await self._objects.stage(
                ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)), metadata
            )
            payload_ref = await self._objects.finalize(staged)
            schema = EventSchema(
                "finding_recorded" if type(payload) is FindingRecordedPayload else "check_recorded",
                "1.1.0",
            )
            entries.append(
                AppendEntry(
                    EventDraft(
                        event,
                        schema,
                        timestamp_from_datetime(accepted_at),
                        (),
                        payload,
                        (),
                        (),
                    ),
                    author,
                    payload_ref,
                    payload_ref.commitment,
                    metadata.media_type,
                    payload_ref.plaintext_size,
                    PublicationChannel.ENGINE_DERIVED,
                    coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
                    "projected",
                )
            )
        command = AppendCommand(
            self._task_id,
            frozen.lease.session_id,
            frozen.lease.writer_id,
            request_id,
            OperationKind.CHECK,
            request_digest_value,
            current.sequence,
            tuple(entries),
        )
        previous_ledger = current.head_digest
        previous_writer = writer.head_digest
        new_records: list[LedgerRecord] = []
        for offset in range(len(entries)):
            accepted = _record_preimage(
                command,
                offset,
                ingestion_sequence=current.sequence + offset + 1,
                writer_sequence=writer.next_sequence + offset,
                previous_ledger_digest=previous_ledger,
                previous_writer_digest=previous_writer,
                accepted_at=accepted_at,
            )
            new_records.append(accepted)
            previous_ledger = accepted.entry_digest
            previous_writer = accepted.entry_digest
        proposed_records = snapshot_records + tuple(new_records)
        try:
            proposed_projection = replay(proposed_records)
        except ValueError as exc:
            raise _error(PublicErrorCode.STORAGE_CORRUPT) from exc
        result_frontier = Frontier(proposed_projection.frontier, proposed_projection.head_digest)
        packs = tuple(
            f"{execution.policy_id}/{execution.policy_version}" for execution in policy_executions
        )
        result = CheckCommitResult(
            "committed",
            self._task_id,
            frozen.lease.session_id,
            frozen.lease.writer_id,
            request_id,
            frozen.case.frontier,
            result_frontier,
            findings.verdict,
            findings.findings,
            findings.suppressed_count,
            policy_executions,
            semantic_status,
            semantic_reason,
            semantic_provenance,
            findings.coverage,
            CheckVersionSlice("0.1", "0.1.0", PROJECTION_VERSION, packs),
        )
        canonical = canonical_encode(
            {
                "finding_ids": tuple(finding.finding_id for finding in findings.findings),
                "request_id": request_id,
                "result_frontier": result_frontier.as_wire(),
                "subject_frontier": frozen.case.frontier.as_wire(),
                "verdict": findings.verdict.value,
            }
        )
        from yoetz.ports.ledger import OperationResultLocator

        locator = OperationResultLocator(
            new_records[0].ledger.ingestion_sequence,
            new_records[-1].ledger.ingestion_sequence,
            None,
            tuple(sorted((row.event_id for row in new_records), key=str.encode)),
        )
        async with self._lock:
            current_record = self._require_lease(frozen.lease)
            current_head = Frontier(
                self._state.projection.frontier, self._state.projection.head_digest
            )
            if (
                current_record != record
                or current_head != current
                or self._state.records != snapshot_records
                or self._state.projection != snapshot_projection
                or self._pending_import(frozen.lease.session_id)
            ):
                raise _frontier_conflict(current_head)
            terminal = replace(
                current_record,
                state=OperationState.COMPLETE,
                phase=CheckPhase.TERMINAL,
                owner_generation=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                resume_object_ref=None,
                result_canonical=canonical,
                result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
                result_locator=locator,
                terminal_at=accepted_at,
            )
            self._state.records = proposed_records
            self._state.projection = proposed_projection
            self._state.object_refs = {
                **self._state.object_refs,
                **{entry.payload_object.object_id: entry.payload_object for entry in entries},
            }
            self._state.writers = {
                **self._state.writers,
                frozen.lease.writer_id: _WriterState(
                    self._task_id,
                    frozen.lease.session_id,
                    writer.next_sequence + len(new_records),
                    previous_writer,
                ),
            }
            self._state.operations[key] = (terminal, None)
            self._state.check_results[key] = result
            return result

    async def fail_check_if_current(
        self, lease: OperationLease, failure: PublicOperationError
    ) -> None:
        """Terminalize an admitted check whose application pipeline failed unexpectedly.

        This is the last-resort durability path: it records no check event and advances no
        frontier, but it closes the exact operation so replay returns a stable terminal error
        instead of waiting forever on a lease that no worker can resume safely.
        """

        if failure.retryable:
            raise TypeError("retryable_check_failure_cannot_terminalize")
        canonical = canonical_encode(
            {
                "code": failure.code.value,
                "message": str(failure),
                "retryable": failure.retryable,
                "safe_details": dict(failure.safe_details),
            }
        )
        key = (lease.writer_id, lease.operation_id)
        async with self._lock:
            current = self._state.operations.get(key)
            if (
                current is not None
                and current[0].state is OperationState.COMPLETE
                and key in self._state.check_errors
            ):
                return
            record = self._require_lease(lease)
            terminal = replace(
                record,
                state=OperationState.COMPLETE,
                phase=CheckPhase.TERMINAL,
                owner_generation=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                resume_object_ref=None,
                result_canonical=canonical,
                result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
                result_locator=None,
                terminal_at=_now(self._clock),
            )
            self._state.operations[key] = (terminal, None)
            self._state.check_errors[key] = failure
