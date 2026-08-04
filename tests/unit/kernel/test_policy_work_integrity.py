"""Minimum trigger and closest-nontrigger vectors for work integrity."""

from __future__ import annotations

from builders.policy_cases import (
    BASE_COVERAGE,
    FRONTIER,
    act,
    clm,
    evd,
    evidence_record,
    evt,
    fnd,
    make_case,
    obl,
    obligation_record,
    plan_record,
    record,
    res,
)
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    ClaimKind,
    ClaimRecordedPayload,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationChange,
    ObligationChangeKind,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    PlanRevisedPayload,
    RequestedItem,
    RequestedItemKind,
    ResponseDisposition,
    ResponseRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import (
    Finding,
    FindingKind,
    FindingOrigin,
)
from yoetz.domain.values import (
    SubjectStateRef,
    object_id,
    timestamp_from_string,
)
from yoetz.kernel.deterministic_checks import (
    CaseGap,
    run_deterministic_policies,
)
from yoetz.kernel.policies.work_integrity import WORK_INTEGRITY_POLICY_PACK
from yoetz.kernel.projections import ContradictionKey, ContradictionRecord
from yoetz.protocol.coverage import EvidenceImmutability

_NOW = timestamp_from_string("2026-01-01T00:00:00.000Z")
_DIGEST_A = "sha256:" + "1" * 64
_DIGEST_B = "sha256:" + "2" * 64


def _kinds(case: object) -> tuple[FindingKind, ...]:
    result = run_deterministic_policies(case, WORK_INTEGRITY_POLICY_PACK)  # type: ignore[arg-type]
    return tuple(item.candidate.kind for item in result.assessments)


def _open_obligation(number: int, *, requested: str | None = None) -> ObligationPublishedPayload:
    items = () if requested is None else (RequestedItem(RequestedItemKind.CHANGE, requested),)
    return ObligationPublishedPayload(
        obligation_id=obl(number),
        description="Synthetic obligation",
        evidence_expectation="Typed evidence",
        status=ObligationStatus.OPEN,
        requested_items=items,
    )


def _action(number: int, obligation: int) -> ActionRecordedPayload:
    return ActionRecordedPayload(
        action_id=act(number),
        action_kind=ActionKind.EDIT,
        description="Synthetic action",
        obligation_refs=(obl(obligation),),
        attempted_items=(f"item-{obligation}",),
    )


def _result(
    number: int,
    action: int,
    outcome: ResultOutcome,
    *,
    state: SubjectStateRef | None = None,
) -> ResultRecordedPayload:
    return ResultRecordedPayload(
        result_id=res(number),
        action_id=act(action),
        outcome=outcome,
        subject_state=state,
    )


def _evidence(number: int, state: SubjectStateRef | None = None) -> EvidenceRecordedPayload:
    return EvidenceRecordedPayload(
        evidence_id=evd(number),
        evidence_kind=EvidenceKind.TEST_RESULT,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=_NOW,
        captured_object_id=object_id(f"obj_10000000-0000-4000-8000-{number:012x}"),
        content_digest=_DIGEST_A,
        subject_state=state,
    )


def test_completion_with_open_obligations_and_waiver_nontrigger() -> None:
    obligation = _open_obligation(1)
    claim = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.COMPLETION,
        statement="Complete",
        supporting_refs=(obl(1),),
        obligation_refs=(obl(1),),
    )
    trigger = make_case(
        obligations={obl(1): obligation_record(obligation, 1)},
        claims={clm(1): record(claim, 2)},
    )
    result = run_deterministic_policies(trigger, WORK_INTEGRITY_POLICY_PACK)
    finding = next(
        item
        for item in result.assessments
        if item.candidate.kind is FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS
    )
    assert finding.candidate.subject_refs == (clm(1), obl(1))
    assert tuple(fact.fact_code for fact in finding.basis.observed_facts) == (
        "completion_claim_present",
        "open_obligation_present",
    )
    waived = make_case(
        obligations={
            obl(1): obligation_record(
                obligation,
                1,
                plan_change=ObligationChangeKind.WAIVED,
            )
        },
        claims={clm(1): record(claim, 2)},
    )
    assert FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS not in _kinds(waived)


