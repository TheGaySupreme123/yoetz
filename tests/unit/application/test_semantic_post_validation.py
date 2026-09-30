from __future__ import annotations

from dataclasses import replace

import pytest

from builders.policy_cases import (
    BASE_COVERAGE,
    FRONTIER,
    act,
    clm,
    evt,
    finding_record,
    fnd,
    make_case,
    obl,
    record,
    res,
)
from yoetz.application.check import (
    SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM,
    SEMANTIC_REJECTED_REF_OUTSIDE_CASE,
    SEMANTIC_REJECTED_SUBJECTS_OVER_LIMIT,
    SemanticJudgmentRejected,
    SemanticJudgmentReview,
    validate_semantic_judgment,
)
from yoetz.domain.events import (
    ResponseDisposition,
    ResponseRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import (
    Finding,
    FindingKind,
    FindingOrigin,
    SamplingParams,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.ports.semantic import PriorFindingVerdict, ReviewerChallenge, SemanticJudgment
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


def _verdict(number: int, kind: str, *refs: str) -> PriorFindingVerdict:
    return PriorFindingVerdict(str(fnd(number)), kind, tuple(sorted(refs)))  # type: ignore[arg-type]


def _dialogue_case() -> DeterministicCase:
    """kea shape: a real defect raised at 5, repaired with a regression result recorded at 9."""

    older = record(
        ResultRecordedPayload(res(1), act(1), ResultOutcome.SUCCESS, summary="before"), 3
    )
    repair = record(
        ResultRecordedPayload(res(2), act(2), ResultOutcome.SUCCESS, summary="regression"), 9
    )
    rejected = ResponseRecordedPayload(
        finding_id=fnd(3),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.REJECTED,
        reason="The task requires plain text without a tail under Ascii.",
    )
    return make_case(
        results={res(1): older, res(2): repair},
        findings={
            fnd(1): finding_record(_recorded_semantic_finding(1, str(obl(1))), 5),
            fnd(2): finding_record(_recorded_semantic_finding(2, str(obl(2))), 6),
            fnd(3): finding_record(_recorded_semantic_finding(3, str(obl(3))), 7),
            fnd(4): finding_record(
                replace(
                    _recorded_semantic_finding(4, str(obl(4))),
                    origin=FindingOrigin.DETERMINISTIC,
                    provenance=None,
                ),
                8,
            ),
        },
        responses={fnd(3): record(rejected, 8)},
        extra_refs=(obl(1), obl(2), obl(3), obl(4)),
    )


def test_prior_finding_rulings_are_admitted_only_with_their_own_support() -> None:
    """A hallucinated ``fixed`` must not close a real defect (issue #905)."""

    case = _dialogue_case()
    judgment = SemanticJudgment(
        "insufficient_packet",
        (),
        (
            _verdict(1, "fixed", str(res(2))),  # cites the repair recorded after the finding
            _verdict(2, "fixed", str(res(1))),  # cites only material older than the finding
            _verdict(3, "withdrawn"),  # accepts a readable rejected response
            _verdict(4, "still_present", str(obl(4))),  # a local finding is not the reviewer's
        ),
    )

    review = validate_semantic_judgment(
        case, (), judgment, _provenance(), expected_frontier=case.frontier
    )

    assert [(str(item.finding_id), item.verdict) for item in review.verdicts] == [
        (str(fnd(1)), "fixed"),
        (str(fnd(2)), "unassessable"),
        (str(fnd(3)), "withdrawn"),
    ]
    assert review.verdicts_unsupported == 2
    assert review.candidates == ()


def test_rulings_without_cited_material_or_a_rejection_to_accept_are_unassessable() -> None:
    case = _dialogue_case()
    judgment = SemanticJudgment(
        "no_material_discrepancy",
        (),
        (
            _verdict(1, "still_present"),
            _verdict(2, "withdrawn"),
            _verdict(3, "unassessable"),
            _verdict(9, "fixed", str(res(2))),
        ),
    )

    review = validate_semantic_judgment(
        case, (), judgment, _provenance(), expected_frontier=case.frontier
    )

    assert [(str(item.finding_id), item.verdict) for item in review.verdicts] == [
        (str(fnd(1)), "unassessable"),
        (str(fnd(2)), "unassessable"),
        (str(fnd(3)), "unassessable"),
    ]
    # Two reduced, one outside the fence; an honest unassessable is not counted.
    assert review.verdicts_unsupported == 3


_FOREIGN = "evd_99999999-9999-4999-8999-999999999999"


def _review(judgment: SemanticJudgment, **fence: frozenset[str]) -> SemanticJudgmentReview:
    case = _dialogue_case()
    return validate_semantic_judgment(
        case, (), judgment, _provenance(), expected_frontier=case.frontier, **fence
    )


def _rulings(review: SemanticJudgmentReview) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(str(item.finding_id), item.verdict, item.cited_refs) for item in review.verdicts]


def test_a_ruling_that_loses_a_cited_ref_is_unassessable_never_silently_dropped() -> None:
    """A dropped ruling would let its finding close by silence (review of #905)."""

    review = _review(
        SemanticJudgment(
            "no_material_discrepancy",
            (),
            (
                _verdict(1, "still_present", str(obl(1)), _FOREIGN),
                _verdict(2, "fixed", str(res(2)), _FOREIGN),
            ),
        )
    )

    assert _rulings(review) == [
        (str(fnd(1)), "unassessable", (str(obl(1)),)),
        (str(fnd(2)), "unassessable", (str(res(2)),)),
    ]
    assert review.verdicts_unsupported == 2


def test_repeated_rulings_on_one_finding_are_counted_and_conflicts_are_unassessable() -> None:
    review = _review(
        SemanticJudgment(
            "insufficient_packet",
            (),
            (
                _verdict(1, "fixed", str(res(2))),
                _verdict(1, "still_present", str(obl(1))),
                _verdict(2, "still_present", str(obl(2))),
                _verdict(2, "still_present", str(obl(2))),
            ),
        )
    )

    assert _rulings(review) == [
        (str(fnd(1)), "unassessable", ()),
        (str(fnd(2)), "still_present", (str(obl(2)),)),
    ]
    assert review.verdicts_unsupported == 2


def test_withdrawn_needs_a_rejection_not_an_acknowledgement() -> None:
    acknowledged = ResponseRecordedPayload(
        finding_id=fnd(1),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.ACKNOWLEDGED,
        evidence_refs=(res(2),),
    )
    base = _dialogue_case()
    case = replace(
        base,
        projection=replace(
            base.projection,
            responses={**base.projection.responses, fnd(1): record(acknowledged, 10)},
        ),
    )
    review = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("no_material_discrepancy", (), (_verdict(1, "withdrawn"),)),
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert _rulings(review) == [(str(fnd(1)), "unassessable", ())]
    assert review.verdicts_unsupported == 1


def test_rulings_are_fenced_to_the_packet_the_reviewer_was_shown() -> None:
    """A ruling on a finding the prior-findings section never carried stays unassessable (never
    silence), and a ref the packet did not offer as citable cannot carry a ruling."""

    judgment = SemanticJudgment(
        "no_material_discrepancy",
        (),
        (_verdict(1, "fixed", str(res(2))), _verdict(2, "still_present", str(obl(2)))),
    )
    review = _review(
        judgment,
        prior_finding_refs=frozenset({str(fnd(1))}),
        citable_refs=frozenset({str(fnd(1)), str(obl(2))}),
    )

    # fnd(2) was not carried; res(2) was not citable, so fnd(1)'s fixed has nothing left.
    assert _rulings(review) == [
        (str(fnd(1)), "unassessable", ()),
        (str(fnd(2)), "unassessable", (str(obl(2)),)),
    ]
    assert review.verdicts_unsupported == 2


def test_a_fixed_ruling_on_a_finding_the_same_review_re_raises_is_unassessable() -> None:
    judgment = SemanticJudgment(
        "challenges_returned",
        (_challenge(str(fnd(1)), str(obl(1)), summary="The Map key collision remains"),),
        (_verdict(1, "fixed", str(res(2))),),
    )

    review = _review(judgment)

    assert [candidate.related_finding_ids for candidate in review.candidates] == [(fnd(1),)]
    assert _rulings(review) == [(str(fnd(1)), "unassessable", ())]
    assert review.verdicts_unsupported == 1


def test_rulings_the_normalizer_dropped_are_disclosed_by_the_fence() -> None:
    review = _review(
        SemanticJudgment(
            "no_material_discrepancy",
            (),
            (_verdict(1, "fixed", str(res(2))),),
            prior_finding_verdicts_dropped=2,
        )
    )

    assert _rulings(review) == [(str(fnd(1)), "fixed", (str(res(2)),))]
    assert review.verdicts_unsupported == 2


def _restatement_case(
    obligation_recorded_at: int,
    *,
    repaired: bool = False,
    resolved: bool = False,
    hidden: bool = False,
) -> DeterministicCase:
    """numba shape: one AI-powered finding on an obligation, recorded at sequence 5."""

    from builders.policy_cases import obligation_record
    from yoetz.domain.events import ObligationPublishedPayload, ObligationStatus

    obligation = obligation_record(
        ObligationPublishedPayload(
            obl(1), "Verify with llvmlite 0.46.0", "stencil tests pass", ObligationStatus.OPEN
        ),
        obligation_recorded_at,
    )
    repair = record(
        ResultRecordedPayload(res(2), act(2), ResultOutcome.SUCCESS, summary="regression"), 9
    )
    withheld = replace(BASE_COVERAGE, known_gaps=("captured_object_unavailable",))
    return make_case(
        obligations={obl(1): obligation},
        coverage_overrides={obl(1): withheld} if hidden else None,
        results={res(2): repair} if repaired else None,
        findings={
            fnd(1): finding_record(
                _recorded_semantic_finding(1, str(obl(1))),
                5,
                resolved_by_check_event_id=evt(8) if resolved else None,
            )
        },
    )


def test_a_re_raise_of_a_verified_resolved_finding_is_a_new_item_not_a_restatement() -> None:
    """Done stays done, and a re-raise after verified resolution is a #458 successor, minted.

    Suppressing it would hide a problem the reviewer found again behind a closed row: the
    receipt would read clean with nothing blocking (slice-4 verification D1)."""

    case = _restatement_case(obligation_recorded_at=2, resolved=True)
    review = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("challenges_returned", (_obligation_challenge(str(obl(1))),)),
        _provenance(),
        expected_frontier=case.frontier,
    )
    assert len(review.candidates) == 1
    assert review.restatements_suppressed == 0
    assert review.verdicts == ()


