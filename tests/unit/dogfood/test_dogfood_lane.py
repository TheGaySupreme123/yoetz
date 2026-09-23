"""Bounded unit coverage for the unattended dogfood lane tooling (scripts/dogfood_ci).

The lane itself needs a runner, host CLIs, and a disposable instance; these tests lock the pure
pieces that decide what a lane reports: result parsing, frontier discovery, redaction, the
connection-mode choice per platform, the verdict rule (agent failure is not catastrophic unless
strict), and the pseudo-terminal ceremony driver against a harmless stand-in child.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

_SCRIPTS = Path(__file__).parents[3] / "scripts" / "dogfood_ci"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"dogfood_ci_{name}", _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"dogfood_ci_{name}"] = module
    spec.loader.exec_module(module)
    return module


_LANE = _load("lane")
_CEREMONY = _load("ceremony")


def _namespace(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "host": "codex",
        "checkout": str(tmp_path / "checkout"),
        "base": str(tmp_path / "base"),
        "tag": "df",
        "evidence": str(tmp_path / "evidence"),
        "python": "3.14.6",
        "host_path": None,
        "host_config_root": None,
        "project": str(tmp_path / "project"),
        "connection_mode": "auto",
        "semantic_model": "accounts/fireworks/models/qwen3-235b-a22b",
        "agent_model": None,
        "cursor_model": "gpt-5.6-luna-low",
        "agent_timeout": 5.0,
        "strict_agent": False,
        "skip_agent": True,
        "skip_restart": True,
        "allow_dirty": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_find_frontier_prefers_result_frontier_and_parse_json_tolerates_prefix() -> None:
    result = {
        "subject_frontier": {"sequence": "1", "head_digest": "sha256:" + "a" * 64},
        "result_frontier": {"sequence": "2", "head_digest": "sha256:" + "b" * 64},
    }
    assert _LANE._find_frontier(result) == result["result_frontier"]
    assert _LANE._find_frontier(
        {"nested": [{"frontier": {"sequence": "0", "head_digest": "genesis"}}]}
    ) == {
        "sequence": "0",
        "head_digest": "genesis",
    }
    assert _LANE._find_frontier({"x": 1}) is None
    assert _LANE._parse_json('human line\n{"ok": true}\n') == {"ok": True}
    assert _LANE._parse_json("not json") is None
    assert _LANE._find_key({"a": {"b": {"c": 3}}}, "c") == 3


def test_redaction_masks_secrets_and_runner_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOGFOOD_VAULT_PASSPHRASE", "vault-pass-phrase-0123456789")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw_secret_key_value")
    monkeypatch.setenv("DOGFOOD_OS_PASSWORD", "os-password-value")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    lane = _LANE.Lane(_namespace(tmp_path))
    text = (
        f"key fw_secret_key_value pass vault-pass-phrase-0123456789 os os-password-value "
        f"home {Path.home()} base {tmp_path / 'base'} temp {tmp_path / 'runner-temp'}/x"
    )
    redacted = cast(str, lane.redact(text))
    assert "fw_secret_key_value" not in redacted
    assert "vault-pass-phrase" not in redacted
    assert "os-password-value" not in redacted
    assert str(tmp_path / "base") not in redacted
    assert "<base>" in redacted and "<runner-temp>/x" in redacted and "<home>" in redacted
    nested = lane._redact_obj({"a": ["fw_secret_key_value", {"b": str(tmp_path / "base")}]})
    assert nested == {"a": ["<secret>", {"b": "<base>"}]}


@pytest.mark.parametrize(
    ("host", "system", "os_password", "expected"),
    [
        ("codex", "Darwin", "", "setup-run"),
        ("codex", "Linux", "", "setup-run"),
        ("claude", "Linux", "pw", "setup-run"),
        ("claude", "Linux", "", "plugin-dir"),
        ("claude", "Darwin", "pw", "plugin-dir"),
        ("cursor", "Linux", "pw", "setup-run"),
        ("cursor", "Darwin", "pw", "mcp-only"),
        ("cursor", "Linux", "", "mcp-only"),
    ],
)
def test_connection_mode_follows_platform_and_os_presence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    system: str,
    os_password: str,
    expected: str,
) -> None:
    monkeypatch.setattr(_LANE.platform, "system", lambda: system)
    if os_password:
        monkeypatch.setenv("DOGFOOD_OS_PASSWORD", os_password)
    else:
        monkeypatch.delenv("DOGFOOD_OS_PASSWORD", raising=False)
    lane = _LANE.Lane(_namespace(tmp_path, host=host))
    assert lane.connection_mode == expected
    explicit = _LANE.Lane(_namespace(tmp_path, host=host, connection_mode="none"))
    assert explicit.connection_mode == "none"


def test_verdict_treats_agent_failure_as_non_catastrophic_unless_strict(tmp_path: Path) -> None:
    lane = _LANE.Lane(_namespace(tmp_path))
    (tmp_path / "evidence").mkdir()
    lane._record("provision", "install", status="pass")
    lane._record("agent_run", "native", status="fail", exit_code=3, reason="agent_exit_3")
    lane.agent = {"ran": True, "exit_code": 3, "mapping_present": False}
    body = cast(dict[str, Any], lane.report())
    assert body["verdict"] == {
        "catastrophic": False,
        "catastrophic_steps": [],
        "failed_steps": ["agent_run"],
        "agent_ok": False,
        "strict_agent": False,
        "green": True,
    }
    lane.strict_agent = True
    assert lane.report()["verdict"]["green"] is False
    lane._record("observe_drain_after_probe_verdict", "ledger", status="fail", reason="not_drained")
    strict_off = _LANE.Lane(_namespace(tmp_path))
    strict_off.steps = lane.steps
    strict_off.agent = lane.agent
    verdict = strict_off.report()["verdict"]
    assert verdict["catastrophic"] is True
    assert verdict["catastrophic_steps"] == ["observe_drain_after_probe_verdict"]
    assert verdict["green"] is False
    written = json.loads((tmp_path / "evidence" / "01-provision.json").read_text())
    assert written["step"] == "provision"


def test_catastrophic_diagnostics_are_detected_from_hook_reasons() -> None:
    status = {
        "hook_diagnostics": {"reasons": {"service_unavailable": 2, "workspace_unconsented": 1}}
    }
    assert _LANE.Lane._catastrophic_diagnostics(status) == ["service_unavailable"]
    assert _LANE.Lane._catastrophic_diagnostics({"hook_diagnostics": {"reasons": {}}}) == []
    assert _LANE.Lane._catastrophic_diagnostics(None) == ["observe_status_unavailable"]


def test_prompt_names_tool_hint_workspace_and_external_ref() -> None:
    prompt = _LANE._PROMPT_TEMPLATE.format(
        tool_hint="plugin_yoetz_yoetz", workspace="/w", external_ref="native-claude-1"
    )
    assert "plugin_yoetz_yoetz" in prompt and "'/w'" in prompt and "native-claude-1" in prompt
    assert "single_atomic_change" in prompt and prompt.endswith("Do not create or edit files.")


_CHILD = textwrap.dedent(
    """
    import sys
    sys.stdout.write("Yoetz trusted foreground ceremony\\n")
    sys.stdout.write("Passphrase (16-1024 UTF-8 bytes; no control characters): ")
    sys.stdout.flush()
    first = sys.stdin.readline().strip()
    sys.stdout.write("Confirm passphrase (16-1024 UTF-8 bytes; no control characters): ")
    sys.stdout.flush()
    second = sys.stdin.readline().strip()
    sys.stdout.write("Decision [approve/deny]: ")
    sys.stdout.flush()
    decision = sys.stdin.readline().strip()
    ok = first == second == "correct-horse-battery-staple" and decision == "approve"
    sys.stdout.write('{"state": "%s"}\\n' % ("ready" if ok else "rejected"))
    raise SystemExit(0 if ok else 3)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_ceremony_driver_answers_prompts_once_and_masks_secrets(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    replies = [
        _CEREMONY.Reply(r"Decision \[approve/deny\]: ", "approve", secret=False),
        _CEREMONY.Reply(r"Passphrase \(", "correct-horse-battery-staple"),
        _CEREMONY.Reply(r"Confirm passphrase", "correct-horse-battery-staple"),
    ]
    result = _CEREMONY.run_ceremony([sys.executable, str(child)], replies, timeout=30.0)
    assert result.exit_code == 0, result.transcript
    assert result.timed_out is False
    assert result.answered == [
        r"Passphrase \(",
        r"Confirm passphrase",
        r"Decision \[approve/deny\]: ",
    ]
    assert "correct-horse-battery-staple" not in result.transcript
    assert "<secret>" in result.transcript
    assert '{"state": "ready"}' in result.transcript


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")
def test_ceremony_driver_reports_timeout_and_child_failure(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.py"
    sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    result = _CEREMONY.run_ceremony([sys.executable, str(sleeper)], [], timeout=0.5)
    assert result.timed_out is True and result.exit_code == 124
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    wrong = [
        _CEREMONY.Reply(r"Passphrase \(", "one-passphrase-value-here"),
        _CEREMONY.Reply(r"Confirm passphrase", "another-passphrase-value"),
        _CEREMONY.Reply(r"Decision \[approve/deny\]: ", "approve", secret=False),
    ]
    failed = _CEREMONY.run_ceremony([sys.executable, str(child)], wrong, timeout=30.0)
    assert failed.exit_code == 3 and '{"state": "rejected"}' in failed.transcript


def test_prompt_regexes_match_the_product_prompts_as_rendered() -> None:
    """Lock every lane regex to the real prompt text, rendered the way the product renders it."""

    import re

    import typer
    from typer.testing import CliRunner

    from yoetz.cli import unlock

    app = typer.Typer()

    @app.command()
    def render() -> None:
        typer.confirm("Use this recommended privacy policy?", default=True)
        typer.prompt("Choose a privacy option", default="3")
        typer.confirm("Create this exact privacy proposal (Assisted review)?", default=False)

    assert render is not None
    rendered = CliRunner().invoke(app, [], input="n\n3\ny\n").output
    for pattern in (
        _LANE.PROMPT_PRIVACY_RECOMMENDED,
        _LANE.PROMPT_PRIVACY_CHOICE,
        _LANE.PROMPT_PRIVACY_CREATE,
    ):
        assert re.search(pattern, rendered), (pattern, rendered)

    passphrase_prompt = cast(str, getattr(unlock, "_passphrase_prompt")("Passphrase"))
    confirm_prompt = cast(str, getattr(unlock, "_passphrase_prompt")("Confirm passphrase"))
    assert re.search(_LANE.PROMPT_PASSPHRASE, passphrase_prompt)
    assert re.search(_LANE.PROMPT_CONFIRM_PASSPHRASE, confirm_prompt)
    assert not re.search(_LANE.PROMPT_CONFIRM_PASSPHRASE, passphrase_prompt)

    sources = {
        "unlock": (Path(__file__).parents[3] / "src/yoetz/cli/unlock.py").read_text("utf-8"),
        "privacy_control": (
            Path(__file__).parents[3] / "src/yoetz/cli/privacy_control.py"
        ).read_text("utf-8"),
        "linux_presence": (
            Path(__file__).parents[3] / "src/yoetz/adapters/integrations/linux_artifact_presence.py"
        ).read_text("utf-8"),
    }
    assert '"Provider credential: "' in sources["unlock"]
    assert re.search(_LANE.PROMPT_PROVIDER_CREDENTIAL, "Provider credential: ")
    assert '"Decision [approve/deny]: "' in sources["unlock"]
    assert '"Decision [approve/deny/edit]: "' in sources["privacy_control"]
    assert re.search(_LANE.PROMPT_DECISION, "Decision [approve/deny]: ")
    assert re.search(_LANE.PROMPT_DECISION, "Decision [approve/deny/edit]: ")
    assert 'f"Password for {account}: "' in sources["linux_presence"]
    assert re.search(_LANE.PROMPT_PAM_PASSWORD, "Password for runner: ")
