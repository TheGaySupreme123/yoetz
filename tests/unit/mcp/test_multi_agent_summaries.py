"""Compact MCP text distinguishes lineage facts and advice without copying project content."""

from __future__ import annotations

from yoetz.mcp.summaries import summary_for_check, summary_for_status


def test_project_summary_carries_generation_and_counts_without_content() -> None:
    text = summary_for_status(
        {
            "view": "project",
            "head_frontier": {"sequence": "1", "head_digest": "sha256:" + "a" * 64},
            "page": {
                "project_id": "prj_59000000-0000-4000-8000-000000000001",
                "membership_generation": "3",
                "grant_state": "active",
                "title": "private project title",
                "description": "private project description",
                "members": [{"task_title": "private task"}],
                "lineage": {"children": [], "annotations": [{"origin": "host_observed"}]},
                "detections": [{"private_path": "/secret/file"}],
                "coverage": [{"task_id": "tsk_59000000-0000-4000-8000-000000000002"}],
                "receipts": [],
                "next_cursor": "private cursor",
            },
        }
    )
    assert "generation: 3; grant: active; members: 1" in text
    assert "detections: 1; receipts: 0; children: 0; host annotations: 1" in text
    assert "coverage: 1" in text
    assert "More pages available" in text
    assert "private" not in text and "/secret" not in text
    assert len(text.encode()) <= 512


def test_hostile_project_grant_does_not_crash_or_leak() -> None:
    text = summary_for_status(
        {
            "view": "project",
            "page": {"grant_state": {"private": "secret"}},
        }
    )
    assert "grant: unavailable" in text
    assert "secret" not in text


def test_check_summary_marks_preview_and_non_verdict_advice() -> None:
    text = summary_for_check(
        {
            "verdict": "no_issue_detected",
            "findings": [],
            "suppressed_count": "0",
            "semantic_status": "not_requested",
            "semantic_reason": "deterministic_mode",
            "children": {"label": "preview", "items": [{"acceptance": "pending"}]},
            "advisory_notes": [{"kind": "live_member_present"}],
        }
    )
    assert "check verdict: no_issue_detected" in text
    assert "children (preview): 1" in text
    assert "project advice (non-verdict): 1" in text
    assert len(text.encode()) <= 512
