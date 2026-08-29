"""Canonical receipt builder context, conclusion, section, and profile tests."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from yoetz.domain.events import (
    CheckMode,
    CheckRecordedPayload,
    NoObligationsReason,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    PolicyVersion,
    ResponseRecordedPayload,
    encode_payload,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CheckVerdict,
    Finding,
    FindingKind,
    FindingOrigin,
    ResponseDisposition,
)
from yoetz.domain.receipts import (
    CHECK_CURRENT_AS_OF_EARLIER_FRONTIER_GAP,
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
    PolicyVersionEntry,
    ReceiptConclusion,
    ReceiptObligationStatus,
    ReceiptRedactionCategory,
    ReceiptRedactionReason,
    ReceiptSectionKey,
    ReceiptVersionSlice,
    SchemaVersionEntry,
    receipt_document_to_json,
    render_receipt_compact,
    resolved_finding_ids_for_render,
    unresolved_findings_for_render,
)
from yoetz.domain.values import (
    FindingId,
    Frontier,
    event_id,
    finding_id,
    freeze_json,
    obligation_id,
    receipt_id,
    result_id,
    session_id,
    task_id,
    timestamp_from_string,
)
from yoetz.kernel.deterministic_checks import CaseAvailabilityFacts, CaseGap
from yoetz.kernel.projections import (
    FindingProjectionRecord,
    LatestTestedState,
    ObligationProjectionRecord,
    PlanProjectionRecord,
    ProjectionRecord,
    ProjectionState,
    empty_projection_state,
)
from yoetz.kernel.receipt_builder import (
    ReceiptBuildContext,
    ReceiptFindingState,
    build_receipt,
)
from yoetz.protocol.canonical import canonical_digest
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
    ReceiptInclude,
    ReceiptRedactionProfile,
    SemanticReason,
    SemanticStatus,
)

_DIGEST = "sha256:" + "1" * 64
_HEAD = "sha256:" + "2" * 64
_FINDING_ID = finding_id("fnd_00000000-0000-4000-8000-000000000001")
_SOURCE_EVENT_ID = event_id("evt_00000000-0000-4000-8000-000000000001")
_CHECK_EVENT_ID = event_id("evt_00000000-0000-4000-8000-000000000002")
_FRONTIER = Frontier(2, _HEAD)


def _coverage(*, gaps: tuple[str, ...] = ()) -> Coverage:
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.ARTIFACT_VERIFIED,
        evidence_immutability=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        ledger_freshness=LedgerFreshness.CURRENT if not gaps else LedgerFreshness.PARTIAL,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=gaps,
    )


def _finding() -> Finding:
    kind = FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS
    return Finding(
        finding_id=_FINDING_ID,
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="An obligation remains open.",
        detail="Resolve the recorded obligation.",
        subject_refs=(obligation_id("obl_00000000-0000-4000-8000-000000000001"),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(1, _DIGEST),
        coverage=_coverage(),
    )


def _check(
    verdict: CheckVerdict,
    coverage: Coverage,
    *,
    returned: tuple[FindingId, ...] = (),
    suppressed: int = 0,
) -> CheckRecordedPayload:
    return CheckRecordedPayload(
        mode=CheckMode.DETERMINISTIC_ONLY,
        policies=(PolicyVersion("work-integrity", "0.1.0"),),
        scope=CheckScopeModel(claim_ids=(), obligation_ids=()),
        policy_executions=(
            CheckPolicyExecutionModel(
                policy_id="work-integrity",
                policy_version="0.1.0",
                outcome="run",
                reason="completed",
            ),
        ),
        subject_frontier=_FRONTIER,
        verdict=verdict,
        returned_finding_ids=returned,
        suppressed_count=suppressed,
        coverage=coverage,
        semantic_status=SemanticStatus.NOT_REQUESTED,
        semantic_reason=SemanticReason.DETERMINISTIC_MODE,
        engine_version="0.1.0",
        projection_version="yoetz/0.1.0",
    )


def _projection(
    *,
    finding: Finding | None = None,
    response: ResponseRecordedPayload | None = None,
    check: CheckRecordedPayload | None = None,
    coverage_gaps: tuple[str, ...] = (),
    plan: PlanPublishedPayload | None = None,
    obligation: ObligationPublishedPayload | None = None,
    plan_redacted: bool = False,
    obligation_redacted: bool = False,
) -> object:
    findings: dict[object, object] = {}
    if finding is not None:
        findings[finding.finding_id] = FindingProjectionRecord(
            payload=finding,
            payload_digest=canonical_digest(encode_payload(finding)),
            redacted=False,
            source_event_id=_SOURCE_EVENT_ID,
            source_frontier=1,
        )
    responses: dict[object, object] = {}
    if response is not None:
        responses[response.finding_id] = ProjectionRecord(
            payload=response,
            payload_digest=canonical_digest(encode_payload(response)),
            redacted=False,
            source_event_id=event_id("evt_00000000-0000-4000-8000-000000000003"),
            source_frontier=2,
        )
    latest = None
    if check is not None:
        latest = LatestTestedState(
            source_check_event_id=_CHECK_EVENT_ID,
            subject_frontier=check.subject_frontier,
            verdict=check.verdict,
            returned_finding_ids=check.returned_finding_ids,
            suppressed_count=check.suppressed_count,
            coverage=check.coverage,
        )
    plans = {}
    if plan is not None:
        plans[plan.plan_version] = PlanProjectionRecord(
            payload=None if plan_redacted else plan,
            payload_digest=canonical_digest(encode_payload(plan)),
            redacted=plan_redacted,
            source_event_id=event_id("evt_00000000-0000-4000-8000-000000000010"),
            source_frontier=1,
        )
    obligations = {}
    if obligation is not None:
        obligations[obligation.obligation_id] = ObligationProjectionRecord(
            payload=None if obligation_redacted else obligation,
            payload_digest=canonical_digest(encode_payload(obligation)),
            redacted=obligation_redacted,
            source_event_id=event_id("evt_00000000-0000-4000-8000-000000000011"),
            source_frontier=1,
        )
    return replace(
        empty_projection_state(),
        frontier=2,
        head_digest=_HEAD,
        plans=plans,
        obligations=obligations,
        findings=findings,
        responses=responses,
        latest_tested_state=latest,
        freshness=LedgerFreshness.CURRENT,
        coverage_gaps=coverage_gaps,
    )


def _versions() -> ReceiptVersionSlice:
    return ReceiptVersionSlice(
        package_name="yoetz",
        package_version="0.1.0",
        protocol_version="0.1",
        engine_version="0.1.0",
        projection_version="yoetz/0.1.0",
        object_format_version="yoetz-object/1",
        catalog_schema_version="1",
        bundle_schema_version="1",
        policy_versions=(
            PolicyVersionEntry("research-evidence", "0.1.0"),
            PolicyVersionEntry("work-integrity", "0.1.0"),
        ),
        schema_versions=(SchemaVersionEntry("receipts/receipt-document", "1.0.0"),),
        resource_manifest_digest="sha256:" + "9" * 64,
    )


def _context(
    *,
    finding: Finding | None = None,
    resolved: bool = False,
    response: ResponseRecordedPayload | None = None,
    check: CheckRecordedPayload | None = None,
    coverage: Coverage | None = None,
    gaps: tuple[CaseGap, ...] = (),
    plan: PlanPublishedPayload | None = None,
    obligation: ObligationPublishedPayload | None = None,
    plan_redacted: bool = False,
    obligation_redacted: bool = False,
) -> ReceiptBuildContext:
    actual_coverage = _coverage() if coverage is None else coverage
    projection = _projection(
        finding=finding,
        response=response,
        check=check,
        coverage_gaps=(),
        plan=plan,
        obligation=obligation,
        plan_redacted=plan_redacted,
        obligation_redacted=obligation_redacted,
    )
    return ReceiptBuildContext(
        projection=cast("ProjectionState", projection),
        subject_frontier=_FRONTIER,
        availability=CaseAvailabilityFacts(),
        coverage=actual_coverage,
        gaps=gaps,
        finding_states=(
            () if finding is None else (ReceiptFindingState(finding.finding_id, resolved=resolved),)
        ),
        applicable_check=check,
    )


def _build(
    context: ReceiptBuildContext,
    *,
    profile: ReceiptRedactionProfile = ReceiptRedactionProfile.FULL_LOCAL,
    include: ReceiptInclude = ReceiptInclude.FULL,
):
    return build_receipt(
        context,
        receipt_id("rcp_00000000-0000-4000-8000-000000000001"),
        task_id("tsk_00000000-0000-4000-8000-000000000001"),
        session_id("ses_00000000-0000-4000-8000-000000000001"),
        timestamp_from_string("2026-07-19T00:00:00.000Z"),
        _versions(),
        profile,
        include,
    )


def test_frontier_mismatch_is_rejected() -> None:
    coverage = _coverage()
    check = _check(CheckVerdict.NO_ISSUE_DETECTED, coverage)
    projection = cast("ProjectionState", _projection(check=check))
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        ReceiptBuildContext(
            projection=projection,
            subject_frontier=Frontier(1, _DIGEST),
            availability=CaseAvailabilityFacts(),
            coverage=coverage,
            gaps=(),
            finding_states=(),
            applicable_check=check,
        )


def test_conclusion_selection_matches_state_strength() -> None:
    finding = _finding()
    action_check = _check(CheckVerdict.ACTION_REQUIRED, _coverage(), returned=(_FINDING_ID,))
    assert _build(_context(finding=finding, check=action_check)).conclusion is (
        ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN
    )

    clear_check = _check(CheckVerdict.NO_ISSUE_DETECTED, _coverage())
    assert _build(_context(check=clear_check)).conclusion is (
        ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
    )

    gap = CaseGap("check_not_recorded", "check_not_recorded", ())
    weak = _coverage(gaps=("check_not_recorded",))
    assert _build(_context(coverage=weak, gaps=(gap,))).conclusion is (
        ReceiptConclusion.INSUFFICIENT_COVERAGE
    )


def test_digest_provenance_limitation_is_retained_in_receipt() -> None:
    code = "evidence_content_digest_only"
    gap = CaseGap(f"{code}:{_SOURCE_EVENT_ID}", code, (_SOURCE_EVENT_ID,))
    coverage = _coverage(gaps=(code,))
    check = _check(CheckVerdict.NO_ISSUE_DETECTED, coverage)
    receipt = _build(_context(coverage=coverage, gaps=(gap,), check=check))
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert receipt.coverage.known_gaps == (code,)
    assert tuple(item.code for item in receipt.gaps) == (code,)


def test_suppressed_findings_block_clear_conclusion_until_fresh_check() -> None:
    capped = _check(CheckVerdict.NO_ISSUE_DETECTED, _coverage(), suppressed=2)
    receipt = _build(_context(check=capped))
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert receipt.suppressed_finding_count == 2
    fresh = _build(_context(check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage())))
    assert fresh.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
    assert fresh.suppressed_finding_count == 0


def test_section_order_is_canonical() -> None:
    context = _context(check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()))
    expected = {
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
    for include, keys in expected.items():
        assert tuple(section.key for section in _build(context, include=include).sections) == keys


def test_redaction_profiles_change_canonical_bytes_without_changing_truth() -> None:
    finding = _finding()
    response = ResponseRecordedPayload(
        finding_id=finding.finding_id,
        finding_frontier=finding.subject_frontier,
        disposition=ResponseDisposition.REJECTED,
        reason="A protected rejection reason.",
    )
    check = _check(CheckVerdict.ACTION_REQUIRED, _coverage(), returned=(finding.finding_id,))
    context = _context(finding=finding, response=response, check=check)
    full = _build(context)
    shared = _build(context, profile=ReceiptRedactionProfile.REDACTED_SHARE)
    assert full.conclusion is shared.conclusion
    assert full.subject_frontier == shared.subject_frontier
    assert full.coverage == shared.coverage
    assert full.responses and not shared.responses
    assert shared.redactions == (
        next(
            row
            for row in shared.redactions
            if row.category is ReceiptRedactionCategory.FINDING_DETAIL
            and row.reason is ReceiptRedactionReason.POLICY_REDACTED
        ),
    )
    assert canonical_digest(freeze_json(receipt_document_to_json(full))) != canonical_digest(
        freeze_json(receipt_document_to_json(shared))
    )


def test_profile_by_include_matrix_is_exhaustive() -> None:
    context = _context(check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()))
    documents = {
        (profile, include): _build(context, profile=profile, include=include)
        for profile in ReceiptRedactionProfile
        for include in ReceiptInclude
    }
    assert len(documents) == 9
    for profile in ReceiptRedactionProfile:
        truth = [documents[(profile, include)] for include in ReceiptInclude]
        assert len({document.findings for document in truth}) == 1
        assert len({document.coverage for document in truth}) == 1
        assert len({tuple(gap.code for gap in document.gaps) for document in truth}) == 1


def test_context_requires_explicit_availability_and_applicable_check() -> None:
    coverage = _coverage()
    projection = cast("ProjectionState", _projection())
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        ReceiptBuildContext(
            projection=projection,
            subject_frontier=_FRONTIER,
            availability=CaseAvailabilityFacts(),
            coverage=coverage,
            gaps=(),
            finding_states=(),
            applicable_check=None,
        )


def test_applicable_check_at_earlier_subject_frontier_builds() -> None:
    """Applicability follows the material state, not frontier equality: a check recorded at an
    earlier subject frontier still applies to this context. The builder trusts the application's
    applicability decision and never re-derives it."""

    coverage = _coverage()
    check = replace(
        _check(CheckVerdict.NO_ISSUE_DETECTED, coverage),
        subject_frontier=Frontier(1, _DIGEST),
    )
    receipt = _build(_context(check=check))
    assert receipt.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
    assert receipt.suppressed_finding_count == 0


def test_check_current_as_of_earlier_frontier_names_the_tested_frontier() -> None:
    """The attributed-check gap must read as a qualification, never as a clean re-check: it names
    the frontier the verdict is current as of and still blocks the strong conclusion."""

    code = CHECK_CURRENT_AS_OF_EARLIER_FRONTIER_GAP
    coverage = _coverage(gaps=(code,))
    check = replace(
        _check(CheckVerdict.NO_ISSUE_DETECTED, coverage),
        subject_frontier=Frontier(1, _DIGEST),
    )
    receipt = _build(_context(coverage=coverage, gaps=(CaseGap(code, code, ()),), check=check))
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    limitations = next(
        section.body
        for section in receipt.sections
        if section.key is ReceiptSectionKey.LIMITATIONS_AND_COVERAGE
    )
    assert "A check is recorded at subject frontier 1 and still contributes here" in limitations
    assert "only responses to the findings it returned were published after it" in limitations
    assert "Its verdict is current as of subject frontier 1, not frontier 2." in limitations


def test_check_current_as_of_earlier_frontier_without_a_check_is_rejected() -> None:
    """The gap qualifies a check that contributes; it can never stand in for an absent one."""

    code = CHECK_CURRENT_AS_OF_EARLIER_FRONTIER_GAP
    coverage = _coverage(gaps=(code, "check_not_recorded"))
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        ReceiptBuildContext(
            projection=cast("ProjectionState", _projection()),
            subject_frontier=_FRONTIER,
            availability=CaseAvailabilityFacts(),
            coverage=coverage,
            gaps=(
                CaseGap(code, code, ()),
                CaseGap("check_not_recorded", "check_not_recorded", ()),
            ),
            finding_states=(),
            applicable_check=None,
        )


def test_applicable_check_ahead_of_context_is_rejected() -> None:
    coverage = _coverage()
    check = _check(CheckVerdict.NO_ISSUE_DETECTED, coverage)
    projection = cast("ProjectionState", _projection(check=check))
    ahead = replace(check, subject_frontier=Frontier(3, "sha256:" + "3" * 64))
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        ReceiptBuildContext(
            projection=projection,
            subject_frontier=_FRONTIER,
            availability=CaseAvailabilityFacts(),
            coverage=coverage,
            gaps=(),
            finding_states=(),
            applicable_check=ahead,
        )


def test_receipt_version_slice_is_exact() -> None:
    context = _context(check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()))
    receipt = _build(context)
    assert receipt.versions is not None
    assert receipt.versions.resource_manifest_digest == "sha256:" + "9" * 64
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        build_receipt(
            context,
            receipt.receipt_id,
            receipt.task_id,
            receipt.session_id,
            receipt.generated_at,
            cast(ReceiptVersionSlice, {"engine_version": "0.1.0"}),
            ReceiptRedactionProfile.FULL_LOCAL,
            ReceiptInclude.FULL,
        )


def test_builder_never_adds_new_findings_or_evidence() -> None:
    finding = _finding()
    check = _check(CheckVerdict.ACTION_REQUIRED, _coverage(), returned=(finding.finding_id,))
    receipt = _build(_context(finding=finding, check=check))
    assert receipt.findings == (finding,)
    assert receipt.evidence_refs == ()


def test_semantic_review_not_configured_limitations_state_not_run() -> None:
    """Requirement: receipt limitations disclose that semantic relevance review was not run."""

    from yoetz.domain.receipts import SEMANTIC_REVIEW_NOT_CONFIGURED_GAP, render_receipt_compact

    gap = CaseGap(
        f"semantic_outcome:{SEMANTIC_REVIEW_NOT_CONFIGURED_GAP}",
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
        (),
    )
    coverage = _coverage(gaps=(SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,))
    check = _check(
        CheckVerdict.INSUFFICIENT_COVERAGE,
        coverage,
    )
    # Override semantic outcome on the applicable check for documentation; gaps drive disclosure.
    check = replace(
        check,
        semantic_status=SemanticStatus.NOT_CONFIGURED,
        semantic_reason=SemanticReason.PROVIDER_NOT_CONFIGURED,
    )
    receipt = _build(_context(coverage=coverage, gaps=(gap,), check=check))
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert SEMANTIC_REVIEW_NOT_CONFIGURED_GAP in {item.code for item in receipt.gaps}
    limitations = next(
        section
        for section in receipt.sections
        if section.key is ReceiptSectionKey.LIMITATIONS_AND_COVERAGE
    )
    assert limitations.body.startswith("Semantic relevance review was not run.")
    assert SEMANTIC_REVIEW_NOT_CONFIGURED_GAP in limitations.items
    rendered = render_receipt_compact(receipt)
    assert "semantic relevance review was not run" in rendered
    assert "optional semantic review was blocked" not in rendered


@pytest.mark.parametrize("profile", tuple(ReceiptRedactionProfile))
@pytest.mark.parametrize("reason", tuple(NoObligationsReason))
def test_empty_completion_scope_wording_is_bounded_and_survives_profiles(
    profile: ReceiptRedactionProfile,
    reason: NoObligationsReason,
) -> None:
    plan = PlanPublishedPayload(1, "Atomic task", (), ())
    undeclared_gap = CaseGap(
        "completion_scope_undeclared:plan",
        COMPLETION_SCOPE_UNDECLARED_GAP,
        (),
    )
    undeclared_coverage = _coverage(gaps=(COMPLETION_SCOPE_UNDECLARED_GAP,))
    undeclared = _build(
        _context(
            check=_check(CheckVerdict.INSUFFICIENT_COVERAGE, undeclared_coverage),
            coverage=undeclared_coverage,
            gaps=(undeclared_gap,),
            plan=plan,
        ),
        profile=profile,
    )
    assert undeclared.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    outstanding = next(
        section
        for section in undeclared.sections
        if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert outstanding.body == "Completion scope was never declared."
    assert "completion scope was never declared" in render_receipt_compact(undeclared)

    typed_plan = replace(
        plan,
        no_obligations_reason=reason,
    )
    declared_gap = CaseGap(
        "completion_scope_declared_none:plan",
        COMPLETION_SCOPE_DECLARED_NONE_GAP,
        (),
    )
    declared_coverage = _coverage(gaps=(COMPLETION_SCOPE_DECLARED_NONE_GAP,))
    declared = _build(
        _context(
            check=_check(CheckVerdict.INSUFFICIENT_COVERAGE, declared_coverage),
            coverage=declared_coverage,
            gaps=(declared_gap,),
            plan=typed_plan,
        ),
        profile=profile,
    )
    assert declared.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    outstanding = next(
        section
        for section in declared.sections
        if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert outstanding.body == f"The plan declared none, reason: {reason.value}."
    assert declared.gaps[0].detail == reason.value
    assert f"the plan declared none, reason: {reason.value}" in render_receipt_compact(declared)
    assert "Atomic task" not in render_receipt_compact(declared)


@pytest.mark.parametrize(
    "gap_code",
    (COMPLETION_SCOPE_UNDECLARED_GAP, COMPLETION_SCOPE_DECLARED_NONE_GAP),
)
def test_empty_completion_scope_gap_dominates_unresolved_actionable_finding(
    gap_code: str,
) -> None:
    finding = _finding()
    gap = CaseGap(f"{gap_code}:plan", gap_code, ())
    coverage = _coverage(gaps=(gap_code,))
    reason = (
        NoObligationsReason.SINGLE_ATOMIC_CHANGE
        if gap_code == COMPLETION_SCOPE_DECLARED_NONE_GAP
        else None
    )
    plan = PlanPublishedPayload(1, "Atomic task", (), (), reason)
    receipt = _build(
        _context(
            finding=finding,
            check=_check(
                CheckVerdict.INSUFFICIENT_COVERAGE,
                coverage,
                returned=(finding.finding_id,),
            ),
            coverage=coverage,
            gaps=(gap,),
            plan=plan,
        )
    )

    assert receipt.findings == (finding,)
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE


def test_declared_resolved_scope_has_distinct_clean_wording() -> None:
    obligation = ObligationPublishedPayload(
        obligation_id("obl_00000000-0000-4000-8000-000000000020"),
        "Run focused verification",
        "Recorded successful result",
        ObligationStatus.RESOLVED,
        resolution_evidence_refs=(result_id("res_00000000-0000-4000-8000-000000000020"),),
    )
    plan = PlanPublishedPayload(1, "Verified task", (obligation.obligation_id,), ())
    receipt = _build(
        _context(
            check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()),
            plan=plan,
            obligation=obligation,
        )
    )
    outstanding = next(
        section for section in receipt.sections if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert receipt.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
    assert outstanding.body == "Declared obligations are all resolved."
    assert "declared obligations are all resolved" in render_receipt_compact(receipt)


def test_compact_resolved_wording_ignores_out_of_scope_open_finding_obligation() -> None:
    effective = ObligationPublishedPayload(
        obligation_id("obl_00000000-0000-4000-8000-000000000020"),
        "Run focused verification",
        "Recorded successful result",
        ObligationStatus.RESOLVED,
        resolution_evidence_refs=(result_id("res_00000000-0000-4000-8000-000000000020"),),
    )
    historical = ObligationPublishedPayload(
        obligation_id("obl_00000000-0000-4000-8000-000000000001"),
        "Historical finding subject",
        "Historical acceptance criterion",
        ObligationStatus.OPEN,
    )
    finding = _finding()
    plan = PlanPublishedPayload(1, "Verified task", (effective.obligation_id,), ())
    base = _context(
        finding=finding,
        resolved=True,
        check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()),
        plan=plan,
        obligation=effective,
    )
    projection = replace(
        base.projection,
        obligations={
            **base.projection.obligations,
            historical.obligation_id: ObligationProjectionRecord(
                payload=historical,
                payload_digest=canonical_digest(encode_payload(historical)),
                redacted=False,
                source_event_id=event_id("evt_00000000-0000-4000-8000-000000000012"),
                source_frontier=1,
            ),
        },
    )
    context = replace(base, projection=projection)

    receipt = _build(context)

    assert tuple(item.status for item in receipt.obligations) == (
        ReceiptObligationStatus.OPEN,
        ReceiptObligationStatus.RESOLVED,
    )
    outstanding = next(
        section for section in receipt.sections if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert outstanding.body == "Declared obligations are all resolved."
    assert "declared obligations are all resolved" in render_receipt_compact(receipt)


@pytest.mark.parametrize("state", ("missing", "redacted", "unreadable_plan"))
def test_unknown_effective_obligation_scope_never_renders_as_zero(state: str) -> None:
    obligation = ObligationPublishedPayload(
        obligation_id("obl_00000000-0000-4000-8000-000000000030"),
        "Inspect declared work",
        "Recorded result",
        ObligationStatus.OPEN,
    )
    plan = PlanPublishedPayload(1, "Declared scope", (obligation.obligation_id,), ())
    gap_code = "missing_ref" if state == "missing" else "redacted_event"
    gap = CaseGap(f"{gap_code}:scope", gap_code, ())
    coverage = _coverage(gaps=(gap_code,))
    receipt = _build(
        _context(
            check=_check(CheckVerdict.INSUFFICIENT_COVERAGE, coverage),
            coverage=coverage,
            gaps=(gap,),
            plan=plan,
            obligation=None if state == "missing" else obligation,
            plan_redacted=state == "unreadable_plan",
            obligation_redacted=state == "redacted",
        )
    )
    outstanding = next(
        section for section in receipt.sections if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert receipt.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert "unknown" in outstanding.body
    assert "No open obligations are recorded" not in outstanding.body
    assert "all resolved" not in outstanding.body


def test_no_plan_receipt_does_not_infer_zero_obligations() -> None:
    receipt = _build(_context(check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage())))
    outstanding = next(
        section for section in receipt.sections if section.key is ReceiptSectionKey.OUTSTANDING_WORK
    )
    assert outstanding.body == "No plan is recorded; completion scope is unknown."


def test_compact_renderer_never_echoes_unrecognized_scope_reason_detail() -> None:
    plan = PlanPublishedPayload(
        1,
        "Caller-controlled plan prose must not render",
        (),
        (),
        NoObligationsReason.NO_MATERIAL_CHANGE,
    )
    gap = CaseGap("completion_scope_declared_none:plan", COMPLETION_SCOPE_DECLARED_NONE_GAP, ())
    coverage = _coverage(gaps=(COMPLETION_SCOPE_DECLARED_NONE_GAP,))
    receipt = _build(
        _context(
            check=_check(CheckVerdict.INSUFFICIENT_COVERAGE, coverage),
            coverage=coverage,
            gaps=(gap,),
            plan=plan,
        )
    )
    tampered = replace(receipt, gaps=(replace(receipt.gaps[0], detail="CALLER SECRET"),))
    rendered = render_receipt_compact(tampered)
    assert "CALLER SECRET" not in rendered
    assert "closed reason is unavailable" in rendered


def test_resolved_history_is_named_apart_from_current_findings_and_gaps() -> None:
    """A resolved finding stays in the document as history, is listed by id in the summary
    section (the one every include level carries), and the fixed templates say so beside the
    current count so a reader can tell "was fixed" from "still open" and from coverage limits."""

    finding = _finding()
    receipt = _build(
        _context(
            finding=finding,
            resolved=True,
            check=_check(CheckVerdict.NO_ISSUE_DETECTED, _coverage()),
        ),
        include=ReceiptInclude.STANDARD,
    )
    assert receipt.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
    assert receipt.findings == (finding,)
    sections = {section.key: section for section in receipt.sections}
    summary = sections[ReceiptSectionKey.SUMMARY]
    assert summary.items == (finding.finding_id,)
    assert summary.body == (
        "No unresolved deterministic findings were recorded at frontier 2. One earlier finding "
        "was resolved by a later qualifying check and remains visible as history."
    )
    dispositions = sections[ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS]
    assert dispositions.items == ()
    assert dispositions.body == (
        "No findings remain open. One earlier finding was resolved by a later qualifying check "
        "and remains visible as history."
    )
    assert resolved_finding_ids_for_render(receipt) == frozenset({finding.finding_id})
    assert unresolved_findings_for_render(receipt) == ()
    compact = render_receipt_compact(receipt)
    assert "no unresolved deterministic findings were recorded" in compact
    assert "1 unresolved finding" not in compact


def test_resolved_history_beside_a_current_finding_counts_only_the_current_one() -> None:
    current = _finding()
    resolved = replace(
        current,
        finding_id=finding_id("fnd_00000000-0000-4000-8000-000000000002"),
        subject_refs=(obligation_id("obl_00000000-0000-4000-8000-000000000002"),),
    )
    base = _context(
        finding=current,
        check=_check(CheckVerdict.ACTION_REQUIRED, _coverage(), returned=(current.finding_id,)),
    )
    projection = replace(
        base.projection,
        findings={
            **base.projection.findings,
            resolved.finding_id: FindingProjectionRecord(
                payload=resolved,
                payload_digest=canonical_digest(encode_payload(resolved)),
                redacted=False,
                source_event_id=event_id("evt_00000000-0000-4000-8000-000000000013"),
                source_frontier=1,
                resolved_by_check_event_id=_CHECK_EVENT_ID,
            ),
        },
    )
    context = replace(
        base,
        projection=projection,
        finding_states=(
            ReceiptFindingState(current.finding_id, resolved=False),
            ReceiptFindingState(resolved.finding_id, resolved=True),
        ),
    )
    receipt = _build(context, include=ReceiptInclude.SUMMARY)
    assert receipt.conclusion is ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN
    assert len(receipt.findings) == 2
    summary = next(s for s in receipt.sections if s.key is ReceiptSectionKey.SUMMARY)
    assert summary.items == (resolved.finding_id,)
    assert summary.body.startswith("One actionable finding remain unresolved at frontier 2.")
    assert "One earlier finding was resolved by a later qualifying check" in summary.body
    assert unresolved_findings_for_render(receipt) == (current,)
    assert render_receipt_compact(receipt).endswith("1 unresolved finding remains.")
