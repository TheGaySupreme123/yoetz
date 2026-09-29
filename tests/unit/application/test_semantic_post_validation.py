from __future__ import annotations

from dataclasses import replace

import pytest

from builders.policy_cases import (
    BASE_COVERAGE,
    FRONTIER,
    clm,
    finding_record,
    fnd,
    make_case,
    obl,
)
from yoetz.application.check import (
    SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM,
    SEMANTIC_REJECTED_REF_OUTSIDE_CASE,
    SEMANTIC_REJECTED_SUBJECTS_OVER_LIMIT,
    SemanticJudgmentRejected,
    SemanticJudgmentReview,
    validate_semantic_judgment,
)
from yoetz.domain.findings import (
    Finding,
    FindingKind,
    FindingOrigin,
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


def test_rejection_counts_aggregate_and_reasons_sort_deterministically() -> None:
    """Repeated drops sum, and two reasons come back in one stable order.

    With a single rejection under a single reason, neither the counter nor the sort is observable;
    a mixed judgment pins both. Deterministic reproducibility is the point — the same judgment
    must always produce the same accounting.
    """

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
        (
            _challenge(_INVENTED, summary="First invented"),
            hidden,
            _challenge(str(clm(2)), summary="Real"),
        ),
    )

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert [candidate.summary for candidate in review.candidates] == ["Real"]
    assert review.challenges_returned == 3
    assert review.challenges_rejected == 2
    # Sorted by ASCII reason token, so hidden_source_claim precedes ref_outside_case.
    assert review.rejected_by_reason == (
        (SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM, 1),
        (SEMANTIC_REJECTED_REF_OUTSIDE_CASE, 1),
    )
    assert review.challenges_returned == len(review.candidates) + review.challenges_rejected


def test_review_rejects_accounting_that_does_not_add_up() -> None:
    """The value type owns the reconciliation the diagnostic reports."""

    with pytest.raises(ValueError, match="semantic_judgment_review_invalid"):
        SemanticJudgmentReview((), 3, ((SEMANTIC_REJECTED_REF_OUTSIDE_CASE, 1),))


def _recorded_semantic_finding(number: int, *subjects: str) -> Finding:
    return Finding(
        finding_id=fnd(number),
        kind=FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        origin=FindingOrigin.SEMANTIC_MODEL_DERIVED,
        priority=1,
        summary="Required llvmlite 0.46.0 verification remains open.",
        detail="Make one concrete authorized attempt to obtain llvmlite 0.46.0.",
        subject_refs=tuple(sorted(subjects)),  # type: ignore[arg-type]
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=FRONTIER,
        coverage=BASE_COVERAGE,
        provenance=_provenance(),
    )


def test_challenge_citing_a_recorded_prior_finding_resolves_to_its_subjects() -> None:
    """citable_refs offers every recorded fnd_ id, so citing one must not drop the challenge.

    Regression for the httpx/katex v2 rounds (issue #905): the reviewer named the earlier finding
    its re-raise concerned, the fence only knew this check's local findings, and the challenge
    was dropped as ``ref_outside_case`` with ``semantic_challenges_rejected`` left on the check.
    """

    prior = _recorded_semantic_finding(1, str(obl(7)), str(clm(1)))
    case = make_case(
        findings={fnd(1): finding_record(prior, 5)},
        extra_refs=(clm(1), clm(2), obl(7)),
    )
    judgment = SemanticJudgment(
        "challenges_returned",
        (_challenge(str(fnd(1)), str(clm(2)), summary="Still open after newer material"),),
    )

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert review.rejected_by_reason == ()
    assert len(review.candidates) == 1
    assert review.candidates[0].subject_refs == tuple(
        sorted((clm(1), clm(2), obl(7)), key=lambda ref: str(ref).encode())
    )


def test_cited_prior_finding_must_be_readable_and_inside_the_frozen_fence() -> None:
    """Only the fnd_ lookup widened: an unreadable, unknown, or out-of-fence finding still drops."""

    prior = _recorded_semantic_finding(1, str(clm(1)))
    redacted = replace(finding_record(prior, 5), payload=None, redacted=True)
    unreadable = make_case(findings={fnd(1): redacted}, extra_refs=(clm(1), clm(2)))
    unknown = make_case(extra_refs=(clm(1), clm(2)))
    for case, cited in ((unreadable, fnd(1)), (unknown, fnd(9))):
        review = validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("challenges_returned", (_challenge(str(cited), str(clm(2))),)),
            _provenance(),
            expected_frontier=case.frontier,
        )
        assert review.candidates == ()
        assert review.rejected_by_reason == ((SEMANTIC_REJECTED_REF_OUTSIDE_CASE, 1),)


def test_challenge_whose_resolved_subjects_exceed_the_finding_bound_is_counted_not_fatal() -> None:
    """Several cited findings can union past a finding's 64 subjects; that costs one challenge."""

    wide = tuple(str(clm(number)) for number in range(1, 41))
    wider = tuple(str(clm(number)) for number in range(41, 81))
    first = _recorded_semantic_finding(1, *wide)
    second = _recorded_semantic_finding(2, *wider)
    case = make_case(
        findings={fnd(1): finding_record(first, 5), fnd(2): finding_record(second, 6)},
        extra_refs=(clm(90),),
    )
    judgment = SemanticJudgment(
        "challenges_returned",
        (
            _challenge(str(fnd(1)), str(fnd(2)), summary="Too wide"),
            _challenge(str(clm(90)), summary="Kept"),
        ),
    )

    review = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert [candidate.summary for candidate in review.candidates] == ["Kept"]
    assert review.rejected_by_reason == ((SEMANTIC_REJECTED_SUBJECTS_OVER_LIMIT, 1),)
