"""Classification and delivery of what an ``insufficient_packet`` review named (issue #907)."""

from __future__ import annotations

from dataclasses import replace

from builders.policy_cases import act, evt, make_case, record, res
from builders.privacy_policies import local_only_policy, minimal_external_policy
from yoetz.application.missing_for_assessment import supplied_since, unsuppliable_missing_kinds
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    MissingForAssessmentItem,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.privacy import (
    EgressChannel,
    PrivacyPolicy,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.kernel.projections import PendingMissingForAssessment
from yoetz.mcp.summaries import summary_for_check
from yoetz.ports.semantic import MissingForAssessment, PriorFindingVerdict, SemanticJudgment
from yoetz.protocol.canonical import JsonValue
from yoetz.service import ready_composition

_CLAIM = "clm_10000000-0000-4000-8000-000000000001"


def test_expanded_selection_leaves_every_kind_suppliable() -> None:
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    assert unsuppliable_missing_kinds(selection, ()) == ()


def test_structural_and_withheld_selections_name_what_no_agent_action_can_carry() -> None:
    structural = ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL)
    assert unsuppliable_missing_kinds(structural, ()) == (
        "command_identity",
        "current_diff_for_path",
        "other",
        "plan_or_claim_text",
        "task_statement",
        "verification_output",
    )
    assisted = ReviewSelectionPolicy.for_profile(ReviewContextProfile.ASSISTED)
    # Assisted never carries exact command text.
    assert unsuppliable_missing_kinds(assisted, ()) == ("command_identity",)
    expanded = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    assert "verification_output" in unsuppliable_missing_kinds(expanded, ("evidence_excerpt",))
    narrow = replace(expanded, excerpt_kinds=("command", "failure", "test"))
    assert unsuppliable_missing_kinds(narrow, ()) == ("current_diff_for_path",)


def test_check_summary_names_missing_items_with_closed_tokens_only() -> None:
    envelope: dict[str, JsonValue] = {
        "verdict": "insufficient_coverage",
        "findings": [],
        "suppressed_count": "0",
        "semantic_status": "succeeded",
        "semantic_reason": "semantic_completed",
        "missing_for_assessment": [
            {
                "availability": "agent_suppliable",
                "kind": "verification_output",
                "target_refs": [_CLAIM],
            },
            {
                "availability": "structurally_unavailable_on_this_host",
                "kind": "command_identity",
                "target_refs": [],
            },
            {"availability": "agent_suppliable", "kind": "</script>", "target_refs": []},
        ],
    }
    text = summary_for_check(envelope)
    assert "missing for assessment: 2 (agent-suppliable: 1)" in text
    assert "verification_output=agent_suppliable" in text
    assert "command_identity=structurally_unavailable_on_this_host" in text
    assert "</script>" not in text
    assert len(text.encode("ascii")) <= 512


def test_durable_semantic_response_keeps_named_items_and_reads_legacy_bytes() -> None:
    judgment = SemanticJudgment(
        "insufficient_packet",
        (),
        missing_for_assessment=(
            MissingForAssessment("verification_output", (_CLAIM,), "The jest summary is absent."),
        ),
    )
    encoded = ready_composition._judgment_to_response_json(judgment)  # pyright: ignore[reportPrivateUsage]
    assert ready_composition._judgment_from_response_json(encoded) == judgment  # pyright: ignore[reportPrivateUsage]
    # Issue #905 rulings travel in the same durable response beside the named items.
    ruled = replace(
        judgment,
        prior_finding_verdicts=(
            PriorFindingVerdict("fnd_30000000-0000-4000-8000-000000000001", "unassessable", ()),
        ),
    )
    encoded_ruled = ready_composition._judgment_to_response_json(ruled)  # pyright: ignore[reportPrivateUsage]
    assert ready_composition._judgment_from_response_json(encoded_ruled) == ruled  # pyright: ignore[reportPrivateUsage]
    legacy: dict[str, JsonValue] = {"conclusion": "insufficient_packet", "reviewer_challenges": []}
    decoded = ready_composition._judgment_from_response_json(legacy)  # pyright: ignore[reportPrivateUsage]
    assert decoded == SemanticJudgment("insufficient_packet", ())


def test_only_agent_published_actions_and_results_answer_a_request() -> None:
    """Hook capture records every tool call; it never turns a still-missing item into supplied."""

    hook_action = ActionRecordedPayload(
        act(60),
        ActionKind.COMMAND,
        "Observed command via Claude Code",
        command="omitted:structural",
    )
    agent_action = ActionRecordedPayload(
        act(62), ActionKind.COMMAND, "Ran the unit tests", command="uv run pytest -q"
    )
    case = make_case(
        actions={act(60): record(hook_action, 60), act(62): record(agent_action, 62)},
        results={
            res(61): record(ResultRecordedPayload(res(61), act(60), ResultOutcome.SUCCESS), 61),
            res(63): record(ResultRecordedPayload(res(63), act(62), ResultOutcome.SUCCESS), 63),
        },
        extra_refs=(act(60), res(61), act(62), res(63)),
    )
    pending = PendingMissingForAssessment(
        evt(50),
        50,
        (
            MissingForAssessmentItem("command_identity", (), "agent_suppliable"),
            MissingForAssessmentItem("verification_output", (), "agent_suppliable"),
        ),
    )
    allowed = frozenset(str(ref) for ref in case.allowed_ids)
    assert supplied_since(case.projection, pending, allowed) == (
        tuple(sorted((str(act(62)), str(res(63))), key=str.encode)),
        (str(res(63)),),
    )


def test_packet_planning_ceiling_is_the_narrower_owner_channel_limit() -> None:
    """Issue #907: plan against max_bytes or max_tokens at four bytes per token, whichever binds."""

    policy = minimal_external_policy()
    llm = next(
        item for item in policy.channel_policies if item.channel is EgressChannel.LLM_INFERENCE
    )

    def with_limits(max_bytes: int, max_tokens: int) -> PrivacyPolicy:
        channel = replace(llm, max_bytes=max_bytes, max_tokens=max_tokens)
        return replace(
            policy,
            channel_policies=tuple(
                channel if item.channel is EgressChannel.LLM_INFERENCE else item
                for item in policy.channel_policies
            ),
        )

    ceiling = ready_composition._semantic_prepared_byte_ceiling  # pyright: ignore[reportPrivateUsage]
    assert ceiling(with_limits(100_000, 10_000)) == 40_000
    assert ceiling(with_limits(30_000, 10_000)) == 30_000
    assert ceiling(with_limits(0, 5_000)) == 20_000
    assert ceiling(with_limits(0, 0)) is None
    assert ceiling(local_only_policy()) is None
