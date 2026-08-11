"""Pure accepted-event replay into immutable work projections."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Final, cast

from yoetz.domain.events import (
    AcceptedEvent,
    ActionRecordedPayload,
    AssignmentRecordedPayload,
    CheckRecordedPayload,
    ClaimRecordedPayload,
    DecisionRecordedPayload,
    EvidenceRecordedPayload,
    FindingRecordedPayload,
    LedgerRecord,
    NoObligationsReasonMismatch,
    ObligationPublishedPayload,
    ObligationResolutionMismatch,
    ObligationStatus,
    PlanPublishedPayload,
    PlanRevisedPayload,
    RedactionRecordedPayload,
    ResponseRecordedPayload,
    ResultRecordedPayload,
    UnknownEvent,
    encode_payload,
    obligation_meaning_field_diffs,
)
from yoetz.domain.findings import Finding
from yoetz.domain.values import (
    ActionId,
    ClaimId,
    EventId,
    EvidenceId,
    FindingId,
    ObjectId,
    ObligationId,
    ResultId,
    action_id,
    claim_id,
    event_id,
    evidence_id,
    finding_id,
    object_id,
    obligation_id,
    result_id,
    validate_sha256_digest,
)
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.projections import (
    ContradictionKey,
    ContradictionRecord,
    DecisionProjectionRecord,
    EvidenceProjectionRecord,
    LatestTestedState,
    ObligationProjectionRecord,
    PlanProjectionRecord,
    ProjectionRecord,
    ProjectionState,
    empty_projection_state,
)
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.coverage import LedgerFreshness

__all__ = [
    "EvidenceObjectSource",
    "ReplayIndex",
    "empty_replay_index",
    "extend_replay_index",
    "invalidates_recorded_check",
    "is_material_event_family",
    "reduce_event",
    "supersedes_recorded_check",
    "replay",
]

_MAX_SQLITE_SIGNED_INTEGER: Final = 2**63 - 1
_MATERIAL_FAMILIES: Final = frozenset(
    {
        "action_recorded",
        "assignment_recorded",
        "claim_recorded",
        "decision_recorded",
        "evidence_recorded",
        "finding_recorded",
        "obligation_published",
        "plan_published",
        "plan_revised",
        "response_recorded",
        "result_recorded",
    }
)


def is_material_event_family(name: str) -> bool:
    """True when an event of this family invalidates a previously recorded check."""
    return name in _MATERIAL_FAMILIES


def supersedes_recorded_check(
    name: str,
    payload: object,
    returned_finding_ids: tuple[FindingId, ...],
) -> bool:
    """True when a record of *name* carrying *payload* supersedes a check returning those findings.

    Answering a finding the check itself returned reports on that check's own output rather than
    publishing untested work, so such a response leaves the check attributable to a later receipt.
    Every other material-family record supersedes the check, including a response to a finding the
    check did not return and a response whose payload is unreadable.
    """

    if not is_material_event_family(name):
        return False
    if name != "response_recorded":
        return True
    if type(payload) is not ResponseRecordedPayload:
        return True
    return payload.finding_id not in returned_finding_ids


def invalidates_recorded_check(
    record: LedgerRecord,
    check_sequence: int,
    returned_finding_ids: tuple[FindingId, ...],
) -> bool:
    """True when *record* supersedes the check recorded at *check_sequence*."""

    if record.ledger.ingestion_sequence <= check_sequence:
        return False
    return supersedes_recorded_check(record.schema.name, record.payload, returned_finding_ids)


def _corrupt() -> ValueError:
    return ValueError("projection_corrupt")


def _ascii_key(value: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _corrupt() from exc


def _next_record(frontier: int, head_digest: str, event: LedgerRecord) -> None:
    if type(event) not in {AcceptedEvent, UnknownEvent}:
        raise _corrupt()
    if (
        event.ledger.ingestion_sequence != frontier + 1
        or event.ledger.previous_entry_digest != head_digest
    ):
        raise _corrupt()


@dataclass(frozen=True, slots=True)
class EvidenceObjectSource:
    evidence_id: EvidenceId
    source_event_id: EventId

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "evidence_id", evidence_id(self.evidence_id))
            object.__setattr__(self, "source_event_id", event_id(self.source_event_id))
        except ValueError as exc:
            raise _corrupt() from exc


@dataclass(frozen=True, slots=True)
class ReplayIndex:
    frontier: int
    head_digest: str
    payload_event_by_object: Mapping[ObjectId, EventId]
    evidence_sources_by_object: Mapping[ObjectId, tuple[EvidenceObjectSource, ...]]
    redaction_root_by_object: Mapping[ObjectId, EventId]

    def __post_init__(self) -> None:
        if type(self.frontier) is not int or not 0 <= self.frontier <= _MAX_SQLITE_SIGNED_INTEGER:
            raise _corrupt()
        try:
            if self.frontier == 0:
                if type(self.head_digest) is not str or self.head_digest != "genesis":
                    raise _corrupt()
            else:
                validate_sha256_digest(self.head_digest)
        except ValueError as exc:
            raise _corrupt() from exc
        payloads = self._copy_payload_owners(self.payload_event_by_object)
        evidence = self._copy_evidence_sources(self.evidence_sources_by_object)
        roots = self._copy_redaction_roots(self.redaction_root_by_object)
        accepted_event_ids = frozenset(payloads.values())
        if len(payloads) != self.frontier or len(accepted_event_ids) != len(payloads):
            raise _corrupt()
        seen_associations: set[EvidenceObjectSource] = set()
        for associations in evidence.values():
            for association in associations:
                if (
                    association.source_event_id not in accepted_event_ids
                    or association in seen_associations
                ):
                    raise _corrupt()
                seen_associations.add(association)
        if any(root not in accepted_event_ids for root in roots.values()):
            raise _corrupt()
        object.__setattr__(self, "payload_event_by_object", MappingProxyType(payloads))
        object.__setattr__(self, "evidence_sources_by_object", MappingProxyType(evidence))
        object.__setattr__(self, "redaction_root_by_object", MappingProxyType(roots))

    @staticmethod
    def _copy_payload_owners(
        source: Mapping[ObjectId, EventId],
    ) -> dict[ObjectId, EventId]:
        if not isinstance(cast(object, source), Mapping):
            raise _corrupt()
        result: dict[ObjectId, EventId] = {}
        try:
            for raw_object, raw_event in source.items():
                result[object_id(raw_object)] = event_id(raw_event)
        except ValueError as exc:
            raise _corrupt() from exc
        return result

    @staticmethod
    def _copy_evidence_sources(
        source: Mapping[ObjectId, tuple[EvidenceObjectSource, ...]],
    ) -> dict[ObjectId, tuple[EvidenceObjectSource, ...]]:
        if not isinstance(cast(object, source), Mapping):
            raise _corrupt()
        result: dict[ObjectId, tuple[EvidenceObjectSource, ...]] = {}
        try:
            for raw_object, raw_sources in source.items():
                key = object_id(raw_object)
                if type(raw_sources) is not tuple or any(
                    type(item) is not EvidenceObjectSource for item in raw_sources
                ):
                    raise _corrupt()
                ordered = tuple(
                    sorted(
                        raw_sources,
                        key=lambda item: (
                            _ascii_key(item.evidence_id),
                            _ascii_key(item.source_event_id),
                        ),
                    )
                )
                if raw_sources != ordered or len(raw_sources) != len(set(raw_sources)):
                    raise _corrupt()
                result[key] = tuple(raw_sources)
        except ValueError as exc:
            if str(exc) == "projection_corrupt":
                raise
            raise _corrupt() from exc
        return result

    @staticmethod
    def _copy_redaction_roots(
        source: Mapping[ObjectId, EventId],
    ) -> dict[ObjectId, EventId]:
        if not isinstance(cast(object, source), Mapping):
            raise _corrupt()
        result: dict[ObjectId, EventId] = {}
        try:
            for raw_object, raw_event in source.items():
                result[object_id(raw_object)] = event_id(raw_event)
        except ValueError as exc:
            raise _corrupt() from exc
        return result


def empty_replay_index() -> ReplayIndex:
    """Return the exact non-plaintext genesis reverse index."""

    return ReplayIndex(
        frontier=0,
        head_digest="genesis",
        payload_event_by_object={},
        evidence_sources_by_object={},
        redaction_root_by_object={},
    )


def extend_replay_index(index: ReplayIndex, event: LedgerRecord) -> ReplayIndex:
    """Validate and extend the reverse index by one accepted envelope."""

    if type(index) is not ReplayIndex:
        raise _corrupt()
    _next_record(index.frontier, index.head_digest, event)
    payload_owners = dict(index.payload_event_by_object)
    evidence_sources = dict(index.evidence_sources_by_object)
    redaction_roots = dict(index.redaction_root_by_object)

    payload_object = event.payload_ref.object_id
    if payload_object in payload_owners:
        raise _corrupt()
    payload_owners[payload_object] = event.event_id

    if type(event) is AcceptedEvent and event.schema.name == "evidence_recorded":
        logical_key = event.projection_locator.logical_key
        if logical_key is None or len(event.artifact_refs) > 1:
            raise _corrupt()
        try:
            evidence_key = evidence_id(logical_key)
        except ValueError as exc:
            raise _corrupt() from exc
        if event.payload is not None:
            payload = cast(EvidenceRecordedPayload, event.payload)
            expected = () if payload.captured_object_id is None else (payload.captured_object_id,)
            if event.artifact_refs != expected:
                raise _corrupt()
        for captured_object in event.artifact_refs:
            association = EvidenceObjectSource(evidence_key, event.event_id)
            current = evidence_sources.get(captured_object, ())
            if association in current:
                raise _corrupt()
            evidence_sources[captured_object] = tuple(
                sorted(
                    (*current, association),
                    key=lambda item: (
                        _ascii_key(item.evidence_id),
                        _ascii_key(item.source_event_id),
                    ),
                )
            )

    if type(event) is AcceptedEvent and event.schema.name == "redaction_recorded":
        targets = event.projection_locator.redaction_target_object_ids
        if event.artifact_refs != targets or event.payload_ref.object_id in targets:
            raise _corrupt()
        if event.payload is not None:
            payload = cast(RedactionRecordedPayload, event.payload)
            if (
                payload.target_event_ids != event.projection_locator.redaction_target_event_ids
                or payload.target_object_ids != targets
            ):
                raise _corrupt()
        for target in targets:
            redaction_roots.setdefault(target, event.event_id)

    return ReplayIndex(
        frontier=event.ledger.ingestion_sequence,
        head_digest=event.entry_digest,
        payload_event_by_object=payload_owners,
        evidence_sources_by_object=evidence_sources,
        redaction_root_by_object=redaction_roots,
    )


def _projection_record[T](event: AcceptedEvent, payload: T) -> ProjectionRecord[T]:
    return ProjectionRecord(
        payload=payload,
        payload_digest=event.projection_locator.canonical_payload_digest,
        redacted=False,
        source_event_id=event.event_id,
        source_frontier=event.ledger.ingestion_sequence,
    )


def _tombstone[T](event: AcceptedEvent, payload_type: type[T]) -> ProjectionRecord[T]:
    del payload_type
    return ProjectionRecord(
        payload=None,
        payload_digest=event.projection_locator.canonical_payload_digest,
        redacted=True,
        source_event_id=event.event_id,
        source_frontier=event.ledger.ingestion_sequence,
    )


def _verify_exact_event(state: ProjectionState, event: LedgerRecord, index: ReplayIndex) -> None:
    if type(state) is not ProjectionState or type(index) is not ReplayIndex:
        raise _corrupt()
    _next_record(state.frontier, state.head_digest, event)
    if (
        index.frontier != event.ledger.ingestion_sequence
        or index.head_digest != event.entry_digest
        or index.payload_event_by_object.get(event.payload_ref.object_id) != event.event_id
    ):
        raise _corrupt()
    for marker in state.coverage_gaps:
        if marker.startswith("redacted_object:"):
            try:
                target = object_id(marker.removeprefix("redacted_object:"))
            except ValueError as exc:
                raise _corrupt() from exc
            if target not in index.redaction_root_by_object:
                raise _corrupt()
    if type(event) is AcceptedEvent and event.payload is not None:
        if (
            canonical_digest(encode_payload(event.payload))
            != event.projection_locator.canonical_payload_digest
        ):
            raise _corrupt()
    if type(event) is AcceptedEvent and event.schema.name == "evidence_recorded":
        logical_key = event.projection_locator.logical_key
        if logical_key is None:
            raise _corrupt()
        association = EvidenceObjectSource(evidence_id(logical_key), event.event_id)
        associated_objects = tuple(
            target_object
            for target_object, sources in index.evidence_sources_by_object.items()
            if association in sources
        )
        if tuple(sorted(associated_objects, key=_ascii_key)) != event.artifact_refs:
            raise _corrupt()
    if type(event) is AcceptedEvent and event.schema.name == "redaction_recorded":
        for target_object in event.projection_locator.redaction_target_object_ids:
            if target_object not in index.redaction_root_by_object:
                raise _corrupt()


def _plan_key(event: AcceptedEvent) -> int:
    logical_key = event.projection_locator.logical_key
    if logical_key is None:
        raise _corrupt()
    try:
        parsed = int(logical_key)
    except ValueError as exc:
        raise _corrupt() from exc
    if str(parsed) != logical_key or parsed < 1:
        raise _corrupt()
    return parsed


def _locator_id[T](event: AcceptedEvent, constructor: Callable[[object], T]) -> T:
    logical_key = event.projection_locator.logical_key
    if logical_key is None:
        raise _corrupt()
    try:
        return constructor(logical_key)
    except ValueError as exc:
        raise _corrupt() from exc


def _apply_obligation(
    obligations: dict[ObligationId, ObligationProjectionRecord],
    event: AcceptedEvent,
) -> None:
    key = _locator_id(event, obligation_id)
    existing = obligations.get(key)
    if event.payload is None:
        obligations[key] = ObligationProjectionRecord(
            payload=None,
            payload_digest=event.projection_locator.canonical_payload_digest,
            redacted=True,
            source_event_id=event.event_id,
            source_frontier=event.ledger.ingestion_sequence,
        )
        return
    payload = cast(ObligationPublishedPayload, event.payload)
    if existing is not None and existing.payload is not None:
        previous = existing.payload
        # Resolution is open→resolved only. Meaning fields must repeat; only status and
        # resolution_evidence_refs may change. The comparison deliberately clears evidence
        # refs for meaning equality — that is not free mutation of resolved history.
        meaning_diffs = obligation_meaning_field_diffs(previous, payload)
        valid_transition = (
            previous.status is ObligationStatus.OPEN and payload.status is ObligationStatus.RESOLVED
        )
        if not valid_transition or meaning_diffs:
            if meaning_diffs:
                raise ObligationResolutionMismatch(
                    meaning_diffs,
                    invariant="meaning_fields_must_repeat",
                    event_id=event.event_id,
                )
            raise ObligationResolutionMismatch(
                ("status",),
                invariant="open_to_resolved_only",
                event_id=event.event_id,
            )
    obligations[key] = ObligationProjectionRecord(
        payload=payload,
        payload_digest=event.projection_locator.canonical_payload_digest,
        redacted=False,
        source_event_id=event.event_id,
        source_frontier=event.ledger.ingestion_sequence,
        plan_change=None if existing is None else existing.plan_change,
        plan_change_reason=None if existing is None else existing.plan_change_reason,
        superseded_by_obligation_ids=(
            () if existing is None else existing.superseded_by_obligation_ids
        ),
    )


def _redact_current_records(
    target_event_ids: tuple[EventId, ...],
    plans: dict[int, PlanProjectionRecord],
    obligations: dict[ObligationId, ObligationProjectionRecord],
    decisions: dict[EventId, DecisionProjectionRecord],
    assignments: dict[EventId, ProjectionRecord[AssignmentRecordedPayload]],
    actions: dict[ActionId, ProjectionRecord[ActionRecordedPayload]],
    results: dict[ResultId, ProjectionRecord[ResultRecordedPayload]],
    evidence: dict[EvidenceId, EvidenceProjectionRecord],
    claims: dict[ClaimId, ProjectionRecord[ClaimRecordedPayload]],
    findings: dict[FindingId, ProjectionRecord[Finding]],
    responses: dict[FindingId, ProjectionRecord[ResponseRecordedPayload]],
) -> None:
    targets = frozenset(target_event_ids)
    for key, record in tuple(plans.items()):
        if record.source_event_id in targets:
            plans[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(obligations.items()):
        if record.source_event_id in targets:
            obligations[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(decisions.items()):
        if record.source_event_id in targets:
            decisions[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(assignments.items()):
        if record.source_event_id in targets:
            assignments[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(actions.items()):
        if record.source_event_id in targets:
            actions[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(results.items()):
        if record.source_event_id in targets:
            results[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(evidence.items()):
        if record.source_event_id in targets:
            evidence[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(claims.items()):
        if record.source_event_id in targets:
            claims[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(findings.items()):
        if record.source_event_id in targets:
            findings[key] = replace(record, payload=None, redacted=True)
    for key, record in tuple(responses.items()):
        if record.source_event_id in targets:
            responses[key] = replace(record, payload=None, redacted=True)


def _recompute_secondary_effects(
    plans: dict[int, PlanProjectionRecord],
    obligations: dict[ObligationId, ObligationProjectionRecord],
    decisions: dict[EventId, DecisionProjectionRecord],
    claims: Mapping[ClaimId, ProjectionRecord[ClaimRecordedPayload]],
) -> dict[ContradictionKey, ContradictionRecord]:
    for key, record in tuple(plans.items()):
        plans[key] = replace(record, superseded_by_plan_version=None)
    for key, record in tuple(obligations.items()):
        obligations[key] = replace(
            record,
            plan_change=None,
            plan_change_reason=None,
            superseded_by_obligation_ids=(),
        )
    revisions = sorted(
        (record for record in plans.values() if type(record.payload) is PlanRevisedPayload),
        key=lambda record: (record.source_frontier, _ascii_key(record.source_event_id)),
    )
    for record in revisions:
        payload = cast(PlanRevisedPayload, record.payload)
        prior = plans.get(payload.supersedes_plan_version)
        if prior is not None:
            plans[payload.supersedes_plan_version] = replace(
                prior,
                superseded_by_plan_version=payload.plan_version,
            )
        for change in payload.obligation_changes:
            obligation = obligations.get(change.obligation_id)
            if obligation is not None:
                obligations[change.obligation_id] = replace(
                    obligation,
                    plan_change=change.change,
                    plan_change_reason=change.reason,
                    superseded_by_obligation_ids=change.replacement_obligation_ids,
                )

    for key, record in tuple(decisions.items()):
        decisions[key] = replace(record, superseded_by_event_id=None)
    ordered_decisions = sorted(
        (
            record
            for record in decisions.values()
            if type(record.payload) is DecisionRecordedPayload
        ),
        key=lambda record: (record.source_frontier, _ascii_key(record.source_event_id)),
    )
    for record in ordered_decisions:
        payload = cast(DecisionRecordedPayload, record.payload)
        if payload.supersedes_event_id is not None:
            prior = decisions.get(payload.supersedes_event_id)
            if prior is not None:
                decisions[payload.supersedes_event_id] = replace(
                    prior,
                    superseded_by_event_id=record.source_event_id,
                )

    contradictions: dict[ContradictionKey, ContradictionRecord] = {}
    for record in sorted(
        claims.values(),
        key=lambda item: (item.source_frontier, _ascii_key(item.source_event_id)),
    ):
        if type(record.payload) is not ClaimRecordedPayload:
            continue
        for disputed_ref in record.payload.disputes_refs:
            key = ContradictionKey(record.payload.claim_id, disputed_ref)
            contradictions[key] = ContradictionRecord(
                disputing_claim_id=record.payload.claim_id,
                disputed_ref=disputed_ref,
                source_event_id=record.source_event_id,
                source_frontier=record.source_frontier,
            )
    return contradictions


def _target_visible(
    target: str,
    obligations: Mapping[ObligationId, object],
    actions: Mapping[ActionId, object],
    results: Mapping[ResultId, object],
    evidence: Mapping[EvidenceId, object],
    claims: Mapping[ClaimId, object],
    findings: Mapping[FindingId, object],
) -> bool:
    if target.startswith("obl_"):
        return obligation_id(target) in obligations
    if target.startswith("act_"):
        return action_id(target) in actions
    if target.startswith("res_"):
        return result_id(target) in results
    if target.startswith("evd_"):
        return evidence_id(target) in evidence
    if target.startswith("clm_"):
        return claim_id(target) in claims
    if target.startswith("fnd_"):
        return finding_id(target) in findings
    raise _corrupt()


def _recompute_missing_gaps(
    retained_gaps: set[str],
    plans: Mapping[int, PlanProjectionRecord],
    obligations: Mapping[ObligationId, ObligationProjectionRecord],
    decisions: Mapping[EventId, DecisionProjectionRecord],
    assignments: Mapping[EventId, ProjectionRecord[AssignmentRecordedPayload]],
    actions: Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]],
    results: Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]],
    evidence: Mapping[EvidenceId, EvidenceProjectionRecord],
    claims: Mapping[ClaimId, ProjectionRecord[ClaimRecordedPayload]],
    findings: Mapping[FindingId, ProjectionRecord[Finding]],
    responses: Mapping[FindingId, ProjectionRecord[ResponseRecordedPayload]],
) -> tuple[str, ...]:
    gaps = {marker for marker in retained_gaps if not marker.startswith("missing_ref:")}

    def require(source_event: EventId, target: str) -> None:
        if not _target_visible(
            target,
            obligations,
            actions,
            results,
            evidence,
            claims,
            findings,
        ):
            gaps.add(f"missing_ref:{source_event}:{target}")

    for record in plans.values():
        payload = record.payload
        if type(payload) is PlanPublishedPayload:
            for target in payload.obligation_refs:
                require(record.source_event_id, target)
        elif type(payload) is PlanRevisedPayload:
            for change in payload.obligation_changes:
                require(record.source_event_id, change.obligation_id)
                for target in change.replacement_obligation_ids:
                    require(record.source_event_id, target)
    for record in obligations.values():
        if record.payload is not None:
            for target in record.payload.resolution_evidence_refs:
                require(record.source_event_id, target)
    for record in decisions.values():
        if record.payload is not None:
            for target in record.payload.affected_obligation_ids:
                require(record.source_event_id, target)
    for record in assignments.values():
        if record.payload is not None:
            for target in record.payload.obligation_ids:
                require(record.source_event_id, target)
    for record in actions.values():
        if record.payload is not None:
            for target in record.payload.obligation_refs:
                require(record.source_event_id, target)
    for record in results.values():
        if record.payload is not None:
            require(record.source_event_id, record.payload.action_id)
            for target in record.payload.evidence_refs:
                require(record.source_event_id, target)
    for record in claims.values():
        if record.payload is not None:
            for target in record.payload.supporting_refs:
                require(record.source_event_id, target)
            for target in record.payload.obligation_refs:
                require(record.source_event_id, target)
            for target in record.payload.disputes_refs:
                if target.startswith("clm_"):
                    require(record.source_event_id, target)
    for record in findings.values():
        if record.payload is not None:
            for target in record.payload.subject_refs:
                if not target.startswith("evt_"):
                    require(record.source_event_id, target)
    for record in responses.values():
        if record.payload is not None:
            require(record.source_event_id, record.payload.finding_id)
            for target in record.payload.evidence_refs:
                require(record.source_event_id, target)
    return tuple(sorted(gaps, key=_ascii_key))


def _freshness(
    frontier: int,
    gaps: tuple[str, ...],
    *,
    stale: bool,
    check_freshness: LedgerFreshness | None,
) -> LedgerFreshness:
    if frontier == 0:
        return LedgerFreshness.UNKNOWN
    if any(marker.startswith("redacted_") for marker in gaps):
        return LedgerFreshness.REDACTED_GAP
    if any(
        marker.startswith("unknown_event:") or marker.startswith("missing_ref:") for marker in gaps
    ):
        return LedgerFreshness.PARTIAL
    if stale:
        return LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE
    if check_freshness is not None:
        return check_freshness
    return LedgerFreshness.CURRENT


def reduce_event(
    state: ProjectionState,
    event: LedgerRecord,
    replay_index: ReplayIndex,
) -> ProjectionState:
    """Fold one exact next accepted record without I/O or mutation."""

    _verify_exact_event(state, event, replay_index)
    plans = dict(state.plans)
    obligations = dict(state.obligations)
    decisions = dict(state.decisions)
    assignments = dict(state.assignments)
    actions = dict(state.actions)
    results = dict(state.results)
    evidence = dict(state.evidence)
    claims = dict(state.claims)
    findings = dict(state.findings)
    responses = dict(state.responses)
    contradictions = dict(state.contradictions)
    gaps = set(state.coverage_gaps)
    latest = state.latest_tested_state
    unknown_count = state.unknown_event_count
    stale = state.freshness is LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE
    check_freshness: LedgerFreshness | None = None

    if type(event) is UnknownEvent:
        unknown_count += 1
        gaps.add(f"unknown_event:{event.event_id}:{event.schema.name}@{event.schema.version}")
        coverage_gaps = tuple(sorted(gaps, key=_ascii_key))
    else:
        accepted = cast(AcceptedEvent, event)
        family = accepted.schema.name
        payload = accepted.payload
        if latest is not None and supersedes_recorded_check(
            family, payload, latest.returned_finding_ids
        ):
            stale = True

        if family in {"session_opened", "session_resumed", "receipt_recorded"}:
            pass
        elif family in {"plan_published", "plan_revised"}:
            key = _plan_key(accepted)
            if key in plans:
                raise _corrupt()
            if payload is None:
                plans[key] = PlanProjectionRecord(
                    payload=None,
                    payload_digest=accepted.projection_locator.canonical_payload_digest,
                    redacted=True,
                    source_event_id=accepted.event_id,
                    source_frontier=accepted.ledger.ingestion_sequence,
                )
            else:
                plans[key] = PlanProjectionRecord(
                    payload=cast(PlanPublishedPayload | PlanRevisedPayload, payload),
                    payload_digest=accepted.projection_locator.canonical_payload_digest,
                    redacted=False,
                    source_event_id=accepted.event_id,
                    source_frontier=accepted.ledger.ingestion_sequence,
                )
                typed_plan = cast(PlanPublishedPayload | PlanRevisedPayload, payload)
                if typed_plan.no_obligations_reason is not None:
                    scope = current_plan_scope(
                        plans,
                        tuple(sorted(gaps, key=_ascii_key)),
                    )
                    if not scope.readable or scope.declared_obligation_count != 0:
                        raise NoObligationsReasonMismatch(event_id=accepted.event_id)
        elif family == "obligation_published":
            _apply_obligation(obligations, accepted)
        elif family == "assignment_recorded":
            if accepted.event_id in assignments:
                raise _corrupt()
            assignments[accepted.event_id] = (
                _tombstone(accepted, AssignmentRecordedPayload)
                if payload is None
                else _projection_record(accepted, cast(AssignmentRecordedPayload, payload))
            )
        elif family == "decision_recorded":
            if accepted.event_id in decisions:
                raise _corrupt()
            decisions[accepted.event_id] = DecisionProjectionRecord(
                payload=(None if payload is None else cast(DecisionRecordedPayload, payload)),
                payload_digest=accepted.projection_locator.canonical_payload_digest,
                redacted=payload is None,
                source_event_id=accepted.event_id,
                source_frontier=accepted.ledger.ingestion_sequence,
            )
        elif family == "action_recorded":
            key = _locator_id(accepted, action_id)
            actions[key] = (
                _tombstone(accepted, ActionRecordedPayload)
                if payload is None
                else _projection_record(accepted, cast(ActionRecordedPayload, payload))
            )
        elif family == "result_recorded":
            key = _locator_id(accepted, result_id)
            results[key] = (
                _tombstone(accepted, ResultRecordedPayload)
                if payload is None
                else _projection_record(accepted, cast(ResultRecordedPayload, payload))
            )
        elif family == "evidence_recorded":
            key = _locator_id(accepted, evidence_id)
            evidence[key] = EvidenceProjectionRecord(
                payload=(None if payload is None else cast(EvidenceRecordedPayload, payload)),
                payload_digest=accepted.projection_locator.canonical_payload_digest,
                redacted=payload is None,
                source_event_id=accepted.event_id,
                source_frontier=accepted.ledger.ingestion_sequence,
            )
        elif family == "claim_recorded":
            key = _locator_id(accepted, claim_id)
            claims[key] = (
                _tombstone(accepted, ClaimRecordedPayload)
                if payload is None
                else _projection_record(accepted, cast(ClaimRecordedPayload, payload))
            )
        elif family == "finding_recorded":
            key = _locator_id(accepted, finding_id)
            findings[key] = (
                _tombstone(accepted, Finding)
                if payload is None
                else _projection_record(accepted, cast(FindingRecordedPayload, payload))
            )
        elif family == "response_recorded":
            key = _locator_id(accepted, finding_id)
            responses[key] = (
                _tombstone(accepted, ResponseRecordedPayload)
                if payload is None
                else _projection_record(accepted, cast(ResponseRecordedPayload, payload))
            )
        elif family == "check_recorded":
            if payload is None:
                latest = None
                stale = False
            else:
                check = cast(CheckRecordedPayload, payload)
                latest = LatestTestedState(
                    source_check_event_id=accepted.event_id,
                    subject_frontier=check.subject_frontier,
                    verdict=check.verdict,
                    returned_finding_ids=check.returned_finding_ids,
                    suppressed_count=check.suppressed_count,
                    coverage=check.coverage,
                )
                check_freshness = check.coverage.ledger_freshness
                stale = check_freshness is LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE
        elif family == "redaction_recorded":
            event_targets = set(accepted.projection_locator.redaction_target_event_ids)
            object_targets = accepted.projection_locator.redaction_target_object_ids
            for target_object in object_targets:
                owner = replay_index.payload_event_by_object.get(target_object)
                if owner is not None:
                    event_targets.add(owner)
            ordered_event_targets = tuple(sorted(event_targets, key=_ascii_key))
            _redact_current_records(
                ordered_event_targets,
                plans,
                obligations,
                decisions,
                assignments,
                actions,
                results,
                evidence,
                claims,
                findings,
                responses,
            )
            if latest is not None and latest.source_check_event_id in event_targets:
                latest = None
                stale = False
            for target_event in ordered_event_targets:
                gaps.add(f"redacted_event:{target_event}")
            for target_object in object_targets:
                root = replay_index.redaction_root_by_object.get(target_object)
                if root is None:
                    raise _corrupt()
                del root
                for association in replay_index.evidence_sources_by_object.get(target_object, ()):
                    record = evidence.get(association.evidence_id)
                    if record is not None and record.source_event_id == association.source_event_id:
                        evidence[association.evidence_id] = replace(
                            record,
                            object_available=False,
                            redacted_object_id=target_object,
                        )
                gaps.add(f"redacted_object:{target_object}")
        else:
            raise _corrupt()

        contradictions = _recompute_secondary_effects(
            plans,
            obligations,
            decisions,
            claims,
        )
        coverage_gaps = _recompute_missing_gaps(
            gaps,
            plans,
            obligations,
            decisions,
            assignments,
            actions,
            results,
            evidence,
            claims,
            findings,
            responses,
        )

    frontier = event.ledger.ingestion_sequence
    freshness = _freshness(
        frontier,
        coverage_gaps,
        stale=stale,
        check_freshness=check_freshness,
    )
    return ProjectionState(
        frontier=frontier,
        head_digest=event.entry_digest,
        plans=plans,
        obligations=obligations,
        decisions=decisions,
        assignments=assignments,
        actions=actions,
        results=results,
        evidence=evidence,
        claims=claims,
        contradictions=contradictions,
        findings=findings,
        responses=responses,
        latest_tested_state=latest,
        freshness=freshness,
        unknown_event_count=unknown_count,
        coverage_gaps=coverage_gaps,
    )


def replay(events: Iterable[LedgerRecord]) -> ProjectionState:
    """Fold an already ledger-ordered iterable from genesis without sorting."""

    state = empty_projection_state()
    index = empty_replay_index()
    for event in events:
        index = extend_replay_index(index, event)
        state = reduce_event(state, event, index)
    return state
