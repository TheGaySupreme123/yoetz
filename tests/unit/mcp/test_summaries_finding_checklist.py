"""The status findings summary counts only to-dos (issue #905 re-verification R1)."""

from __future__ import annotations

from yoetz.mcp.summaries import summary_for_status


def _row(number: int, kind: str, state: str, rounds: str) -> dict[str, object]:
    return {
        "finding_id": f"fnd_59000000-0000-4000-8000-{number:012d}",
        "kind": kind,
        "todo_state": state,
        "review_rounds": rounds,
    }


def _summary(*rows: dict[str, object]) -> str:
    return summary_for_status(
        {
            "view": "findings",
            "head_frontier": {"sequence": "4", "head_digest": "sha256:" + "a" * 64},
            "page": {"items": list(rows), "next_cursor": None, "attempt_budget": "5"},
            "gaps": [],
        }
    )


def test_a_coverage_limitation_row_is_not_counted_as_a_to_do() -> None:
    text = _summary(_row(1, "ledger_stale_or_incomplete", "open", "7"))
    assert "to-do:" not in text
    assert "at budget" not in text


def test_actionable_rows_are_counted_and_unknown_kinds_are_ignored() -> None:
    text = _summary(
        _row(1, "completion_with_open_obligations", "open", "5"),
        _row(2, "ledger_stale_or_incomplete", "open", "9"),
        _row(3, "claim_without_admissible_evidence", "verified_resolved", "0"),
        _row(4, "not_a_kind", "open", "9"),
    )
    assert "to-do: open 1 (1 at budget 5), verified 1, not done 0, rejection accepted 0;" in text
    assert "not_a_kind" not in text
    assert len(text.encode()) <= 512
