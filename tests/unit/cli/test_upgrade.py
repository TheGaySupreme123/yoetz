from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from yoetz.adapters import package_upgrade as package_adapter
from yoetz.application import upgrade
from yoetz.cli.app import app

_RUNNER = CliRunner()


def test_plan_does_not_execute_package_or_infer_host_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected() -> str:
        raise AssertionError("package execution without acceptance")

    monkeypatch.setattr("yoetz.cli.upgrade.execute_package_upgrade", unexpected)
    result = _RUNNER.invoke(app, ["upgrade"])
    assert result.exit_code == 0, result.output
    assert "Plan only" in result.output
    for host in upgrade.HOSTS:
        assert f"{host}: select existing target" in result.output
    assert "Expanded review" in result.output


def test_acceptance_requires_quiescence_before_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected() -> str:
        raise AssertionError("execution before quiescence")

    monkeypatch.setattr("yoetz.cli.upgrade.execute_package_upgrade", unexpected)
    result = _RUNNER.invoke(app, ["upgrade", "--accept"])
    assert result.exit_code == 2
    assert "upgrade_writers_must_be_stopped" in result.output


@pytest.mark.parametrize(
    "outcome, code",
    [("package_command_succeeded", 0), ("package_command_failed", 2), ("outcome_unknown", 2)],
)
def test_package_result_never_claims_host_or_data_upgrade(
    monkeypatch: pytest.MonkeyPatch, outcome: str, code: int
) -> None:
    calls: list[bool] = []

    def execute() -> str:
        calls.append(True)
        return outcome

    monkeypatch.setattr("yoetz.cli.upgrade.execute_package_upgrade", execute)
    result = _RUNNER.invoke(app, ["upgrade", "--accept", "--writers-stopped"])
    assert result.exit_code == code
    assert calls == [True]
    assert outcome in result.output
    if code == 0:
        assert "Host refresh, migration and activation remain unverified" in result.output


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_native_plan_preserves_explicit_paths_and_profiles(host: str) -> None:
    options = {
        "project-root": "/project with spaces",
        "claude-path": "/bin/claude",
        "claude-config-root": "/fixture/claude-home",
        "cache-root": "/cache/claude",
        "marketplace-root": "/market/claude",
        "cursor-config-root": "/fixture/cursor-home",
        "mcp-ownership": "plugin-managed",
        "route-profile": "strict",
        "observation-profile": "ordinary",
    }
    steps = upgrade.build_upgrade_plan([host], options)
    stage = next(step for step in steps if step.title == f"{host}: refresh installed plugin")
    assert stage.cwd == "/project with spaces"
    for command in stage.commands:
        assert command[command.index("--project-root") + 1] == "/project with spaces"
        assert command[command.index("--route-profile") + 1] == "strict"
        assert command[command.index("--observation-profile") + 1] == "ordinary"
        assert "--accept" not in command
        assert "<preview_digest>" not in command
        result = _RUNNER.invoke(app, [*command[1:], "--help"])
        assert result.exit_code == 0, result.output
    assert stage.commands[1][-3:-1] == ("--action", "update" if host == "claude" else "replace")


def test_codex_plan_has_no_invented_plugin_update_or_general_setup() -> None:
    steps = upgrade.build_upgrade_plan(
        ["codex"],
        {
            "project-root": "/project",
            "codex-path": "/bin/codex",
            "codex-home": "/fixture/codex-home",
        },
    )
    stage = next(step for step in steps if step.title.startswith("codex: refresh"))
    assert stage.environment == (
        ("CODEX_HOME", "/fixture/codex-home"),
        ("CODEX_TESTING_HOME", "/fixture/codex-home"),
    )
    assert stage.commands[-1][-2:] == ("--codex-home", "/fixture/codex-home")
    for command in stage.commands:
        assert "setup" not in command
        assert "update" not in command
        assert _RUNNER.invoke(app, [*command[1:], "--help"]).exit_code == 0


def test_existing_task_upgrade_uses_normal_startup_and_status() -> None:
    steps = upgrade.build_upgrade_plan(["codex"], {})
    data_step = next(step for step in steps if step.title == "Data and service")
    assert data_step.commands == (("yoetz", "service", "status", "--json"),)
    assert "upgrade automatically" in data_step.detail
    assert "Existing tasks, settings and permissions are retained" in data_step.detail
    assert "resumes the recorded upgrade" in data_step.detail


def test_invalid_targets_fail_before_execution() -> None:
    with pytest.raises(ValueError, match="upgrade_target_must_be_absolute"):
        upgrade.build_upgrade_plan(["cursor"], {"project-root": "relative"})
    with pytest.raises(ValueError, match="upgrade_option_invalid"):
        upgrade.build_upgrade_plan(["cursor"], {"project-root": "/safe\ninjected"})


def test_isolated_runtime_cannot_upgrade_ambient_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("yoetz.config.paths.isolated_root", lambda: tmp_path)

    def unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError("uv must not run from isolated runtime")

    monkeypatch.setattr(package_adapter.subprocess, "run", unexpected)
    assert package_adapter.execute_package_upgrade() == "refused_isolated_runtime"


@pytest.mark.parametrize("matched", [False, True])
def test_package_execution_binds_invoking_uv_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, matched: bool
) -> None:
    root = tmp_path / "tools"
    monkeypatch.setattr("yoetz.config.paths.isolated_root", lambda: None)
    monkeypatch.setattr(
        package_adapter.sys, "prefix", str(root / "yoetz" if matched else tmp_path / "source")
    )
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        assert kwargs["stdin"] == subprocess.DEVNULL
        if argv == ("uv", "tool", "dir"):
            return SimpleNamespace(returncode=0, stdout=str(root).encode())
        assert argv == upgrade.PACKAGE_UPGRADE_ARGV
        assert kwargs["stdout"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(package_adapter.subprocess, "run", run)
    assert package_adapter.execute_package_upgrade() == (
        "package_command_succeeded" if matched else "refused_non_uv_tool_runtime"
    )
    assert len(calls) == (2 if matched else 1)


def test_package_timeout_reports_unknown_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("yoetz.config.paths.isolated_root", lambda: None)
    monkeypatch.setattr(package_adapter.sys, "prefix", str(tmp_path / "yoetz"))
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        if argv == ("uv", "tool", "dir"):
            return SimpleNamespace(returncode=0, stdout=str(tmp_path).encode())
        raise subprocess.TimeoutExpired(argv, 120)

    monkeypatch.setattr(package_adapter.subprocess, "run", run)
    assert package_adapter.execute_package_upgrade() == "outcome_unknown"
    assert calls == [("uv", "tool", "dir"), upgrade.PACKAGE_UPGRADE_ARGV]
