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
from unittest.mock import Mock

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
        "semantic_model": "accounts/fireworks/models/minimax-m3",
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
    unrun = _LANE.Lane(_namespace(tmp_path, strict_agent=True, skip_agent=False))
    unrun._record("provision", "install", status="pass")
    assert unrun.report()["verdict"]["green"] is False, "strict mode must not pass an unrun agent"
    skipped = _LANE.Lane(_namespace(tmp_path, strict_agent=True, skip_agent=True))
    skipped._record("provision", "install", status="pass")
    assert skipped.report()["verdict"]["green"] is True
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

    privacy_source = (Path(__file__).parents[3] / "src/yoetz/cli/privacy_setup.py").read_text(
        "utf-8"
    )
    for literal in (
        '"Use this recommended privacy policy?"',
        '"Choose a privacy option"',
        'f"Create this exact privacy proposal ({_RECIPE_LABELS[recipe]})?"',
    ):
        assert literal in privacy_source, literal

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


def _native_output(host: str, text: str) -> str:
    if host == "codex":
        return "\n".join(
            json.dumps(event)
            for event in [
                {"type": "turn.started"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
                {"type": "turn.completed"},
            ]
        )
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text})


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize(
    "text,expected",
    [
        ("DONE", True),
        ("Probe completed.\n\nDONE\n", True),
        ("The required Yoetz MCP tools are not available in this session.", False),
        ("NOT_DONE", False),
        ("   ", False),
        ('The prompt says "DONE".', False),
        ("DONE\nCoverage limitation: check_not_recorded.", True),
    ],
)
def test_native_completion_requires_standalone_marker_in_final_response(
    host: str, text: str, expected: bool
) -> None:
    assert _LANE._native_done(host, _native_output(host, text)) is expected


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_native_result_error_and_malformed_output_cannot_supply_completion(host: str) -> None:
    for output in [
        "DONE",
        '{"result":"DONE"}',
        "[]",
        "not json",
        _native_output(host, "DONE") + "bad",
    ]:
        assert _LANE._native_done(host, output) is False
    result = json.loads(_native_output(host, "DONE"))
    result["is_error"] = True
    assert _LANE._native_done(host, json.dumps(result)) is False
    result["is_error"] = False
    result["subtype"] = "error_max_turns"
    assert _LANE._native_done(host, json.dumps(result)) is False


def test_codex_completion_ignores_tool_output_and_requires_completed_turn() -> None:
    tool_done = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "aggregated_output": "DONE"},
        }
    )
    assert _LANE._native_done("codex", tool_done + '\n{"type":"turn.completed"}') is False
    good = _native_output("codex", "DONE")
    assert _LANE._native_done("codex", good.rsplit("\n", 1)[0]) is False
    for suffix in [
        '{"type":"turn.failed"}',
        '{"type":"error"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"Unavailable"}}',
        "malformed",
    ]:
        assert _LANE._native_done("codex", good + "\n" + suffix) is False


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("done", [False, True])
def test_native_phase_requires_completion_even_with_preexisting_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    done: bool,
) -> None:
    lane = _LANE.Lane(_namespace(tmp_path, host=host, strict_agent=True, skip_agent=False))
    lane.evidence.mkdir()
    monkeypatch.setattr(lane, "_agent_command", Mock(return_value=(["native"], {}, None)))
    output = _native_output(host, "DONE" if done else "Yoetz MCP tools unavailable")
    monkeypatch.setattr(lane, "_run", Mock(return_value=(0, output, "", 1)))
    monkeypatch.setattr(lane, "_observe_status", Mock(return_value={"mapping_present": True}))
    monkeypatch.setattr(lane, "_observe_drain", Mock(return_value=None))
    monkeypatch.setattr(lane, "_yoetz", Mock(return_value=(0, {})))
    lane.phase_native_agent()
    verdict = lane.report()["verdict"]
    assert verdict["agent_ok"] is done
    assert verdict["green"] is done
    step = next(s for s in lane.steps if s.name == "agent_run")
    assert step.status == ("pass" if done else "fail")
    assert step.reason == (None if done else "agent_completion_missing")
    lane.strict_agent = False
    assert lane.report()["verdict"]["green"] is True
    assert lane.report()["verdict"]["agent_ok"] is done


