"""Frozen privacy-selected semantic case construction (plan 03 / issue #69)."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.policy_cases import (
    clm,
    evd,
    evidence_record,
    make_case,
    obl,
    obligation_record,
    plan_record,
    record,
)
from builders.replay import replay_records
from yoetz.application.check import (
    CheckScope,
    allocate_findings,
    prior_finding_ids,
    run_deterministic_policies,
)
from yoetz.application.semantic_case import (
    OVER_CASE_ITEM_LIMIT_REASON,
    REVIEW_PACKET_ITEM_ID,
    CapturedContentScope,
    CapturedSemanticContent,
    bounded_case_envelope,
    build_semantic_case,
    review_selection_digest,
    semantic_case_to_candidate_context,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
    MAX_TEXT_BYTES,
    ClaimKind,
    ClaimRecordedPayload,
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
)
from yoetz.domain.findings import Finding
from yoetz.domain.observation import ObservationContentKind, ObservationContentManifest
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
from yoetz.domain.values import EvidenceId, object_id, timestamp_from_string
from yoetz.kernel.deterministic_checks import (
    CaseAvailabilityFacts,
    DeterministicCase,
    build_deterministic_case,
)
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.kernel.reducers import replay
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.ports.semantic import ExcerptDigestProvenance, SemanticCase
from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.coverage import EvidenceImmutability
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import MAX_REVIEW_TEXT_BYTES, DataCategory


class _Ids:
    def new(self, kind: IdKind) -> str:
        return new_id(kind)


def _case_with_material(*, with_evidence: bool = True) -> DeterministicCase:
    plan = plan_record(PlanPublishedPayload(1, "Ship the review packet", (obl(1),)), 1)
    obligation = obligation_record(
        ObligationPublishedPayload(
            obl(1), "Build the real packet", "tests pass", ObligationStatus.OPEN
        ),
        2,
    )
    supports = (evd(1),) if with_evidence else ()
    claim = record(
        ClaimRecordedPayload(
            clm(1),
            ClaimKind.COMPLETION,
            "Work is complete",
            supports,
            obligation_refs=(obl(1),),
        ),
        3,
    )
    evidence: dict[EvidenceId, EvidenceProjectionRecord] = {}
    extra = (clm(1), obl(1))
    if with_evidence:
        evidence[evd(1)] = evidence_record(
            EvidenceRecordedPayload(
                evd(1),
                EvidenceKind.TEST_RESULT,
                EvidenceImmutability.METADATA_ONLY,
                timestamp_from_string("2026-07-01T00:00:00.000Z"),
                description="test output: 1 failed assertion",
            ),
            4,
        )
        extra = (*extra, evd(1))
    return make_case(
        plans={1: plan},
        obligations={obl(1): obligation},
        claims={clm(1): claim},
        evidence=evidence,
        extra_refs=extra,
    )


def _findings_for(case: DeterministicCase) -> tuple[Finding, ...]:
    assessments, _ = run_deterministic_policies(
        case,
        CheckScope((), ()),
        ("research-evidence/0.1.0", "work-integrity/0.1.0"),
    )
    return allocate_findings(
        _Ids(),
        tuple(item.candidate for item in assessments),
        prior_finding_ids(case.projection),
    )


def _build(
    case: DeterministicCase,
    profile: ReviewContextProfile,
    *,
    findings: Sequence[Finding] = (),
    dependency: str = "sha256:" + "b" * 64,
    captured_content: Sequence[CapturedSemanticContent] = (),
    captured_content_scope: CapturedContentScope | None = None,
    captured_content_gaps: Sequence[str] = (),
) -> SemanticCase:
    return build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=case,
        dependency_digest=dependency,
        findings=findings,
        review_context_profile=profile,
        review_selection=ReviewSelectionPolicy.for_profile(profile),
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
        captured_content=captured_content,
        captured_content_scope=captured_content_scope,
        captured_content_gaps=captured_content_gaps,
    )


def _captured_case_values(
    content: bytes = b"planted-defect-marker: missing validation",
    *,
    redacted: bool = False,
    phase_identity: str = "sha256:" + "4" * 64,
) -> tuple[DeterministicCase, CapturedSemanticContent, CapturedContentScope]:
    base = _case_with_material(with_evidence=True)
    object_value = object_id("obj_00000000-0000-4000-8000-000000000302")
    content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    payload = EvidenceRecordedPayload(
        evidence_id=evd(1),
        evidence_kind=EvidenceKind.OTHER,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=timestamp_from_string("2026-07-01T00:00:00.000Z"),
        captured_object_id=object_value,
        content_digest=content_digest,
        description="Observation-captured tool output bytes part=1/1",
        digest_binding=EvidenceDigestBinding(
            subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
            content_availability=EvidenceContentAvailability.CAPTURED,
            byte_count=len(content),
            provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
        ),
    )
    observed = make_case(
        plans=base.projection.plans,
        obligations=base.projection.obligations,
        claims=base.projection.claims,
        evidence={evd(1): evidence_record(payload, 4)},
        extra_refs=(clm(1), obl(1), evd(1)),
    )
    envelope_digest = "sha256:" + "5" * 64
    object_ref = ObjectRef(
        object_id=object_value,
        plaintext_size=1_024,
        commitment="hmac-sha256:" + "6" * 64,
        envelope_digest=envelope_digest,
        encryption_format="yoetz-object/1",
        key_slot="task",
        metadata=ObjectMetadata(
            ObjectKind.CAPTURED_CONTENT,
            "application/vnd.yoetz.observation-content+json",
            "tsk_10000000-0000-4000-8000-000000000001",
            datetime(2026, 7, 1, tzinfo=UTC),
        ),
    )
    manifest = ObservationContentManifest(
        object_id=object_value,
        envelope_digest=envelope_digest,
        content_kind=ObservationContentKind.TOOL_OUTPUT,
        part_index=0,
        part_count=1,
        redacted=redacted,
        content_digest=content_digest,
        content_bytes=len(content),
        correlation_identity="tool-use-1",
        source_commitment="hmac-sha256:" + "7" * 64,
    )
    captured = CapturedSemanticContent(
        object_ref=object_ref,
        manifest=manifest,
        content=content,
        task_id="tsk_10000000-0000-4000-8000-000000000001",
        session_id="ses_10000000-0000-4000-8000-000000000001",
        workspace_commitment="hmac-sha256:" + "8" * 64,
        phase_identity=phase_identity,
        capture_profile="claude-code-ordinary-observation-v1",
        capture_gaps=("content_redacted",) if redacted else (),
    )
    scope = CapturedContentScope(
        task_id=captured.task_id,
        session_id=captured.session_id,
        workspace_commitment=captured.workspace_commitment,
        authorized_profiles=("claude-code-ordinary-observation-v1",),
        phase_bindings=((str(evd(1)), phase_identity),),
    )
    return observed, captured, scope


def test_structural_profile_sends_no_prose_and_declares_omitted_sections() -> None:
    case = _case_with_material()
    semantic = _build(case, ReviewContextProfile.STRUCTURAL)

    assert {item.category for item in semantic.items} == {DataCategory.BOUNDED_STRUCTURAL_METADATA}
    assert semantic.packet.goal_item_ids == ()
    assert semantic.packet.obligation_item_ids == ()
    assert semantic.packet.claim_item_ids == ()
    assert semantic.packet.targeted_excerpts == ()
    reasons = {item.reason for item in semantic.packet.omissions}
    assert "not_selected" in reasons
    # Timeline / frontier structural facts remain present.
    assert semantic.packet.timeline_item_ids
    assert all(item.section == "timeline" for item in semantic.items)


def test_goal_aware_profile_includes_bounded_goal_obligation_claim_with_categories() -> None:
    case = _case_with_material()
    semantic = _build(case, ReviewContextProfile.GOAL_AWARE)

    categories = {item.category for item in semantic.items}
    assert DataCategory.TASK_DESCRIPTION in categories
    assert DataCategory.OBLIGATION_TEXT in categories
    assert DataCategory.CLAIM_TEXT in categories
    assert semantic.packet.goal_item_ids
    assert semantic.packet.obligation_item_ids
    assert semantic.packet.claim_item_ids
    goal = next(item for item in semantic.items if item.section == "goal")
    assert b"Ship the review packet" in goal.content
    # Goal-aware still excludes targeted excerpts.
    assert semantic.packet.targeted_excerpts == ()


def test_provider_packet_preserves_detailed_frozen_history_by_category() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    semantic = _build(case, ReviewContextProfile.GOAL_AWARE)
    timeline = [
        item
        for item in semantic.items
        if item.item_id in semantic.packet.timeline_item_ids and item.item_id.startswith("history-")
    ]
    assert [item.occurred_order for item in timeline] == sorted(
        item.occurred_order for item in timeline
    )
    by_kind: dict[str, Mapping[str, JsonValue]] = {}
    for item in timeline:
        document = strict_json_parse(item.content)
        assert isinstance(document, Mapping)
        typed = cast(Mapping[str, JsonValue], document)
        kind = typed.get("kind")
        assert type(kind) is str
        assert typed["accepted_at"] is not None
        assert typed["occurred_at_consistency"] in {
            "within_forward_skew_allowance",
            "ahead_of_forward_skew_allowance",
        }
        by_kind[kind] = typed

    def payload_for(kind: str) -> Mapping[str, JsonValue]:
        payload = by_kind[kind]["payload"]
        assert isinstance(payload, Mapping)
        return cast(Mapping[str, JsonValue], payload)

    obligation = payload_for("obligation_published")
    assert obligation["acceptance_criteria"] == "All fixture assertions are reproducible offline."
    assert obligation["evidence_expectation"] == "A deterministic test-result snapshot"
    assert obligation["requested_items"] == [{"item_kind": "change", "value": "synthetic-replay"}]
    decision = payload_for("decision_recorded")
    assert (
        decision["rationale"] == "A deterministic command is the smallest complete public example."
    )
    action = payload_for("action_recorded")
    assert action["description"] == "Run the synthetic replay verification"
    assert action["attempted_items"] == ["synthetic-replay"]
    assert "command" not in action
    result = payload_for("result_recorded")
    assert result["summary"] == "Synthetic replay verification passed"
    evidence = payload_for("evidence_recorded")
    assert evidence["observed_at"] == "2026-03-02T00:00:08.000Z"
    assert evidence["strength"] == "immutable_snapshot"
    assert {
        "check_recorded",
        "response_recorded",
        "plan_published",
        "plan_revised",
    } <= by_kind.keys()
    categories = {(item.source_kind, item.category) for item in timeline}
    assert ("obligation", DataCategory.OBLIGATION_TEXT) in categories
    assert ("action", DataCategory.COMMAND_METADATA) in categories
    assert ("result", DataCategory.COMMAND_METADATA) in categories
    assert ("evidence", DataCategory.EVIDENCE_EXCERPT) in categories

    prepared = semantic_case_to_prepared_payload(
        semantic, {item.item_id for item in semantic.items}
    )
    provider_document = strict_json_parse(prepared)
    assert isinstance(provider_document, Mapping)
    provider_items = cast(Mapping[str, JsonValue], provider_document)["items"]
    assert isinstance(provider_items, list)
    provider_kinds: set[str] = set()
    for raw_item in provider_items:
        if not isinstance(raw_item, Mapping):
            continue
        item = cast(Mapping[str, JsonValue], raw_item)
        item_id = item.get("item_id")
        content = item.get("content")
        if type(item_id) is not str or not item_id.startswith("history-"):
            continue
        assert type(content) is str
        rendered = strict_json_parse(content.encode("utf-8"))
        assert isinstance(rendered, Mapping)
        kind = cast(Mapping[str, JsonValue], rendered).get("kind")
        assert type(kind) is str
        provider_kinds.add(kind)
    assert {
        "action_recorded",
        "check_recorded",
        "obligation_published",
        "plan_revised",
        "response_recorded",
        "result_recorded",
    } <= provider_kinds


def test_structural_history_has_no_recorded_prose_and_declares_omissions() -> None:
    records = replay_records("all-event-families")
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    semantic = _build(case, ReviewContextProfile.STRUCTURAL)
    history = [item for item in semantic.items if item.item_id.startswith("history-")]
    assert history
    assert {item.category for item in history} == {DataCategory.BOUNDED_STRUCTURAL_METADATA}
    assert all(b'"payload"' not in item.content for item in history)
    assert any(
        omission.reason == "not_selected" and omission.category is DataCategory.OBLIGATION_TEXT
        for omission in semantic.packet.omissions
    )


def test_assisted_profile_includes_only_linked_recorded_capped_excerpts() -> None:
    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)

    assert semantic.packet.targeted_excerpts
    excerpt = semantic.packet.targeted_excerpts[0]
    assert excerpt.source_kind == "test"
    assert excerpt.content_visibility == "available"
    body = next(item for item in semantic.items if item.item_id == excerpt.excerpt_item_id)
    assert body.content == b"test output: 1 failed assertion"
    assert set(excerpt.linked_subject_refs) <= (semantic.frontier_refs | semantic.local_check_refs)


def _typed_digest_case(*, description: str | None) -> DeterministicCase:
    case = _case_with_material(with_evidence=True)
    record = case.projection.evidence[evd(1)]
    assert record.payload is not None
    typed_payload = EvidenceRecordedPayload(
        evidence_id=record.payload.evidence_id,
        evidence_kind=EvidenceKind.TEST_RESULT,
        strength=EvidenceImmutability.CONTENT_DIGEST,
        observed_at=record.payload.observed_at,
        content_digest="sha256:" + "1" * 64,
        description=description,
        digest_binding=EvidenceDigestBinding(
            subject=EvidenceDigestSubject.TEST_STDOUT,
            content_availability=EvidenceContentAvailability.DIGEST_ONLY,
            byte_count=512,
            provenance=EvidenceDigestProvenance.CALLER_ASSERTED,
        ),
    )
    return make_case(
        plans=case.projection.plans,
        obligations=case.projection.obligations,
        claims=case.projection.claims,
        evidence={evd(1): evidence_record(typed_payload, 4)},
        extra_refs=(clm(1), obl(1), evd(1)),
    )


def test_assisted_digest_excerpt_prefers_description_and_carries_provenance() -> None:
    typed = _typed_digest_case(description="caller prose the reviewer must be able to read")
    semantic = _build(typed, ReviewContextProfile.ASSISTED, findings=_findings_for(typed))
    excerpt = semantic.packet.targeted_excerpts[0]
    item = next(row for row in semantic.items if row.item_id == excerpt.excerpt_item_id)
    assert item.content == b"caller prose the reviewer must be able to read"
    provenance = excerpt.digest_provenance
    assert provenance is not None
    assert provenance.content_digest == "sha256:" + "1" * 64
    assert provenance.digest_subject is EvidenceDigestSubject.TEST_STDOUT
    assert provenance.content_availability is EvidenceContentAvailability.DIGEST_ONLY
    assert provenance.byte_count == 512
    assert provenance.provenance is EvidenceDigestProvenance.CALLER_ASSERTED
    assert provenance.evidence_kind is EvidenceKind.TEST_RESULT
    assert provenance.strength is EvidenceImmutability.CONTENT_DIGEST


def test_assisted_digest_excerpt_without_description_keeps_typed_provenance_text() -> None:
    typed = _typed_digest_case(description=None)
    semantic = _build(typed, ReviewContextProfile.ASSISTED, findings=_findings_for(typed))
    excerpt = semantic.packet.targeted_excerpts[0]
    item = next(row for row in semantic.items if row.item_id == excerpt.excerpt_item_id)
    document = strict_json_parse(item.content)
    assert isinstance(document, Mapping)
    assert document["digest_subject"] == "test_stdout"
    assert document["content_availability"] == "digest_only"
    assert excerpt.digest_provenance is not None


def _excerpt_provenance(**overrides: object) -> ExcerptDigestProvenance:
    fields: dict[str, object] = {
        "evidence_kind": EvidenceKind.TEST_RESULT,
        "strength": EvidenceImmutability.CONTENT_DIGEST,
        "content_digest": "sha256:" + "1" * 64,
        "digest_subject": EvidenceDigestSubject.TEST_STDOUT,
        "content_availability": EvidenceContentAvailability.DIGEST_ONLY,
        "byte_count": 512,
        "provenance": EvidenceDigestProvenance.CALLER_ASSERTED,
    }
    fields.update(overrides)
    return ExcerptDigestProvenance(**fields)  # type: ignore[arg-type]


def test_excerpt_digest_provenance_rejects_binding_invariant_violations() -> None:
    approval = {
        "approval_commitment": "sha256:" + "a" * 64,
        "approved_check_result_digest": "sha256:" + "b" * 64,
    }
    # Reserved subjects require their owning provenance.
    for subject, wrong in (
        (EvidenceDigestSubject.APPROVED_CHECK_RECEIPT, EvidenceDigestProvenance.CALLER_ASSERTED),
        (EvidenceDigestSubject.APPROVED_CHECK_RECEIPT, EvidenceDigestProvenance.IMPORT_OBSERVED),
        (EvidenceDigestSubject.IMPORT_REPORT, EvidenceDigestProvenance.CALLER_ASSERTED),
        (EvidenceDigestSubject.IMPORT_REPORT, EvidenceDigestProvenance.APPROVED_CHECK),
    ):
        with pytest.raises(ValueError, match="semantic_case_invalid"):
            _excerpt_provenance(
                digest_subject=subject,
                provenance=wrong,
                **(approval if wrong is EvidenceDigestProvenance.APPROVED_CHECK else {}),
            )
    # approved_check requires both approval digests, exactly.
    with pytest.raises(ValueError, match="semantic_case_invalid"):
        _excerpt_provenance(provenance=EvidenceDigestProvenance.APPROVED_CHECK)
    with pytest.raises(ValueError, match="semantic_case_invalid"):
        _excerpt_provenance(
            provenance=EvidenceDigestProvenance.APPROVED_CHECK,
            approval_commitment="sha256:" + "a" * 64,
        )
    with pytest.raises(ValueError, match="semantic_case_invalid"):
        _excerpt_provenance(**approval)
    accepted = _excerpt_provenance(
        digest_subject=EvidenceDigestSubject.APPROVED_CHECK_RECEIPT,
        provenance=EvidenceDigestProvenance.APPROVED_CHECK,
        **approval,
    )
    assert accepted.provenance is EvidenceDigestProvenance.APPROVED_CHECK


def test_case_envelope_serializes_excerpt_digest_provenance() -> None:
    typed = _typed_digest_case(description="caller prose the reviewer must be able to read")
    semantic = _build(typed, ReviewContextProfile.ASSISTED, findings=_findings_for(typed))
    document = strict_json_parse(bounded_case_envelope(semantic))
    assert isinstance(document, Mapping)
    packet = document["review_packet"]
    assert isinstance(packet, Mapping)
    rows = packet["targeted_excerpts"]
    assert isinstance(rows, list) and rows
    row = rows[0]
    assert isinstance(row, Mapping)
    provenance = row["digest_provenance"]
    assert isinstance(provenance, Mapping)
    assert provenance["content_digest"] == "sha256:" + "1" * 64
    assert provenance["digest_subject"] == "test_stdout"
    assert provenance["content_availability"] == "digest_only"
    assert provenance["byte_count"] == 512
    assert provenance["provenance"] == "caller_asserted"
    assert provenance["evidence_kind"] == "test_result"
    assert provenance["strength"] == "content_digest"
    assert "approval_commitment" not in provenance


def test_observation_captured_excerpt_exposes_provenance_not_stored_object_bytes() -> None:
    case = _case_with_material(with_evidence=True)
    record = case.projection.evidence[evd(1)]
    assert record.payload is not None
    payload = EvidenceRecordedPayload(
        evidence_id=record.payload.evidence_id,
        evidence_kind=EvidenceKind.OTHER,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=record.payload.observed_at,
        captured_object_id=object_id("obj_00000000-0000-4000-8000-000000000302"),
        content_digest="sha256:" + "3" * 64,
        description="Observation-captured tool output bytes part=1/1",
        digest_binding=EvidenceDigestBinding(
            subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
            content_availability=EvidenceContentAvailability.CAPTURED,
            byte_count=512,
            provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
        ),
    )
    observed = make_case(
        plans=case.projection.plans,
        obligations=case.projection.obligations,
        claims=case.projection.claims,
        evidence={evd(1): evidence_record(payload, 4)},
        extra_refs=(clm(1), obl(1), evd(1)),
    )
    semantic = _build(observed, ReviewContextProfile.ASSISTED, findings=_findings_for(observed))
    excerpt = semantic.packet.targeted_excerpts[0]
    item = next(row for row in semantic.items if row.item_id == excerpt.excerpt_item_id)
    assert item.content == b"Observation-captured tool output bytes part=1/1"
    assert b"captured-object-secret-marker" not in bounded_case_envelope(semantic)
    assert excerpt.digest_provenance is not None
    assert excerpt.digest_provenance.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
    assert excerpt.digest_provenance.content_availability is EvidenceContentAvailability.CAPTURED
    document = strict_json_parse(bounded_case_envelope(semantic))
    assert isinstance(document, Mapping)
    packet = document["review_packet"]
    assert isinstance(packet, Mapping)
    rows = packet["targeted_excerpts"]
    assert isinstance(rows, list) and rows
    provenance = cast(
        Mapping[str, object], cast(Mapping[str, object], rows[0])["digest_provenance"]
    )
    assert provenance["provenance"] == "observation_captured"


def test_authenticated_captured_bytes_reach_selected_case_and_prepared_packet() -> None:
    case, captured, scope = _captured_case_values()
    semantic = _build(
        case,
        ReviewContextProfile.ASSISTED,
        findings=_findings_for(case),
        captured_content=(captured,),
        captured_content_scope=scope,
    )

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content == captured.content
    assert semantic.packet.targeted_excerpts[0].content_visibility == "available"
    # The pre-egress case envelope is structural metadata only.
    assert captured.content not in bounded_case_envelope(semantic)

    # The privacy-approved projection is the first point at which the actual
    # authenticated retained bytes are assembled into the provider packet.
    prepared = semantic_case_to_prepared_payload(
        semantic,
        {item.item_id for item in semantic.items},
    )
    assert captured.content in prepared


def test_wrong_phase_captured_bytes_are_excluded_even_with_valid_digest() -> None:
    case, captured, scope = _captured_case_values()
    wrong_phase = replace(captured, phase_identity="sha256:" + "9" * 64)
    semantic = _build(
        case,
        ReviewContextProfile.ASSISTED,
        findings=_findings_for(case),
        captured_content=(wrong_phase,),
        captured_content_scope=scope,
    )

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content != captured.content
    assert captured.content not in semantic_case_to_prepared_payload(
        semantic,
        {item.item_id for item in semantic.items},
    )
    assert "content_unselected" in semantic.packet.coverage.known_gaps


def test_redacted_captured_bytes_remain_available_with_explicit_gap() -> None:
    case, captured, scope = _captured_case_values(redacted=True)
    semantic = _build(
        case,
        ReviewContextProfile.ASSISTED,
        findings=_findings_for(case),
        captured_content=(captured,),
        captured_content_scope=scope,
    )

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content == captured.content
    assert "content_redacted" in semantic.packet.coverage.known_gaps


def test_captured_content_input_is_bounded_before_grouping() -> None:
    case, captured, scope = _captured_case_values()
    with pytest.raises(ValueError, match="over_limit"):
        _build(
            case,
            ReviewContextProfile.ASSISTED,
            captured_content=tuple(captured for _ in range(65)),
            captured_content_scope=scope,
        )

    large_case, large_capture, large_scope = _captured_case_values(b"x" * 300_000)
    with pytest.raises(ValueError, match="over_limit"):
        _build(
            large_case,
            ReviewContextProfile.ASSISTED,
            captured_content=tuple(large_capture for _ in range(7)),
            captured_content_scope=large_scope,
        )


def test_assisted_legacy_digest_is_an_explicit_omission() -> None:
    case = _case_with_material(with_evidence=True)
    record = case.projection.evidence[evd(1)]
    assert record.payload is not None
    legacy_payload = EvidenceRecordedPayload(
        evidence_id=record.payload.evidence_id,
        evidence_kind=EvidenceKind.TEST_RESULT,
        strength=EvidenceImmutability.CONTENT_DIGEST,
        observed_at=record.payload.observed_at,
        content_digest="sha256:" + "2" * 64,
        description="legacy prose",
    )
    legacy = make_case(
        plans=case.projection.plans,
        obligations=case.projection.obligations,
        claims=case.projection.claims,
        evidence={evd(1): evidence_record(legacy_payload, 4)},
        extra_refs=(clm(1), obl(1), evd(1)),
    )
    semantic = _build(legacy, ReviewContextProfile.ASSISTED, findings=_findings_for(legacy))
    assert semantic.packet.targeted_excerpts == ()
    assert any(
        omission.subject_ref == evd(1) and omission.reason == "not_recorded"
        for omission in semantic.packet.omissions
    )


def test_withheld_and_not_selected_material_use_correct_omission_reasons() -> None:
    case = _case_with_material()
    structural = _build(case, ReviewContextProfile.STRUCTURAL)
    omitted = {
        (item.source_kind, item.reason, item.category) for item in structural.packet.omissions
    }
    assert ("task", "not_selected", DataCategory.TASK_DESCRIPTION) in omitted
    assert ("obligation", "not_selected", DataCategory.OBLIGATION_TEXT) in omitted
    assert ("claim", "not_selected", DataCategory.CLAIM_TEXT) in omitted

    # No recorded decision → no decision prose under goal-aware, no invented content.
    goal_aware = _build(case, ReviewContextProfile.GOAL_AWARE)
    assert goal_aware.packet.decision_item_ids == ()
    assert not any(item.section == "decision" for item in goal_aware.items)


def test_every_supplied_ref_belongs_to_frozen_allowlist() -> None:
    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)
    allowed = semantic.frontier_refs | semantic.local_check_refs

    for item in semantic.items:
        assert set(item.linked_subject_refs) <= allowed
    for assessment in semantic.packet.deterministic_assessments:
        assert assessment.finding_ref in semantic.local_check_refs
        assert set(assessment.subject_refs) <= semantic.frontier_refs
        assert set(assessment.supporting_refs) <= allowed
    for omission in semantic.packet.omissions:
        assert omission.subject_ref in allowed


def test_deterministic_finding_basis_and_evidence_produce_projected_assessment() -> None:
    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    assert findings
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)

    assert semantic.packet.deterministic_assessments
    assessment = semantic.packet.deterministic_assessments[0]
    assert assessment.observed_facts
    assert assessment.summary_item_id is not None
    assert assessment.detail_item_id is not None
    summary = next(item for item in semantic.items if item.item_id == assessment.summary_item_id)
    assert summary.category is DataCategory.FINDING_SUMMARY
    assert summary.source_ref == str(assessment.finding_ref)


def test_case_and_dependency_changes_invalidate_case_digest() -> None:
    case = _case_with_material()
    left = _build(case, ReviewContextProfile.GOAL_AWARE, dependency="sha256:" + "b" * 64)
    right = _build(case, ReviewContextProfile.GOAL_AWARE, dependency="sha256:" + "c" * 64)
    assert left.case_digest != right.case_digest

    other = _case_with_material(with_evidence=False)
    third = _build(other, ReviewContextProfile.GOAL_AWARE, dependency="sha256:" + "b" * 64)
    assert left.case_digest != third.case_digest


def test_case_builder_performs_no_ambient_state_access() -> None:
    source = inspect.getsource(build_semantic_case)
    # Capability-free contract: no ambient import or runner symbols in the builder body.
    for forbidden in (
        "subprocess",
        "Path(",
        "open(",
        "os.environ",
        "socket",
        "Git",
        "filesystem",
        "transcript",
        "httpx",
        "aiohttp",
    ):
        assert forbidden not in source


def test_candidate_context_preserves_item_categories_and_packet_envelope() -> None:
    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)
    scope = AuthorizationScope(
        AuthorizationScopeKind.TASK,
        "ins_10000000-0000-4000-8000-000000000001",
        "hmac-sha256:" + "a" * 64,
        "tsk_10000000-0000-4000-8000-000000000001",
    )
    binding = ProviderBinding("fake", "model", "profile", "1.0.0", "external")
    candidate = semantic_case_to_candidate_context(
        semantic,
        request_id="req_10000000-0000-4000-8000-000000000001",
        scope=scope,
        provider_binding=binding,
    )
    assert candidate.purpose == "semantic-review"
    assert candidate.subject_digest == semantic.case_digest
    assert candidate.items[0].item_id == REVIEW_PACKET_ITEM_ID
    assert candidate.items[0].category is DataCategory.BOUNDED_STRUCTURAL_METADATA
    categories = {item.category for item in candidate.items[1:]}
    assert DataCategory.TASK_DESCRIPTION in categories
    assert DataCategory.EVIDENCE_EXCERPT in categories or DataCategory.FINDING_SUMMARY in categories


def test_prepared_payload_binds_selected_packet_and_withheld_omissions() -> None:
    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)
    # Approve only structural timeline + envelope-equivalent content subset.
    included = {
        item.item_id
        for item in semantic.items
        if item.category is DataCategory.BOUNDED_STRUCTURAL_METADATA
    }
    payload = semantic_case_to_prepared_payload(semantic, included)
    assert b"yoetz.review-packet-case/1" in payload
    document = strict_json_parse(payload)
    assert isinstance(document, dict)
    packet = document.get("review_packet")
    assert isinstance(packet, dict)
    omissions = packet.get("omissions")
    assert isinstance(omissions, list)
    reasons: set[str] = set()
    for row in omissions:
        if isinstance(row, dict):
            reason = row.get("reason")
            if type(reason) is str:
                reasons.add(reason)
    assert "withheld_by_policy" in reasons
    raw_items = document.get("items")
    assert isinstance(raw_items, list)
    item_ids: set[str] = set()
    for row in raw_items:
        if isinstance(row, dict):
            item_id = row.get("item_id")
            if type(item_id) is str:
                item_ids.add(item_id)
    assert item_ids == included
    assert semantic.case_digest.encode("ascii") in payload


def test_review_selection_digest_is_stable() -> None:
    left = review_selection_digest(ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED))
    right = review_selection_digest(
        ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED)
    )
    assert left == right
    structural = review_selection_digest(
        ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL)
    )
    assert left != structural


def test_structural_assessments_have_no_finding_prose_items() -> None:
    case = _case_with_material()
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.STRUCTURAL, findings=findings)
    assert all(
        assessment.summary_item_id is None and assessment.detail_item_id is None
        for assessment in semantic.packet.deterministic_assessments
    )
    assert not any(
        item.section in {"deterministic_summary", "deterministic_detail"} for item in semantic.items
    )


def test_prepared_payload_names_the_refs_post_validation_will_accept() -> None:
    """The reviewer gets one list of citable ids, matching the fence exactly.

    Every ``cited_refs`` value is fenced against ``frontier_refs | local_check_refs``, but the
    packet only ever carried them as two separate arrays, while the ids most visible in the
    document — ``items[].item_id``, e.g. ``goal-3`` — are not citable at all. Citing wrong costs
    the challenge, so the accept set is stated explicitly instead of left to be inferred.
    """

    case = _case_with_material(with_evidence=True)
    findings = _findings_for(case)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=findings)
    included = {item.item_id for item in semantic.items}
    document = strict_json_parse(semantic_case_to_prepared_payload(semantic, included))
    assert isinstance(document, dict)

    raw_citable = document.get("citable_refs")
    assert isinstance(raw_citable, list)
    citable = [ref for ref in cast(list[object], raw_citable) if type(ref) is str]
    assert len(citable) == len(raw_citable)
    assert set(citable) == semantic.frontier_refs | semantic.local_check_refs
    assert citable == sorted(citable)
    assert citable

    raw_items = document.get("items")
    assert isinstance(raw_items, list)
    item_ids: set[str] = set()
    for row in cast(list[object], raw_items):
        if isinstance(row, dict):
            item_id = cast(dict[str, object], row).get("item_id")
            if type(item_id) is str:
                item_ids.add(item_id)
    # The two vocabularies are disjoint: an item_id is never a citable ref.
    assert not item_ids & set(citable)


def _case_with_long_evidence(description: str) -> DeterministicCase:
    """The same shape as ``_case_with_material``, with one oversized evidence description."""

    plan = plan_record(PlanPublishedPayload(1, "Ship the review packet", (obl(1),)), 1)
    obligation = obligation_record(
        ObligationPublishedPayload(
            obl(1), "Build the real packet", "tests pass", ObligationStatus.OPEN
        ),
        2,
    )
    claim = record(
        ClaimRecordedPayload(
            clm(1),
            ClaimKind.COMPLETION,
            "Work is complete",
            (evd(1),),
            obligation_refs=(obl(1),),
        ),
        3,
    )
    evidence = {
        evd(1): evidence_record(
            EvidenceRecordedPayload(
                evd(1),
                EvidenceKind.TEST_RESULT,
                EvidenceImmutability.METADATA_ONLY,
                timestamp_from_string("2026-07-01T00:00:00.000Z"),
                description=description,
            ),
            4,
        )
    }
    return make_case(
        plans={1: plan},
        obligations={obl(1): obligation},
        claims={clm(1): claim},
        evidence=evidence,
        extra_refs=(clm(1), obl(1), evd(1)),
    )


def test_prose_that_publishes_but_cannot_be_carried_whole_is_named_in_coverage() -> None:
    """The publish-fits-but-case-drops window is disclosed, not silently shortened (issue #177).

    Publish-side prose accepts up to ``MAX_TEXT_BYTES`` (8192), while one case item carries at
    most ``MAX_REVIEW_TEXT_BYTES`` (4096). A 5 KB description therefore records cleanly and then
    reaches the reviewer shortened. Nothing said so: the only omission raised was the generic
    ``not_selected``, which reads as a selection-policy choice rather than a size drop.
    """

    body = "e" * 5_000
    assert len(body.encode("utf-8")) <= MAX_TEXT_BYTES
    assert len(body.encode("utf-8")) > MAX_REVIEW_TEXT_BYTES
    # The selection would carry it whole; only the per-item case bound stands in the way.
    assert ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED).max_excerpt_bytes > len(
        body.encode("utf-8")
    )

    case = _case_with_long_evidence(body)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=_findings_for(case))

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content_bytes == MAX_REVIEW_TEXT_BYTES
    assert SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP in semantic.packet.coverage.known_gaps


def test_narrow_custom_excerpt_bound_is_selection_not_a_size_gap() -> None:
    """Clipping at a custom ``max_excerpt_bytes`` below the item bound raises no size gap.

    The excerpt bound is the selection policy's declared choice — the packet metadata carries
    ``max_excerpt_bytes`` — so a reviewer can already attribute the shortening. Only the
    per-item case bound (``MAX_REVIEW_TEXT_BYTES``) names a size drop the selection accepted.
    """

    body = "e" * 1_000
    narrow = replace(
        ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED),
        max_excerpt_bytes=512,
    )
    case = _case_with_long_evidence(body)
    semantic = build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=case,
        dependency_digest="sha256:" + "b" * 64,
        findings=_findings_for(case),
        review_context_profile=ReviewContextProfile.ASSISTED,
        review_selection=narrow,
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
    )

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content_bytes == 512
    assert SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP not in semantic.packet.coverage.known_gaps


def test_prose_within_the_case_item_bound_raises_no_over_limit_gap() -> None:
    body = "e" * 1_000
    case = _case_with_long_evidence(body)
    semantic = _build(case, ReviewContextProfile.ASSISTED, findings=_findings_for(case))

    excerpt = next(item for item in semantic.items if item.item_id == f"excerpt-{evd(1)}")
    assert excerpt.content == body.encode("utf-8")
    assert SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP not in semantic.packet.coverage.known_gaps


def test_payload_replaced_by_the_bounded_marker_names_the_size_drop() -> None:
    """A payload too large to encode is replaced wholesale; the marker says why.

    ``_bounded_json`` swaps the entire event payload for a ``yoetz.bounded-content-omission/1``
    marker. Its ``reason`` was ``not_selected`` — the same token the packet uses for material the
    selection policy declined — so a reviewer could not tell an unsent section from one that was
    admitted and then dropped for size.
    """

    plan = plan_record(PlanPublishedPayload(1, "s" * 6_000, (obl(1),)), 1)
    obligation = obligation_record(
        ObligationPublishedPayload(
            obl(1), "Build the real packet", "tests pass", ObligationStatus.OPEN
        ),
        2,
    )
    case = make_case(
        plans={1: plan},
        obligations={obl(1): obligation},
        extra_refs=(obl(1),),
    )
    semantic = _build(case, ReviewContextProfile.GOAL_AWARE)

    goal = next(item for item in semantic.items if item.section == "goal")
    marker = strict_json_parse(goal.content)
    assert isinstance(marker, dict)
    assert marker["schema"] == "yoetz.bounded-content-omission/1"
    assert marker["reason"] == OVER_CASE_ITEM_LIMIT_REASON
    assert SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP in semantic.packet.coverage.known_gaps