def test_requested_item_never_attempted_and_exact_attempt_nontrigger() -> None:
    obligation = _open_obligation(1, requested="item-1")
    plan = PlanPublishedPayload(1, "Plan", (obl(1),))
    trigger = make_case(
        plans={1: plan_record(plan, 1)},
        obligations={obl(1): obligation_record(obligation, 2)},
    )
    assert FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED in _kinds(trigger)
    attempted = make_case(
        plans={1: plan_record(plan, 1)},
        obligations={obl(1): obligation_record(obligation, 2)},
        actions={act(1): record(_action(1, 1), 3)},
    )
    assert FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED not in _kinds(attempted)


def test_requested_item_on_carried_revision_uses_effective_plan_scope() -> None:
    obligation = _open_obligation(2, requested="item-2")
    initial = PlanPublishedPayload(1, "Initial plan", ())
    revision = PlanRevisedPayload(
        2,
        1,
        "Carry newly declared work.",
        "Expanded plan.",
        (ObligationChange(obl(2), ObligationChangeKind.CARRIED),),
    )
    case = make_case(
        plans={1: plan_record(initial, 1), 2: plan_record(revision, 2)},
        obligations={obl(2): obligation_record(obligation, 3)},
    )

    assert FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED in _kinds(case)


def test_failed_work_omitted_and_exact_disclosure_nontrigger() -> None:
    action = _action(1, 1)
    failed = _result(1, 1, ResultOutcome.FAILURE)
    omitted = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.COMPLETION,
        statement="Complete",
        supporting_refs=(),
    )
    trigger = make_case(
        actions={act(1): record(action, 1)},
        results={res(1): record(failed, 2)},
        claims={clm(1): record(omitted, 3)},
    )
    assert FindingKind.FAILED_WORK_OMITTED in _kinds(trigger)
    disclosed = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.COMPLETION,
        statement="Partial",
        supporting_refs=(res(1),),
    )
    near = make_case(
        actions={act(1): record(action, 1)},
        results={res(1): record(failed, 2)},
        claims={clm(1): record(disclosed, 3)},
    )
    assert FindingKind.FAILED_WORK_OMITTED not in _kinds(near)


def test_claim_without_admissible_evidence_and_supported_nontrigger() -> None:
    unsupported = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.MATERIAL,
        statement="Material claim",
        supporting_refs=(),
    )
    assert FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE in _kinds(
        make_case(claims={clm(1): record(unsupported, 1)})
    )
    evidence = _evidence(1)
    supported = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.MATERIAL,
        statement="Material claim",
        supporting_refs=(evd(1),),
    )
    near = make_case(
        evidence={evd(1): evidence_record(evidence, 1)},
        claims={clm(1): record(supported, 2)},
    )
    assert FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE not in _kinds(near)


def test_result_without_action_and_linked_action_nontrigger() -> None:
    result = _result(1, 1, ResultOutcome.SUCCESS)
    assert FindingKind.RESULT_WITHOUT_ACTION in _kinds(
        make_case(results={res(1): record(result, 1)})
    )
    linked = make_case(
        actions={act(1): record(_action(1, 1), 1)},
        results={res(1): record(result, 2)},
    )
    assert FindingKind.RESULT_WITHOUT_ACTION not in _kinds(linked)


def test_action_without_result_requires_later_disjoint_work() -> None:
    unresolved = _action(1, 1)
    later = _action(2, 2)
    trigger = make_case(
        actions={
            act(1): record(unresolved, 1),
            act(2): record(later, 2),
        }
    )
    assert FindingKind.ACTION_WITHOUT_RESULT in _kinds(trigger)
    latest_only = make_case(actions={act(1): record(unresolved, 1)})
    assert FindingKind.ACTION_WITHOUT_RESULT not in _kinds(latest_only)


def test_stale_evidence_requires_comparable_different_tree_state() -> None:
    old_state = SubjectStateRef(tree_digest=_DIGEST_A)
    new_state = SubjectStateRef(tree_digest=_DIGEST_B)
    evidence = _evidence(1, old_state)
    claim = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.MATERIAL,
        statement="Changed state",
        supporting_refs=(evd(1),),
        subject_state=new_state,
    )
    trigger = make_case(
        evidence={evd(1): evidence_record(evidence, 1)},
        claims={clm(1): record(claim, 2)},
    )
    result = run_deterministic_policies(trigger, WORK_INTEGRITY_POLICY_PACK)
    finding = next(
        item
        for item in result.assessments
        if item.candidate.kind is FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE
    )
    assert finding.basis.subject_state_relation.value == "different"
    same = ClaimRecordedPayload(
        claim_id=clm(1),
        claim_kind=ClaimKind.MATERIAL,
        statement="Same state",
        supporting_refs=(evd(1),),
        subject_state=old_state,
    )
    near = make_case(
        evidence={evd(1): evidence_record(evidence, 1)},
        claims={clm(1): record(same, 2)},
    )
    assert FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE not in _kinds(near)


