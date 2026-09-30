"""The proof-based finding-resolution relation and its projection fold (issue #458).

A finding stays visible forever; whether it is *current* changes only when a later check whose
recorded state contains the finding, whose matching policy pack ran to completion with nothing
suppressed, whose scope covers the finding's subject, and whose coverage carries no weakening gap
for the finding's proof class did not return the same issue again. Everything else — responses,
weak or scoped-away checks, failed packs, suppression, stale freshness, unreadable rows — leaves
the finding exactly as it was.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from builders.policy_cases import act, clm, evt, finding_record, fnd, obl
from yoetz.domain.events import CheckMode, CheckRecordedPayload, PolicyVersion
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CheckVerdict,
    Finding,
    FindingKind,
    FindingOrigin,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.values import Frontier
from yoetz.kernel.finding_resolution import (
    apply_check_resolution,
    finding_is_resolved,
    issue_key,
    qualifying_check_resolves,
    reopen_findings_resolved_by,
    resolved_finding_ids,
)
from yoetz.kernel.projections import (
    FindingProjectionRecord,
    ProjectionState,
    empty_projection_state,
    projection_from_snapshot,
    projection_snapshot,
)
from yoetz.ports.semantic import SamplingParams
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)
from yoetz.protocol.models import (
    CheckPolicyExecutionModel,
    CheckScopeModel,
    SemanticReason,
    SemanticStatus,
)

_DIGEST = "sha256:" + "1" * 64
_WORK = ("work-integrity", "0.1.0")
_RESEARCH = ("research-evidence", "0.1.0")


def _coverage(
    *,
    gaps: tuple[str, ...] = (),
    freshness: LedgerFreshness | None = None,
    semantic: bool = False,
) -> Coverage:
    if freshness is None:
        freshness = LedgerFreshness.PARTIAL if gaps else LedgerFreshness.CURRENT
    checks = (CheckType.DETERMINISTIC, CheckType.SEMANTIC_MODEL_DERIVED)
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=freshness,
        check_types=checks if semantic else (CheckType.DETERMINISTIC,),
        known_gaps=tuple(sorted(gaps, key=str.encode)),
    )


def _finding(
    number: int = 1,
    *,
    kind: FindingKind = FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
    subject_refs: tuple[object, ...] = (obl(1),),
    origin: FindingOrigin = FindingOrigin.DETERMINISTIC,
    policy_id: str = "work-integrity",
) -> Finding:
    provenance = None
    if origin is FindingOrigin.SEMANTIC_MODEL_DERIVED:
        provenance = _provenance()
    return Finding(
        finding_id=fnd(number),
        kind=kind,
        origin=origin,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="A completion claim covers an open obligation.",
        detail="Resolve or revise the obligation before claiming completion.",
        subject_refs=subject_refs,  # type: ignore[arg-type]
        policy_id=policy_id,
        policy_version="0.1.0",
        subject_frontier=Frontier(3, _DIGEST),
        coverage=_coverage(semantic=origin is FindingOrigin.SEMANTIC_MODEL_DERIVED),
        provenance=provenance,
    )


def _provenance() -> SemanticProvenance:
    return SemanticProvenance(
        provider="fake",
        endpoint_profile_id="fake",
        endpoint_profile_version="1.0.0",
        model="fake/model",
        sdk_version="1.0.0",
        prompt_digest=_DIGEST,
        schema_digest=_DIGEST,
        policy_digest=_DIGEST,
        privacy_policy_digest=_DIGEST,
        sampling_params=SamplingParams(128),
        latency_ms=1,
        semantic_attempt_id="att_00000000-0000-4000-8000-000000000001",
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        privacy_receipt_id="egr_00000000-0000-4000-8000-000000000001",
        status=SemanticStatus.SUCCEEDED,
        reason=SemanticReason.SEMANTIC_COMPLETED,
        provider_request_id="fake-1",
        egress_authorization_id="aut_00000000-0000-4000-8000-000000000001",
        request_commitment="hmac-sha256:" + "b" * 64,
    )


def _execution(policy: tuple[str, str], outcome: str, reason: str) -> CheckPolicyExecutionModel:
    return CheckPolicyExecutionModel(
        policy_id=policy[0],  # type: ignore[arg-type]
        policy_version=policy[1],  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
    )


def _check(
    *,
    tested: int = 8,
    returned: tuple[object, ...] = (),
    suppressed: int = 0,
    coverage: Coverage | None = None,
    scope: CheckScopeModel | None = None,
    work_outcome: tuple[str, str] = ("run", "completed"),
    policies: tuple[tuple[str, str], ...] = (_RESEARCH, _WORK),
    semantic: tuple[SemanticStatus, SemanticReason] = (
        SemanticStatus.NOT_REQUESTED,
        SemanticReason.DETERMINISTIC_MODE,
    ),
) -> CheckRecordedPayload:
    executions = tuple(
        _execution(policy, *work_outcome)
        if policy == _WORK
        else _execution(policy, "run", "completed")
        for policy in policies
    )
    verdict = CheckVerdict.ACTION_REQUIRED if returned else CheckVerdict.NO_ISSUE_DETECTED
    if coverage is None:
        coverage = _coverage(
            gaps=()
            if semantic[0] is SemanticStatus.SUCCEEDED
            else ("semantic_review_not_requested",),
            semantic=semantic[0] is SemanticStatus.SUCCEEDED,
        )
    return CheckRecordedPayload(
        mode=(
            CheckMode.SEMANTIC_REQUIRED
            if semantic[0] is SemanticStatus.SUCCEEDED
            else CheckMode.DETERMINISTIC_ONLY
        ),
        policies=tuple(PolicyVersion(*policy) for policy in policies),
        scope=CheckScopeModel(claim_ids=(), obligation_ids=()) if scope is None else scope,
        policy_executions=executions,
        subject_frontier=Frontier(tested, _DIGEST),
        verdict=verdict,
        returned_finding_ids=returned,  # type: ignore[arg-type]
        suppressed_count=suppressed,
        coverage=coverage,
        semantic_status=semantic[0],
        semantic_reason=semantic[1],
        engine_version="0.1.0",
        projection_version="yoetz/0.1.0",
        semantic_provenance=_provenance() if semantic[0] is SemanticStatus.SUCCEEDED else None,
    )


_SEMANTIC_OK = (SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED)


def _changed_state(check: CheckRecordedPayload, *, recorded_at: int = 4) -> ProjectionState:
    """The pre-check projection with one new action recorded after the finding (issue #884)."""

    from builders.policy_cases import act, record
    from yoetz.domain.events import ActionKind, ActionRecordedPayload

    changed = record(ActionRecordedPayload(act(9), ActionKind.EDIT, "Repair"), recorded_at + 1)
    return replace(
        empty_projection_state(),
        frontier=max(check.subject_frontier.sequence, recorded_at + 1),
        head_digest=_DIGEST,
        actions={act(9): changed},
    )


def _resolves(
    finding: Finding,
    check: CheckRecordedPayload,
    *,
    recorded_at: int = 4,
    changed: bool = True,
) -> bool:
    """Resolve with a material change after the finding unless ``changed`` is false."""

    state = _changed_state(check, recorded_at=recorded_at) if changed else None
    return qualifying_check_resolves(finding, recorded_at, check, frozenset(), proof_state=state)


def test_the_happy_path_resolves_a_deterministic_finding() -> None:
    assert _resolves(_finding(), _check()) is True


def test_a_check_that_never_saw_the_finding_cannot_speak_to_it() -> None:
    """The tested frontier precedes the finding's own record: the state it checked lacks it."""

    assert _resolves(_finding(), _check(tested=3), recorded_at=4) is False
    assert _resolves(_finding(), _check(tested=4), recorded_at=4) is True


def test_a_check_returning_the_same_issue_refires_rather_than_resolves() -> None:
    finding = _finding()
    successor = _finding(2)  # same issue key under a fresh id
    assert issue_key(successor) == issue_key(finding)
    keys = frozenset({issue_key(successor)})
    assert qualifying_check_resolves(finding, 4, _check(returned=(fnd(2),)), keys) is False


def test_suppression_leaves_absence_unproven() -> None:
    assert _resolves(_finding(), _check(suppressed=1)) is False


@pytest.mark.parametrize(
    "outcome",
    (
        ("skipped", "material_unavailable"),
        ("skipped", "scope_excluded"),
        ("failed", "policy_failure"),
    ),
)
def test_a_pack_that_did_not_complete_proves_nothing(outcome: tuple[str, str]) -> None:
    assert _resolves(_finding(), _check(work_outcome=outcome)) is False


def test_a_check_that_did_not_run_the_owning_pack_proves_nothing() -> None:
    assert _resolves(_finding(), _check(policies=(_RESEARCH,))) is False


def test_scope_must_name_one_of_the_findings_subjects() -> None:
    finding = _finding(subject_refs=(obl(1),))
    on_target = CheckScopeModel(claim_ids=(), obligation_ids=(obl(1),))
    elsewhere = CheckScopeModel(claim_ids=(clm(9),), obligation_ids=(obl(2),))
    assert _resolves(finding, _check(scope=on_target)) is True
    assert _resolves(finding, _check(scope=elsewhere)) is False


@pytest.mark.parametrize(
    "freshness",
    (
        LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE,
        LedgerFreshness.REDACTED_GAP,
        LedgerFreshness.UNKNOWN,
    ),
)
def test_unproven_freshness_cannot_resolve(freshness: LedgerFreshness) -> None:
    coverage = _coverage(gaps=("semantic_review_not_requested",), freshness=freshness)
    assert _resolves(_finding(), _check(coverage=coverage)) is False


@pytest.mark.parametrize(
    "gap",
    ("redacted_event", "event_payload_unavailable", "missing_ref", "completion_scope_undeclared"),
)
def test_a_non_semantic_gap_weakens_every_proof_class(gap: str) -> None:
    coverage = _coverage(gaps=("semantic_review_not_requested", gap))
    assert _resolves(_finding(), _check(coverage=coverage)) is False


@pytest.mark.parametrize(
    "gap",
    (
        "semantic_review_not_requested",
        "semantic_review_not_configured",
        "semantic_relevance_review_not_run",
        "optional_semantic_review_blocked_by_policy",
    ),
)
def test_semantic_absence_does_not_weaken_a_deterministic_proof(gap: str) -> None:
    """A local finding is proven absent by the local pack, not by the reviewer."""

    assert _resolves(_finding(), _check(coverage=_coverage(gaps=(gap,)))) is True


def test_registration_drift_check_resolves_deterministic_never_semantic() -> None:
    """Issue #537: a drift check resolves like a plain ceiling check.

    The drift gap rides alongside the ceiling gap, so the pair still tolerates
    local proof — and still proves nothing about an AI-powered finding.
    """

    from yoetz.domain.receipts import (
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP,
    )

    drift_check_gaps = (
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP,
    )
    assert _resolves(_finding(), _check(coverage=_coverage(gaps=drift_check_gaps))) is True
    semantic = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    weakened = _coverage(gaps=drift_check_gaps, semantic=True)
    assert _resolves(semantic, _check(semantic=_SEMANTIC_OK, coverage=weakened)) is False


@pytest.mark.parametrize(
    "gap",
    (
        "evidence_content_digest_only",
        "evidence_content_withheld",
        "evidence_digest_subject_legacy_unknown",
    ),
)
def test_evidence_strength_gaps_bound_the_receipt_but_not_the_proof(gap: str) -> None:
    """Digest-only evidence is readable ledger state the pack judged; it stays a receipt limit."""

    assert _resolves(_finding(), _check(coverage=_coverage(gaps=(gap,)))) is True
    semantic = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    coverage = _coverage(gaps=(gap,), semantic=True)
    assert _resolves(semantic, _check(semantic=_SEMANTIC_OK, coverage=coverage)) is True


@pytest.mark.parametrize(
    ("gap", "freshness"),
    (
        ("captured_object_unavailable", LedgerFreshness.REDACTED_GAP),
        ("content_unselected", LedgerFreshness.PARTIAL),
        ("host_outcome_unavailable", LedgerFreshness.PARTIAL),
        ("unpaired_event", LedgerFreshness.PARTIAL),
    ),
)
def test_each_host_observation_gap_preserves_clean_deterministic_proof(
    gap: str, freshness: LedgerFreshness
) -> None:
    coverage = _coverage(
        gaps=(gap, "semantic_review_not_requested"),
        freshness=freshness,
    )
    assert _resolves(_finding(), _check(coverage=coverage)) is True


@pytest.mark.parametrize(
    "gap",
    (
        "captured_object_unavailable",
        "content_unselected",
        "host_outcome_unavailable",
        "unpaired_event",
    ),
)
def test_hook_observed_finding_coverage_preserves_clean_deterministic_proof(gap: str) -> None:
    """Issue #547: derived hook coverage may carry the host gap onto the finding itself."""

    finding = replace(
        _finding(),
        coverage=_coverage(gaps=(gap,), freshness=LedgerFreshness.PARTIAL),
    )
    check_coverage = _coverage(
        gaps=(gap, "semantic_review_not_requested"),
        freshness=LedgerFreshness.PARTIAL,
    )

    assert _resolves(finding, _check(coverage=check_coverage)) is True


@pytest.mark.parametrize(
    "freshness",
    (LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE, LedgerFreshness.UNKNOWN),
)
def test_host_observation_gaps_do_not_admit_stale_or_unknown_freshness(
    freshness: LedgerFreshness,
) -> None:
    """Only ``redacted_gap`` is admitted; the other unproven freshnesses stay fail-closed (#538)."""

    coverage = _coverage(
        gaps=("captured_object_unavailable", "unpaired_event"),
        freshness=freshness,
    )
    assert _resolves(_finding(), _check(coverage=coverage)) is False


def test_host_observation_gaps_do_not_veto_clean_deterministic_proof() -> None:
    """Combined host limits remain on the check without making repair unprovable (#538)."""

    coverage = _coverage(
        gaps=(
            "captured_object_unavailable",
            "content_unselected",
            "host_outcome_unavailable",
            "semantic_review_not_requested",
            "unpaired_event",
        ),
        freshness=LedgerFreshness.REDACTED_GAP,
    )

    assert _resolves(_finding(), _check(coverage=coverage)) is True
    research_finding = _finding(
        kind=FindingKind.MATERIAL_LIMITATION_OMITTED,
        subject_refs=(clm(1),),
        policy_id="research-evidence",
    )
    assert _resolves(research_finding, _check(coverage=coverage)) is True
    semantic = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    assert _resolves(semantic, _check(semantic=_SEMANTIC_OK, coverage=coverage)) is False

    unreadable_finding = replace(
        _finding(),
        coverage=_coverage(
            gaps=("captured_object_unavailable",),
            freshness=LedgerFreshness.REDACTED_GAP,
        ),
    )
    assert _resolves(unreadable_finding, _check(coverage=coverage)) is False
    partial_host_coverage = _coverage(gaps=("unpaired_event",))
    assert _resolves(unreadable_finding, _check(coverage=partial_host_coverage)) is False

    hidden_event = _coverage(
        gaps=(*coverage.known_gaps, "redacted_event"),
        freshness=LedgerFreshness.REDACTED_GAP,
    )
    assert _resolves(_finding(), _check(coverage=hidden_event)) is False


def test_a_semantic_finding_needs_a_completed_semantic_review() -> None:
    finding = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    assert _resolves(finding, _check()) is False, "local-only proof is the wrong class"
    assert _resolves(finding, _check(semantic=_SEMANTIC_OK)) is True


@pytest.mark.parametrize(
    "gap",
    (
        "semantic_review_context_withheld",
        "semantic_challenges_rejected",
        "semantic_packet_insufficient",
    ),
)
def test_a_weakened_semantic_review_cannot_resolve_a_semantic_finding(gap: str) -> None:
    finding = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    coverage = _coverage(gaps=(gap,), semantic=True)
    assert _resolves(finding, _check(semantic=_SEMANTIC_OK, coverage=coverage)) is False
    # The same weakened review still proves a local issue absent.
    assert _resolves(_finding(), _check(semantic=_SEMANTIC_OK, coverage=coverage)) is True


def test_apply_marks_the_qualifying_row_and_reopens_returned_rows() -> None:
    proven_earlier = finding_record(
        _finding(2, subject_refs=(obl(2),)), 5, resolved_by_check_event_id=evt(6)
    )
    findings = {fnd(1): finding_record(_finding(1), 4), fnd(2): proven_earlier}

    apply_check_resolution(findings, _check(returned=(fnd(2),)), evt(9))

    assert findings[fnd(1)].resolved_by_check_event_id == evt(9)
    assert findings[fnd(2)].resolved_by_check_event_id is None, "returned again: current"


def test_apply_resolves_nothing_when_a_returned_row_is_unreadable() -> None:
    """An unreadable returned finding might be this very issue; the check cannot say."""

    findings = {
        fnd(1): finding_record(_finding(1), 4),
        fnd(2): replace(
            finding_record(_finding(2, subject_refs=(obl(2),)), 5), payload=None, redacted=True
        ),
    }
    apply_check_resolution(findings, _check(returned=(fnd(2),)), evt(9))
    assert findings[fnd(1)].resolved_by_check_event_id is None


def test_apply_never_re_resolves_or_weakens_an_existing_proof() -> None:
    findings = {fnd(1): finding_record(_finding(1), 4, resolved_by_check_event_id=evt(6))}
    apply_check_resolution(findings, _check(suppressed=3), evt(9))
    assert findings[fnd(1)].resolved_by_check_event_id == evt(6), "a weak later check does nothing"


def test_reopen_drops_only_proof_from_the_named_events() -> None:
    findings = {
        fnd(1): finding_record(_finding(1), 4, resolved_by_check_event_id=evt(6)),
        fnd(2): finding_record(
            _finding(2, subject_refs=(obl(2),)), 5, resolved_by_check_event_id=evt(7)
        ),
    }
    reopen_findings_resolved_by(findings, frozenset({evt(6)}))
    assert findings[fnd(1)].resolved_by_check_event_id is None
    assert findings[fnd(2)].resolved_by_check_event_id == evt(7)


def test_finding_is_resolved_reads_the_record_and_the_shared_rule() -> None:
    state = replace(
        empty_projection_state(),
        frontier=9,
        head_digest=_DIGEST,
        findings={
            fnd(1): finding_record(_finding(1), 4, resolved_by_check_event_id=evt(6)),
            fnd(2): finding_record(_finding(2, subject_refs=(obl(2),)), 5),
        },
        freshness=LedgerFreshness.CURRENT,
    )
    assert finding_is_resolved(state, fnd(1)) is True
    assert finding_is_resolved(state, fnd(2)) is False
    assert finding_is_resolved(state, fnd(3)) is False
    assert resolved_finding_ids(state) == frozenset({fnd(1)})


def test_snapshot_round_trips_resolution_and_omits_it_when_absent() -> None:
    """Old snapshots stay byte-identical: the key appears only once a row is resolved."""

    current = finding_record(_finding(1), 4)
    resolved = finding_record(
        _finding(2, subject_refs=(obl(2),)), 5, resolved_by_check_event_id=evt(6)
    )
    state = replace(
        empty_projection_state(),
        frontier=9,
        head_digest=_DIGEST,
        findings={fnd(1): current, fnd(2): resolved},
        freshness=LedgerFreshness.CURRENT,
    )
    snapshot = projection_snapshot(state)
    rows = snapshot["findings"]
    assert isinstance(rows, dict)
    assert "resolved_by_check_event_id" not in rows[fnd(1)]  # type: ignore[operator]
    assert rows[fnd(2)]["resolved_by_check_event_id"] == evt(6)  # type: ignore[index]
    decoded = projection_from_snapshot(snapshot)
    assert decoded == state
    assert type(decoded.findings[fnd(2)]) is FindingProjectionRecord
    assert canonical_encode(projection_snapshot(decoded)) == canonical_encode(snapshot)


def test_snapshot_rejects_a_null_resolution_key() -> None:
    state = replace(
        empty_projection_state(),
        frontier=9,
        head_digest=_DIGEST,
        findings={fnd(1): finding_record(_finding(1), 4)},
        freshness=LedgerFreshness.CURRENT,
    )
    snapshot = projection_snapshot(state)
    rows = snapshot["findings"]
    assert isinstance(rows, dict)
    rows[fnd(1)]["resolved_by_check_event_id"] = None  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid_projection_state"):
        projection_from_snapshot(snapshot)


@pytest.mark.parametrize(
    "gap",
    [
        "host_outcome_unavailable",
        "unpaired_event",
        "semantic_case_content_over_item_limit",
        "semantic_case_finding_refs_over_limit",
    ],
)
def test_resolution_explains_only_disqualifying_semantic_gaps(gap: str) -> None:
    from yoetz.kernel.finding_resolution import resolution_blockers

    finding = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    check = _check(
        semantic=_SEMANTIC_OK,
        coverage=_coverage(
            gaps=tuple(sorted((gap, "evidence_content_digest_only"))), semantic=True
        ),
    )
    state = _changed_state(check)
    assert resolution_blockers(finding, 4, check, frozenset(), proof_state=state) == (
        "coverage:" + gap,
    )
    assert not _resolves(finding, check)
    tolerated = _check(
        semantic=_SEMANTIC_OK,
        coverage=_coverage(gaps=("evidence_content_digest_only",), semantic=True),
    )
    assert resolution_blockers(finding, 4, tolerated, frozenset(), proof_state=state) == ()
    assert _resolves(finding, tolerated)
    # Without a material change after the finding, a review that merely did not repeat the issue
    # proves nothing.
    assert resolution_blockers(finding, 4, tolerated, frozenset()) == (
        "no_material_change_since_finding",
    )


def test_resolution_explanation_preserves_scope_policy_suppression_and_refire() -> None:
    from yoetz.kernel.finding_resolution import resolution_blockers

    finding = _finding()
    check = _check(
        suppressed=1,
        policies=(_RESEARCH,),
        scope=CheckScopeModel(claim_ids=(clm(99),), obligation_ids=()),
    )
    reasons = resolution_blockers(finding, 4, check, frozenset({issue_key(finding)}))
    assert reasons == (
        "issue_returned_again",
        "findings_suppressed",
        "matching_policy_not_completed",
        "subject_outside_checked_scope",
    )


def _command_proof_state() -> ProjectionState:
    from builders.policy_cases import act, make_case, obligation_record, plan_record, record, res
    from yoetz.domain.events import (
        ActionKind,
        ActionRecordedPayload,
        ObligationPublishedPayload,
        ObligationStatus,
        PlanPublishedPayload,
        RequestedItem,
        RequestedItemKind,
        ResultOutcome,
        ResultRecordedPayload,
    )

    return make_case(
        plans={1: plan_record(PlanPublishedPayload(1, "Plan", (obl(1), obl(2))), 1)},
        obligations={
            obl(1): obligation_record(
                ObligationPublishedPayload(obl(1), "Edit", "Result", ObligationStatus.OPEN), 2
            ),
            obl(2): obligation_record(
                ObligationPublishedPayload(
                    obl(2),
                    "Test",
                    "Result",
                    ObligationStatus.RESOLVED,
                    requested_items=(
                        RequestedItem(RequestedItemKind.COMMAND, "pytest unrelated.py"),
                    ),
                    resolution_evidence_refs=(res(2),),
                ),
                3,
            ),
        },
        actions={
            act(1): record(
                ActionRecordedPayload(act(1), ActionKind.EDIT, "Edit", obligation_refs=(obl(1),)),
                10,
            )
        },
        results={res(1): record(ResultRecordedPayload(res(1), act(1), ResultOutcome.SUCCESS), 20)},
    ).projection


@pytest.mark.parametrize("gap", ("command_attempt_uncorroborated", "command_attempt_mismatch"))
def test_command_gap_only_allows_proven_independent_action_result(gap: str) -> None:
    from yoetz.kernel.finding_resolution import resolution_blockers

    finding = _finding(kind=FindingKind.ACTION_WITHOUT_RESULT, subject_refs=(evt(10),))
    check = _check(tested=100, coverage=_coverage(gaps=(gap,)))
    state = _command_proof_state()
    assert resolution_blockers(finding, 4, check, frozenset(), proof_state=state) == ()
    assert qualifying_check_resolves(finding, 4, check, frozenset(), proof_state=state)
    assert not qualifying_check_resolves(finding, 4, check, frozenset())


def test_command_gap_relation_through_resolution_result_blocks_independence() -> None:
    """A command obligation's result link is an explicit relation to the action too."""
    from builders.policy_cases import obligation_record, res
    from yoetz.kernel.finding_resolution import resolution_blockers

    state = _command_proof_state()
    command_obligation = state.obligations[obl(2)]
    assert command_obligation.payload is not None
    state = replace(
        state,
        obligations={
            **state.obligations,
            obl(2): obligation_record(
                replace(command_obligation.payload, resolution_evidence_refs=(res(1),)),
                command_obligation.source_frontier,
            ),
        },
    )
    finding = _finding(kind=FindingKind.ACTION_WITHOUT_RESULT, subject_refs=(evt(10),))
    check = _check(tested=100, coverage=_coverage(gaps=("command_attempt_uncorroborated",)))
    reasons = resolution_blockers(finding, 4, check, frozenset(), proof_state=state)
    assert "command_relation_overlaps_obligation:" + obl(2) in reasons


def test_explanation_cache_is_scoped_to_the_candidate_check() -> None:
    """Two checks can share a subject frontier without sharing a pre-check projection."""
    from types import SimpleNamespace
    from typing import cast

    from yoetz.domain.events import LedgerRecord
    from yoetz.kernel.finding_resolution import (
        _historical_proof_state,  # pyright: ignore[reportPrivateUsage]
    )
    from yoetz.kernel.projections import empty_projection_state

    replayed: list[tuple[int, ...]] = []

    def fake_replay(events: tuple[LedgerRecord, ...]) -> ProjectionState:
        replayed.append(tuple(event.ledger.ingestion_sequence for event in events))
        return empty_projection_state()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("yoetz.kernel.reducers.replay", fake_replay)
    try:
        check = cast(
            CheckRecordedPayload,
            SimpleNamespace(subject_frontier=SimpleNamespace(sequence=1, head_digest=_DIGEST)),
        )
        records = cast(
            tuple[LedgerRecord, ...],
            tuple(
                SimpleNamespace(ledger=SimpleNamespace(ingestion_sequence=sequence))
                for sequence in (1, 2)
            ),
        )
        first = cast(LedgerRecord, SimpleNamespace(ledger=SimpleNamespace(ingestion_sequence=2)))
        second = cast(LedgerRecord, SimpleNamespace(ledger=SimpleNamespace(ingestion_sequence=3)))
        cache: dict[tuple[int, int, str], ProjectionState | None] = {}
        _historical_proof_state(check, first, records, cache)
        _historical_proof_state(check, second, records, cache)
    finally:
        monkeypatch.undo()
    assert replayed == [(1,), (1, 2)]


@pytest.mark.parametrize(
    "weakness",
    (
        "missing_result",
        "late_result",
        "unbound_action",
        "unreadable_result",
        "unknown_plan",
        "missing_obligation",
        "overlap",
        "original_gap",
        "semantic",
        "other_kind",
        "refired",
        "suppressed",
        "stale",
        "scoped_away",
    ),
)
def test_command_partition_never_upgrades_weak_or_overlapping_proof(weakness: str) -> None:
    from builders.policy_cases import act, record, res
    from yoetz.kernel.finding_resolution import resolution_blockers

    state = _command_proof_state()
    finding = _finding(kind=FindingKind.ACTION_WITHOUT_RESULT, subject_refs=(evt(10),))
    check = _check(tested=100, coverage=_coverage(gaps=("command_attempt_uncorroborated",)))
    keys: frozenset[tuple[object, ...]] = frozenset()
    if weakness == "missing_result":
        state = replace(state, results={})
    elif weakness == "late_result":
        check = _check(tested=15, coverage=check.coverage)
    elif weakness in {"unbound_action", "overlap"}:
        action = state.actions[act(1)]
        assert action.payload is not None
        state = replace(
            state,
            actions={
                act(1): record(
                    replace(
                        action.payload,
                        obligation_refs=() if weakness == "unbound_action" else (obl(2),),
                    ),
                    10,
                )
            },
        )
    elif weakness == "unreadable_result":
        state = replace(
            state, results={res(1): replace(state.results[res(1)], payload=None, redacted=True)}
        )
    elif weakness == "unknown_plan":
        state = replace(state, plans={})
    elif weakness == "missing_obligation":
        state = replace(state, obligations={obl(2): state.obligations[obl(2)]})
    elif weakness == "original_gap":
        finding = replace(finding, coverage=_coverage(gaps=("missing_ref",)))
    elif weakness == "semantic":
        finding = _finding(
            kind=FindingKind.ACTION_WITHOUT_RESULT,
            subject_refs=(evt(10),),
            origin=FindingOrigin.SEMANTIC_MODEL_DERIVED,
        )
    elif weakness == "other_kind":
        finding = _finding()
    elif weakness == "refired":
        keys = frozenset({issue_key(finding)})
    elif weakness == "suppressed":
        check = replace(check, suppressed_count=1)
    elif weakness == "stale":
        check = replace(
            check,
            coverage=_coverage(
                gaps=check.coverage.known_gaps,
                freshness=LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE,
            ),
        )
    elif weakness == "scoped_away":
        check = replace(check, scope=CheckScopeModel(claim_ids=(clm(99),), obligation_ids=()))
    reasons = resolution_blockers(finding, 4, check, keys, proof_state=state)
    assert reasons
    if weakness == "overlap":
        assert "command_relation_overlaps_obligation:" + obl(2) in reasons


def test_independent_command_gap_can_coexist_with_tolerated_host_gaps() -> None:
    finding = _finding(kind=FindingKind.ACTION_WITHOUT_RESULT, subject_refs=(evt(10),))
    check = _check(
        tested=100,
        coverage=_coverage(
            gaps=("command_attempt_uncorroborated", "content_unselected"),
            freshness=LedgerFreshness.REDACTED_GAP,
        ),
    )
    assert qualifying_check_resolves(
        finding, 4, check, frozenset(), proof_state=_command_proof_state()
    )


def _coordination_explanation_state(
    *, resolved_at: int | None, closure_at: int | None, kind: FindingKind
) -> tuple[ProjectionState, tuple[object, ...]]:
    """A finding derived from the context at 3, a check at 5, and an optional closure (#842)."""

    from types import SimpleNamespace

    from builders.policy_cases import record
    from yoetz.domain.coordination import CoordinationGapCode, OverlapKind
    from yoetz.domain.events import CoordinationContextRecordedPayload

    context = CoordinationContextRecordedPayload(
        detection_id=evt(1),
        project_id="prj_20000000-0000-4000-8000-000000000001",
        membership_generation=4,
        left_task_id="tsk_20000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        right_task_id="tsk_20000000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
        recipient_task_id="tsk_20000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        counterpart_task_id="tsk_20000000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
        source_task_id="tsk_20000000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
        overlap_kind=OverlapKind.PHYSICAL,
        resource_identities=("sha256:" + "a" * 64,),
        resource_count=1,
        source_repository_commitment="hmac-sha256:" + "b" * 64,
        source_workspace_commitment="hmac-sha256:" + "c" * 64,
        source_route_generation=1,
        source_attributable_paths=True,
        context_digest="sha256:" + "d" * 64,
    )
    contexts = {evt(3): record(context, 3)}
    if closure_at is not None:
        closure = replace(
            context,
            resource_identities=(),
            resource_count=0,
            source_attributable_paths=False,
            gap_codes=(CoordinationGapCode.REVOKED,),
            context_digest="sha256:" + "e" * 64,
        )
        contexts[evt(closure_at)] = record(closure, closure_at)
    policy = ("coordination", "0.1.0") if kind is FindingKind.COORDINATION_OVERLAP else _WORK
    finding = _finding(kind=kind, subject_refs=(evt(3),), policy_id=policy[0])
    state = replace(
        empty_projection_state(),
        frontier=20,
        head_digest=_DIGEST,
        findings={
            fnd(1): finding_record(
                finding,
                4,
                resolved_by_check_event_id=None if resolved_at is None else evt(resolved_at),
            )
        },
        coordination_contexts=contexts,
    )
    checks = tuple(
        SimpleNamespace(
            event_id=evt(sequence),
            schema=SimpleNamespace(name="check_recorded"),
            ledger=SimpleNamespace(ingestion_sequence=sequence),
            payload=_check(tested=sequence, policies=(policy,)),
        )
        for sequence in sorted({5, *(() if resolved_at is None else (resolved_at,))})
    )
    return state, checks


def test_resolution_explanation_names_a_superseded_coordination_generation() -> None:
    from typing import cast

    from yoetz.domain.events import LedgerRecord
    from yoetz.kernel.finding_resolution import finding_resolution_explanation

    state, records = _coordination_explanation_state(
        resolved_at=12, closure_at=10, kind=FindingKind.COORDINATION_OVERLAP
    )
    explanation = finding_resolution_explanation(
        state, fnd(1), cast(tuple[LedgerRecord, ...], records)
    )
    assert explanation == (
        f"Resolved by qualifying check {evt(12)} after project coordination generation 4 was "
        "superseded; the context is retained as history, not as a current coordination "
        "obligation."
    )


def test_resolution_before_the_closure_keeps_the_ordinary_explanation() -> None:
    """A disposition-backed resolution that predates the closure is not re-described."""

    from typing import cast

    from yoetz.domain.events import LedgerRecord
    from yoetz.kernel.finding_resolution import finding_resolution_explanation

    state, records = _coordination_explanation_state(
        resolved_at=8, closure_at=10, kind=FindingKind.COORDINATION_OVERLAP
    )
    explanation = finding_resolution_explanation(
        state, fnd(1), cast(tuple[LedgerRecord, ...], records)
    )
    assert explanation == f"Resolved by qualifying check {evt(8)}; retained as history."


def test_unresolved_superseded_finding_points_at_the_next_qualifying_check() -> None:
    from typing import cast

    from yoetz.domain.events import LedgerRecord
    from yoetz.kernel.finding_resolution import finding_resolution_explanation

    state, records = _coordination_explanation_state(
        resolved_at=None, closure_at=10, kind=FindingKind.COORDINATION_OVERLAP
    )
    explanation = finding_resolution_explanation(
        state, fnd(1), cast(tuple[LedgerRecord, ...], records)
    )
    assert explanation.startswith(
        "Unresolved: project coordination generation 4 was superseded, so this is historical "
        "context rather than a current coordination obligation"
    )
    assert "can resolve it" in explanation


@pytest.mark.parametrize(
    ("closure_at", "kind"),
    ((None, FindingKind.COORDINATION_OVERLAP), (10, FindingKind.ACTION_WITHOUT_RESULT)),
)
def test_supersession_wording_needs_a_closure_for_that_coordination_finding(
    closure_at: int | None, kind: FindingKind
) -> None:
    from typing import cast

    from yoetz.domain.events import LedgerRecord
    from yoetz.kernel.finding_resolution import finding_resolution_explanation

    state, records = _coordination_explanation_state(
        resolved_at=12, closure_at=closure_at, kind=kind
    )
    explanation = finding_resolution_explanation(
        state, fnd(1), cast(tuple[LedgerRecord, ...], records)
    )
    assert explanation == f"Resolved by qualifying check {evt(12)}; retained as history."


@pytest.mark.parametrize(
    "gap",
    [
        "captured_object_unavailable",
        "content_unselected",
        "host_outcome_unavailable",
        "unpaired_event",
        "content_capture_unavailable",
        "semantic_case_content_over_item_limit",
    ],
)
def test_completed_semantic_recheck_can_retain_original_readable_capture_limits(gap: str) -> None:
    coverage = _coverage(gaps=(gap,), semantic=True, freshness=LedgerFreshness.PARTIAL)
    original = replace(_finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED), coverage=coverage)
    later = replace(
        _check(semantic=_SEMANTIC_OK, coverage=coverage),
        semantic_conclusion="no_material_discrepancy",
    )
    assert _resolves(original, later) is True
    # The same review over unchanged state is a re-roll, not proof (issue #884).
    assert _resolves(original, later, changed=False) is False
    # A legacy check did not record whether it was unassessable: no inferred success.
    assert _resolves(original, _check(semantic=_SEMANTIC_OK, coverage=coverage)) is False
    assert _resolves(original, replace(later, semantic_conclusion="insufficient_packet")) is False
    # A new capture weakness is not licensed by an originally unbounded finding.
    assert (
        _resolves(
            _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED),
            _check(semantic=_SEMANTIC_OK, coverage=coverage),
        )
        is False
    )
    # An acknowledgement/local check cannot supply the independent semantic proof.
    assert _resolves(original, _check(coverage=coverage)) is False
    assert original.coverage.known_gaps == (gap,)


