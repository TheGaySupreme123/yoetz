"""Tests for bounded, payload-free hook diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from yoetz.cli.hook_diagnostics import (
    hook_diagnostic_summary,
    record_hook_diagnostic,
    record_hook_timing,
)


def test_record_is_structural_and_owner_only(tmp_path: Path) -> None:
    record_hook_diagnostic("service_unavailable", "PostToolUse", _state=tmp_path)
    path = tmp_path / "observation/hook-diagnostics.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    assert set(row) == {"event", "reason", "ts"}
    assert row["event"] == "PostToolUse"
    assert row["reason"] == "service_unavailable"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_invalid_plaintext_is_not_persisted(tmp_path: Path) -> None:
    record_hook_diagnostic("customer private repository", "payload\ntext", _state=tmp_path)
    row = json.loads((tmp_path / "observation/hook-diagnostics.jsonl").read_text(encoding="utf-8"))
    assert row["event"] == "unknown_event"
    assert row["reason"] == "unknown_reason"
    assert "customer" not in json.dumps(row)


def test_sensitive_looking_legal_tokens_are_not_persisted(tmp_path: Path) -> None:
    record_hook_diagnostic("sk_live_ABC123", "BearerTokenABC123", _state=tmp_path)
    row = json.loads((tmp_path / "observation/hook-diagnostics.jsonl").read_text(encoding="utf-8"))
    assert row["event"] == "unknown_event"
    assert row["reason"] == "unknown_reason"
    assert "ABC123" not in json.dumps(row)


def test_rotates_at_64_kib_and_keeps_one_backup(tmp_path: Path) -> None:
    directory = tmp_path / "observation"
    directory.mkdir(mode=0o700)
    path = directory / "hook-diagnostics.jsonl"
    path.write_bytes(b"x" * (64 * 1024 - 1))
    record_hook_diagnostic("service_unavailable", "SessionStart", _state=tmp_path)
    rotated = directory / "hook-diagnostics.jsonl.1"
    assert rotated.stat().st_size == 64 * 1024 - 1
    assert path.stat().st_size < 1024

    path.write_bytes(b"y" * (64 * 1024 - 1))
    record_hook_diagnostic("timeout", "Stop", _state=tmp_path)
    assert rotated.read_bytes().startswith(b"y")
    assert len(list(directory.glob("hook-diagnostics.jsonl.*"))) == 1


def test_discards_an_externally_oversized_active_file(tmp_path: Path) -> None:
    directory = tmp_path / "observation"
    directory.mkdir(mode=0o700)
    path = directory / "hook-diagnostics.jsonl"
    path.write_bytes(b"x" * (64 * 1024 + 1))
    record_hook_diagnostic("timeout", "Stop", _state=tmp_path)
    assert path.stat().st_size < 1024
    assert not (directory / "hook-diagnostics.jsonl.1").exists()


def test_concurrent_processes_preserve_bound_and_json_lines(tmp_path: Path) -> None:
    script = (
        "from pathlib import Path; "
        "from yoetz.cli.hook_diagnostics import record_hook_diagnostic; "
        f"root=Path({str(tmp_path)!r}); "
        "[record_hook_diagnostic('service_unavailable','PostToolUse',_state=root) "
        "for _ in range(180)]"
    )
    processes = [subprocess.Popen((sys.executable, "-c", script)) for _ in range(4)]
    for process in processes:
        assert process.wait(timeout=15) == 0
    directory = tmp_path / "observation"
    files = [directory / "hook-diagnostics.jsonl", directory / "hook-diagnostics.jsonl.1"]
    for path in files:
        if not path.exists():
            continue
        assert path.stat().st_size <= 64 * 1024
        for line in path.read_text(encoding="utf-8").splitlines():
            assert set(json.loads(line)) == {"event", "reason", "ts"}
    assert len(list(directory.glob("hook-diagnostics.jsonl.*"))) <= 1


def test_timing_rows_round_trip_and_reason_counts_are_unpolluted(tmp_path: Path) -> None:
    """Two row shapes share one file; neither may be counted as the other."""

    record_hook_diagnostic("hook_budget_exceeded", "PostToolUse", _state=tmp_path)
    record_hook_timing(
        "SessionEnd",
        ms=1_842,
        stages={"import": 42, "store": 900, "advice": 8, "drain": 150, "bogus": 5},
        _state=tmp_path,
    )
    path = tmp_path / "observation/hook-diagnostics.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert set(rows[0]) == {"event", "reason", "ts"}
    assert set(rows[1]) == {"event", "kind", "ms", "stages", "ts"}
    assert rows[1]["kind"] == "timing"
    assert rows[1]["ms"] == 1_842
    # Unknown stage names are dropped rather than persisted verbatim.
    assert set(rows[1]["stages"]) == {"import", "store", "advice", "drain"}

    summary = hook_diagnostic_summary(_state=tmp_path)
    assert summary["count"] == 1
    assert summary["recent_count"] == 1
    assert summary["last_reason"] == "hook_budget_exceeded"
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert set(reasons) == {"hook_budget_exceeded"}
    budget = dict(cast(Mapping[str, object], reasons["hook_budget_exceeded"]))
    assert budget["count"] == 1
    assert budget["recent"] == 1
    assert budget["first_seen"] == budget["last_seen"] == summary["last_seen"]
    timings = dict(cast(Mapping[str, object], summary["timings"]))
    assert timings["count"] == 1
    assert timings["recent_count"] == 1
    assert timings["last_ms"] == timings["max_ms"] == timings["recent_max_ms"] == 1_842
    assert timings["max_ts"] is not None


def _seed(directory: Path, rows: list[dict[str, object]]) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "hook-diagnostics.jsonl"
    path.write_text(
        "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows), encoding="utf-8"
    )
    path.chmod(0o600)


def test_fixed_and_gone_failures_are_dated_rather_than_reported_as_live(tmp_path: Path) -> None:
    """#310: an all-time tally read a two-day-old, since-fixed failure as live."""

    _seed(
        tmp_path / "observation",
        [
            {"event": "PreToolUse", "reason": "runtime_gate_unsafe", "ts": "2026-08-15T18:49:51Z"},
            {"event": "Stop", "reason": "runtime_gate_unsafe", "ts": "2026-08-15T19:00:00Z"},
            {
                "event": "Stop",
                "kind": "timing",
                "ms": 60_001,
                "stages": {"store": 59_780},
                "ts": "2026-08-15T22:29:17Z",
            },
            {
                "event": "Stop",
                "kind": "timing",
                "ms": 789,
                "stages": {"store": 247},
                "ts": "2026-08-17T12:00:00Z",
            },
        ],
    )
    summary = hook_diagnostic_summary(
        _state=tmp_path, _now=datetime(2026, 8, 17, 12, 30, tzinfo=UTC)
    )

    assert summary["count"] == 2
    assert summary["recent_count"] == 0
    assert summary["first_seen"] == "2026-08-15T18:49:51Z"
    assert summary["last_seen"] == "2026-08-15T19:00:00Z"
    assert summary["window_seconds"] == 3_600
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert dict(cast(Mapping[str, object], reasons["runtime_gate_unsafe"])) == {
        "count": 2,
        "first_seen": "2026-08-15T18:49:51Z",
        "last_seen": "2026-08-15T19:00:00Z",
        "recent": 0,
    }
    # The all-time extreme is retained, but dated, and the live window has its own.
    timings = dict(cast(Mapping[str, object], summary["timings"]))
    assert timings == {
        "count": 2,
        "last_ms": 789,
        "max_ms": 60_001,
        "max_ts": "2026-08-15T22:29:17Z",
        "recent_count": 1,
        "recent_max_ms": 789,
    }


