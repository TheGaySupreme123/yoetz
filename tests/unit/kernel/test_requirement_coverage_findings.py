"""Completion needs an answerable signal: statement mapping and observed corroboration (TB4 pilot).

The TB4 pilot (2026-10-05) showed an agent miss a stated requirement, resolve its own coarse
obligations with its own evidence, and reach a receipt while the only relevant signal was an
advisory coverage note. These vectors lock the two promoted, agent-actionable findings: a task
statement that no effective obligation cites, and a completion claim that cites no hook-observed
verification run made after the latest observed edit. Both are structural; no prose is read.
"""

from __future__ import annotations

from dataclasses import replace

from builders.policy_cases import (
    BASE_COVERAGE,
    act,
    claim_record,
    clm,
    evt,
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
    ClaimRecordedPayloadV1_1,
    NoObligationsReason,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import FindingKind
from yoetz.domain.task_statement import RecordedTaskStatement
from yoetz.domain.values import ResultId
from yoetz.kernel.closure_readiness import GapClass, classify_gap
from yoetz.kernel.deterministic_checks import (
    DeterministicCase,
    FindingBasisRef,
    build_task_statement_unmapped_assessment,
)
from yoetz.kernel.plan_drift import (
    INSTRUCTION_REQUIREMENT_UNMAPPED_GAP,
    OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP,
    PLAN_DRIFT_ADVISORY_GAPS,
    PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP,
)
from yoetz.kernel.policies.work_integrity import work_integrity_findings
from yoetz.kernel.projections import ProjectionRecord
from yoetz.protocol.coverage import Coverage, PublicationChannel

_STATEMENT_EVENT = evt(1)


def _statement() -> RecordedTaskStatement:
    return RecordedTaskStatement(
        "Fix the planner; over-limit loads must not clear.", evt(1), "session_opened", 1
    )


def _obligation(
    number: int,
    *,
    cites: bool,
    status: ObligationStatus = ObligationStatus.OPEN,
    evidence: tuple[str, ...] = (),
) -> ObligationPublishedPayload:
    return ObligationPublishedPayload(
        obl(number),
        "Requirement",
        "A result",
        status,
        source_refs=(_STATEMENT_EVENT,) if cites else (),
        resolution_evidence_refs=evidence,  # pyright: ignore[reportArgumentType]
    )


def _statement_case(
    *obligations: ObligationPublishedPayload, empty_reason: bool = False
) -> DeterministicCase:
    plan = (
        PlanPublishedPayload(1, "Plan", (), (), NoObligationsReason.SINGLE_ATOMIC_CHANGE)
        if empty_reason
        else PlanPublishedPayload(1, "Plan", tuple(item.obligation_id for item in obligations))
    )
    case = make_case(
        plans={1: plan_record(plan, 2)},
        obligations={
            item.obligation_id: obligation_record(item, 10 + index)
            for index, item in enumerate(obligations)
        },
        extra_refs=(_STATEMENT_EVENT,),
    )
    return replace(case, task_statement=_statement())


def test_unmapped_statement_raises_an_actionable_finding_naming_the_statement_event() -> None:
    assessment = build_task_statement_unmapped_assessment(
        _statement_case(_obligation(1, cites=False), _obligation(2, cites=False))
    )

    assert assessment is not None
    candidate = assessment.candidate
    assert candidate.kind is FindingKind.TASK_REQUIREMENT_UNMET
    assert candidate.subject_refs == (_STATEMENT_EVENT,)
    assert candidate.policy_id == "research-evidence"
    assert candidate.summary == (
        "No obligation in the current plan cites the recorded task statement."
    )
    assert f"source_refs [{_STATEMENT_EVENT}]" in candidate.detail
    assert "plan_revised" in candidate.detail
    assert "A check rerun without that plan change returns this finding again." in candidate.detail
    # Fixed repository wording plus structural ids only: the statement text never appears.
    assert "over-limit" not in candidate.detail


def test_one_statement_sourced_obligation_clears_the_unmapped_finding() -> None:
    case = _statement_case(_obligation(1, cites=False), _obligation(2, cites=True))

    assert build_task_statement_unmapped_assessment(case) is None


def test_no_statement_and_explicit_empty_scope_keep_their_existing_behaviour() -> None:
    without_statement = replace(_statement_case(_obligation(1, cites=False)), task_statement=None)
    empty_scope = _statement_case(empty_reason=True)

    assert build_task_statement_unmapped_assessment(without_statement) is None
    assert build_task_statement_unmapped_assessment(empty_scope) is None


def test_only_the_unmapped_statement_gap_is_agent_actionable() -> None:
    assert PLAN_DRIFT_ADVISORY_GAPS == {
        PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP,
        OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP,
    }
    flags = {"semantic_review_required": False, "semantic_review_current": False}
    assert classify_gap(INSTRUCTION_REQUIREMENT_UNMAPPED_GAP, **flags) is GapClass.AGENT_ACTIONABLE
    for code in PLAN_DRIFT_ADVISORY_GAPS:
        assert classify_gap(code, **flags) is GapClass.STANDING_LIMITATION


# --- observed corroboration of a completion claim ------------------------------------------

_HOOK = Coverage(
    publication_channels=(PublicationChannel.HOOK_OBSERVED,),
    authorship_assurance=BASE_COVERAGE.authorship_assurance,
    artifact_observation=BASE_COVERAGE.artifact_observation,
    evidence_immutability=BASE_COVERAGE.evidence_immutability,
    ledger_freshness=BASE_COVERAGE.ledger_freshness,
    check_types=BASE_COVERAGE.check_types,
    known_gaps=(),
)


def _observed_edit(number: int) -> ActionRecordedPayload:
    return ActionRecordedPayload(act(number), ActionKind.EDIT, "Edit (tool apply_patch)")


def _observed_command(number: int, runner: str | None = "test") -> ActionRecordedPayload:
    suffix = "" if runner is None else f" (runner {runner})"
    return ActionRecordedPayload(
        act(number),
        ActionKind.COMMAND,
        f"Command (tool Bash){suffix}",
        command="omitted:structural",
    )


def _result(number: int, outcome: ResultOutcome = ResultOutcome.SUCCESS) -> ResultRecordedPayload:
    return ResultRecordedPayload(res(number), act(number), outcome)


def _claim(
    *support: str, limitations: tuple[str, ...] = (), obligations: tuple[str, ...] = ()
) -> ClaimRecordedPayloadV1_1:
    return ClaimRecordedPayloadV1_1(
        claim_id=clm(1),
        claim_kind=ClaimKind.COMPLETION,
        statement="Done.",
        supporting_refs=support,  # pyright: ignore[reportArgumentType]
        obligation_refs=obligations,  # pyright: ignore[reportArgumentType]
        limitation_refs=limitations,  # pyright: ignore[reportArgumentType]
        supersedes_claim_refs=(),
    )


def _corroboration_case(
    *,
    edits: tuple[int, ...] = (),
    commands: tuple[tuple[int, str | None, ResultOutcome], ...] = (),
    cooperative_result: int | None = None,
    claim: ClaimRecordedPayloadV1_1,
    obligations: tuple[ObligationPublishedPayload, ...] = (),
) -> DeterministicCase:
    """Observed edits/commands at the given frontiers; the claim is recorded at frontier 90."""

    actions = {act(number): record(_observed_edit(number), number) for number in edits}
    results: dict[ResultId, ProjectionRecord[ResultRecordedPayload]] = {}
    observed: set[FindingBasisRef] = {evt(number) for number in edits}
    for number, runner, outcome in commands:
        actions[act(number)] = record(_observed_command(number, runner), number)
        results[res(number)] = record(_result(number, outcome), number + 1)
        observed.update({evt(number), evt(number + 1)})
    if cooperative_result is not None:
        actions[act(cooperative_result)] = record(
            ActionRecordedPayload(
                act(cooperative_result), ActionKind.COMMAND, "Ran tests", command="pytest"
            ),
            cooperative_result,
        )
        results[res(cooperative_result)] = record(
            _result(cooperative_result), cooperative_result + 1
        )
    plan = PlanPublishedPayload(1, "Plan", tuple(item.obligation_id for item in obligations))
    return make_case(
        plans={1: plan_record(plan, 2)},
        obligations={
            item.obligation_id: obligation_record(item, 80 + index)
            for index, item in enumerate(obligations)
        },
        actions=actions,
        results=results,
        claims={clm(1): claim_record(claim, 90)},
        coverage_overrides={ref: _HOOK for ref in observed},
    )


def _uncorroborated(case: DeterministicCase) -> list[str]:
    return [
        assessment.candidate.summary
        for assessment in work_integrity_findings(case)
        if assessment.candidate.kind is FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE
        and any(
            fact.fact_code == "observed_verification_uncited"
            for fact in assessment.basis.observed_facts
        )
    ]


def test_cargo_shape_self_asserted_completion_after_observed_work_is_actionable() -> None:
    """TB4 cargo: observed edits and test runs, but the claim cites only its own result."""

    case = _corroboration_case(
        edits=(20,),
        commands=((30, "test", ResultOutcome.SUCCESS),),
        cooperative_result=60,
        claim=_claim(res(60)),
    )

    findings = [
        assessment
        for assessment in work_integrity_findings(case)
        if assessment.candidate.kind is FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE
    ]
    assert len(findings) == 1
    candidate = findings[0].candidate
    assert candidate.subject_refs == (clm(1),)
    assert "status view=results" in candidate.detail
    assert f"supersedes_claim_refs [{clm(1)}]" in candidate.detail
    assert "A check rerun alone returns this finding again" in candidate.detail


def test_claim_citing_an_observed_run_after_the_last_edit_is_quiet() -> None:
    case = _corroboration_case(
        edits=(20,),
        commands=((30, "test", ResultOutcome.SUCCESS),),
        cooperative_result=60,
        claim=_claim(res(30), res(60)),
    )

    assert _uncorroborated(case) == []


def test_observed_run_cited_through_a_resolved_obligation_is_quiet() -> None:
    resolved = _obligation(1, cites=True, status=ObligationStatus.RESOLVED, evidence=(res(30),))
    case = _corroboration_case(
        edits=(20,),
        commands=((30, None, ResultOutcome.SUCCESS),),
        claim=_claim(obl(1), obligations=(obl(1),)),
        obligations=(resolved,),
    )

    assert _uncorroborated(case) == []


def test_run_before_a_later_edit_or_an_exploration_run_does_not_corroborate() -> None:
    stale = _corroboration_case(
        edits=(20, 40),
        commands=((30, "test", ResultOutcome.SUCCESS),),
        claim=_claim(res(30)),
    )
    exploration = _corroboration_case(
        edits=(20,),
        commands=((30, "exploration", ResultOutcome.SUCCESS),),
        claim=_claim(res(30)),
    )

    assert len(_uncorroborated(stale)) == 1
    assert len(_uncorroborated(exploration)) == 1


def test_disclosed_failing_observed_run_counts_as_corroboration() -> None:
    case = _corroboration_case(
        edits=(20,),
        commands=((30, "test", ResultOutcome.FAILURE),),
        cooperative_result=60,
        claim=_claim(res(60), limitations=(res(30),)),
    )

    assert _uncorroborated(case) == []


def test_no_hook_observation_keeps_the_rule_silent() -> None:
    case = _corroboration_case(cooperative_result=60, claim=_claim(res(60)))

    assert _uncorroborated(case) == []
