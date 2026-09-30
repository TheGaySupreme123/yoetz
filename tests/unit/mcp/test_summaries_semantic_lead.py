"""MCP check summaries lead with the AI-powered-review-not-requested limitation."""

from __future__ import annotations

from yoetz.mcp.summaries import summary_for_check


def test_summary_for_deterministic_only_leads_with_semantic_not_requested() -> None:
    text = summary_for_check(
        {
            "verdict": "no_issue_detected",
            "findings": [],
            "suppressed_count": "0",
            "semantic_status": "not_requested",
            "semantic_reason": "deterministic_mode",
            "result_frontier": {"sequence": "3", "head_digest": "sha256:" + "a" * 64},
        }
    )
    assert text.startswith("AI-powered review not requested;")
    assert "local-only check verdict: no_issue_detected" in text


def test_capacity_summary_names_non_dispatch_and_bounded_next_step() -> None:
    text = summary_for_check(
        {
            "verdict": "incomplete_check",
            "findings": [],
            "suppressed_count": "0",
            "semantic_status": "failed",
            "semantic_reason": "case_capacity_exceeded",
            "result_frontier": {"sequence": "3", "head_digest": "sha256:" + "a" * 64},
        }
    )
    assert "Continuation: semantic_capacity_exceeded." in text
    assert "AI-powered review status/reason: failed/case_capacity_exceeded" in text
    assert "sha256:" + "a" * 64 in text
    assert "No provider attempt" not in text
    assert len(text.encode("ascii")) <= 512


def test_check_summary_states_why_the_check_time_change_was_unavailable() -> None:
    text = summary_for_check(
        {
            "verdict": "no_issue_detected",
            "findings": [],
            "suppressed_count": "0",
            "semantic_status": "succeeded",
            "semantic_reason": "completed",
            "result_frontier": {"sequence": "3", "head_digest": "sha256:" + "a" * 64},
            "coverage": {
                "known_gaps": [
                    "check_time_change_unavailable",
                    "check_time_change_unavailable_changed_during_capture",
                ]
            },
        }
    )
    assert "The check-time change was unavailable: the working tree kept changing" in text
    assert len(text.encode("ascii")) <= 512
