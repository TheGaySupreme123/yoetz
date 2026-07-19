from __future__ import annotations

import pytest

from builders.policy_cases import BASE_COVERAGE, clm, make_case, record
from yoetz.application.check import (
    CheckScope,
    FinalSemanticEvaluation,
    allocate_findings,
    case_coverage,
    run_deterministic_policies,
)
from yoetz.domain.events import ClaimKind, ClaimRecordedPayload
from yoetz.domain.findings import CheckVerdict
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.ranking import CheckCompleteness, RankingContext, rank_findings
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
