from __future__ import annotations

from dataclasses import replace

import pytest

from builders.policy_cases import (
    BASE_COVERAGE,
    FRONTIER,
    clm,
    evd,
    evidence_record,
    evt,
    fnd,
    make_case,
    record,
)
from yoetz.application.check import (
    CheckScope,
    FinalSemanticEvaluation,
    allocate_findings,
    carried_semantic_attempt_gaps,
    case_coverage,
    run_deterministic_policies,
)
from yoetz.domain.events import (
    ClaimKind,
    ClaimRecordedPayload,
    EvidenceKind,
    EvidenceRecordedPayload,
    ResponseDisposition,
    ResponseRecordedPayload,
)
from yoetz.domain.findings import (
    CheckVerdict,
    Finding,
    FindingKind,
    FindingOrigin,
    WaiverScope,
)
from yoetz.domain.receipts import (
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
    OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
)
from yoetz.domain.values import EventId, object_id, timestamp_from_string
from yoetz.kernel.deterministic_checks import CaseGap, DeterministicCase
from yoetz.kernel.projections import LatestTestedState
from yoetz.kernel.ranking import CheckCompleteness, RankingContext, rank_findings
from yoetz.protocol.coverage import EvidenceImmutability
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import SemanticReason, SemanticStatus


class _Ids:
    def __init__(self) -> None:
        self.ordinal = 0

    def new(self, kind: IdKind) -> str:
        assert kind is IdKind.FINDING
        self.ordinal += 1
        return f"fnd_20000000-0000-4000-8000-{self.ordinal:012x}"


def _unsupported_claim_case():
    claim = ClaimRecordedPayload(clm(1), ClaimKind.MATERIAL, "Claim", ())
    return make_case(claims={clm(1): record(claim, 1)})


def test_deterministic_packs_account_and_rank_actual_kernel_findings() -> None:
    case = _unsupported_claim_case()

    assessments, executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )
    findings = allocate_findings(_Ids(), tuple(item.candidate for item in assessments))
    coverage = case_coverage(case)
    ranked = rank_findings(
        findings,
        (),
        RankingContext(coverage, CheckCompleteness.COMPLETE),
        3,
    )

    assert tuple((item.policy_id, item.outcome, item.reason) for item in executions) == (
        ("research-evidence", "run", "completed"),
        ("work-integrity", "run", "completed"),
    )
    assert ranked.verdict is CheckVerdict.ACTION_REQUIRED
    assert ranked.findings
    assert coverage != BASE_COVERAGE


def _finding_response_case(
    disposition: ResponseDisposition,
    *,
    stale: bool,
    work_only_gap: bool = False,
) -> DeterministicCase:
    finding = Finding(
        finding_id=fnd(1),
        kind=FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        origin=FindingOrigin.DETERMINISTIC,
        priority=1,
        summary="A completion claim covers an obligation that remains open.",
        detail="Resolve the open obligation.",
        subject_refs=(evt(99),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=FRONTIER,
        coverage=BASE_COVERAGE,
        provenance=None,
    )
    response_frontier = replace(FRONTIER, sequence=FRONTIER.sequence - 1) if stale else FRONTIER
    response = ResponseRecordedPayload(
        finding_id=fnd(1),
        finding_frontier=response_frontier,
        disposition=disposition,
        reason="The finding is not applicable.",
        waiver_scope=(
            WaiverScope.FINDING_ONLY if disposition is ResponseDisposition.WAIVED else None
        ),
        evidence_refs=(evd(1),) if work_only_gap else (),
    )
    evidence = EvidenceRecordedPayload(
        evidence_id=evd(1),
        evidence_kind=EvidenceKind.TEST_RESULT,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=timestamp_from_string("2026-01-01T00:00:00.000Z"),
        captured_object_id=object_id("obj_10000000-0000-4000-8000-000000000001"),
        content_digest="sha256:" + "1" * 64,
    )
    return make_case(
        evidence={evd(1): evidence_record(evidence, 1)} if work_only_gap else None,
        findings={fnd(1): record(finding, 2)},
        responses={fnd(1): record(response, 3)},
        extra_refs=(evt(99),),
        coverage_overrides=(
            {
                evd(1): replace(
                    BASE_COVERAGE,
                    known_gaps=("evidence_digest_subject_legacy_unknown",),
                )
            }
            if work_only_gap
            else None
        ),
    )


@pytest.mark.parametrize(
    "disposition",
    (ResponseDisposition.REJECTED, ResponseDisposition.WAIVED),
)
@pytest.mark.parametrize(
    ("stale", "expected_kind"),
    (
        (False, FindingKind.QUESTIONABLE_FINDING_REJECTION),
        (True, FindingKind.WEAK_OR_STALE_RESPONSE),
    ),
)
def test_finding_response_penalty_has_one_policy_owner(
    disposition: ResponseDisposition,
    stale: bool,
    expected_kind: FindingKind,
) -> None:
    assessments, executions = run_deterministic_policies(
        _finding_response_case(disposition, stale=stale),
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )

    assert tuple(item.candidate.kind for item in assessments) == (expected_kind,)
    assert all(item.outcome == "run" and item.reason == "completed" for item in executions)


def test_response_with_work_only_evidence_gap_is_not_dropped() -> None:
    assessments, _executions = run_deterministic_policies(
        _finding_response_case(
            ResponseDisposition.REJECTED,
            stale=False,
            work_only_gap=True,
        ),
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )

    assert tuple(item.candidate.kind for item in assessments) == (
        FindingKind.WEAK_OR_STALE_RESPONSE,
    )


def test_direct_scope_excludes_pack_without_selected_root() -> None:
    case = _unsupported_claim_case()
    unrelated = "obl_20000000-0000-4000-8000-000000000001"

    assessments, executions = run_deterministic_policies(
        case,
        CheckScope((), (unrelated,)),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )

    assert assessments == ()
    assert all(item.reason == "scope_excluded" for item in executions)


def test_policy_failure_is_accounted_without_inventing_findings() -> None:
    case = _unsupported_claim_case()

    def fail(_case: DeterministicCase):
        raise RuntimeError("policy defect")

    assessments, executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        ("work-integrity/0.1.0",),
        evaluators={"work-integrity/0.1.0": fail},
    )

    assert assessments == ()
    assert tuple((item.outcome, item.reason) for item in executions) == (
        ("failed", "policy_failure"),
    )