@pytest.mark.parametrize(
    "gap",
    [
        "semantic_packet_insufficient",
        "semantic_case_finding_refs_over_limit",
        "semantic_review_context_withheld",
        "semantic_challenges_rejected",
        "truncated_payload",
        "content_redacted",
        "event_payload_unavailable",
        "redacted_event",
        "missing_ref",
        "observation_input_loss",
    ],
)
def test_matching_material_gaps_never_become_semantic_absence_proof(gap: str) -> None:
    from yoetz.kernel.finding_resolution import resolution_blockers

    coverage = _coverage(gaps=(gap,), semantic=True, freshness=LedgerFreshness.PARTIAL)
    original = replace(_finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED), coverage=coverage)
    # An assessable conclusion and a material change reach the capture-baseline branch, so the
    # original gap alone is what must keep blocking.
    later = replace(
        _check(semantic=_SEMANTIC_OK, coverage=coverage),
        semantic_conclusion="no_material_discrepancy",
    )
    blockers = resolution_blockers(
        original, 4, later, frozenset(), proof_state=_changed_state(later)
    )
    assert "coverage:" + gap in blockers
    assert "no_material_change_since_finding" not in blockers
    assert _resolves(original, later) is False


def test_redacted_freshness_from_a_recorded_captured_object_gap_is_a_baseline() -> None:
    """The deterministic case caps freshness at redacted_gap for captured_object_unavailable."""

    coverage = _coverage(
        gaps=("captured_object_unavailable",),
        semantic=True,
        freshness=LedgerFreshness.REDACTED_GAP,
    )
    original = replace(_finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED), coverage=coverage)
    later = replace(
        _check(semantic=_SEMANTIC_OK, coverage=coverage),
        semantic_conclusion="no_material_discrepancy",
    )
    assert _resolves(original, later) is True
    assert _resolves(original, later, changed=False) is False
    # Not recorded on the original finding: still unproven.
    fresh = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    assert _resolves(fresh, later) is False
    # A redaction with its own gap code is never explained by the capture baseline.
    hidden = _coverage(
        gaps=("captured_object_unavailable", "redacted_object"),
        semantic=True,
        freshness=LedgerFreshness.REDACTED_GAP,
    )
    assert _resolves(original, replace(later, coverage=hidden)) is False


