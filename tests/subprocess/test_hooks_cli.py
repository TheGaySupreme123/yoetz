"""CLI coverage for `yoetz hooks session-start` inactive mapping path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yoetz.adapters.integrations.codex_lifecycle as lifecycle
import yoetz.cli.app as cli

_RUNNER = CliRunner()


def test_hooks_session_start_inactive_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _state_dir(*, _probe: object | None = None) -> Path:
        return tmp_path

    monkeypatch.setattr(lifecycle, "state_dir", _state_dir)
    result = _RUNNER.invoke(
        cli.app,
        ["hooks", "session-start"],
        input=json.dumps(
            {
                "session_id": "subprocess-session-1",
                "source": "resume",
                "hook_event_name": "SessionStart",
            }
        ),
    )
    assert result.exit_code == 0, result.stderr
    body = json.loads(result.stdout)
    context = body["hookSpecificOutput"]["additionalContext"]
    assert "No Yoetz task is mapped" in context
    assert "tsk_" not in context
