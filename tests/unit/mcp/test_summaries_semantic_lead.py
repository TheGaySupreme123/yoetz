"""MCP check summaries lead with the semantic-not-requested limitation."""

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
    assert text.startswith("Semantic review not requested;")
    assert "deterministic-only check verdict: no_issue_detected" in text