def test_a_live_failure_is_still_counted_as_recent(tmp_path: Path) -> None:
    _seed(
        tmp_path / "observation",
        [
            {"event": "PreToolUse", "reason": "runtime_gate_unsafe", "ts": "2026-08-15T18:49:51Z"},
            {"event": "PostToolUse", "reason": "service_unavailable", "ts": "2026-08-17T12:25:00Z"},
        ],
    )
    summary = hook_diagnostic_summary(
        _state=tmp_path, _now=datetime(2026, 8, 17, 12, 30, tzinfo=UTC)
    )
    assert summary["count"] == 2
    assert summary["recent_count"] == 1
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert dict(cast(Mapping[str, object], reasons["service_unavailable"]))["recent"] == 1
    assert dict(cast(Mapping[str, object], reasons["runtime_gate_unsafe"]))["recent"] == 0


def test_a_future_diagnostic_is_retained_but_not_called_recent(tmp_path: Path) -> None:
    """Clock-skewed rows must not remain live until their future timestamp arrives."""

    _seed(
        tmp_path / "observation",
        [{"event": "Stop", "reason": "timeout", "ts": "2026-08-18T12:30:00+02:00"}],
    )
    summary = hook_diagnostic_summary(
        _state=tmp_path, _now=datetime(2026, 8, 17, 12, 30, tzinfo=UTC)
    )
    assert summary["count"] == 1
    assert summary["recent_count"] == 0
    # Dated fields are normalized to the same UTC wire form as writer-created rows.
    assert summary["last_seen"] == "2026-08-18T10:30:00Z"