def test_semantic_required_unavailable_is_valid_terminal_fallback() -> None:
    evaluation = FinalSemanticEvaluation(
        SemanticStatus.NOT_CONFIGURED,
        SemanticReason.PROVIDER_NOT_CONFIGURED,
    )

    assert evaluation.judgment is None
    assert evaluation.provenance is None
    assert CheckCompleteness.REQUIRED_INCOMPLETE.value == "required_incomplete"


def test_semantic_status_reason_cross_pair_is_rejected() -> None:
    with pytest.raises(ValueError):
        FinalSemanticEvaluation(
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_TIMEOUT,
        )


def test_deterministic_only_marks_semantic_not_requested_gap_without_verdict_change() -> None:
    from yoetz.application.check import semantic_coverage_gap_code
    from yoetz.domain.receipts import SEMANTIC_REVIEW_NOT_REQUESTED_GAP
    from yoetz.kernel.ranking import CheckCompleteness, RankingContext, rank_findings

    gap = semantic_coverage_gap_code(
        SemanticStatus.NOT_REQUESTED, SemanticReason.DETERMINISTIC_MODE
    )
    assert gap == SEMANTIC_REVIEW_NOT_REQUESTED_GAP

    case = _unsupported_claim_case()
    assessments, _executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )
    findings = allocate_findings(_Ids(), tuple(item.candidate for item in assessments))
    coverage = case_coverage(case)
    # Simulate check merge: add gap, CURRENT -> PARTIAL, COVERAGE_INCOMPLETE
    gaps = set(coverage.known_gaps)
    gaps.add(gap)
    from yoetz.protocol.coverage import LedgerFreshness as LF

    freshness = coverage.ledger_freshness
    if freshness is LF.CURRENT:
        freshness = LF.PARTIAL
    coverage = replace(
        coverage, ledger_freshness=freshness, known_gaps=tuple(sorted(gaps, key=str.encode))
    )
    ranked = rank_findings(
        findings,
        (),
        RankingContext(coverage, CheckCompleteness.COVERAGE_INCOMPLETE),
        3,
    )
    # Verdict still driven by findings, not by the semantic gap alone.
    assert ranked.verdict is CheckVerdict.ACTION_REQUIRED
    assert SEMANTIC_REVIEW_NOT_REQUESTED_GAP in coverage.known_gaps
    assert coverage.ledger_freshness is LF.PARTIAL


@pytest.mark.parametrize(
    "gap_code",
    (COMPLETION_SCOPE_UNDECLARED_GAP, COMPLETION_SCOPE_DECLARED_NONE_GAP),
)
@pytest.mark.parametrize(
    "completeness",
    (CheckCompleteness.COVERAGE_INCOMPLETE, CheckCompleteness.REQUIRED_INCOMPLETE),
)
def test_completion_scope_gaps_dominate_an_actionable_finding(
    gap_code: str,
    completeness: CheckCompleteness,
) -> None:
    case = replace(
        _unsupported_claim_case(),
        gaps=(CaseGap(gap_code, gap_code, ()),),
    )
    assessments, _executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )
    findings = allocate_findings(_Ids(), tuple(item.candidate for item in assessments))
    coverage = case_coverage(case)
    ranked = rank_findings(
        findings,
        (),
        RankingContext(coverage, completeness),
        3,
    )

    assert coverage.known_gaps == (gap_code,)
    assert ranked.findings
    assert ranked.verdict is CheckVerdict.INSUFFICIENT_COVERAGE


