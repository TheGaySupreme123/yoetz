"""Deterministic case construction, rendering, and dispatch conformance."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from builders.replay import replay_records
from yoetz.domain.events import AcceptedEvent
from yoetz.domain.findings import FindingKind, FindingOrigin
from yoetz.domain.values import claim_id, object_id, obligation_id
from yoetz.kernel.deterministic_checks import (
    DETERMINISTIC_FINDING_TEMPLATES,
    CaseAvailabilityFacts,
    PolicyPack,
    UnavailableCapturedObject,
    build_deterministic_case,
    finding_basis_to_status_json,
    render_deterministic_finding_text,
    run_deterministic_policies,
)
from yoetz.kernel.policies.research_evidence import RESEARCH_EVIDENCE_POLICY_PACK
from yoetz.kernel.policies.work_integrity import WORK_INTEGRITY_POLICY_PACK
from yoetz.kernel.projections import empty_projection_state
from yoetz.kernel.reducers import replay


def test_genesis_case_is_exact_and_immutable() -> None:
    case = build_deterministic_case(
        empty_projection_state(),
        (),
        CaseAvailabilityFacts(),
    )
    assert case.frontier.sequence == 0
    assert case.frontier.head_digest == "genesis"
    assert case.allowed_ids == frozenset()
    assert case.coverage_by_ref == MappingProxyType({})
    assert case.gaps == ()
    with pytest.raises(TypeError):
        case.coverage_by_ref["evt_00000000-0000-4000-8000-000000000001"] = None  # type: ignore[index, assignment]


def test_case_requires_the_exact_projection_prefix_head_pair() -> None:
    records = replay_records("all-event-families")
    projection = replay(records[:-1])
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        build_deterministic_case(projection, records, CaseAvailabilityFacts())


def test_replay_redaction_gaps_keep_typed_roots_and_caps() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    gaps = {(gap.code, gap.subject_refs) for gap in case.gaps}
    assert gaps == {
        (
            "redacted_event",
            ("evt_20000002-0000-4000-8000-000000000008",),
        ),
        (
            "redacted_object",
            ("evt_20000002-0000-4000-8000-00000000000e",),
        ),
    }
    evidence = case.coverage_by_ref[next(iter(case.projection.evidence))]
    assert evidence.artifact_observation.value == "published_only"
    assert evidence.evidence_immutability.value == "metadata_only"
    assert evidence.ledger_freshness.value == "redacted_gap"
    assert evidence.known_gaps == ("redacted_event", "redacted_object")


def test_event_payload_unavailability_is_explicit_and_exact() -> None:
    records = replay_records("all-event-families")[:2]
    obligation = records[1]
    assert type(obligation) is AcceptedEvent
    unavailable_record = replace(obligation, payload=None)
    unavailable_prefix = (records[0], unavailable_record)
    projection = replay(unavailable_prefix)
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        build_deterministic_case(projection, unavailable_prefix, CaseAvailabilityFacts())

    facts = CaseAvailabilityFacts((unavailable_record.event_id,), ())
    case = build_deterministic_case(projection, unavailable_prefix, facts)
    assert case.gaps[-1].code == "event_payload_unavailable"
    obligation_id = next(iter(projection.obligations))
    coverage = case.coverage_by_ref[obligation_id]
    assert coverage.known_gaps == ("event_payload_unavailable",)
    assert coverage.artifact_observation.value == "published_only"
    assert coverage.ledger_freshness.value == "redacted_gap"


def test_captured_object_unavailability_requires_current_exact_association() -> None:
    records = replay_records("all-event-families")[:8]
    projection = replay(records)
    evidence = next(iter(projection.evidence.values()))
    assert evidence.payload is not None
    captured = evidence.payload.captured_object_id
    assert captured is not None
    facts = CaseAvailabilityFacts(
        (),
        (UnavailableCapturedObject(evidence.source_event_id, captured),),
    )
    case = build_deterministic_case(projection, records, facts)
    evidence_id = evidence.payload.evidence_id
    assert case.coverage_by_ref[evidence_id].known_gaps == ("captured_object_unavailable",)
    bad = CaseAvailabilityFacts(
        (),
        (
            UnavailableCapturedObject(
                evidence.source_event_id,
                object_id("obj_00000000-0000-4000-8000-000000000999"),
            ),
        ),
    )
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        build_deterministic_case(projection, records, bad)


def test_templates_are_complete_exact_and_id_only() -> None:
    assert frozenset(DETERMINISTIC_FINDING_TEMPLATES) == frozenset(FindingKind)
    assert len(DETERMINISTIC_FINDING_TEMPLATES) == 14
    refs = (
        claim_id("clm_00000000-0000-4000-8000-000000000001"),
        obligation_id("obl_00000000-0000-4000-8000-000000000001"),
    )
    assert render_deterministic_finding_text(
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        refs,
    ) == (
        "A completion claim covers an obligation that remains open.",
        "Subjects: clm_00000000-0000-4000-8000-000000000001, "
        "obl_00000000-0000-4000-8000-000000000001. Main agent: "
        "Resolve the obligation or revise the completion claim.",
    )


def test_dispatch_is_assessments_only_ordered_and_origin_deterministic() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    work = run_deterministic_policies(case, WORK_INTEGRITY_POLICY_PACK)
    assert len(work.assessments) == 1
    assessment = work.assessments[0]
    assert assessment.candidate.kind is FindingKind.LEDGER_STALE_OR_INCOMPLETE
    assert assessment.candidate.origin is FindingOrigin.DETERMINISTIC
    assert assessment.candidate.provenance is None
    assert not hasattr(work, "outcome")
    assert (
        run_deterministic_policies(
            case,
            RESEARCH_EVIDENCE_POLICY_PACK,
        ).assessments
        == ()
    )


def test_status_basis_projection_is_controlled_and_exact() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    assessment = run_deterministic_policies(
        case,
        WORK_INTEGRITY_POLICY_PACK,
    ).assessments[0]
    projected = finding_basis_to_status_json(assessment)
    assert projected["rule_id"] == "ledger_stale_or_incomplete"
    assert projected["observed_fact_codes"] == ["redaction_gap_present"]
    assert projected["frozen_source_availability"] == "redacted"
    assert projected["subject_state_relation"] == "unknown"
    assert projected["evidence_refs"] == []
    assert "supporting_refs" not in projected


def test_unknown_or_tampered_pack_is_rejected() -> None:
    case = build_deterministic_case(
        empty_projection_state(),
        (),
        CaseAvailabilityFacts(),
    )
    with pytest.raises(ValueError, match="policy_wiring_invalid"):
        run_deterministic_policies(case, PolicyPack("work-integrity", "0.1.1"))