def test_an_undatable_row_is_counted_but_never_called_recent(tmp_path: Path) -> None:
    """An unreadable stamp is exactly what must stop being presented as live."""

    _seed(
        tmp_path / "observation",
        [
            {"event": "Stop", "reason": "timeout", "ts": "not-a-timestamp"},
            {
                "event": "Stop",
                "kind": "timing",
                "ms": 4_000,
                "stages": {"store": 1},
                "ts": "not-a-timestamp",
            },
        ],
    )
    summary = hook_diagnostic_summary(
        _state=tmp_path, _now=datetime(2026, 8, 17, 12, 30, tzinfo=UTC)
    )
    assert summary["count"] == 1
    assert summary["recent_count"] == 0
    assert summary["first_seen"] is None
    assert summary["last_seen"] is None
    assert summary["last_reason"] == "timeout"
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert dict(cast(Mapping[str, object], reasons["timeout"])) == {
        "count": 1,
        "first_seen": None,
        "last_seen": None,
        "recent": 0,
    }
    timings = dict(cast(Mapping[str, object], summary["timings"]))
    assert timings["count"] == 1
    assert timings["max_ms"] == 4_000
    assert timings["max_ts"] is None
    assert timings["recent_count"] == 0
    assert timings["recent_max_ms"] is None


def test_an_absent_diagnostics_file_summarizes_to_an_empty_dated_shape(tmp_path: Path) -> None:
    summary = hook_diagnostic_summary(_state=tmp_path)
    assert summary["count"] == 0
    assert summary["recent_count"] == 0
    assert summary["first_seen"] is None
    assert summary["last_seen"] is None
    assert summary["last_event"] is None
    assert summary["last_reason"] is None
    assert dict(cast(Mapping[str, object], summary["reasons"])) == {}
    assert dict(cast(Mapping[str, object], summary["timings"])) == {
        "count": 0,
        "last_ms": None,
        "max_ms": None,
        "max_ts": None,
        "recent_count": 0,
        "recent_max_ms": None,
    }


def test_budget_reason_is_an_admitted_token_and_unknown_reasons_are_closed(
    tmp_path: Path,
) -> None:
    for reason in ("hook_budget_exceeded", "async_hook_downgraded"):
        record_hook_diagnostic(reason, "PostToolUse", _state=tmp_path)
    rows = [
        json.loads(line)
        for line in (tmp_path / "observation/hook-diagnostics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    # The async-downgrade detector was removed: its predicate flagged compliant
    # async hosts, so its token is no longer admitted and closes to the
    # unknown-reason fallback.
    assert [row["reason"] for row in rows] == ["hook_budget_exceeded", "unknown_reason"]
