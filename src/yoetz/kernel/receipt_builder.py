"""Pure canonical receipt assembly from one complete frozen application context."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final, cast

from yoetz.domain.events import (
    CheckRecordedPayload,
    ClaimKind,
    ObligationChangeKind,
    ObligationStatus,
    PlanPublishedPayload,
    PlanRevisedPayload,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CheckVerdict,
    Finding,
    FindingOrigin,
    ResponseDisposition,
    rank_key,
)
from yoetz.domain.receipts import (
    ReceiptConclusion,
    ReceiptDocument,
    ReceiptGap,
    ReceiptObligation,
    ReceiptObligationStatus,
    ReceiptRedaction,
    ReceiptRedactionCategory,
    ReceiptRedactionReason,
    ReceiptResponse,
    ReceiptSection,
    ReceiptSectionKey,
    ReceiptVersionSlice,
)
from yoetz.domain.values import (
    ClaimId,
    EventId,
    EvidenceId,
    FindingId,
    Frontier,
    ObligationId,
    ReceiptId,
    ResultId,
    SessionId,
    TaskId,
    Timestamp,
    finding_id,
)
from yoetz.kernel.deterministic_checks import CaseAvailabilityFacts, CaseGap
from yoetz.kernel.projections import ObligationProjectionRecord, ProjectionRecord, ProjectionState
from yoetz.protocol.coverage import LEDGER_FRESHNESS_ORDER, Coverage, weakest
from yoetz.protocol.models import ReceiptInclude, ReceiptRedactionProfile

__all__ = [
    "ReceiptBuildContext",
    "ReceiptFindingState",
    "build_receipt",
]

_CONTEXT_INVALID: Final = "receipt_build_context_invalid"
_CHECK_ABSENCE_GAPS: Final = frozenset(
    {"check_not_recorded", "check_not_applicable", "check_payload_unavailable"}
)
_SECTION_TITLES: Final = {
    ReceiptSectionKey.SUMMARY: "Summary",
    ReceiptSectionKey.OUTSTANDING_WORK: "Outstanding work",
    ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS: "Findings",
    ReceiptSectionKey.EVIDENCE_AND_CLAIM_BASIS: "Evidence basis",
    ReceiptSectionKey.LIMITATIONS_AND_COVERAGE: "Limitations",
    ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY: "Versions and policy",
}
_SECTION_KEYS: Final = {
    ReceiptInclude.SUMMARY: (
        ReceiptSectionKey.SUMMARY,
        ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
        ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
    ),
    ReceiptInclude.STANDARD: (
        ReceiptSectionKey.SUMMARY,
        ReceiptSectionKey.OUTSTANDING_WORK,
        ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS,
        ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
        ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
    ),
    ReceiptInclude.FULL: (
        ReceiptSectionKey.SUMMARY,
        ReceiptSectionKey.OUTSTANDING_WORK,
        ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS,
        ReceiptSectionKey.EVIDENCE_AND_CLAIM_BASIS,
        ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
        ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
    ),
}


def _ascii_key(value: str) -> bytes:
    try:
        return value.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(_CONTEXT_INVALID) from exc


def _sorted_unique[T: str](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(sorted(set(values), key=_ascii_key))


@dataclass(frozen=True, slots=True)
class ReceiptFindingState:
    finding_id: FindingId
    resolved: bool

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "finding_id", finding_id(self.finding_id))
        except ValueError as exc:
            raise ValueError(_CONTEXT_INVALID) from exc
        if type(self.resolved) is not bool:
            raise ValueError(_CONTEXT_INVALID)


def _issue_key(finding: Finding) -> tuple[object, ...]:
    return (
        finding.origin,
        finding.policy_id,
        finding.policy_version,
        finding.kind,
        finding.subject_refs,
    )


def _projection_records(projection: ProjectionState) -> tuple[ProjectionRecord[object], ...]:
    records: list[ProjectionRecord[object]] = []
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
    ):
        records.extend(cast(Iterable[ProjectionRecord[object]], collection.values()))
    return tuple(records)


def _validate_availability(context: ReceiptBuildContext) -> None:
    gaps_by_marker = {gap.marker: gap for gap in context.gaps}
    projection_records = _projection_records(context.projection)
    for source_event_id in context.availability.unavailable_event_ids:
        marker = f"unavailable_event:{source_event_id}"
        gap = gaps_by_marker.get(marker)
        if (
            gap is None
            or gap.code != "event_payload_unavailable"
            or gap.subject_refs != (source_event_id,)
            or not any(
                record.source_event_id == source_event_id and record.payload is None
                for record in projection_records
            )
        ):
            raise ValueError(_CONTEXT_INVALID)
    for unavailable in context.availability.unavailable_captured_objects:
        marker = (
            f"unavailable_captured_object:{unavailable.source_event_id}:{unavailable.object_id}"
        )
        gap = gaps_by_marker.get(marker)
        evidence_matches = tuple(
            record
            for record in context.projection.evidence.values()
            if record.source_event_id == unavailable.source_event_id
            and record.payload is not None
            and record.payload.captured_object_id == unavailable.object_id
        )
        if (
            gap is None
            or gap.code != "captured_object_unavailable"
            or gap.subject_refs != (unavailable.source_event_id,)
            or len(evidence_matches) != 1
        ):
            raise ValueError(_CONTEXT_INVALID)


def _validate_finding_states(context: ReceiptBuildContext) -> None:
    ids = tuple(state.finding_id for state in context.finding_states)
    if len(ids) != len(set(ids)):
        raise ValueError(_CONTEXT_INVALID)
    findings: list[Finding] = []
    for state in context.finding_states:
        record = context.projection.findings.get(state.finding_id)
        if record is None or record.payload is None:
            raise ValueError(_CONTEXT_INVALID)
        findings.append(record.payload)
    typed = tuple(findings)
    if typed != tuple(sorted(typed, key=rank_key)):
        raise ValueError(_CONTEXT_INVALID)
    if len({_issue_key(finding) for finding in typed}) != len(typed):
        raise ValueError(_CONTEXT_INVALID)

    newest_readable: dict[tuple[object, ...], tuple[int, FindingId]] = {}
    for current_id, record in context.projection.findings.items():
        if record.payload is None:
            continue
        key = _issue_key(record.payload)
        candidate = (record.source_frontier, current_id)
        previous = newest_readable.get(key)
        if previous is None or candidate > previous:
            newest_readable[key] = candidate
    for finding in typed:
        newest = newest_readable[_issue_key(finding)][1]
        if newest != finding.finding_id:
            raise ValueError(_CONTEXT_INVALID)


def _validate_applicable_check(context: ReceiptBuildContext) -> None:
    latest = context.projection.latest_tested_state
    check = context.applicable_check
    if check is None:
        if not (_CHECK_ABSENCE_GAPS & set(context.coverage.known_gaps)):
            raise ValueError(_CONTEXT_INVALID)
        return
    if latest is None:
        raise ValueError(_CONTEXT_INVALID)
    if (
        check.subject_frontier != latest.subject_frontier
        or check.subject_frontier != context.subject_frontier
        or check.verdict is not latest.verdict
        or check.returned_finding_ids != latest.returned_finding_ids
        or check.suppressed_count != latest.suppressed_count
        or check.coverage != latest.coverage
    ):
        raise ValueError(_CONTEXT_INVALID)


@dataclass(frozen=True, slots=True)
class ReceiptBuildContext:
    projection: ProjectionState
    subject_frontier: Frontier
    availability: CaseAvailabilityFacts
    coverage: Coverage
    gaps: tuple[CaseGap, ...]
    finding_states: tuple[ReceiptFindingState, ...]
    applicable_check: CheckRecordedPayload | None

    def __post_init__(self) -> None:
        if (
            type(self.projection) is not ProjectionState
            or type(self.subject_frontier) is not Frontier
            or type(self.availability) is not CaseAvailabilityFacts
            or type(self.coverage) is not Coverage
            or type(self.gaps) is not tuple
            or any(type(gap) is not CaseGap for gap in self.gaps)
            or type(self.finding_states) is not tuple
            or any(type(state) is not ReceiptFindingState for state in self.finding_states)
            or (
                self.applicable_check is not None
                and type(self.applicable_check) is not CheckRecordedPayload
            )
        ):
            raise ValueError(_CONTEXT_INVALID)
        expected_frontier = Frontier(self.projection.frontier, self.projection.head_digest)
        if self.subject_frontier != expected_frontier:
            raise ValueError(_CONTEXT_INVALID)
        ordered_gaps = tuple(
            sorted(
                self.gaps,
                key=lambda gap: (
                    _ascii_key(gap.marker),
                    tuple(_ascii_key(ref) for ref in gap.subject_refs),
                ),
            )
        )
        if self.gaps != ordered_gaps or len({gap.marker for gap in self.gaps}) != len(self.gaps):
            raise ValueError(_CONTEXT_INVALID)
        if set(self.coverage.known_gaps) != {gap.code for gap in self.gaps}:
            raise ValueError(_CONTEXT_INVALID)
        if (
            LEDGER_FRESHNESS_ORDER[self.coverage.ledger_freshness]
            > LEDGER_FRESHNESS_ORDER[self.projection.freshness]
        ):
            raise ValueError(_CONTEXT_INVALID)
        for state in self.finding_states:
            record = self.projection.findings.get(state.finding_id)
            if record is None or record.payload is None:
                raise ValueError(_CONTEXT_INVALID)
            if weakest(self.coverage, record.payload.coverage) != self.coverage:
                raise ValueError(_CONTEXT_INVALID)
        if self.applicable_check is not None:
            if weakest(self.coverage, self.applicable_check.coverage) != self.coverage:
                raise ValueError(_CONTEXT_INVALID)
        _validate_availability(self)
        _validate_finding_states(self)
        _validate_applicable_check(self)


def _current_plan_obligation_ids(projection: ProjectionState) -> set[ObligationId]:
    published = sorted(
        (
            record
            for record in projection.plans.values()
            if type(record.payload) is PlanPublishedPayload
        ),
        key=lambda record: (record.source_frontier, _ascii_key(record.source_event_id)),
    )
    if not published:
        return set()
    current = set(cast(PlanPublishedPayload, published[0].payload).obligation_refs)
    revisions = sorted(
        (
            record
            for record in projection.plans.values()
            if type(record.payload) is PlanRevisedPayload
        ),
        key=lambda record: (record.source_frontier, _ascii_key(record.source_event_id)),
    )
    for record in revisions:
        payload = cast(PlanRevisedPayload, record.payload)
        for change in payload.obligation_changes:
            if change.change is ObligationChangeKind.SUPERSEDED:
                current.discard(change.obligation_id)
                current.update(change.replacement_obligation_ids)
            else:
                current.add(change.obligation_id)
    return current


def _receipt_obligation_status(record: ObligationProjectionRecord) -> ReceiptObligationStatus:
    if record.payload is None:
        raise ValueError(_CONTEXT_INVALID)
    if record.plan_change is ObligationChangeKind.SUPERSEDED:
        return ReceiptObligationStatus.SUPERSEDED
    if record.plan_change is ObligationChangeKind.WAIVED:
        return ReceiptObligationStatus.WAIVED
    if record.payload.status is ObligationStatus.RESOLVED:
        return ReceiptObligationStatus.RESOLVED
    return ReceiptObligationStatus.OPEN


def _select_obligations(
    context: ReceiptBuildContext,
    findings: tuple[Finding, ...],
) -> tuple[ReceiptObligation, ...]:
    selected = _current_plan_obligation_ids(context.projection)
    for finding in findings:
        selected.update(
            cast(ObligationId, ref) for ref in finding.subject_refs if ref.startswith("obl_")
        )
    if len(selected) > 100:
        raise ValueError(_CONTEXT_INVALID)

    obligations: list[ReceiptObligation] = []
    for obligation_id_value in _sorted_unique(selected):
        record = context.projection.obligations.get(obligation_id_value)
        if record is None or record.payload is None:
            continue
        claim_roots: set[ClaimId] = set()
        for claim_id_value, claim_record in context.projection.claims.items():
            if (
                claim_record.payload is not None
                and obligation_id_value in claim_record.payload.obligation_refs
            ):
                claim_roots.add(claim_id_value)
        for finding in findings:
            if obligation_id_value in finding.subject_refs:
                claim_roots.update(
                    cast(ClaimId, ref) for ref in finding.subject_refs if ref.startswith("clm_")
                )
        source_refs = _sorted_unique((*record.payload.source_refs, *claim_roots))
        obligations.append(
            ReceiptObligation(
                obligation_id=obligation_id_value,
                status=_receipt_obligation_status(record),
                source_refs=source_refs,
                summary=record.payload.description,
            )
        )
    return tuple(obligations)


def _select_claim_refs(
    context: ReceiptBuildContext,
    findings: tuple[Finding, ...],
) -> tuple[ClaimId, ...]:
    refs: set[ClaimId] = {
        claim_id_value
        for claim_id_value, record in context.projection.claims.items()
        if record.payload is not None and record.payload.claim_kind is ClaimKind.COMPLETION
    }
    for finding in findings:
        refs.update(cast(ClaimId, ref) for ref in finding.subject_refs if ref.startswith("clm_"))
    for gap in context.gaps:
        refs.update(cast(ClaimId, ref) for ref in gap.subject_refs if ref.startswith("clm_"))
    if len(refs) > 100:
        raise ValueError(_CONTEXT_INVALID)
    return _sorted_unique(refs)


def _evidence_from_result(
    projection: ProjectionState,
    result_id_value: ResultId,
) -> set[EvidenceId]:
    record = projection.results.get(result_id_value)
    if record is None or record.payload is None:
        return set()
    return set(record.payload.evidence_refs)


def _select_evidence_refs(
    context: ReceiptBuildContext,
    claims: tuple[ClaimId, ...],
    obligations: tuple[ReceiptObligation, ...],
    responses: tuple[ReceiptResponse, ...],
) -> tuple[EvidenceId, ...]:
    refs: set[EvidenceId] = set()
    for claim_id_value in claims:
        record = context.projection.claims.get(claim_id_value)
        if record is None or record.payload is None:
            continue
        for supporting_ref in record.payload.supporting_refs:
            if supporting_ref.startswith("evd_"):
                refs.add(cast(EvidenceId, supporting_ref))
            elif supporting_ref.startswith("res_"):
                refs.update(
                    _evidence_from_result(context.projection, cast(ResultId, supporting_ref))
                )
            elif supporting_ref.startswith("obl_"):
                obligation_record = context.projection.obligations.get(
                    cast(ObligationId, supporting_ref)
                )
                if obligation_record is not None and obligation_record.payload is not None:
                    for resolution_ref in obligation_record.payload.resolution_evidence_refs:
                        if resolution_ref.startswith("evd_"):
                            refs.add(cast(EvidenceId, resolution_ref))
                        else:
                            refs.update(
                                _evidence_from_result(
                                    context.projection, cast(ResultId, resolution_ref)
                                )
                            )
    for obligation in obligations:
        record = context.projection.obligations.get(obligation.obligation_id)
        if record is None or record.payload is None:
            continue
        for resolution_ref in record.payload.resolution_evidence_refs:
            if resolution_ref.startswith("evd_"):
                refs.add(cast(EvidenceId, resolution_ref))
            else:
                refs.update(
                    _evidence_from_result(context.projection, cast(ResultId, resolution_ref))
                )
    for response in responses:
        for ref in response.evidence_refs:
            if ref.startswith("evd_"):
                refs.add(cast(EvidenceId, ref))
            else:
                refs.update(_evidence_from_result(context.projection, cast(ResultId, ref)))
    for gap in context.gaps:
        for root in gap.subject_refs:
            if not root.startswith("evt_"):
                continue
            refs.update(
                evidence_id_value
                for evidence_id_value, record in context.projection.evidence.items()
                if record.source_event_id == root
            )
    if len(refs) > 100:
        raise ValueError(_CONTEXT_INVALID)
    return _sorted_unique(refs)


def _select_responses(
    context: ReceiptBuildContext,
    finding_ids: frozenset[FindingId],
) -> tuple[ReceiptResponse, ...]:
    values: list[ReceiptResponse] = []
    for finding_id_value in sorted(finding_ids, key=_ascii_key):
        record = context.projection.responses.get(finding_id_value)
        if record is None or record.payload is None:
            continue
        payload = record.payload
        values.append(
            ReceiptResponse(
                finding_id=payload.finding_id,
                finding_frontier=payload.finding_frontier,
                disposition=payload.disposition,
                evidence_refs=payload.evidence_refs,
                reason=payload.reason,
                waiver_scope=payload.waiver_scope,
                waiver_expiry=payload.waiver_expiry,
            )
        )
    if len(values) > 100:
        raise ValueError(_CONTEXT_INVALID)
    return tuple(values)


def _select_gaps(context: ReceiptBuildContext) -> tuple[ReceiptGap, ...]:
    values = tuple(
        ReceiptGap(code=gap.code, subject_refs=gap.subject_refs)
        for gap in sorted(
            context.gaps,
            key=lambda gap: (
                _ascii_key(gap.code),
                tuple(_ascii_key(ref) for ref in gap.subject_refs),
                _ascii_key(gap.marker),
            ),
        )
    )
    if len(values) > 64:
        raise ValueError(_CONTEXT_INVALID)
    return values


def _redacted_event_category(
    projection: ProjectionState,
    source_event_id: EventId,
) -> ReceiptRedactionCategory:
    family_categories: tuple[
        tuple[Iterable[ProjectionRecord[object]], ReceiptRedactionCategory], ...
    ] = (
        (
            cast(Iterable[ProjectionRecord[object]], projection.claims.values()),
            ReceiptRedactionCategory.CLAIM_TEXT,
        ),
        (
            cast(Iterable[ProjectionRecord[object]], projection.findings.values()),
            ReceiptRedactionCategory.FINDING_DETAIL,
        ),
        (
            cast(Iterable[ProjectionRecord[object]], projection.responses.values()),
            ReceiptRedactionCategory.FINDING_DETAIL,
        ),
        (
            cast(Iterable[ProjectionRecord[object]], projection.obligations.values()),
            ReceiptRedactionCategory.OBLIGATION_TEXT,
        ),
        (
            cast(Iterable[ProjectionRecord[object]], projection.evidence.values()),
            ReceiptRedactionCategory.EVIDENCE_CONTENT,
        ),
    )
    for records, category in family_categories:
        if any(record.source_event_id == source_event_id for record in records):
            return category
    return ReceiptRedactionCategory.REPOSITORY_CONTENT


def _source_redaction_counts(
    context: ReceiptBuildContext,
) -> Counter[tuple[ReceiptRedactionCategory, ReceiptRedactionReason]]:
    counts: Counter[tuple[ReceiptRedactionCategory, ReceiptRedactionReason]] = Counter()
    redacted_objects: set[str] = set()
    redacted_events: set[EventId] = set()
    for gap in context.gaps:
        if gap.marker.startswith("redacted_object:"):
            redacted_objects.add(gap.marker.removeprefix("redacted_object:"))
        elif gap.marker.startswith("redacted_event:"):
            redacted_events.add(cast(EventId, gap.marker.removeprefix("redacted_event:")))
    if redacted_objects:
        counts[
            (ReceiptRedactionCategory.EVIDENCE_CONTENT, ReceiptRedactionReason.SOURCE_REDACTED)
        ] += len(redacted_objects)
    for source_event_id in redacted_events:
        category = _redacted_event_category(context.projection, source_event_id)
        counts[(category, ReceiptRedactionReason.SOURCE_REDACTED)] += 1
    return counts


def _apply_profile(
    context: ReceiptBuildContext,
    profile: ReceiptRedactionProfile,
    findings: tuple[Finding, ...],
    obligations: tuple[ReceiptObligation, ...],
    responses: tuple[ReceiptResponse, ...],
    gaps: tuple[ReceiptGap, ...],
) -> tuple[
    tuple[Finding, ...],
    tuple[ReceiptObligation, ...],
    tuple[ReceiptResponse, ...],
    tuple[ReceiptGap, ...],
    tuple[ReceiptRedaction, ...],
]:
    counts = _source_redaction_counts(context)
    retained_findings = findings
    retained_obligations = obligations
    retained_responses = responses
    retained_gaps = gaps

    if profile in {
        ReceiptRedactionProfile.DEFAULT_LOCAL_EXPORT,
        ReceiptRedactionProfile.REDACTED_SHARE,
    }:
        cleared = sum(obligation.summary is not None for obligation in obligations)
        if cleared:
            counts[
                (ReceiptRedactionCategory.OBLIGATION_TEXT, ReceiptRedactionReason.POLICY_REDACTED)
            ] += cleared
        retained_obligations = tuple(
            replace(obligation, summary=None) for obligation in obligations
        )
        cleared_gaps = sum(gap.detail is not None for gap in gaps)
        if cleared_gaps:
            counts[
                (ReceiptRedactionCategory.FINDING_DETAIL, ReceiptRedactionReason.POLICY_REDACTED)
            ] += cleared_gaps
        retained_gaps = tuple(replace(gap, detail=None) for gap in gaps)

    if profile is ReceiptRedactionProfile.REDACTED_SHARE:
        semantic_count = sum(
            finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED for finding in findings
        )
        if semantic_count:
            counts[
                (ReceiptRedactionCategory.FINDING_DETAIL, ReceiptRedactionReason.POLICY_REDACTED)
            ] += 2 * semantic_count
        retained_findings = tuple(
            finding for finding in findings if finding.origin is FindingOrigin.DETERMINISTIC
        )
        transformed_responses: list[ReceiptResponse] = []
        for response in responses:
            if response.disposition is ResponseDisposition.ACKNOWLEDGED:
                if response.reason is not None:
                    counts[
                        (
                            ReceiptRedactionCategory.FINDING_DETAIL,
                            ReceiptRedactionReason.POLICY_REDACTED,
                        )
                    ] += 1
                transformed_responses.append(replace(response, reason=None))
            else:
                counts[
                    (
                        ReceiptRedactionCategory.FINDING_DETAIL,
                        ReceiptRedactionReason.POLICY_REDACTED,
                    )
                ] += 1
        retained_responses = tuple(transformed_responses)

    rows = tuple(
        ReceiptRedaction(category=category, reason=reason, count=count)
        for (category, reason), count in sorted(
            counts.items(),
            key=lambda item: (_ascii_key(item[0][0].value), _ascii_key(item[0][1].value)),
        )
        if count > 0
    )
    return (
        retained_findings,
        retained_obligations,
        retained_responses,
        retained_gaps,
        rows,
    )


def _conclusion(
    context: ReceiptBuildContext,
    unresolved_actionable: tuple[Finding, ...],
) -> ReceiptConclusion:
    if unresolved_actionable:
        return ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN
    check = context.applicable_check
    if check is None:
        return ReceiptConclusion.INSUFFICIENT_COVERAGE
    if check.suppressed_count > 0:
        return ReceiptConclusion.INSUFFICIENT_COVERAGE
    if check.verdict in {
        CheckVerdict.INSUFFICIENT_COVERAGE,
        CheckVerdict.INCOMPLETE_CHECK,
    }:
        return ReceiptConclusion.INSUFFICIENT_COVERAGE
    if check.verdict is CheckVerdict.ACTION_REQUIRED:
        raise ValueError(_CONTEXT_INVALID)
    executions_complete = all(
        execution.outcome == "run" and execution.reason == "completed"
        for execution in check.policy_executions
    )
    if not executions_complete or context.gaps or context.coverage.known_gaps:
        return ReceiptConclusion.INSUFFICIENT_COVERAGE
    return ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS


def _count_phrase(count: int, singular: str, plural: str) -> str:
    if count == 0:
        return f"no {plural}"
    if count == 1:
        return f"one {singular}"
    return f"{count} {plural}"


def _sections(
    *,
    include: ReceiptInclude,
    conclusion: ReceiptConclusion,
    frontier: Frontier,
    versions: ReceiptVersionSlice,
    findings: tuple[Finding, ...],
    unresolved_actionable_count: int,
    unresolved_actionable_ids: tuple[FindingId, ...],
    obligations: tuple[ReceiptObligation, ...],
    claim_refs: tuple[ClaimId, ...],
    evidence_refs: tuple[EvidenceId, ...],
    coverage: Coverage,
    redactions: tuple[ReceiptRedaction, ...],
) -> tuple[ReceiptSection, ...]:
    open_obligations = tuple(
        obligation
        for obligation in obligations
        if obligation.status is ReceiptObligationStatus.OPEN
    )
    gap_codes = coverage.known_gaps
    bodies: dict[ReceiptSectionKey, str] = {}
    items: dict[ReceiptSectionKey, tuple[str, ...]] = {}

    if conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS:
        bodies[ReceiptSectionKey.SUMMARY] = (
            f"No unresolved deterministic findings were recorded at frontier {frontier.sequence}."
        )
    elif conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE:
        bodies[ReceiptSectionKey.SUMMARY] = (
            f"Coverage is insufficient at frontier {frontier.sequence}."
        )
    else:
        phrase = _count_phrase(
            unresolved_actionable_count, "actionable finding", "actionable findings"
        )
        bodies[ReceiptSectionKey.SUMMARY] = (
            f"{phrase[:1].upper() + phrase[1:]} remain unresolved at frontier {frontier.sequence}."
        )
    items[ReceiptSectionKey.SUMMARY] = ()

    open_count = len(open_obligations)
    if open_count == 0:
        bodies[ReceiptSectionKey.OUTSTANDING_WORK] = "No open obligations are recorded."
    elif open_count == 1:
        bodies[ReceiptSectionKey.OUTSTANDING_WORK] = "One obligation remains open."
    else:
        bodies[ReceiptSectionKey.OUTSTANDING_WORK] = f"{open_count} obligations remain open."
    items[ReceiptSectionKey.OUTSTANDING_WORK] = tuple(
        obligation.obligation_id for obligation in open_obligations
    )

    actionable_count = unresolved_actionable_count
    if actionable_count == 0 and conclusion is not ReceiptConclusion.INSUFFICIENT_COVERAGE:
        bodies[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS] = "No findings remain open."
    elif actionable_count == 0:
        bodies[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS] = (
            "No actionable finding is selected, but weak coverage prevents the strong conclusion."
        )
    elif actionable_count == 1:
        bodies[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS] = (
            "One actionable finding remains unresolved."
        )
    else:
        bodies[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS] = (
            f"{actionable_count} actionable findings remain unresolved."
        )
    items[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS] = tuple(
        finding_id_value
        for finding_id_value in unresolved_actionable_ids
        if any(finding.finding_id == finding_id_value for finding in findings)
    )

    claim_phrase = _count_phrase(len(claim_refs), "claim reference", "claim references")
    evidence_phrase = _count_phrase(len(evidence_refs), "evidence reference", "evidence references")
    bodies[ReceiptSectionKey.EVIDENCE_AND_CLAIM_BASIS] = (
        f"The receipt retains {claim_phrase} and {evidence_phrase}."
    )
    items[ReceiptSectionKey.EVIDENCE_AND_CLAIM_BASIS] = (*claim_refs, *evidence_refs)

    if gap_codes:
        bodies[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = (
            f"Coverage is limited by: {', '.join(gap_codes)}."
        )
        items[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = gap_codes
    elif redactions:
        bodies[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = (
            "Visibility is reduced by recorded redactions; coverage is not proof of correctness."
        )
        items[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = ()
    else:
        bodies[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = (
            "Coverage is bounded to the recorded evidence and is not proof of correctness."
        )
        items[ReceiptSectionKey.LIMITATIONS_AND_COVERAGE] = ()

    policy_rows = "; ".join(
        f"{entry.policy_id} {entry.policy_version}" for entry in versions.policy_versions
    )
    bodies[ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY] = (
        f"Engine {versions.engine_version}; protocol {versions.protocol_version}; {policy_rows}."
    )
    items[ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY] = ()

    return tuple(
        ReceiptSection(
            key=key,
            title=_SECTION_TITLES[key],
            body=bodies[key],
            items=items[key],
        )
        for key in _SECTION_KEYS[include]
    )


def build_receipt(
    context: ReceiptBuildContext,
    receipt_id: ReceiptId,
    task_id: TaskId,
    session_id: SessionId,
    generated_at: Timestamp,
    versions: ReceiptVersionSlice,
    redaction_profile: ReceiptRedactionProfile,
    include: ReceiptInclude,
) -> ReceiptDocument:
    """Build the one immutable canonical receipt for a complete frozen context."""

    if (
        type(context) is not ReceiptBuildContext
        or type(versions) is not ReceiptVersionSlice
        or type(redaction_profile) is not ReceiptRedactionProfile
        or type(include) is not ReceiptInclude
    ):
        raise ValueError(_CONTEXT_INVALID)
    states_by_id = {state.finding_id: state for state in context.finding_states}
    findings = tuple(
        cast(Finding, context.projection.findings[state.finding_id].payload)
        for state in context.finding_states
    )
    unresolved_actionable = tuple(
        finding
        for finding in findings
        if not states_by_id[finding.finding_id].resolved and FINDING_KIND_TRAITS[finding.kind][1]
    )
    conclusion = _conclusion(context, unresolved_actionable)
    obligations = _select_obligations(context, findings)
    responses = _select_responses(context, frozenset(finding.finding_id for finding in findings))
    claim_refs = _select_claim_refs(context, findings)
    evidence_refs = _select_evidence_refs(context, claim_refs, obligations, responses)
    gaps = _select_gaps(context)
    (
        retained_findings,
        retained_obligations,
        retained_responses,
        retained_gaps,
        redactions,
    ) = _apply_profile(
        context,
        redaction_profile,
        findings,
        obligations,
        responses,
        gaps,
    )
    sections = _sections(
        include=include,
        conclusion=conclusion,
        frontier=context.subject_frontier,
        versions=versions,
        findings=retained_findings,
        unresolved_actionable_count=len(unresolved_actionable),
        unresolved_actionable_ids=tuple(finding.finding_id for finding in unresolved_actionable),
        obligations=retained_obligations,
        claim_refs=claim_refs,
        evidence_refs=evidence_refs,
        coverage=context.coverage,
        redactions=redactions,
    )
    suppressed_count = (
        0 if context.applicable_check is None else context.applicable_check.suppressed_count
    )
    return ReceiptDocument(
        receipt_id=receipt_id,
        task_id=task_id,
        session_id=session_id,
        generated_at=generated_at,
        subject_frontier=context.subject_frontier,
        conclusion=conclusion,
        suppressed_finding_count=suppressed_count,
        versions=versions,
        coverage=context.coverage,
        findings=retained_findings,
        obligations=retained_obligations,
        responses=retained_responses,
        claim_refs=claim_refs,
        evidence_refs=evidence_refs,
        gaps=retained_gaps,
        redactions=redactions,
        sections=sections,
    )
