"""Transport-neutral summary weakening checks shared by CLI and MCP."""

from __future__ import annotations

from yoetz.mcp.summaries import render_safe_compact_summary


def test_human_summary_is_weaker_than_structured_output() -> None:
    secret = "raw-user-payload-must-not-appear"
    check = {
        "ok": True,
        "verdict": "incomplete_check",
        "findings": [{"summary": secret}],
        "suppressed_count": "3",
        "semantic_status": "not_configured",
        "semantic_reason": "provider_not_configured",
        "result_frontier": {"sequence": "7", "head_digest": "sha256:" + "0" * 64},
    }
    summary = render_safe_compact_summary(check)
    assert summary == (
        "Check verdict: incomplete_check; findings returned: 1; suppressed: 3; semantic "
        "status/reason: not_configured/provider_not_configured; frontier: 7."
    )
    assert secret not in summary
    assert len(summary.encode("ascii")) <= 512

    status = {
        "ok": True,
        "view": "compact",
        "result_frontier": {"sequence": "9"},
        "page": {
            "items": [
                {
                    "freshness": "stale_after_material_change",
                    "open_obligation_count": "2",
                    "unresolved_finding_count": "4",
                    "task_title": secret,
                }
            ]
        },
        "gaps": ["redacted", "unknown"],
    }
    status_summary = render_safe_compact_summary(status)
    assert status_summary == (
        "Status view: compact; frontier: 9; freshness: stale_after_material_change; open "
        "obligations: 2; unresolved findings: 4; reported gaps: 2."
    )
    assert secret not in status_summary

    receipt = {
        "ok": True,
        "receipt_id": "rcp_00000000-0000-4000-8000-000000000001",
        "conclusion": "insufficient_coverage",
        "subject_frontier": {"sequence": "11"},
        "coverage": {"known_gaps": ["redacted_gap"]},
        "suppressed_finding_count": 5,
        "human_text": secret,
    }
    receipt_summary = render_safe_compact_summary(receipt)
    assert receipt_summary == (
        "Receipt conclusion: insufficient_coverage; frontier: 11; coverage limitations: 1; "
        "suppressed findings: 5."
    )
    assert secret not in receipt_summary