def test_semantic_resolution_needs_new_work_or_a_subject_revision() -> None:
    from builders.policy_cases import claim_record
    from yoetz.domain.events import ClaimKind, ClaimRecordedPayload
    from yoetz.kernel.finding_resolution import resolution_blockers

    original = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED, subject_refs=(clm(1),))
    later = replace(_check(semantic=_SEMANTIC_OK), semantic_conclusion="no_material_discrepancy")
    base = replace(empty_projection_state(), frontier=8, head_digest=_DIGEST)
    # Nothing new, or only a record older than the finding: no proof.
    assert resolution_blockers(original, 4, later, frozenset(), proof_state=base) == (
        "no_material_change_since_finding",
    )
    unrelated = claim_record(
        ClaimRecordedPayload(
            clm(2), ClaimKind.COMPLETION, "Other", (obl(2),), obligation_refs=(obl(2),)
        ),
        6,
    )
    assert not qualifying_check_resolves(
        original, 4, later, frozenset(), proof_state=replace(base, claims={clm(2): unrelated})
    )
    revised = claim_record(
        ClaimRecordedPayload(
            clm(1), ClaimKind.COMPLETION, "Revised", (obl(1),), obligation_refs=(obl(1),)
        ),
        6,
    )
    assert qualifying_check_resolves(
        original, 4, later, frozenset(), proof_state=replace(base, claims={clm(1): revised})
    )
    # A change after the checked frontier is not part of the tested state.
    late = _changed_state(later, recorded_at=8)
    assert late.frontier == 9 and later.subject_frontier.sequence == 8
    assert not qualifying_check_resolves(original, 4, later, frozenset(), proof_state=late)


