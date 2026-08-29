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

from builders.policy_cases import clm, evt, finding_record, fnd, obl
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
        policy_id="work-integrity",
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


def _resolves(finding: Finding, check: CheckRecordedPayload, *, recorded_at: int = 4) -> bool:
    return qualifying_check_resolves(finding, recorded_at, check, frozenset())


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
    """A deterministic finding is proven absent by the deterministic pack, not by the reviewer."""

    assert _resolves(_finding(), _check(coverage=_coverage(gaps=(gap,)))) is True


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


def test_a_semantic_finding_needs_a_completed_semantic_review() -> None:
    finding = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    assert _resolves(finding, _check()) is False, "deterministic-only proof is the wrong class"
    assert _resolves(finding, _check(semantic=_SEMANTIC_OK)) is True


@pytest.mark.parametrize(
    "gap",
    ("semantic_review_context_withheld", "semantic_challenges_rejected"),
)
def test_a_weakened_semantic_review_cannot_resolve_a_semantic_finding(gap: str) -> None:
    finding = _finding(origin=FindingOrigin.SEMANTIC_MODEL_DERIVED)
    coverage = _coverage(gaps=(gap,), semantic=True)
    assert _resolves(finding, _check(semantic=_SEMANTIC_OK, coverage=coverage)) is False
    # The same weakened review still proves a deterministic issue absent.
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