def _obligation_challenge(*refs: str) -> ReviewerChallenge:
    return replace(
        _challenge(*refs, summary="Required llvmlite 0.46.0 verification remains open"),
        finding_kind=FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
    )


def test_a_restatement_without_newer_material_is_seen_again_and_suppressed() -> None:
    """Three numba restatements must become one item, disclosed, never a silent drop."""

    case = _restatement_case(obligation_recorded_at=2)
    judgment = SemanticJudgment(
        "challenges_returned",
        (_obligation_challenge(str(obl(1))), _obligation_challenge(str(fnd(1)))),
    )

    review = validate_semantic_judgment(
        case, (), judgment, _provenance(), expected_frontier=case.frontier
    )

    assert review.candidates == ()
    assert review.restatements_suppressed == 2
    assert review.rejected_by_reason == ()  # a restatement is not a rejected challenge
    # Suppression never reads as absence: the open item is recorded as still present.
    assert _rulings(review) == [(str(fnd(1)), "still_present", (str(obl(1)),))]


def test_a_restatement_contradicts_an_explicit_fixed_and_leaves_terminal_items_alone() -> None:
    case = _restatement_case(obligation_recorded_at=2)
    repaired = _restatement_case(obligation_recorded_at=2, repaired=True)
    supported = validate_semantic_judgment(
        repaired,
        (),
        SemanticJudgment("no_material_discrepancy", (), (_verdict(1, "fixed", str(res(2))),)),
        _provenance(),
        expected_frontier=repaired.frontier,
    )
    assert _rulings(supported) == [(str(fnd(1)), "fixed", (str(res(2)),))]
    contradicted = validate_semantic_judgment(
        repaired,
        (),
        SemanticJudgment(
            "challenges_returned",
            (_obligation_challenge(str(obl(1))),),
            (_verdict(1, "fixed", str(res(2))),),  # a supported fixed, then restated anyway
        ),
        _provenance(),
        expected_frontier=repaired.frontier,
    )
    assert _rulings(contradicted) == [(str(fnd(1)), "unassessable", ())]
    assert contradicted.restatements_suppressed == 1
    # R3: the contradicted ruling is set aside on its own item, not an unsupported-ruling gap.
    assert (contradicted.verdicts_unsupported, contradicted.verdicts_set_aside) == (0, 1)

    not_done = ResponseRecordedPayload(
        finding_id=fnd(1),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.ACKNOWLEDGED_NOT_DONE,
        reason="Out of scope for this task.",
    )
    terminal = replace(
        case, projection=replace(case.projection, responses={fnd(1): record(not_done, 6)})
    )
    review = validate_semantic_judgment(
        terminal,
        (),
        SemanticJudgment("challenges_returned", (_obligation_challenge(str(obl(1))),)),
        _provenance(),
        expected_frontier=terminal.frontier,
    )
    assert review.candidates == () and review.restatements_suppressed == 1
    assert review.verdicts == ()  # a final item is never re-reviewed


