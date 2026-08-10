"""Frozen privacy-selected semantic case construction (plan 03 / issue #69)."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import cast

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
from yoetz.application.check import CheckScope, allocate_findings, run_deterministic_policies
from yoetz.application.semantic_case import (
    REVIEW_PACKET_ITEM_ID,
    bounded_case_envelope,
    build_semantic_case,
    review_selection_digest,
    semantic_case_to_candidate_context,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
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
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import EvidenceId, timestamp_from_string
from yoetz.kernel.deterministic_checks import (
    CaseAvailabilityFacts,
    DeterministicCase,
    build_deterministic_case,
)
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.kernel.reducers import replay
from yoetz.ports.semantic import SemanticCase
from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.coverage import EvidenceImmutability
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import DataCategory


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
    return allocate_findings(_Ids(), tuple(item.candidate for item in assessments))


def _build(
    case: DeterministicCase,
    profile: ReviewContextProfile,
    *,
    findings: Sequence[Finding] = (),
    dependency: str = "sha256:" + "b" * 64,
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
    )


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
