"""Tests for bounded, payload-free hook diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from yoetz.cli.hook_diagnostics import record_hook_diagnostic


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
