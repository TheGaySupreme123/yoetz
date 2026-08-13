"""Tests for bounded, payload-free hook diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
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
    assert summary["last_reason"] == "hook_budget_exceeded"
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert reasons == {"hook_budget_exceeded": 1}
    timings = dict(cast(Mapping[str, object], summary["timings"]))
    assert timings == {"count": 1, "last_ms": 1_842, "max_ms": 1_842}


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