@pytest.mark.parametrize(
    "freshness",
    [
        LedgerFreshness.REDACTED_GAP,
        LedgerFreshness.UNKNOWN,
        LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE,
    ],
)
def test_capture_baseline_cannot_rehabilitate_unreadable_original_proof(
    freshness: LedgerFreshness,
) -> None:
    coverage = _coverage(gaps=("content_unselected",), semantic=True, freshness=freshness)
    original = replace(_finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED), coverage=coverage)
    later = replace(coverage, ledger_freshness=LedgerFreshness.PARTIAL)
    assert _resolves(original, _check(semantic=_SEMANTIC_OK, coverage=later)) is False


def test_unassessable_conclusion_blocks_proof_even_without_a_coverage_gap() -> None:
    original = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    later = replace(_check(semantic=_SEMANTIC_OK), semantic_conclusion="insufficient_packet")
    assert _resolves(original, later) is False


# Issue #904: a bounded AI-powered review selection is disclosure, not a veto on repair proof.
_SCOPE_REDUCED = "semantic_reference_scope_reduced"
# The agent-visible gaps of an ordinary semantic check in a long hook-observed session.
_LONG_SESSION_REVIEW_GAPS = (
    "content_unselected",
    "evidence_content_digest_only",
    "host_outcome_unavailable",
    _SCOPE_REDUCED,
    "unpaired_event",
)


