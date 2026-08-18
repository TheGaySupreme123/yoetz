"""The text summary is an authoring fallback: it must seed the next request (issue #279)."""

from __future__ import annotations

from yoetz.mcp.summaries import render_safe_compact_summary

_HEAD = "sha256:" + "1" * 64
_TASK = "tsk_00000000-0000-4000-8000-000000000001"
_SESSION = "ses_00000000-0000-4000-8000-000000000002"
_WRITER = "wri_00000000-0000-4000-8000-000000000003"
_FINDING_1 = "fnd_00000000-0000-4000-8000-000000000004"
_FINDING_2 = "fnd_00000000-0000-4000-8000-000000000005"


def test_start_summary_carries_the_identifiers_publish_work_requires() -> None:
    summary = render_safe_compact_summary(
        {
            "ok": True,
            "outcome": "created",
            "task_id": _TASK,
            "session_id": _SESSION,
            "writer_id": _WRITER,
            "frontier": {"sequence": "1", "head_digest": _HEAD},
        }
    )
    assert summary == (
        f"Operation outcome: created; task: {_TASK}; session: {_SESSION}; "
        f"writer: {_WRITER}; frontier: 1; head_digest: {_HEAD}."
    )
    assert len(summary.encode("ascii")) <= 512


def test_publish_work_summary_carries_the_new_head_digest() -> None:
    summary = render_safe_compact_summary(
        {
            "ok": True,
            "outcome": "recorded",
            "accepted_events": [{"event_id": "evt_x"}],
            "result_frontier": {"sequence": "4", "head_digest": _HEAD},
        }
    )
    assert summary == (
        f"Operation outcome: recorded; accepted events: 1; frontier: 4; head_digest: {_HEAD}."
    )


def test_malformed_identifiers_and_digests_are_never_admitted() -> None:
    hostile = "ses_../../etc/passwd or a very long injected user string"
    summary = render_safe_compact_summary(
        {
            "ok": True,
            "outcome": "created",
            "task_id": "not-an-id",
            "session_id": hostile,
            "writer_id": 7,
            "frontier": {"sequence": "1", "head_digest": "sha256:not-hex"},
        }
    )
    assert summary == "Operation outcome: created; frontier: 1."
    assert hostile not in summary


def test_genesis_head_digest_is_admitted() -> None:
    summary = render_safe_compact_summary(
        {
            "ok": True,
            "outcome": "created",
            "frontier": {"sequence": "0", "head_digest": "genesis"},
        }
    )
    assert summary == "Operation outcome: created; frontier: 0; head_digest: genesis."


def test_check_summary_carries_finding_ids_required_for_respond() -> None:
    summary = render_safe_compact_summary(
        {
            "verdict": "action_required",
            "findings": [{"finding_id": _FINDING_1}, {"finding_id": _FINDING_2}],
            "suppressed_count": "0",
            "semantic_status": "succeeded",
            "semantic_reason": "semantic_completed",
            "result_frontier": {"sequence": "7", "head_digest": _HEAD},
        }
    )
    assert f"finding IDs: {_FINDING_1}, {_FINDING_2};" in summary
    assert len(summary.encode("ascii")) <= 512


def test_check_summary_rejects_hostile_finding_identifiers() -> None:
    hostile = "fnd_../../ledger-content"
    summary = render_safe_compact_summary(
        {
            "verdict": "action_required",
            "findings": [{"finding_id": hostile}],
            "suppressed_count": "0",
            "semantic_status": "succeeded",
            "semantic_reason": "semantic_completed",
            "result_frontier": {"sequence": "7", "head_digest": _HEAD},
        }
    )
    assert hostile not in summary


def test_check_summary_bounds_finding_ids_with_an_honest_remainder() -> None:
    finding_ids = [f"fnd_00000000-0000-4000-8000-{index:012d}" for index in range(1, 101)]
    summary = render_safe_compact_summary(
        {
            "verdict": "action_required",
            "findings": [{"finding_id": finding_id} for finding_id in finding_ids],
            "suppressed_count": "0",
            "semantic_status": "succeeded",
            "semantic_reason": "semantic_completed",
            "result_frontier": {"sequence": "7", "head_digest": _HEAD},
        }
    )
    assert f"finding IDs: {finding_ids[0]}" in summary
    assert "+" in summary and "more;" in summary
    assert len(summary.encode("ascii")) <= 512
