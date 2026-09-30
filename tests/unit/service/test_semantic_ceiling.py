"""Planning a review case below the channel ceiling (issue #907 Phase 1b)."""

from __future__ import annotations

from dataclasses import replace

from builders.policy_cases import (
    claim_record,
    clm,
    evd,
    evidence_record,
    make_case,
    plan_record,
)
from builders.privacy_policies import minimal_external_policy
from yoetz.application.egress import _within_excerpt_limits  # pyright: ignore[reportPrivateUsage]
from yoetz.application.semantic_case import (
    build_semantic_case,
    semantic_case_to_prepared_payload,
)
from yoetz.domain.events import (
    ClaimKind,
    ClaimRecordedPayload,
    EvidenceKind,
    EvidenceRecordedPayload,
    PlanPublishedPayload,
)
from yoetz.domain.privacy import EgressChannel, ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.values import EvidenceId, timestamp_from_string
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.ports.semantic import SemanticCase
from yoetz.protocol.coverage import EvidenceImmutability
from yoetz.service.semantic_ceiling import (
    CEILING_PLANNING_GAP,
    MAX_CEILING_PLANNING_ROUNDS,
    channel_prepared_limit,
    plan_under_channel_ceiling,
    with_ceiling_planning_gap,
)

_LIMIT = 262_144
_CURRENT = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)


def _case(description: str, count: int = 64) -> DeterministicCase:
    evidence: dict[EvidenceId, EvidenceProjectionRecord] = {}
    for index in range(1, count + 1):
        evidence[evd(index)] = evidence_record(
            EvidenceRecordedPayload(
                evidence_id=evd(index),
                evidence_kind=EvidenceKind.TEST_RESULT,
                strength=EvidenceImmutability.METADATA_ONLY,
                observed_at=timestamp_from_string("2026-07-01T00:00:00.000Z"),
                description=description,
            ),
            index + 3,
        )
    claim = claim_record(
        ClaimRecordedPayload(
            clm(1), ClaimKind.COMPLETION, "Work is complete", tuple(evidence), obligation_refs=()
        ),
        3,
    )
    return make_case(
        plans={1: plan_record(PlanPublishedPayload(1, "Ship it", ()), 1)},
        claims={clm(1): claim},
        evidence=evidence,
        extra_refs=(clm(1), *evidence),
    )


def _builder(case: DeterministicCase):  # noqa: ANN202 - local test factory
    def build(selection: ReviewSelectionPolicy, gaps: tuple[str, ...] = ()) -> SemanticCase:
        return build_semantic_case(
            case_id="cas_90700000-0000-4000-8000-000000000002",
            frozen_case=case,
            dependency_digest="sha256:" + "b" * 64,
            findings=(),
            review_context_profile=ReviewContextProfile.EXPANDED,
            review_selection=selection,
            policy_id="pvy_90700000-0000-4000-8000-000000000001",
            policy_version="1",
            captured_content_gaps=gaps,
        )

    return build


def _prepared(case: SemanticCase) -> bytes:
    return semantic_case_to_prepared_payload(case, {item.item_id for item in case.items})


def _plan(case: DeterministicCase, limit: int) -> tuple[SemanticCase, ReviewSelectionPolicy, int]:
    build = _builder(case)
    return plan_under_channel_ceiling(
        build(_CURRENT),
        _CURRENT,
        limit,
        lambda selection: build(selection, with_ceiling_planning_gap(())),
    )


def test_the_limit_is_the_narrowest_ceiling_and_never_above_the_disclosure_bound() -> None:
    policy = minimal_external_policy()

    def with_llm(max_bytes: int, max_tokens: int):  # noqa: ANN202
        return replace(
            policy,
            channel_policies=tuple(
                replace(channel, max_bytes=max_bytes, max_tokens=max_tokens)
                if channel.channel is EgressChannel.LLM_INFERENCE
                else channel
                for channel in policy.channel_policies
            ),
        )

    assert channel_prepared_limit(with_llm(262_144, 65_536)) == 262_144
    assert channel_prepared_limit(with_llm(262_144, 4_096)) == 16_384
    assert channel_prepared_limit(with_llm(0, 1_000)) == 4_000
    # Unset or high ceilings still plan below the largest disclosure egress can prepare.
    assert channel_prepared_limit(with_llm(0, 0)) == 262_144
    assert channel_prepared_limit(with_llm(0, 100_000)) == 262_144


def test_a_case_over_the_ceiling_is_rebuilt_below_it_with_the_reduction_disclosed() -> None:
    case = _case('"' * 8_000)
    unplanned = _builder(case)(_CURRENT)
    # Without planning, egress would refuse this whole packet.
    assert len(_prepared(unplanned)) > _LIMIT

    planned, selection, rounds = _plan(case, _LIMIT)

    prepared = _prepared(planned)
    assert len(prepared) <= _LIMIT
    assert _within_excerpt_limits(prepared, _CURRENT)
    assert 1 <= rounds <= MAX_CEILING_PLANNING_ROUNDS
    assert CEILING_PLANNING_GAP in planned.packet.coverage.known_gaps
    # A narrowing of the approved selection, never a widening, and still more than 16 excerpts.
    assert selection.max_total_excerpt_bytes < _CURRENT.max_total_excerpt_bytes
    assert selection.max_excerpts == _CURRENT.max_excerpts
    assert 16 < len(planned.packet.targeted_excerpts) < len(unplanned.packet.targeted_excerpts)
    # A replay or recovery builds the identical case.
    again, _, _ = _plan(case, _LIMIT)
    assert again.case_digest == planned.case_digest


def test_a_case_within_the_ceiling_is_left_exactly_as_built() -> None:
    case = _case("test run: 12 passed, 0 failed", count=40)
    built = _builder(case)(_CURRENT)

    planned, selection, rounds = _plan(case, _LIMIT)

    assert (rounds, selection) == (0, _CURRENT)
    assert planned.case_digest == built.case_digest
    assert CEILING_PLANNING_GAP not in planned.packet.coverage.known_gaps
    assert len(planned.packet.targeted_excerpts) == 40


def test_a_ceiling_nothing_fits_under_is_left_for_egress_to_deny() -> None:
    case = _case('"' * 8_000)

    planned, selection, rounds = _plan(case, 1_024)

    assert rounds == 1
    assert selection.max_excerpts == 0
    assert planned.packet.targeted_excerpts == ()
    assert len(_prepared(planned)) > 1_024
