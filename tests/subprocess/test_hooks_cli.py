"""CLI coverage for `yoetz hooks session-start` inactive mapping path."""

from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli

_RUNNER = CliRunner()

# The resume path reaches these through lazy in-function imports, so load them up front: their
# `state_dir` bindings must already exist when `_redirect_state_dir` scans `sys.modules`.
_LAZILY_REACHED_MODULES = (
    "yoetz.adapters.integrations.codex_lifecycle",
    "yoetz.adapters.integrations.observation_local",
    "yoetz.cli.observe_hooks",
)


def _redirect_state_dir(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point every imported binding of ``state_dir`` at ``root``.

    Each module does ``from yoetz.config.paths import state_dir``, so patching one module leaves
    the others bound to the real user state directory. Patching only ``codex_lifecycle`` let the
    ``resume`` path reach ``observation_local``'s binding and read real host observation state:
    the assertions below then passed on a clean runner and failed on any machine that had used
    Yoetz, so dogfooding the tool broke its own suite.
    """

    def _state_dir(*, _probe: object | None = None) -> Path:
        del _probe
        return root

    for name in _LAZILY_REACHED_MODULES:
        import_module(name)

    patched = 0
    for name, module in tuple(sys.modules.items()):
        if not name.startswith("yoetz."):
            continue
        if getattr(module, "state_dir", None) is None:
            continue
        monkeypatch.setattr(module, "state_dir", _state_dir, raising=False)
        patched += 1
    # A future module that resolves its own state root must not silently reintroduce host reads.
    assert patched >= 2, f"expected several state_dir bindings, patched {patched}"


def test_hooks_session_start_inactive_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect_state_dir(monkeypatch, tmp_path)
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