@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
def test_native_launch_resolves_pinned_runtime_before_ambient_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "ambient-bin"))
    lane = _LANE.Lane(_namespace(tmp_path, host=host, host_path="/synthetic/host"))
    lane.launcher = tmp_path / "disposable" / "runtime" / "bin" / "yoetz"
    lane.fireworks_key = "synthetic-provider-key"
    lane.cursor_key = "synthetic-cursor-key"
    argv, env, reason = lane._agent_command()
    assert argv is not None and reason is None
    assert env["PATH"].split(_LANE.os.pathsep) == [
        str(lane.launcher.parent),
        str(tmp_path / "ambient-bin"),
    ]
    child = _LANE._clean_env(env, drop=_LANE._LANE_SECRET_ENV)
    assert child["PATH"] == env["PATH"]
    assert "DOGFOOD_VAULT_PASSPHRASE" not in child
    assert "DOGFOOD_OS_PASSWORD" not in child


@pytest.mark.parametrize("terminal", ["retry_pending", "pass_limit"])
def test_drain_observes_background_completion_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    lane = _LANE.Lane(_namespace(tmp_path))
    lane.evidence.mkdir()
    pending = {"terminal": terminal, "pending_after": 7}
    drained = {"terminal": "drained", "pending_after": 0}
    invoke = Mock(side_effect=[(Mock(exit_code=0), pending), (Mock(exit_code=0), drained)])
    monkeypatch.setattr(lane, "_yoetz", invoke)
    monkeypatch.setattr(_LANE.time, "sleep", Mock())
    assert lane._observe_drain("drain", "native", fatal=False) == drained
    assert invoke.call_count == 2
    assert lane.steps[-1].status == "pass"
    assert lane.steps[-1].summary["polls"] == 2


@pytest.mark.parametrize(
    "result",
    [None, {"terminal": "service_unavailable"}, {"terminal": "drained", "pending_after": 1}],
)
def test_drain_does_not_retry_invalid_or_nonretryable_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object] | None,
) -> None:
    lane = _LANE.Lane(_namespace(tmp_path))
    lane.evidence.mkdir()
    invoke = Mock(return_value=(Mock(exit_code=0), result))
    monkeypatch.setattr(lane, "_yoetz", invoke)
    lane._observe_drain("drain", "native", fatal=False)
    assert invoke.call_count == 1
    assert lane.steps[-1].status == "fail"


def test_drain_deadline_never_turns_pending_into_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _LANE.Lane(_namespace(tmp_path))
    lane.evidence.mkdir()
    invoke = Mock(
        return_value=(Mock(exit_code=0), {"terminal": "retry_pending", "pending_after": 7})
    )
    monkeypatch.setattr(lane, "_yoetz", invoke)
    monkeypatch.setattr(_LANE.time, "monotonic", Mock(side_effect=[0.0, 0.0, 60.0, 60.0]))
    monkeypatch.setattr(_LANE.time, "sleep", Mock())
    lane._observe_drain("drain", "native", fatal=False)
    assert invoke.call_count == 1
    assert lane.steps[-1].status == "fail"
    assert lane.steps[-1].reason == "not_drained"


def test_drain_nonzero_exit_cannot_claim_drained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _LANE.Lane(_namespace(tmp_path))
    lane.evidence.mkdir()
    invoke = Mock(return_value=(Mock(exit_code=1), {"terminal": "drained", "pending_after": 0}))
    monkeypatch.setattr(lane, "_yoetz", invoke)
    lane._observe_drain("drain", "native", fatal=False)
    assert invoke.call_count == 1
    assert lane.steps[-1].status == "fail"