def test_newer_material_or_another_kind_is_a_new_item_not_a_restatement() -> None:
    revised = _restatement_case(obligation_recorded_at=9)  # the obligation changed since
    other_kind = _restatement_case(obligation_recorded_at=2)
    judgments = (
        (revised, _obligation_challenge(str(obl(1)))),
        (other_kind, _challenge(str(obl(1)))),  # claim_without_admissible_evidence
    )
    for case, challenge in judgments:
        review = validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("challenges_returned", (challenge,)),
            _provenance(),
            expected_frontier=case.frontier,
        )
        assert len(review.candidates) == 1
        assert review.restatements_suppressed == 0


def test_a_hidden_source_claim_is_rejected_before_any_restatement_is_recorded() -> None:
    """D5: a challenge the fence rejects must not record ``still_present`` or a review round."""

    case = _restatement_case(obligation_recorded_at=2, hidden=True)
    hidden = replace(
        _obligation_challenge(str(obl(1))),
        discrepancy="The obligation is unchanged.",
    )
    review = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("challenges_returned", (hidden,)),
        _provenance(),
        expected_frontier=case.frontier,
    )
    assert review.rejected_by_reason == ((SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM, 1),)
    assert review.restatements_suppressed == 0
    assert review.verdicts == ()


def test_a_ruling_on_a_final_item_never_raises_the_gap_that_blocks_its_siblings() -> None:
    """R3: a ruling on an ``acknowledged_not_done`` item is set aside as a diagnostic only.

    Since unsupported rulings block every unruled open AI-powered finding on the check, counting
    it there would stall the siblings for no honesty gain. A ruling on an unknown target still
    counts as unsupported.
    """

    case = _dialogue_case()
    not_done = ResponseRecordedPayload(
        finding_id=fnd(2),
        finding_frontier=FRONTIER,
        disposition=ResponseDisposition.ACKNOWLEDGED_NOT_DONE,
        reason="The index is unreachable from this sandbox; out of scope here.",
    )
    final = replace(
        case,
        projection=replace(
            case.projection,
            responses={**case.projection.responses, fnd(2): record(not_done, 8)},
        ),
    )
    judgment = SemanticJudgment(
        "no_material_discrepancy",
        (),
        (_verdict(1, "fixed", str(res(2))), _verdict(2, "fixed", str(res(2)))),
    )
    review = validate_semantic_judgment(
        final, (), judgment, _provenance(), expected_frontier=final.frontier
    )
    assert _rulings(review) == [(str(fnd(1)), "fixed", (str(res(2)),))]
    assert (review.verdicts_unsupported, review.verdicts_set_aside) == (0, 1)

    unknown = validate_semantic_judgment(
        final,
        (),
        SemanticJudgment("no_material_discrepancy", (), (_verdict(9, "fixed", str(res(2))),)),
        _provenance(),
        expected_frontier=final.frontier,
    )
    assert (unknown.verdicts_unsupported, unknown.verdicts_set_aside) == (1, 0)