def test_contradictory_claims_require_explicit_unresolved_edge() -> None:
    left = ClaimRecordedPayload(clm(1), ClaimKind.MATERIAL, "Left", ())
    right = ClaimRecordedPayload(clm(2), ClaimKind.MATERIAL, "Right", ())
    key = ContradictionKey(clm(1), clm(2))
    edge = ContradictionRecord(clm(1), clm(2), evt(1), 1)
    trigger = make_case(
        claims={clm(1): record(left, 1), clm(2): record(right, 2)},
        contradictions={key: edge},
    )
    assert FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED in _kinds(trigger)
    near = make_case(claims={clm(1): record(left, 1), clm(2): record(right, 2)})
    assert FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED not in _kinds(near)


def test_ledger_stale_or_incomplete_requires_a_nonrootless_gap() -> None:
    gap = CaseGap(
        f"unknown_event:{evt(9)}:future_event@2.0.0",
        "unknown_event",
        (evt(9),),
    )
    trigger = make_case(gaps=(gap,), extra_refs=(evt(9),))
    assert FindingKind.LEDGER_STALE_OR_INCOMPLETE in _kinds(trigger)
    rootless = CaseGap("import_source_range_not_universal", "freshness_gap", ())
    assert FindingKind.LEDGER_STALE_OR_INCOMPLETE not in _kinds(make_case(gaps=(rootless,)))


def _recorded_finding() -> Finding:
    return Finding(
        finding_id=fnd(1),
        kind=FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        origin=FindingOrigin.DETERMINISTIC,
        priority=1,
        summary="A completion claim covers an obligation that remains open.",
        detail="Subjects: evt_10000000-0000-4000-8000-000000000063. Main agent: Resolve it.",
        subject_refs=(evt(99),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=FRONTIER,
        coverage=BASE_COVERAGE,
        provenance=None,
    )


def test_weak_or_stale_response_and_supported_rejection_nontrigger() -> None:
    finding = _recorded_finding()
    hollow = ResponseRecordedPayload(
        finding_id=fnd(1),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.REJECTED,
        reason="Rejected",
    )
    trigger = make_case(
        findings={fnd(1): record(finding, 1)},
        responses={fnd(1): record(hollow, 2)},
        extra_refs=(evt(99),),
    )
    assert FindingKind.WEAK_OR_STALE_RESPONSE in _kinds(trigger)
    evidence = _evidence(1)
    supported = ResponseRecordedPayload(
        finding_id=fnd(1),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.REJECTED,
        reason="Rejected",
        evidence_refs=(evd(1),),
    )
    near = make_case(
        evidence={evd(1): evidence_record(evidence, 1)},
        findings={fnd(1): record(finding, 2)},
        responses={fnd(1): record(supported, 3)},
        extra_refs=(evt(99),),
    )
    assert FindingKind.WEAK_OR_STALE_RESPONSE not in _kinds(near)


def test_finding_coverage_adds_only_engine_and_deterministic_dimensions() -> None:
    result = _result(1, 1, ResultOutcome.SUCCESS)
    case = make_case(results={res(1): record(result, 1)})
    assessment = next(
        item
        for item in run_deterministic_policies(
            case,
            WORK_INTEGRITY_POLICY_PACK,
        ).assessments
        if item.candidate.kind is FindingKind.RESULT_WITHOUT_ACTION
    )
    coverage = assessment.candidate.coverage
    assert tuple(channel.value for channel in coverage.publication_channels) == (
        "cooperative_mcp",
        "engine_derived",
    )
    assert tuple(check.value for check in coverage.check_types) == ("deterministic",)
    assert coverage.authorship_assurance is BASE_COVERAGE.authorship_assurance
    assert coverage.artifact_observation is BASE_COVERAGE.artifact_observation
    assert coverage.evidence_immutability is BASE_COVERAGE.evidence_immutability
    assert coverage.ledger_freshness is BASE_COVERAGE.ledger_freshness
