"""Frozen privacy-selected semantic case construction (plan 03 / issue #69)."""

from __future__ import annotations

import inspect
from collections.abc import Sequence

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
from yoetz.application.check import CheckScope, allocate_findings, run_deterministic_policies
from yoetz.application.semantic_case import (
    REVIEW_PACKET_ITEM_ID,
    build_semantic_case,
    review_selection_digest,
    semantic_case_to_candidate_context,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
    ClaimKind,
    ClaimRecordedPayload,
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
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.ports.semantic import SemanticCase
from yoetz.protocol.canonical import strict_json_parse
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
