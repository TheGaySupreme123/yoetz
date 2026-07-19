"""Property checks for receipt frontier, honesty, detail, and redaction invariants."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from yoetz.domain.events import (
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    encode_payload,
)
from yoetz.domain.receipts import (
    PolicyVersionEntry,
    ReceiptConclusion,
    ReceiptDocument,
    ReceiptVersionSlice,
    SchemaVersionEntry,
    receipt_document_to_json,
    receipt_weakest_coverage,
    render_receipt_compact,
)
from yoetz.domain.values import (
    Frontier,
    event_id,
    freeze_json,
    obligation_id,
    receipt_id,
    session_id,
    task_id,
    timestamp_from_string,
)
from yoetz.kernel.deterministic_checks import CaseAvailabilityFacts, CaseGap
from yoetz.kernel.projections import (
    ObligationProjectionRecord,
    PlanProjectionRecord,
    ProjectionState,
    empty_projection_state,
)
from yoetz.kernel.receipt_builder import ReceiptBuildContext, build_receipt
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
from yoetz.protocol.models import ReceiptInclude, ReceiptRedactionProfile

_HEAD = "sha256:" + "4" * 64
_FRONTIER = Frontier(2, _HEAD)
_OBLIGATION_ID = obligation_id("obl_00000000-0000-4000-8000-000000000001")


def _coverage() -> Coverage:
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=LedgerFreshness.PARTIAL,
        check_types=(CheckType.NONE,),
        known_gaps=("check_not_recorded",),
    )


def _projection(summary: str) -> ProjectionState:
    plan = PlanPublishedPayload(1, "Property plan", (_OBLIGATION_ID,))
    obligation = ObligationPublishedPayload(
        obligation_id=_OBLIGATION_ID,
        description=summary,
        evidence_expectation="A recorded result",
        status=ObligationStatus.OPEN,
    )
    return replace(
        empty_projection_state(),
        frontier=2,
        head_digest=_HEAD,
        plans={
            1: PlanProjectionRecord(
                payload=plan,
                payload_digest=canonical_digest(encode_payload(plan)),
                redacted=False,
                source_event_id=event_id("evt_00000000-0000-4000-8000-000000000001"),
                source_frontier=1,
            )
        },
        obligations={
            _OBLIGATION_ID: ObligationProjectionRecord(
                payload=obligation,
                payload_digest=canonical_digest(encode_payload(obligation)),
                redacted=False,
                source_event_id=event_id("evt_00000000-0000-4000-8000-000000000002"),
                source_frontier=2,
            )
        },
        freshness=LedgerFreshness.CURRENT,
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


def _context(summary: str = "A protected obligation summary") -> ReceiptBuildContext:
    return ReceiptBuildContext(
        projection=_projection(summary),
        subject_frontier=_FRONTIER,
        availability=CaseAvailabilityFacts(),
        coverage=_coverage(),
        gaps=(CaseGap("check_not_recorded", "check_not_recorded", ()),),
        finding_states=(),
        applicable_check=None,
    )


def _build(
    context: ReceiptBuildContext,
    profile: ReceiptRedactionProfile,
    include: ReceiptInclude,
) -> ReceiptDocument:
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


@given(st.sampled_from(tuple(ReceiptInclude)))
def test_receipt_frontier_matches_frozen_state(include: ReceiptInclude) -> None:
    document = _build(_context(), ReceiptRedactionProfile.FULL_LOCAL, include)
    assert document.subject_frontier == _FRONTIER
    mismatch = replace(_context().projection, head_digest="sha256:" + "5" * 64)
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        ReceiptBuildContext(
            mismatch,
            _FRONTIER,
            CaseAvailabilityFacts(),
            _coverage(),
            (CaseGap("check_not_recorded", "check_not_recorded", ()),),
            (),
            None,
        )


@given(st.sampled_from(tuple(ReceiptRedactionProfile)), st.sampled_from(tuple(ReceiptInclude)))
def test_conclusion_never_outruns_findings(
    profile: ReceiptRedactionProfile, include: ReceiptInclude
) -> None:
    document = _build(_context(), profile, include)
    assert document.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert document.coverage.known_gaps == ("check_not_recorded",)


@given(st.sampled_from(tuple(ReceiptRedactionProfile)), st.sampled_from(tuple(ReceiptInclude)))
def test_weakest_coverage_bounds_the_render(
    profile: ReceiptRedactionProfile, include: ReceiptInclude
) -> None:
    document = _build(_context(), profile, include)
    assert receipt_weakest_coverage(document) == document.coverage
    assert "coverage is insufficient" in render_receipt_compact(document).lower()


@given(st.sampled_from(tuple(ReceiptInclude)))
def test_redaction_profiles_change_canonical_output_without_strengthening_truth(
    include: ReceiptInclude,
) -> None:
    context = _context()
    full = _build(context, ReceiptRedactionProfile.FULL_LOCAL, include)
    exported = _build(context, ReceiptRedactionProfile.DEFAULT_LOCAL_EXPORT, include)
    shared = _build(context, ReceiptRedactionProfile.REDACTED_SHARE, include)
    assert full.obligations[0].summary is not None
    assert exported.obligations[0].summary is None
    assert shared.obligations[0].summary is None
    assert full.conclusion == exported.conclusion == shared.conclusion
    assert full.coverage == exported.coverage == shared.coverage
    full_digest = canonical_digest(freeze_json(receipt_document_to_json(full)))
    exported_digest = canonical_digest(freeze_json(receipt_document_to_json(exported)))
    assert full_digest != exported_digest


def test_explicit_context_is_required() -> None:
    context = _context()
    with pytest.raises(ValueError, match="receipt_build_context_invalid"):
        build_receipt(
            cast(ReceiptBuildContext, object()),
            receipt_id("rcp_00000000-0000-4000-8000-000000000001"),
            task_id("tsk_00000000-0000-4000-8000-000000000001"),
            session_id("ses_00000000-0000-4000-8000-000000000001"),
            timestamp_from_string("2026-07-19T00:00:00.000Z"),
            _versions(),
            ReceiptRedactionProfile.FULL_LOCAL,
            ReceiptInclude.FULL,
        )
    assert context.availability == CaseAvailabilityFacts()