def _case_after_check(*gaps: str) -> DeterministicCase:
    """A case whose latest recorded check carried ``gaps`` in its own coverage."""

    case = _unsupported_claim_case()
    tested = LatestTestedState(
        source_check_event_id=EventId("evt_30000000-0000-4000-8000-000000000001"),
        subject_frontier=case.frontier,
        verdict=CheckVerdict.NO_ISSUE_DETECTED,
        returned_finding_ids=(),
        suppressed_count=0,
        coverage=replace(BASE_COVERAGE, known_gaps=tuple(sorted(gaps, key=str.encode))),
    )
    return replace(case, projection=replace(case.projection, latest_tested_state=tested))


def test_deterministic_fallback_carries_a_blocked_semantic_attempt_forward() -> None:
    """A blocked review stays disclosed after the stop-rule deterministic re-check (issue #185).

    A host or policy gate that refuses a semantic check is a coverage gap, not a retry problem, so
    the agent re-checks with ``deterministic_only``. That successor replaces
    ``latest_tested_state`` wholesale, leaving ``semantic_review_not_requested`` as the receipt's
    only account — which blames the agent for never asking, when the environment refused.
    """

    carried = carried_semantic_attempt_gaps(
        _case_after_check(OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP),
        SemanticStatus.NOT_REQUESTED,
    )

    assert carried == {OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP}


@pytest.mark.parametrize(
    "gap",
    (SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP, SEMANTIC_REVIEW_NOT_CONFIGURED_GAP),
)
def test_every_environment_attributed_semantic_gap_carries_forward(gap: str) -> None:
    assert carried_semantic_attempt_gaps(_case_after_check(gap), SemanticStatus.NOT_REQUESTED) == {
        gap
    }


def test_a_prior_check_that_also_did_not_request_review_carries_nothing() -> None:
    """``semantic_review_not_requested`` is the label being disambiguated, never inherited."""

    assert (
        carried_semantic_attempt_gaps(
            _case_after_check(SEMANTIC_REVIEW_NOT_REQUESTED_GAP),
            SemanticStatus.NOT_REQUESTED,
        )
        == set()
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        (SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED),
        (SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.SCOPE_NOT_AUTHORIZED),
    ),
)
def test_a_check_that_made_its_own_attempt_states_only_its_own_outcome(
    status: SemanticStatus, reason: SemanticReason
) -> None:
    """Inheritance is for checks with nothing of their own to say; ``reason`` fixes the pair."""

    assert reason is not None
    assert (
        carried_semantic_attempt_gaps(
            _case_after_check(OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP), status
        )
        == set()
    )


def test_a_task_with_no_recorded_check_carries_nothing() -> None:
    assert (
        carried_semantic_attempt_gaps(_unsupported_claim_case(), SemanticStatus.NOT_REQUESTED)
        == set()
    )


def test_judgment_rejection_preserves_the_truncation_disclosure() -> None:
    """Restating a rejected judgment must not reset ``case_content_over_item_limit``.

    The rejection restates the *outcome* of the same attempt; the case the reviewer judged
    stays the case that was truncated, so the disclosure must survive the restatement.
    """

    from yoetz.application.check import (
        _judgment_rejected_evaluation,  # pyright: ignore[reportPrivateUsage]
    )
    from yoetz.domain.findings import SamplingParams, SemanticDispatchKind, SemanticProvenance
    from yoetz.ports.semantic import SemanticJudgment

    digest = "sha256:" + "a" * 64
    succeeded = FinalSemanticEvaluation(
        SemanticStatus.SUCCEEDED,
        SemanticReason.SEMANTIC_COMPLETED,
        judgment=SemanticJudgment("no_material_discrepancy", ()),
        provenance=SemanticProvenance(
            provider="fake",
            endpoint_profile_id="fake",
            endpoint_profile_version="1.0.0",
            model="fake/model",
            sdk_version="1.0.0",
            prompt_digest=digest,
            schema_digest=digest,
            policy_digest=digest,
            privacy_policy_digest=digest,
            sampling_params=SamplingParams(128),
            latency_ms=1,
            semantic_attempt_id="att_20000000-0000-4000-8000-000000000001",
            dispatch_kind=SemanticDispatchKind.EXTERNAL,
            privacy_receipt_id="egr_20000000-0000-4000-8000-000000000001",
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
            provider_request_id="fake-1",
            egress_authorization_id="aut_20000000-0000-4000-8000-000000000001",
            request_commitment="hmac-sha256:" + "b" * 64,
        ),
        withheld_review_categories=("obligation_text",),
        case_content_over_item_limit=True,
    )

    rejected = _judgment_rejected_evaluation(succeeded)

    assert rejected.status is SemanticStatus.INVALID
    assert rejected.case_content_over_item_limit is True
    assert rejected.withheld_review_categories == ("obligation_text",)
