from __future__ import annotations

from dataclasses import replace

import pytest

from builders.policy_cases import BASE_COVERAGE, clm, make_case
from yoetz.application.check import (
    SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM,
    SEMANTIC_REJECTED_REF_OUTSIDE_CASE,
    SemanticJudgmentRejected,
    validate_semantic_judgment,
)
from yoetz.domain.findings import (
    FindingKind,
    SamplingParams,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.ports.semantic import ReviewerChallenge, SemanticJudgment
from yoetz.protocol.models import SemanticReason, SemanticStatus

_DIGEST = "sha256:" + "a" * 64
_INVENTED = "clm_20000000-0000-4000-8000-000000000099"


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
        semantic_attempt_id="att_20000000-0000-4000-8000-000000000001",
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        privacy_receipt_id="egr_20000000-0000-4000-8000-000000000001",
        status=SemanticStatus.SUCCEEDED,
        reason=SemanticReason.SEMANTIC_COMPLETED,
        provider_request_id="fake-1",
        egress_authorization_id="aut_20000000-0000-4000-8000-000000000001",
        request_commitment="hmac-sha256:" + "b" * 64,
    )


def _challenge(*refs: str, summary: str = "Evidence gap") -> ReviewerChallenge:
    return ReviewerChallenge(
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        summary,
        tuple(sorted(refs)),
        "The claim lacks a recorded basis.",
        "The claim may remain unresolved.",
        "Main agent: provide evidence for the claim.",
        "provide_evidence",
        "The missing material may exist outside the case.",
    )


def test_semantic_judgment_accepts_only_frozen_refs_and_derives_policy() -> None:
    case = make_case(extra_refs=(clm(1),))
    judgment = SemanticJudgment("challenges_returned", (_challenge(str(clm(1))),))

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert len(review.candidates) == 1
    assert review.candidates[0].subject_refs == (clm(1),)
    assert review.candidates[0].policy_id == "work-integrity"
    assert review.candidates[0].policy_version == "0.1.0"
    assert review.challenges_returned == 1
    assert review.rejected_by_reason == ()


def test_one_bad_challenge_does_not_discard_the_others() -> None:
    """The fence is per challenge, so an invented ref costs its own challenge and nothing else.

    Regression for the live failure: three returned challenges where the middle one cited an
    invented ref used to discard all three — and, because the raise escaped the coordinator, the
    entire check with them.
    """

    case = make_case(extra_refs=(clm(1), clm(2)))
    judgment = SemanticJudgment(
        "challenges_returned",
        (
            _challenge(str(clm(1)), summary="First"),
            _challenge(_INVENTED, summary="Second"),
            _challenge(str(clm(2)), summary="Third"),
        ),
    )

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert [candidate.summary for candidate in review.candidates] == ["First", "Third"]
    assert review.challenges_returned == 3
    assert review.rejected_by_reason == ((SEMANTIC_REJECTED_REF_OUTSIDE_CASE, 1),)
    assert review.challenges_rejected == 1


def test_hidden_source_claim_is_counted_and_only_costs_its_own_challenge() -> None:
    """The "nothing changed" claim over a withheld basis is still refused, one challenge at a time."""

    case = make_case(
        extra_refs=(clm(1), clm(2)),
        coverage_overrides={clm(1): replace(BASE_COVERAGE, known_gaps=("missing_ref",))},
    )
    hidden = ReviewerChallenge(
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        "Claims no change",
        (str(clm(1)),),
        "The file is unchanged.",
        "Nothing was modified.",
        "Main agent: no further action required.",
        "state_unresolved_limitation",
        "The excerpt was never disclosed.",
    )
    judgment = SemanticJudgment(
        "challenges_returned",
        (hidden, _challenge(str(clm(2)), summary="Real")),
    )

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert [candidate.summary for candidate in review.candidates] == ["Real"]
    assert review.rejected_by_reason == ((SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM, 1),)


def test_structural_failure_raises_the_narrow_rejection_the_commit_path_catches() -> None:
    case = make_case(extra_refs=(clm(1),))

    with pytest.raises(SemanticJudgmentRejected, match="semantic_judgment_invalid"):
        validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("challenges_returned", (_challenge(str(clm(1))),)),
            _provenance(),
            expected_frontier=type(case.frontier)(99, "sha256:" + "c" * 64),
        )


def test_no_material_discrepancy_returns_no_semantic_candidate() -> None:
    case = make_case(extra_refs=(clm(1),))

    review = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("no_material_discrepancy", ()),
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert review.candidates == ()
    assert review.challenges_returned == 0
    assert review.rejected_by_reason == ()