def _assessed(check: CheckRecordedPayload) -> CheckRecordedPayload:
    return replace(check, semantic_conclusion="no_material_discrepancy")


def _in_view(*refs: str) -> tuple[str, ...]:
    return tuple(sorted(set(refs), key=str.encode))


# What an ordinary reduced packet carried: the finding's subject and ``_changed_state``'s repair.
_REPAIR_IN_VIEW: tuple[str, ...] = (obl(1), act(9))


def _review_check(
    *gaps: str, tested: int = 8, included: tuple[str, ...] | None = _REPAIR_IN_VIEW
) -> CheckRecordedPayload:
    """A completed, assessable semantic check whose coverage carries exactly *gaps*.

    A reduced-scope check records *included* as its packet's included references (issue #904);
    ``None`` models a check with no such record.
    """

    coverage = _coverage(gaps=gaps, semantic=True, freshness=LedgerFreshness.PARTIAL)
    check = _assessed(_check(tested=tested, semantic=_SEMANTIC_OK, coverage=coverage))
    if _SCOPE_REDUCED in gaps and included is not None:
        check = replace(check, semantic_included_refs=_in_view(*included))
    return check


def _semantic_finding(*gaps: str) -> Finding:
    """An AI-powered finding whose own recorded coverage carries exactly *gaps*."""

    coverage = _coverage(gaps=gaps, semantic=True, freshness=LedgerFreshness.PARTIAL)
    return replace(_finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED), coverage=coverage)


def _blockers(
    finding: Finding,
    check: CheckRecordedPayload,
    *,
    raised_under_reduced_scope: bool = False,
) -> tuple[str, ...]:
    from yoetz.kernel.finding_resolution import resolution_blockers

    return resolution_blockers(
        finding,
        4,
        check,
        frozenset(),
        proof_state=_changed_state(check),
        raised_under_reduced_scope=raised_under_reduced_scope,
    )


