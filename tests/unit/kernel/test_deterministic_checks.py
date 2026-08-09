"""Deterministic case construction, rendering, and dispatch conformance."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

import pytest

from builders.policy_cases import clm, evt, make_case, obl, plan_record, record
from builders.replay import replay_records
from yoetz.domain.events import (
    AcceptedEvent,
    ClaimKind,
    ClaimRecordedPayload,
    NoObligationsReason,
    PlanPublishedPayload,
)
from yoetz.domain.findings import FindingKind, FindingOrigin
from yoetz.domain.receipts import (
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
)
from yoetz.domain.values import claim_id, object_id, obligation_id
from yoetz.kernel import deterministic_checks
from yoetz.kernel.deterministic_checks import (
    DETERMINISTIC_FINDING_TEMPLATES,
    CaseAvailabilityFacts,
    PolicyPack,
    UnavailableCapturedObject,
    build_deterministic_case,
    deterministic_case_from_json,
    deterministic_case_to_json,
    finding_basis_from_json,
    finding_basis_to_json,
    finding_basis_to_status_json,
    render_deterministic_finding_text,
    run_deterministic_policies,
)
from yoetz.kernel.policies.research_evidence import RESEARCH_EVIDENCE_POLICY_PACK
from yoetz.kernel.policies.work_integrity import WORK_INTEGRITY_POLICY_PACK
from yoetz.kernel.projections import empty_projection_state
from yoetz.kernel.reducers import replay
from yoetz.protocol.canonical import canonical_encode, strict_json_parse


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


def test_completion_scope_case_gap_distinguishes_undeclared_declared_none_and_unknown() -> None:
    claim = ClaimRecordedPayload(clm(1), ClaimKind.COMPLETION, "Complete", (), obligation_refs=())
    empty_plan = PlanPublishedPayload(1, "Atomic scope", (), ())
    undeclared = make_case(
        plans={1: plan_record(empty_plan, 1)},
        claims={clm(1): record(claim, 2)},
    )
    gap = deterministic_checks.completion_scope_gap(undeclared.projection)
    assert gap is not None
    assert gap.code == COMPLETION_SCOPE_UNDECLARED_GAP
    assert gap.marker == f"{COMPLETION_SCOPE_UNDECLARED_GAP}:{evt(1)}"
    assert gap.subject_refs == ()

    for reason in NoObligationsReason:
        declared_plan = replace(empty_plan, no_obligations_reason=reason)
        declared = make_case(
            plans={1: plan_record(declared_plan, 1)},
            claims={clm(1): record(claim, 2)},
        )
        declared_gap = deterministic_checks.completion_scope_gap(declared.projection)
        assert declared_gap is not None
        assert declared_gap.code == COMPLETION_SCOPE_DECLARED_NONE_GAP

    no_plan = make_case(claims={clm(1): record(claim, 2)})
    assert deterministic_checks.completion_scope_gap(no_plan.projection) is None

    redacted_plan = replace(plan_record(empty_plan, 1), payload=None, redacted=True)
    unreadable = make_case(
        plans={1: redacted_plan},
        claims={clm(1): record(claim, 2)},
    )
    assert deterministic_checks.completion_scope_gap(unreadable.projection) is None

    declared_positive = make_case(
        plans={1: plan_record(PlanPublishedPayload(1, "Declared", (obl(1),), ()), 1)},
        claims={clm(1): record(claim, 2)},
    )
    assert deterministic_checks.completion_scope_gap(declared_positive.projection) is None


def test_deterministic_case_codec_round_trips_canonical_bytes() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    encoded = deterministic_case_to_json(case)
    decoded = deterministic_case_from_json(encoded)
    assert decoded == case
    assert canonical_encode(deterministic_case_to_json(decoded)) == canonical_encode(encoded)


def test_legacy_digest_gap_is_scoped_to_evidence_linked_by_current_work() -> None:
    records = replay_records("all-event-families")
    unlinked = records[:8]
    unlinked_case = build_deterministic_case(replay(unlinked), unlinked, CaseAvailabilityFacts())
    assert not any(
        gap.code == "evidence_digest_subject_legacy_unknown" for gap in unlinked_case.gaps
    )

    linked = records[:9]
    linked_case = build_deterministic_case(replay(linked), linked, CaseAvailabilityFacts())
    gap = next(
        item for item in linked_case.gaps if item.code == "evidence_digest_subject_legacy_unknown"
    )
    evidence = next(iter(linked_case.projection.evidence.values()))
    assert gap.subject_refs == (evidence.source_event_id,)
    coverage = linked_case.coverage_by_ref[next(iter(linked_case.projection.evidence))]
    assert "evidence_digest_subject_legacy_unknown" in coverage.known_gaps


def test_frozen_history_is_bounded_and_legacy_cases_remain_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = replay_records("all-event-families")
    projection = replay(records)
    monkeypatch.setattr(deterministic_checks, "MAX_FROZEN_HISTORY_EVENTS", 2)
    case = build_deterministic_case(projection, records, CaseAvailabilityFacts())
    assert [item.schema_name for item in case.history] == [
        "response_recorded",
        "plan_revised",
    ]
    assert case.history_omitted_before_count == 9
    encoded = deterministic_case_to_json(case)
    legacy = dict(encoded)
    legacy.pop("history")
    legacy.pop("history_availability")
    legacy.pop("history_omitted_before_count")
    decoded = deterministic_case_from_json(legacy)
    assert decoded.history == ()
    assert decoded.history_availability == "not_recorded"


def test_frozen_history_payload_budget_marks_content_not_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = replay_records("all-event-families")
    monkeypatch.setattr(deterministic_checks, "MAX_FROZEN_HISTORY_BYTES", 1)
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    assert case.history
    assert all(item.payload is None for item in case.history)
    assert {item.content_visibility for item in case.history} == {"not_selected"}


def test_deterministic_case_decoder_rejects_extra_and_contradictory_state() -> None:
    records = replay_records("all-event-families")
    encoded = cast(
        dict[str, Any],
        strict_json_parse(
            canonical_encode(
                deterministic_case_to_json(
                    build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
                )
            )
        ),
    )
    with_extra = deepcopy(encoded)
    with_extra["unexpected"] = None
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        deterministic_case_from_json(with_extra)

    missing_coverage = deepcopy(encoded)
    coverage = cast(dict[str, Any], missing_coverage["coverage_by_ref"])
    coverage.pop(next(iter(coverage)))
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        deterministic_case_from_json(missing_coverage)

    duplicate_allowed = deepcopy(encoded)
    allowed_ids = cast(list[Any], duplicate_allowed["allowed_ids"])
    allowed_ids.append(allowed_ids[0])
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        deterministic_case_from_json(duplicate_allowed)

    wrong_frontier = deepcopy(encoded)
    wrong_frontier["frontier"] = {
        "sequence": "0",
        "head_digest": "genesis",
    }
    with pytest.raises(ValueError, match="deterministic_case_invalid"):
        deterministic_case_from_json(wrong_frontier)


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
            COMPLETION_SCOPE_UNDECLARED_GAP,
            (),
        ),
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


def test_finding_basis_codec_is_lossless_closed_and_distinct_from_status() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    assessment = run_deterministic_policies(case, WORK_INTEGRITY_POLICY_PACK).assessments[0]
    encoded = finding_basis_to_json(assessment.basis)
    assert finding_basis_from_json(encoded) == assessment.basis
    assert canonical_encode(finding_basis_to_json(finding_basis_from_json(encoded))) == (
        canonical_encode(encoded)
    )

    with_extra = deepcopy(encoded)
    cast(dict[str, Any], with_extra)["unexpected"] = None
    with pytest.raises(ValueError, match="finding_basis_invalid"):
        finding_basis_from_json(with_extra)
    with pytest.raises(ValueError, match="finding_basis_invalid"):
        finding_basis_from_json(cast(Any, finding_basis_to_status_json(assessment)))


def test_unknown_or_tampered_pack_is_rejected() -> None:
    case = build_deterministic_case(
        empty_projection_state(),
        (),
        CaseAvailabilityFacts(),
    )
    with pytest.raises(ValueError, match="policy_wiring_invalid"):
        run_deterministic_policies(case, PolicyPack("work-integrity", "0.1.1"))
