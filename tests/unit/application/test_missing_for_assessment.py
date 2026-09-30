"""Classification and delivery of what an ``insufficient_packet`` review named (issue #907)."""

from __future__ import annotations

from dataclasses import replace

from yoetz.application.missing_for_assessment import unsuppliable_missing_kinds
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
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