def test_a_reduced_review_scope_never_weakens_local_proof() -> None:
    """The local-check case is not reduced (ADR-006), so only the review packet is bounded."""

    finding = _finding()
    readable = _check(semantic=_SEMANTIC_OK, coverage=_coverage(semantic=True))
    reduced = _check(
        semantic=_SEMANTIC_OK,
        coverage=_coverage(gaps=(_SCOPE_REDUCED,), semantic=True),
    )
    assert _blockers(finding, readable) == ()
    assert _blockers(finding, reduced) == ()
    assert _resolves(finding, reduced) is True
    # The same holds when the bounded review itself did not run to completion.
    not_run = _check(
        coverage=_coverage(gaps=(_SCOPE_REDUCED, "semantic_relevance_review_not_run")),
    )
    assert _blockers(finding, not_run) == ()


def test_the_dateutil_local_finding_shape_resolves_under_a_reduced_review_scope() -> None:
    """dateutil ``fnd_2c29e40c``: its only blocker was ``semantic_reference_scope_reduced``."""

    finding = replace(
        _finding(kind=FindingKind.LEDGER_STALE_OR_INCOMPLETE),
        coverage=_coverage(
            gaps=("evidence_content_digest_only",), freshness=LedgerFreshness.CURRENT
        ),
    )
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    assert _blockers(finding, later) == ()
    assert _resolves(finding, later) is True
    # Not returned is still required; the interim leaves a truncated payload blocking.
    assert not qualifying_check_resolves(finding, 4, later, frozenset({issue_key(finding)}))
    truncated = _review_check(*_LONG_SESSION_REVIEW_GAPS, "truncated_payload")
    assert _blockers(finding, truncated) == ("coverage:truncated_payload",)


def test_a_semantic_finding_stamped_with_the_scope_resolves_under_the_same_limit() -> None:
    """A post-upgrade finding records the reduced scope its review ran under (issue #904)."""

    original = _semantic_finding(
        "content_unselected", "host_outcome_unavailable", _SCOPE_REDUCED, "unpaired_event"
    )
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    assert _blockers(original, later) == ()
    assert _resolves(original, later) is True
    # Every other requirement stands: a re-roll, an unassessable answer, an unfinished review.
    assert _resolves(original, later, changed=False) is False
    insufficient = replace(
        later,
        semantic_conclusion="insufficient_packet",
        coverage=replace(
            later.coverage,
            known_gaps=tuple(sorted((*later.coverage.known_gaps, "semantic_packet_insufficient"))),
        ),
    )
    assert _blockers(original, insufficient) == (
        "semantic_packet_insufficient",
        "coverage:content_unselected",
        "coverage:host_outcome_unavailable",
        "coverage:semantic_packet_insufficient",
        "coverage:" + _SCOPE_REDUCED,
        "coverage:unpaired_event",
    )
    assert _resolves(original, _check(coverage=later.coverage)) is False


def test_a_new_reduced_review_scope_still_blocks_semantic_proof() -> None:
    """A review that saw the whole ledger raised it; a bounded later review is a new limit."""

    original = _semantic_finding("content_unselected", "host_outcome_unavailable", "unpaired_event")
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    assert _blockers(original, later) == ("coverage:" + _SCOPE_REDUCED,)
    assert _resolves(original, later) is False


def test_a_pre_upgrade_finding_uses_its_raising_checks_recorded_scope() -> None:
    """Lifecycle fallback: the raising check recorded the code the finding could not carry."""

    original = _semantic_finding("content_unselected", "host_outcome_unavailable", "unpaired_event")
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    assert _blockers(original, later, raised_under_reduced_scope=True) == ()
    assert qualifying_check_resolves(
        original,
        4,
        later,
        frozenset(),
        proof_state=_changed_state(later),
        raised_under_reduced_scope=True,
    )
    # The fallback supplies only the scope code: no other limitation and no other requirement.
    truncated = _review_check(*_LONG_SESSION_REVIEW_GAPS, "truncated_payload")
    assert _blockers(original, truncated, raised_under_reduced_scope=True) == (
        "coverage:truncated_payload",
    )
    assert _blockers(
        original,
        replace(later, semantic_conclusion=None, semantic_included_refs=None),
        raised_under_reduced_scope=True,
    ) == (
        "coverage:content_unselected",
        "coverage:host_outcome_unavailable",
        "coverage:" + _SCOPE_REDUCED,
        "coverage:unpaired_event",
    )
    # The finding's recorded coverage is never rewritten.
    assert _SCOPE_REDUCED not in original.coverage.known_gaps


def _raising_check(*gaps: str) -> CheckRecordedPayload:
    """The completed review that raised ``_semantic_finding`` at subject frontier 3."""

    return _review_check(*gaps, tested=3)


def test_apply_records_the_raising_check_only_for_a_reduced_scope_review() -> None:
    raised = _semantic_finding("content_unselected", "host_outcome_unavailable", "unpaired_event")
    local = _finding(2, subject_refs=(obl(2),))
    assert raised.subject_frontier == local.subject_frontier == _raising_check().subject_frontier
    findings = {fnd(1): finding_record(raised, 4), fnd(2): finding_record(local, 4)}

    apply_check_resolution(
        findings, replace(_raising_check(), returned_finding_ids=(fnd(1), fnd(2))), evt(5)
    )
    assert findings[fnd(1)].reduced_scope_raising_check_event_id is None, "scope was not reduced"

    apply_check_resolution(
        findings,
        replace(_raising_check(*_LONG_SESSION_REVIEW_GAPS), returned_finding_ids=(fnd(1), fnd(2))),
        evt(6),
    )
    assert findings[fnd(1)].reduced_scope_raising_check_event_id == evt(6)
    assert findings[fnd(2)].reduced_scope_raising_check_event_id is None, "local proof needs none"

    # A later check that returns the same row again is not the check that raised it.
    later_return = replace(
        _review_check(*_LONG_SESSION_REVIEW_GAPS), returned_finding_ids=(fnd(1),)
    )
    fresh = {fnd(1): finding_record(raised, 4)}
    apply_check_resolution(fresh, later_return, evt(9))
    assert fresh[fnd(1)].reduced_scope_raising_check_event_id is None


def test_the_recorded_raising_scope_resolves_through_the_fold_until_redacted() -> None:
    raised = _semantic_finding("content_unselected", "host_outcome_unavailable", "unpaired_event")
    findings = {fnd(1): finding_record(raised, 4)}
    raising = replace(_raising_check(*_LONG_SESSION_REVIEW_GAPS), returned_finding_ids=(fnd(1),))
    apply_check_resolution(findings, raising, evt(5))
    assert findings[fnd(1)].reduced_scope_raising_check_event_id == evt(5)

    # Redacting the raising check removes the fallback: its coverage is no longer readable.
    redacted = dict(findings)
    reopen_findings_resolved_by(redacted, frozenset({evt(5)}))
    assert redacted[fnd(1)].reduced_scope_raising_check_event_id is None
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    apply_check_resolution(redacted, later, evt(9), proof_state=_changed_state(later))
    assert redacted[fnd(1)].resolved_by_check_event_id is None

    apply_check_resolution(findings, later, evt(9), proof_state=_changed_state(later))
    assert findings[fnd(1)].resolved_by_check_event_id == evt(9)
    assert findings[fnd(1)].resolution_raising_check_event_id == evt(5), "proof read the raiser"
    # Redacting the raising check after resolution reopens the row: that proof read the raising
    # check's recorded scope, and unreadable proof is no proof.
    reopen_findings_resolved_by(findings, frozenset({evt(5)}))
    assert findings[fnd(1)].resolved_by_check_event_id is None
    assert findings[fnd(1)].resolution_raising_check_event_id is None
    assert findings[fnd(1)].reduced_scope_raising_check_event_id is None
    # The reopened row now meets later reviews without the fallback: the scope code blocks.
    again = _review_check(*_LONG_SESSION_REVIEW_GAPS, tested=10)
    apply_check_resolution(findings, again, evt(11), proof_state=_changed_state(again))
    assert findings[fnd(1)].resolved_by_check_event_id is None


@pytest.mark.parametrize("independent", ["stamped_finding", "unreduced_proving_check"])
def test_redacting_the_raising_check_keeps_a_proof_that_never_read_it(independent: str) -> None:
    """Only a resolution that needed the raising check's scope reopens when it is redacted."""

    gaps = ["content_unselected", "host_outcome_unavailable", "unpaired_event"]
    later_gaps = list(_LONG_SESSION_REVIEW_GAPS)
    if independent == "stamped_finding":
        gaps.append(_SCOPE_REDUCED)  # Post-upgrade: the finding's own coverage carries it.
    else:
        later_gaps.remove(_SCOPE_REDUCED)  # The proving review was not reduced at all.
    findings = {fnd(1): finding_record(_semantic_finding(*gaps), 4)}
    raising = replace(_raising_check(*_LONG_SESSION_REVIEW_GAPS), returned_finding_ids=(fnd(1),))
    apply_check_resolution(findings, raising, evt(5))
    assert findings[fnd(1)].reduced_scope_raising_check_event_id == evt(5)
    later = _review_check(*later_gaps)
    apply_check_resolution(findings, later, evt(9), proof_state=_changed_state(later))
    assert findings[fnd(1)].resolved_by_check_event_id == evt(9)
    assert findings[fnd(1)].resolution_raising_check_event_id is None

    reopen_findings_resolved_by(findings, frozenset({evt(5)}))
    assert findings[fnd(1)].resolved_by_check_event_id == evt(9)
    assert findings[fnd(1)].reduced_scope_raising_check_event_id is None
    # Redacting the proving check still reopens it.
    reopen_findings_resolved_by(findings, frozenset({evt(9)}))
    assert findings[fnd(1)].resolved_by_check_event_id is None


