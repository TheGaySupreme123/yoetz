"""What a reduced AI-powered review sent decides what it can resolve (issue #904, PR930-F1).

The check records only the frontier references whose own content item survived in the exact
packet sent to the reviewer: after envelope bounding and privacy minimization, never a mention,
a typed link or an omission row. These tests build a real semantic case, assemble the provider
document from a privacy-approved item set, and ask the resolution kernel what that document can
prove.
"""

from __future__ import annotations

from builders.policy_cases import (
    act,
    clm,
    evd,
    evidence_record,
    evt,
    fnd,
    make_case,
    obl,
    obligation_record,
    plan_record,
    record,
    res,
)
from yoetz.application.semantic_case import (
    REVIEW_PACKET_ITEM_ID,
    build_semantic_case,
    review_packet_content_refs,
    semantic_case_to_candidate_context,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    CheckMode,
    CheckRecordedPayload,
    ClaimKind,
    ClaimRecordedPayload,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    PolicyVersion,
    ResponseRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CheckVerdict,
    Finding,
    FindingKind,
    FindingOrigin,
    ResponseDisposition,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import Frontier, timestamp_from_string
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.finding_resolution import resolution_blockers
from yoetz.ports.semantic import SamplingParams, SemanticCase
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
_SCOPE_REDUCED = "semantic_reference_scope_reduced"
_OUTSIDE = ("finding_material_outside_reduced_review_scope", "coverage:" + _SCOPE_REDUCED)
_REPAIR = evd(7)
_SCOPE = AuthorizationScope(
    AuthorizationScopeKind.TASK,
    "ins_10000000-0000-4000-8000-000000000001",
    "hmac-sha256:" + "a" * 64,
    "tsk_10000000-0000-4000-8000-000000000001",
)
_BINDING = ProviderBinding("fireworks", "test-model", "chat-completions", "1", "external")


def _coverage(*gaps: str) -> Coverage:
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=LedgerFreshness.PARTIAL,
        check_types=(CheckType.DETERMINISTIC, CheckType.SEMANTIC_MODEL_DERIVED),
        known_gaps=tuple(sorted(gaps, key=str.encode)),
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


def _finding() -> Finding:
    """An AI-powered finding about the completion claim, raised under a reduced scope."""

    kind = FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE
    return Finding(
        finding_id=fnd(1),
        kind=kind,
        origin=FindingOrigin.SEMANTIC_MODEL_DERIVED,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="The completion claim has no readable support.",
        detail="Record the result that supports the claim.",
        subject_refs=(clm(1),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(4, _DIGEST),
        coverage=_coverage(_SCOPE_REDUCED),
        provenance=_provenance(),
    )


def _case() -> DeterministicCase:
    """A claim, a finding about it, the repair (action, evidence, result) and an answer citing it.

    Unrelated frontier references make the review packet's reference scope reduced.
    """

    return make_case(
        plans={1: plan_record(PlanPublishedPayload(1, "Ship the parser", (obl(1),)), 1)},
        obligations={
            obl(1): obligation_record(
                ObligationPublishedPayload(
                    obl(1), "Parse offsets", "tests pass", ObligationStatus.OPEN
                ),
                2,
            )
        },
        claims={
            clm(1): record(
                ClaimRecordedPayload(
                    clm(1),
                    ClaimKind.COMPLETION,
                    "Offsets are validated",
                    (obl(1),),
                    obligation_refs=(obl(1),),
                ),
                3,
            )
        },
        findings={fnd(1): record(_finding(), 4)},
        actions={act(9): record(ActionRecordedPayload(act(9), ActionKind.EDIT, "Repair"), 5)},
        evidence={
            _REPAIR: evidence_record(
                EvidenceRecordedPayload(
                    _REPAIR,
                    EvidenceKind.TEST_RESULT,
                    EvidenceImmutability.METADATA_ONLY,
                    timestamp_from_string("2026-07-01T00:00:00.000Z"),
                    description="test output: 12 passed, offsets rejected when negative",
                ),
                6,
            )
        },
        results={
            res(7): record(
                ResultRecordedPayload(
                    res(7), act(9), ResultOutcome.SUCCESS, 0, evidence_refs=(_REPAIR,)
                ),
                7,
            )
        },
        responses={
            fnd(1): record(
                ResponseRecordedPayload(
                    finding_id=fnd(1),
                    finding_frontier=Frontier(4, _DIGEST),
                    disposition=ResponseDisposition.ACKNOWLEDGED,
                    reason="Validated offsets and recorded the test run.",
                    evidence_refs=(_REPAIR,),
                ),
                8,
            )
        },
        extra_refs=tuple(evt(900 + number) for number in range(8)),
    )


def _semantic(selection: ReviewSelectionPolicy, profile: ReviewContextProfile) -> SemanticCase:
    return build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000904",
        frozen_case=_case(),
        dependency_digest="sha256:" + "b" * 64,
        findings=(),
        review_context_profile=profile,
        review_selection=selection,
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
    )


def _expanded() -> SemanticCase:
    profile = ReviewContextProfile.EXPANDED
    return _semantic(ReviewSelectionPolicy.for_profile(profile), profile)


def _offered(case: SemanticCase) -> set[str]:
    candidate = semantic_case_to_candidate_context(
        case,
        request_id="req_10000000-0000-4000-8000-000000000904",
        scope=_SCOPE,
        provider_binding=_BINDING,
    )
    return {item.item_id for item in candidate.items}


def _sent_refs(case: SemanticCase, approved: set[str]) -> frozenset[str]:
    refs = review_packet_content_refs(semantic_case_to_prepared_payload(case, approved))
    assert refs is not None
    return refs


def _blockers(included: frozenset[str]) -> tuple[str, ...]:
    """What a completed reduced review that sent exactly *included* proves about ``fnd(1)``."""

    case = _case()
    check = CheckRecordedPayload(
        mode=CheckMode.SEMANTIC_REQUIRED,
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
        subject_frontier=case.frontier,
        verdict=CheckVerdict.NO_ISSUE_DETECTED,
        returned_finding_ids=(),
        suppressed_count=0,
        coverage=_coverage(_SCOPE_REDUCED),
        semantic_status=SemanticStatus.SUCCEEDED,
        semantic_reason=SemanticReason.SEMANTIC_COMPLETED,
        engine_version="0.1.0",
        projection_version="yoetz/0.1.0",
        semantic_provenance=_provenance(),
        semantic_conclusion="no_material_discrepancy",
        semantic_included_refs=tuple(sorted(included, key=str.encode)) or None,
    )
    return resolution_blockers(_finding(), 4, check, frozenset(), proof_state=case.projection)


def test_a_repair_the_packet_actually_sent_resolves_the_finding() -> None:
    semantic = _expanded()
    assert semantic.omitted_reference_count > 0, "the scope must be reduced"
    sent = _sent_refs(semantic, _offered(semantic))
    assert {_REPAIR, str(clm(1))} <= sent
    assert _blockers(sent) == ()


def test_a_repair_omitted_as_not_selected_leaves_the_finding_open() -> None:
    """The evidence excerpt was not selected: its ref appears only in an omission row.

    The builder's reference closure still names it (that is what the first fix recorded), but the
    reviewer never saw its content, so the scope keeps blocking.
    """

    profile = ReviewContextProfile.CUSTOM
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    selection = ReviewSelectionPolicy(
        sections=selection.sections,
        excerpt_kinds=(),
        relevance=selection.relevance,
        include_finding_prose=selection.include_finding_prose,
        include_exact_command_text=selection.include_exact_command_text,
        max_timeline_items=selection.max_timeline_items,
        max_assessments=selection.max_assessments,
        max_change_observations=selection.max_change_observations,
        max_excerpts=selection.max_excerpts,
        max_omissions=selection.max_omissions,
        max_excerpt_bytes=selection.max_excerpt_bytes,
        max_total_excerpt_bytes=selection.max_total_excerpt_bytes,
    )
    semantic = _semantic(selection, profile)
    assert semantic.omitted_reference_count > 0
    omitted = {
        (row.subject_ref, row.reason)
        for row in semantic.packet.omissions
        if row.subject_ref == _REPAIR
    }
    assert omitted and all(reason == "not_selected" for _ref, reason in omitted)
    assert _REPAIR in semantic.frontier_refs, "mentioned by the omission row and by the result"

    sent = _sent_refs(semantic, _offered(semantic))
    assert _REPAIR not in sent
    assert _blockers(sent) == _OUTSIDE
    # What 7c2988f5 recorded, the pre-minimization reference closure, would have resolved it.
    assert _blockers(semantic.frontier_refs) == ()


def test_a_per_item_privacy_drop_of_the_repair_leaves_the_finding_open() -> None:
    """Privacy minimization withheld the repair excerpt (a per-item data-class decision)."""

    semantic = _expanded()
    offered = _offered(semantic)
    excerpts = {
        item.item_id
        for item in semantic.items
        if item.section == "excerpt" and item.source_ref == _REPAIR
    }
    assert excerpts and excerpts <= offered
    approved = offered - excerpts
    assert REVIEW_PACKET_ITEM_ID in approved

    sent = _sent_refs(semantic, approved)
    assert _REPAIR not in sent
    assert _blockers(sent) == _OUTSIDE
    assert _REPAIR in semantic.frontier_refs
    assert _blockers(semantic.frontier_refs) == ()


def test_only_a_readable_review_packet_yields_sent_references() -> None:
    assert review_packet_content_refs(b"not json") is None
    assert review_packet_content_refs(b'{"schema":"other"}') is None
    semantic = _expanded()
    document = semantic_case_to_prepared_payload(semantic, _offered(semantic))
    sent = review_packet_content_refs(document)
    assert sent is not None and sent <= semantic.frontier_refs
