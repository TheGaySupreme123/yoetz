"""CLI wiring for Codex plugin/marketplace removal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yoetz.adapters.integrations.codex_marketplace import (
    ActivationInspection,
    ActivationState,
    RemovalOutcome,
    RemovalPreview,
    RemovalResult,
)
from yoetz.cli.app import app
from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import HarnessId, IntegrationError, IntegrationReason

_RUNNER = CliRunner()
_DIGEST = "sha256:" + "a" * 64


def _preview(home: Path, executable: Path) -> RemovalPreview:
    return RemovalPreview(
        _DIGEST,
        ActivationInspection(True, True, ActivationState.ACTIVE),
        RemovalOutcome.REMOVE,
        False,
        True,
        True,
        True,
        ("0.1.0",),
        "absent",
        executable,
        "sha256:" + "b" * 64,
        "0.148.0-alpha.6",
        home,
        ("--version",),
        ("plugin", "list", "--marketplace", "yoetz", "--json"),
        ("plugin", "remove", "yoetz@yoetz", "--json"),
        ("plugin", "marketplace", "remove", "yoetz", "--json"),
        "temporary_owner_private_home",
        (("CODEX_HOME", str(home)), ("CODEX_TESTING_HOME", str(home))),
        "sha256:" + "c" * 64,
        "sha256:" + "d" * 64,
        '[marketplaces.yoetz]\nsource_type = "local"\n',
    )


def test_codex_plugin_cli_preview_status_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    executable = home / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    preview = _preview(home, executable)

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return (
            HarnessBinary(HarnessId.CODEX, str(executable), "0.148.0-alpha.6", "untested"),
        )

    import yoetz.cli.codex_plugin as module

    monkeypatch.setattr(module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(module, "preview_removal", lambda *args, **kwargs: preview)
    monkeypatch.setattr(
        module,
        "inspect_activation",
        lambda *args, **kwargs: preview.inspection,
    )
    monkeypatch.setattr(
        module,
        "apply_removal",
        lambda *args, **kwargs: RemovalResult(
            RemovalOutcome.REMOVE,
            ActivationInspection(True, False, ActivationState.INSTALLED_NOT_ACTIVATED),
            "absent",
            False,
        ),
    )

    status = _RUNNER.invoke(
        app,
        ["integrate", "codex", "plugin", "status", "--codex-home", str(home), "--json"],
    )
    assert status.exit_code == 0, status.output
    assert json.loads(status.stdout)["state"] == "active"

    shown = _RUNNER.invoke(
        app,
        ["integrate", "codex", "plugin", "preview", "--codex-home", str(home), "--json"],
    )
    assert shown.exit_code == 0, shown.output
    body = json.loads(shown.stdout)
    assert body["preview_digest"] == _DIGEST
    assert body["action"] == "remove"

    removed = _RUNNER.invoke(
        app,
        [
            "integrate",
            "codex",
            "plugin",
            "remove",
            "--codex-home",
            str(home),
            "--accept",
            "--preview-digest",
            _DIGEST,
            "--json",
        ],
    )
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.stdout)["outcome"] == "remove"


def test_codex_plugin_cli_names_remove_refused_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    executable = home / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return (
            HarnessBinary(HarnessId.CODEX, str(executable), "0.148.0-alpha.6", "untested"),
        )

    def refuse(*_args: object, **_kwargs: object) -> RemovalPreview:
        raise IntegrationError(IntegrationReason.REMOVE_REFUSED, {"conflict": "cache"})

    import yoetz.cli.codex_plugin as module

    monkeypatch.setattr(module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(module, "preview_removal", refuse)

    result = _RUNNER.invoke(
        app,
        ["integrate", "codex", "plugin", "preview", "--codex-home", str(home), "--json"],
    )
    assert result.exit_code == 2
    assert "codex_plugin_remove_refused:cache" in result.stderr


def test_codex_plugin_cli_status_reports_foreign_without_previewing_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    executable = home / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return (
            HarnessBinary(HarnessId.CODEX, str(executable), "0.148.0-alpha.6", "untested"),
        )

    def refuse(*_args: object, **_kwargs: object) -> RemovalPreview:
        raise IntegrationError(IntegrationReason.REMOVE_REFUSED, {"conflict": "cache"})

    import yoetz.cli.codex_plugin as module

    monkeypatch.setattr(module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(
        module,
        "inspect_activation",
        lambda *args, **kwargs: ActivationInspection(
            False, False, ActivationState.FOREIGN
        ),
    )
    monkeypatch.setattr(module, "preview_removal", refuse)
    monkeypatch.setattr(module, "_skill_tree_state", lambda _target: "absent")

    result = _RUNNER.invoke(
        app,
        ["integrate", "codex", "plugin", "status", "--codex-home", str(home), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["state"] == "foreign"


def test_codex_plugin_cli_remove_requires_exact_preview_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    executable = home / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    preview = _preview(home, executable)

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return (
            HarnessBinary(HarnessId.CODEX, str(executable), "0.148.0-alpha.6", "untested"),
        )

    import yoetz.cli.codex_plugin as module

    monkeypatch.setattr(module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(module, "preview_removal", lambda *args, **kwargs: preview)

    result = _RUNNER.invoke(
        app,
        ["integrate", "codex", "plugin", "remove", "--codex-home", str(home), "--json"],
    )
    assert result.exit_code == 3
    assert "codex_plugin_exact_preview_acceptance_required" in result.stderr