def test_a_returned_row_drops_the_raising_check_its_old_proof_relied_on() -> None:
    record = replace(
        finding_record(_semantic_finding("content_unselected"), 4),
        resolved_by_check_event_id=evt(9),
        reduced_scope_raising_check_event_id=evt(5),
        resolution_raising_check_event_id=evt(5),
    )
    findings = {fnd(1): record}
    apply_check_resolution(findings, _check(returned=(fnd(1),)), evt(11))
    assert findings[fnd(1)].resolved_by_check_event_id is None
    assert findings[fnd(1)].resolution_raising_check_event_id is None
    assert findings[fnd(1)].reduced_scope_raising_check_event_id == evt(5)


def test_snapshot_round_trips_the_raising_scope_and_omits_it_when_absent() -> None:
    raised = _semantic_finding("content_unselected")
    plain = finding_record(_finding(2, subject_refs=(obl(2),)), 5)
    recorded = replace(finding_record(raised, 4), reduced_scope_raising_check_event_id=evt(6))
    state = replace(
        empty_projection_state(),
        frontier=9,
        head_digest=_DIGEST,
        findings={fnd(1): recorded, fnd(2): plain},
        freshness=LedgerFreshness.CURRENT,
    )
    snapshot = projection_snapshot(state)
    rows = snapshot["findings"]
    assert isinstance(rows, dict)
    assert "reduced_scope_raising_check_event_id" not in rows[fnd(2)]  # type: ignore[operator]
    assert rows[fnd(1)]["reduced_scope_raising_check_event_id"] == evt(6)  # type: ignore[index]
    decoded = projection_from_snapshot(snapshot)
    assert decoded == state
    assert canonical_encode(projection_snapshot(decoded)) == canonical_encode(snapshot)
    rows[fnd(1)]["reduced_scope_raising_check_event_id"] = None  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid_projection_state"):
        projection_from_snapshot(snapshot)

    relied = replace(
        recorded, resolved_by_check_event_id=evt(9), resolution_raising_check_event_id=evt(6)
    )
    relied_state = replace(state, findings={fnd(1): relied, fnd(2): plain})
    relied_snapshot = projection_snapshot(relied_state)
    relied_rows = relied_snapshot["findings"]
    assert isinstance(relied_rows, dict)
    assert "resolution_raising_check_event_id" not in relied_rows[fnd(2)]  # type: ignore[operator]
    assert relied_rows[fnd(1)]["resolution_raising_check_event_id"] == evt(6)  # type: ignore[index]
    assert projection_from_snapshot(relied_snapshot) == relied_state
    # A relied-on raising check without the resolution it supported is not a valid record.
    with pytest.raises(ValueError, match="invalid_projection_state"):
        replace(relied, resolved_by_check_event_id=None)
    del relied_rows[fnd(1)]["resolved_by_check_event_id"]  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="invalid_projection_state"):
        projection_from_snapshot(relied_snapshot)


# drizzle ``fnd_8d68fbc3``: raised by a review whose coverage carried a gap outside the baseline.
_DRIZZLE_RAISED_UNDER = (
    "completion_plan_not_claimed",
    "content_unselected",
    "evidence_content_digest_only",
    "host_outcome_unavailable",
    "semantic_case_content_over_item_limit",
    "unpaired_event",
)
_DRIZZLE_LATER_REVIEW = (
    "content_unselected",
    "evidence_content_digest_only",
    "host_outcome_unavailable",
    "semantic_case_content_over_item_limit",
    _SCOPE_REDUCED,
    "unpaired_event",
)


@pytest.mark.parametrize(
    "beyond_baseline",
    ["completion_plan_not_claimed", "content_redacted", "command_attempt_uncorroborated"],
)
def test_a_lapsed_limit_beyond_the_baseline_makes_the_baseline_readable(
    beyond_baseline: str,
) -> None:
    """Secondary edge: the first later check without that limit saw at least as much."""

    raised_under = (
        *(gap for gap in _DRIZZLE_RAISED_UNDER if gap != "completion_plan_not_claimed"),
        beyond_baseline,
    )
    original = _semantic_finding(*raised_under)
    later = _review_check(*_DRIZZLE_LATER_REVIEW)
    assert _blockers(original, later, raised_under_reduced_scope=True) == ()
    # Post-upgrade the scope is stamped instead; the answer is the same.
    stamped = _semantic_finding(*raised_under, _SCOPE_REDUCED)
    assert _blockers(stamped, later) == ()
    # The interim still refuses a truncated payload.
    truncated = _review_check(*_DRIZZLE_LATER_REVIEW, "truncated_payload")
    assert _blockers(stamped, truncated) == ("coverage:truncated_payload",)

    # While the limitation is still present it keeps blocking, exactly as before.
    still = _review_check(*_DRIZZLE_LATER_REVIEW, beyond_baseline)
    assert _blockers(original, still, raised_under_reduced_scope=True) == tuple(
        "coverage:" + gap
        for gap in sorted(
            {*_DRIZZLE_LATER_REVIEW, beyond_baseline} - {"evidence_content_digest_only"}
        )
    )


def test_a_lapsed_limit_never_rehabilitates_unproven_original_freshness() -> None:
    original = replace(
        _semantic_finding(*_DRIZZLE_RAISED_UNDER),
        coverage=_coverage(
            gaps=_DRIZZLE_RAISED_UNDER, semantic=True, freshness=LedgerFreshness.REDACTED_GAP
        ),
    )
    later = _review_check(*_DRIZZLE_LATER_REVIEW)
    assert _blockers(original, later, raised_under_reduced_scope=True) == tuple(
        "coverage:" + gap for gap in _DRIZZLE_LATER_REVIEW if gap != "evidence_content_digest_only"
    )


@pytest.mark.parametrize(
    "gap",
    [
        "content_redacted",
        "event_payload_unavailable",
        "redacted_event",
        "redacted_object",
        "truncated_payload",
        "semantic_packet_insufficient",
        "semantic_review_context_withheld",
        "semantic_challenges_rejected",
    ],
)
def test_a_newly_appearing_real_limit_still_blocks_semantic_proof(gap: str) -> None:
    """Regression: the scope tolerance does not open the closed default (issue #904)."""

    original = _semantic_finding(*_LONG_SESSION_REVIEW_GAPS)
    later = _review_check(*_LONG_SESSION_REVIEW_GAPS, gap)
    blockers = _blockers(original, later, raised_under_reduced_scope=True)
    assert "coverage:" + gap in blockers
    assert "coverage:" + _SCOPE_REDUCED not in blockers
    assert _resolves(original, later) is False


def test_capture_failures_block_local_proof_too() -> None:
    """The interim tolerance is the selection code alone; truncation waits for its source test."""

    from yoetz.kernel.finding_resolution import CAPTURE_FAILURE_GAPS

    for gap in sorted(CAPTURE_FAILURE_GAPS):
        later = _review_check(*_LONG_SESSION_REVIEW_GAPS, gap)
        assert _blockers(_finding(), later) == ("coverage:" + gap,), gap


def test_selection_and_capture_failure_classes_are_disjoint_and_closed() -> None:
    from yoetz.application.check import SEMANTIC_CASE_CONTENT_GAPS
    from yoetz.domain.receipts import SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
    from yoetz.kernel.finding_resolution import (
        CAPTURE_FAILURE_GAPS,
        REVIEW_SELECTION_GAPS,
        SEMANTIC_FINDING_CAPTURE_BASELINE_GAPS,
        SEMANTIC_REFERENCE_SCOPE_REDUCED_GAP,
    )

    assert SEMANTIC_REFERENCE_SCOPE_REDUCED_GAP == _SCOPE_REDUCED
    assert SEMANTIC_REFERENCE_SCOPE_REDUCED_GAP in REVIEW_SELECTION_GAPS
    assert not REVIEW_SELECTION_GAPS & CAPTURE_FAILURE_GAPS
    assert REVIEW_SELECTION_GAPS <= SEMANTIC_FINDING_CAPTURE_BASELINE_GAPS
    assert not CAPTURE_FAILURE_GAPS & SEMANTIC_FINDING_CAPTURE_BASELINE_GAPS

    # Every code a review packet can fold into check coverage has a decided resolution class:
    # (blocks local proof, tolerated for semantic proof when unchanged from the finding's own
    # recorded baseline). A new packet code fails here until someone decides its row.
    decided = {
        "captured_object_unavailable": (False, True),
        "content_capture_unavailable": (True, True),
        "content_unselected": (False, True),
        "content_redacted": (True, False),
        "truncated_payload": (True, False),
        "semantic_case_finding_refs_over_limit": (False, False),
        SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP: (False, True),
        SEMANTIC_REFERENCE_SCOPE_REDUCED_GAP: (False, True),
    }
    packet_codes = SEMANTIC_CASE_CONTENT_GAPS | {
        SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
        SEMANTIC_REFERENCE_SCOPE_REDUCED_GAP,
    }
    assert packet_codes == set(decided)
    for code, (blocks_local, unchanged_tolerated) in decided.items():
        later = _review_check(code)
        assert bool(_blockers(_finding(), later)) is blocks_local, code
        assert (not _blockers(_semantic_finding(code), later)) is unchanged_tolerated, code
        assert _blockers(_semantic_finding(), later) == ("coverage:" + code,), code
        if code in REVIEW_SELECTION_GAPS:
            assert (blocks_local, unchanged_tolerated) == (False, True), code
        if code in CAPTURE_FAILURE_GAPS:
            assert (blocks_local, unchanged_tolerated) == (True, False), code