def test_a_narrower_challenge_is_its_own_item_not_a_restatement() -> None:
    """Greptile P1 on #943: a challenge about one subject of a broader recorded finding is a
    distinct issue (the receipt keys issues by their exact subject set). Suppressing it would
    lose its discrepancy and requested next step, which a ``still_present`` ruling cannot carry.
    """

    from builders.policy_cases import obligation_record
    from yoetz.domain.events import ObligationPublishedPayload, ObligationStatus

    obligations = {
        obl(number): obligation_record(
            ObligationPublishedPayload(
                obl(number), f"Obligation {number}", "criteria", ObligationStatus.OPEN
            ),
            2,
        )
        for number in (1, 2)
    }
    case = make_case(
        obligations=obligations,
        findings={
            fnd(1): finding_record(_recorded_semantic_finding(1, str(obl(1)), str(obl(2))), 5)
        },
    )
    narrower = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("challenges_returned", (_obligation_challenge(str(obl(1))),)),
        _provenance(),
        expected_frontier=case.frontier,
    )
    assert len(narrower.candidates) == 1
    assert narrower.candidates[0].subject_refs == (obl(1),)
    assert narrower.restatements_suppressed == 0
    assert narrower.verdicts == ()
    # The exact same subject set is still a restatement.
    same = validate_semantic_judgment(
        case,
        (),
        SemanticJudgment("challenges_returned", (_obligation_challenge(str(obl(1)), str(obl(2))),)),
        _provenance(),
        expected_frontier=case.frontier,
    )
    assert (len(same.candidates), same.restatements_suppressed) == (0, 1)
