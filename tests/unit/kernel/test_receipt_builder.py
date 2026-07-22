"""Canonical receipt builder context, conclusion, section, and profile tests."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from yoetz.domain.events import (
    CheckMode,
    CheckRecordedPayload,
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
    PolicyVersionEntry,
    ReceiptConclusion,
    ReceiptRedactionCategory,
    ReceiptRedactionReason,
    ReceiptSectionKey,
    ReceiptVersionSlice,
    SchemaVersionEntry,
    receipt_document_to_json,
)
from yoetz.domain.values import (
    FindingId,
    Frontier,
    event_id,
    finding_id,
    freeze_json,
    obligation_id,
    receipt_id,
    session_id,
    task_id,
    timestamp_from_string,
)
from yoetz.kernel.deterministic_checks import CaseAvailabilityFacts, CaseGap
from yoetz.kernel.projections import (
    LatestTestedState,
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
) -> object:
    findings: dict[object, object] = {}
    if finding is not None:
        findings[finding.finding_id] = ProjectionRecord(
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
    return replace(
        empty_projection_state(),
        frontier=2,
        head_digest=_HEAD,
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
) -> ReceiptBuildContext:
    actual_coverage = _coverage() if coverage is None else coverage
    projection = _projection(
        finding=finding,
        response=response,
        check=check,
        coverage_gaps=(),
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