def _answered_state(
    check: CheckRecordedPayload, *, cites: str, redacted_evidence: bool = False
) -> ProjectionState:
    """``_changed_state`` plus repair evidence and a response to ``fnd(1)`` citing *cites*.

    The evidence ``evd(7)`` and the result ``res(7)``, which links it, are recorded after the
    finding and before the checked frontier; the response cites one of them.
    """

    from builders.policy_cases import evd, evidence_record, record, res
    from yoetz.domain.events import (
        EvidenceKind,
        EvidenceRecordedPayload,
        ResponseRecordedPayload,
        ResultOutcome,
        ResultRecordedPayload,
    )
    from yoetz.domain.findings import ResponseDisposition
    from yoetz.domain.values import timestamp_from_string

    state = _changed_state(check)
    evidence = evidence_record(
        EvidenceRecordedPayload(
            evidence_id=evd(7),
            evidence_kind=EvidenceKind.TEST_RESULT,
            strength=EvidenceImmutability.METADATA_ONLY,
            observed_at=timestamp_from_string("2026-01-01T00:00:00.000Z"),
            reference="repair-test-log",
        ),
        6,
    )
    if redacted_evidence:
        evidence = replace(evidence, payload=None, redacted=True)
    result = record(
        ResultRecordedPayload(res(7), act(9), ResultOutcome.SUCCESS, 0, evidence_refs=(evd(7),)),
        7,
    )
    response = record(
        ResponseRecordedPayload(
            finding_id=fnd(1),
            finding_frontier=Frontier(4, _DIGEST),
            disposition=ResponseDisposition.ACKNOWLEDGED,
            evidence_refs=(cites,),  # type: ignore[arg-type]
        ),
        8,
    )
    return replace(
        state,
        frontier=max(state.frontier, 8),
        evidence={evd(7): evidence},
        results={res(7): result},
        responses={fnd(1): response},
    )


def test_a_reduced_review_without_the_repair_in_view_cannot_resolve_a_semantic_finding() -> None:
    """PR930-F1: the same reduced-scope code on a later review is not proof that it saw the repair.

    The finding was raised under a reduced scope, the agent made a material change, and a later
    completed review under the same bound did not return the issue. Nothing records that the later
    packet carried that change, so the scope code keeps blocking and stays on the receipt.
    """

    original = _semantic_finding(
        "content_unselected", "host_outcome_unavailable", _SCOPE_REDUCED, "unpaired_event"
    )
    omitted = ("finding_material_outside_reduced_review_scope", "coverage:" + _SCOPE_REDUCED)
    unrecorded = _review_check(*_LONG_SESSION_REVIEW_GAPS, included=None)
    assert _blockers(original, unrecorded) == omitted
    assert _resolves(original, unrecorded) is False
    # The lifecycle fallback supplies the baseline code, never the missing relevance proof.
    unstamped = _semantic_finding(
        "content_unselected", "host_outcome_unavailable", "unpaired_event"
    )
    assert _blockers(unstamped, unrecorded, raised_under_reduced_scope=True) == omitted

    # The recorded packet must carry the finding's subject and the change made after it.
    repair_omitted = _review_check(*_LONG_SESSION_REVIEW_GAPS, included=(obl(1), obl(2)))
    assert _blockers(original, repair_omitted) == omitted
    subject_omitted = _review_check(*_LONG_SESSION_REVIEW_GAPS, included=(act(9),))
    assert _blockers(original, subject_omitted) == omitted
    # The change may be named by its logical row or by the event that recorded it.
    by_event = _review_check(*_LONG_SESSION_REVIEW_GAPS, included=(obl(1), evt(5)))
    assert _blockers(original, by_event) == ()
    assert _blockers(original, _review_check(*_LONG_SESSION_REVIEW_GAPS)) == ()

    # Without a material change the missing repair is the only thing named.
    assert _blockers(original, unrecorded)  # sanity: still blocked
    from yoetz.kernel.finding_resolution import resolution_blockers

    unchanged = replace(_changed_state(unrecorded), actions={})
    assert resolution_blockers(original, 4, unrecorded, frozenset(), proof_state=unchanged) == (
        "no_material_change_since_finding",
    )


@pytest.mark.parametrize("cites", ["evidence", "result"])
def test_a_reduced_review_must_carry_the_repair_evidence_the_response_links(cites: str) -> None:
    """Relevance includes the repair the agent answered with, not only the finding's subjects."""

    from builders.policy_cases import evd, res
    from yoetz.kernel.finding_resolution import resolution_blockers

    original = _semantic_finding(
        "content_unselected", "host_outcome_unavailable", _SCOPE_REDUCED, "unpaired_event"
    )
    cited = evd(7) if cites == "evidence" else res(7)
    omitted = ("finding_material_outside_reduced_review_scope", "coverage:" + _SCOPE_REDUCED)

    def blockers(check: CheckRecordedPayload, **state: bool) -> tuple[str, ...]:
        answered = _answered_state(check, cites=cited, **state)
        return resolution_blockers(original, 4, check, frozenset(), proof_state=answered)

    # The subject and a change were in view, but not the linked repair evidence.
    assert blockers(_review_check(*_LONG_SESSION_REVIEW_GAPS)) == omitted
    with_repair = _review_check(*_LONG_SESSION_REVIEW_GAPS, included=(*_REPAIR_IN_VIEW, evd(7)))
    assert blockers(with_repair) == ()
    # Unreadable repair evidence cannot be shown to have been in view.
    assert blockers(with_repair, redacted_evidence=True) == omitted
    # An unreduced review saw the whole frontier and needs no record.
    unreduced = _review_check(*[gap for gap in _LONG_SESSION_REVIEW_GAPS if gap != _SCOPE_REDUCED])
    assert blockers(unreduced) == ()


def test_the_included_reference_record_is_bound_to_a_completed_reduced_review() -> None:
    from yoetz.domain.events import EventSchema, decode_payload, encode_payload
    from yoetz.protocol.errors import ProtocolValueError

    reduced = _review_check(*_LONG_SESSION_REVIEW_GAPS)
    assert reduced.semantic_included_refs == _in_view(*_REPAIR_IN_VIEW)
    encoded = encode_payload(reduced)
    assert decode_payload(EventSchema("check_recorded", "1.3.0"), encoded) == reduced
    unreduced = _review_check("content_unselected")
    assert "semantic_included_refs" not in encode_payload(unreduced)  # type: ignore[operator]
    for invalid in (
        {"semantic_included_refs": _in_view(*_REPAIR_IN_VIEW)[::-1]},  # not ASCII-sorted
        {"semantic_included_refs": ()},  # empty is not a record
        {"semantic_included_refs": ("free text",)},
        {"semantic_included_refs": _REPAIR_IN_VIEW, "semantic_conclusion": None},
    ):
        with pytest.raises(ProtocolValueError):
            replace(reduced, **invalid)  # type: ignore[arg-type]
    with pytest.raises(ProtocolValueError):
        replace(unreduced, semantic_included_refs=_in_view(*_REPAIR_IN_VIEW))


def test_an_unrecorded_sent_packet_is_disclosed_and_blocks_only_semantic_proof() -> None:
    """``semantic_included_refs_not_recorded`` limits the reduced review, never the local case."""

    from yoetz.domain.events import SEMANTIC_INCLUDED_REFS_NOT_RECORDED_GAP

    later = _review_check(
        *_LONG_SESSION_REVIEW_GAPS, SEMANTIC_INCLUDED_REFS_NOT_RECORDED_GAP, included=None
    )
    assert _blockers(_finding(), later) == ()
    stamped = _semantic_finding(
        "content_unselected", "host_outcome_unavailable", _SCOPE_REDUCED, "unpaired_event"
    )
    assert _blockers(stamped, later) == (
        "finding_material_outside_reduced_review_scope",
        "coverage:" + SEMANTIC_INCLUDED_REFS_NOT_RECORDED_GAP,
        "coverage:" + _SCOPE_REDUCED,
    )
