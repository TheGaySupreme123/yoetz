"""Setup wizard CLI: discovery reporting, confirmation gates, marker, and help fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli
from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter, CommandOutput
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import HarnessId, IntegrationError, IntegrationReason
from yoetz.service.client import ServiceClient

_RUNNER = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    """Strip ANSI SGR sequences so Rich usage panels stay assertable."""

    return _ANSI_ESCAPE.sub("", text)


def _binary(path: str = "/opt/harness/bin/codex") -> HarnessBinary:
    return HarnessBinary(
        harness_id=HarnessId.CODEX,
        executable_path=path,
        reported_version="0.144.5",
        compatibility="untested",
    )


class _ScriptedRunner:
    def __init__(self, outputs: list[CommandOutput]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> CommandOutput:
        self.calls.append(argv)
        return self.outputs.pop(0)


def _yoetz_entry() -> CommandOutput:
    return CommandOutput(0, json.dumps({"command": "yoetz", "args": ["mcp", "serve"]}).encode())


@pytest.fixture
def wizard_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Fake discovery, adapter subprocesses, service client, and marker path."""

    state: dict[str, object] = {
        "binaries": (_binary(),),
        "outputs": [CommandOutput(1, b"")],
        "calls": [],
    }
    marker = tmp_path / "setup-wizard.json"

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return cast(tuple[HarnessBinary, ...], state["binaries"])

    def fake_adapter() -> CodexMcpAdapter:
        runner = _ScriptedRunner(cast(list[CommandOutput], state["outputs"]))
        cast(list[list[tuple[str, ...]]], state["calls"]).append(runner.calls)
        return CodexMcpAdapter(runner)

    async def unreachable_client() -> object:
        raise ControlError("service_unavailable")

    async def unreachable_on_demand(_kind: ControlClientKind) -> ServiceClient:
        raise ControlError("service_unavailable")

    def fake_grant_observation_consent(workspace: Path | None = None) -> dict[str, str]:
        del workspace
        return {
            "outcome": "granted",
            "workspace_commitment": "hmac-sha256:" + "a" * 64,
        }

    class _FakePluginService:
        def preview(self, target: object) -> object:
            del target
            from yoetz.adapters.integrations.codex_plugin import PluginHookPresence
            from yoetz.application.codex_plugin import CodexPluginPreview

            return CodexPluginPreview(
                presence_before=PluginHookPresence.ABSENT,
                planned_file_count=4,
                trust_observable=False,
                installed_digest=None,
                notes=("codex_hook_trust_not_observable_from_installation_state",),
            )

        def inspect(self, target: object) -> object:
            del target
            from yoetz.adapters.integrations.codex_plugin import (
                PluginHookPresence,
                PluginInspection,
            )

            return PluginInspection(
                PluginHookPresence.ABSENT,
                False,
                None,
                ("codex_hook_trust_not_observable_from_installation_state",),
            )

        def install(self, target: object, **kwargs: object) -> object:
            del target, kwargs
            from yoetz.adapters.integrations.codex_plugin import (
                PluginHookPresence,
                PluginInspection,
            )

            return PluginInspection(
                PluginHookPresence.INSTALLED,
                False,
                "sha256:" + "b" * 64,
                ("codex_hook_trust_not_observable_from_installation_state",),
            )

    import yoetz.cli.setup as setup_module
    import yoetz.service.client as service_client_module

    monkeypatch.setattr(setup_module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(setup_module, "CodexMcpAdapter", fake_adapter)
    monkeypatch.setattr(setup_module, "CodexPluginService", _FakePluginService)
    monkeypatch.setattr(setup_module, "setup_marker_path", lambda: marker)
    monkeypatch.setattr(
        setup_module,
        "_grant_observation_consent",
        fake_grant_observation_consent,
    )
    monkeypatch.setattr(cli, "build_service_client", unreachable_client)
    monkeypatch.setattr(
        service_client_module,
        "connect_service_on_demand",
        unreachable_on_demand,
    )
    state["marker"] = marker
    return state


def test_non_interactive_without_accept_is_a_dry_run(wizard_env: dict[str, object]) -> None:
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["schema"] == "yoetz.setup-wizard-report/1"
    assert report["registration"]["outcome"] == "declined"
    assert report["marker_written"] is False
    assert not cast(Path, wizard_env["marker"]).exists()
    # Honest next steps always include the confidential ceremonies it cannot run.
    steps = " ".join(report["next_steps"])
    assert "yoetz privacy setup" in steps
    assert "yoetz provider credential set" in steps
    assert "yoetz service run" in steps


def test_non_interactive_accept_registers_and_writes_marker(
    wizard_env: dict[str, object],
) -> None:
    wizard_env["outputs"] = [
        CommandOutput(1, b""),  # preview get: absent
        CommandOutput(1, b""),  # apply re-preview get: absent
        CommandOutput(0, b""),  # add
        _yoetz_entry(),  # verify get
    ]
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--accept", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["registration"]["outcome"] == "registered"
    assert report["registration"]["state"] == "yoetz_owned"
    assert report["registration"]["observation_consent"] == {
        "outcome": "granted",
        "workspace_commitment": "hmac-sha256:" + "a" * 64,
    }
    assert report["registration"]["plugin"]["outcome"] == "installed"
    assert report["registration"]["plugin"]["presence"] == "installed"
    assert report["readiness"]["observation_ready"] is False  # service unreachable
    assert report["readiness"]["consent"] == "granted"
    assert report["service"]["reachable"] is False
    assert report["marker_written"] is True
    marker = json.loads(cast(Path, wizard_env["marker"]).read_text())
    assert marker["schema"] == "yoetz.setup-wizard-marker/1"
    assert marker["outcome"] == "registered"


def test_already_registered_mcp_still_installs_plugin_and_grants_consent(
    wizard_env: dict[str, object],
) -> None:
    wizard_env["outputs"] = [
        _yoetz_entry(),  # preview get: already yoetz-owned
        _yoetz_entry(),  # status verify after plugin install
    ]
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--accept", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["registration"]["outcome"] == "already_registered"
    assert report["registration"]["plugin"]["outcome"] == "installed"
    assert report["registration"]["observation_consent"]["outcome"] == "granted"
    assert report["marker_written"] is True


def test_foreign_entry_is_preserved_and_reported(wizard_env: dict[str, object]) -> None:
    foreign = CommandOutput(0, json.dumps({"command": "other"}).encode())
    wizard_env["outputs"] = [foreign]
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--accept", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["registration"]["outcome"] == "skipped"
    assert report["registration"]["reason"] == "foreign_entry_present"
    assert report["registration"]["observation_consent"]["outcome"] == "absent"
    assert report["registration"]["plugin"]["outcome"] == "skipped"
    # No mutating `mcp add` ever ran.
    for calls in cast(list[list[tuple[str, ...]]], wizard_env["calls"]):
        assert all(call[1:3] == ("mcp", "get") for call in calls)


def test_multiple_candidates_fail_closed_non_interactively(
    wizard_env: dict[str, object],
) -> None:
    wizard_env["binaries"] = (_binary("/a/codex"), _binary("/b/codex"))
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--accept", "--json"])
    assert result.exit_code == 2
    assert "--codex-path" in result.stderr


def test_interactive_wizard_selects_harness_then_installation_and_requires_y_or_n(
    wizard_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard_env["binaries"] = (_binary("/a/codex"), _binary("/b/codex"))
    wizard_env["outputs"] = [
        CommandOutput(1, b""),  # preview get: absent
        CommandOutput(1, b""),  # apply re-preview get: absent
        CommandOutput(0, b""),  # add
        _yoetz_entry(),  # verify get
    ]

    import yoetz.cli.provider_binding as provider_binding
    import yoetz.cli.setup as setup_module

    monkeypatch.setattr(setup_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(provider_binding, "prompt_provider_endpoint_binding", lambda: None)

    result = _RUNNER.invoke(cli.app, ["setup", "run"], input="1\n2\nmaybe\nY\n")

    assert result.exit_code == 0
    assert "Automatically detected harnesses:" in result.stdout
    assert "1. Codex (2 installations)" in result.stdout
    assert "Select a harness to connect to Yoetz" in result.stdout
    assert "Detected Codex installations:" in result.stdout
    assert "Select the Codex installation to configure" in result.stdout
    assert "complete Yoetz Codex project integration" in result.stdout
    assert "MCP server name: yoetz" in result.stdout
    assert "Command: yoetz mcp serve" in result.stdout
    assert "Codex executable: /b/codex" in result.stdout
    assert "Confirm Codex project setup? [Y/N]" in result.stdout
    assert "Observation consent for this workspace" in result.stdout
    assert "Please enter Y or N." in result.stdout
    assert "MCP registration: registered; automatic activation not tested" in result.stdout
    assert "Skill support: no tested capability profile; automatic activation not tested" in (
        result.stdout
    )
    assert "Plugin installation:" in result.stdout
    assert "Hook installation:" in result.stdout
    assert "Observation readiness:" in result.stdout


def test_interactive_registration_n_declines_without_mutation(
    wizard_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.provider_binding as provider_binding
    import yoetz.cli.setup as setup_module

    monkeypatch.setattr(setup_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(provider_binding, "prompt_provider_endpoint_binding", lambda: None)

    result = _RUNNER.invoke(cli.app, ["setup", "run"], input="1\nN\n")

    assert result.exit_code == 0
    assert "Confirm Codex project setup? [Y/N]" in result.stdout
    assert "MCP registration: declined" in result.stdout
    assert "Skill support: no tested capability profile; automatic activation not tested" in (
        result.stdout
    )
    for calls in cast(list[list[tuple[str, ...]]], wizard_env["calls"]):
        assert all(call[1:3] == ("mcp", "get") for call in calls)


def test_no_codex_found_still_completes_with_guidance(wizard_env: dict[str, object]) -> None:
    wizard_env["binaries"] = ()
    result = _RUNNER.invoke(cli.app, ["setup", "run", "--non-interactive", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["registration"] == {
        "outcome": "skipped",
        "reason": "codex_not_found",
        "state": None,
    }


def test_setup_status_is_read_only(wizard_env: dict[str, object]) -> None:
    wizard_env["outputs"] = [_yoetz_entry()]
    result = _RUNNER.invoke(cli.app, ["setup", "status", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["schema"] == "yoetz.setup-status/1"
    assert report["discovered"][0]["registration_state"] == "yoetz_owned"
    assert report["marker_present"] is False
    assert report["service"]["reachable"] is False
    assert report["integration"] == {
        "hooks": {
            "presence": "absent",
            "trust_observable": False,
            "trust_state": "unknown",
        },
        "plugin": {"digest": None, "presence": "absent"},
        "skill": {
            "automatic_activation_tested": False,
            "source_state": "verified",
            "tested_profiles": [],
        },
    }
    assert not cast(Path, wizard_env["marker"]).exists()


def test_setup_status_reports_invalid_packaged_skill_source(
    wizard_env: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.cli.setup as setup_module

    monkeypatch.setattr(
        setup_module,
        "load_packaged_skill_source",
        lambda: (_ for _ in ()).throw(IntegrationError(IntegrationReason.SOURCE_INVALID, {})),
    )
    wizard_env["outputs"] = [_yoetz_entry()]
    result = _RUNNER.invoke(cli.app, ["setup", "status", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["integration"]["skill"] == {
        "automatic_activation_tested": False,
        "source_state": "source_invalid",
        "tested_profiles": [],
    }


def test_integrate_mcp_status_preview_install(wizard_env: dict[str, object]) -> None:
    wizard_env["outputs"] = [CommandOutput(1, b"")]
    status = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "status", "--json"])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["state"] == "absent"

    wizard_env["outputs"] = [CommandOutput(1, b"")]
    preview = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "preview", "--json"])
    assert preview.exit_code == 0
    body = json.loads(preview.stdout)
    assert body["action"] == "register"
    assert body["preview_digest"].startswith("sha256:")

    wizard_env["outputs"] = [
        CommandOutput(1, b""),
        CommandOutput(1, b""),
        CommandOutput(0, b""),
        _yoetz_entry(),
    ]
    install = _RUNNER.invoke(
        cli.app,
        [
            "integrate",
            "codex",
            "mcp",
            "install",
            "--accept",
            "--preview-digest",
            body["preview_digest"],
            "--json",
        ],
    )
    assert install.exit_code == 0
    assert json.loads(install.stdout)["state_after"] == "yoetz_owned"


def test_integrate_mcp_install_without_accept_fails_closed(
    wizard_env: dict[str, object],
) -> None:
    wizard_env["outputs"] = [CommandOutput(1, b"")]
    result = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "install", "--json"])
    assert result.exit_code == 2
    assert "confirmation_required" in result.stderr


def test_integrate_mcp_refuses_foreign_entry(wizard_env: dict[str, object]) -> None:
    wizard_env["outputs"] = [CommandOutput(0, json.dumps({"command": "other"}).encode())]
    result = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "install", "--accept", "--json"])
    assert result.exit_code == 2
    assert "foreign_entry_present" in result.stderr


def test_setup_surfaces_no_secret_shaped_option() -> None:
    for args in (["setup", "run", "--help"], ["setup", "status", "--help"]):
        result = _RUNNER.invoke(cli.app, args)
        assert result.exit_code == 0
        lowered = result.stdout.lower()
        for token in ("api-key", "apikey", "token", "secret", "password"):
            assert token not in lowered, (args, token)


def test_bare_invocation_without_tty_prints_help() -> None:
    result = _RUNNER.invoke(cli.app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_root_set_fireworks_dispatches_simple_provider_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.setup as setup_module

    received: dict[str, object] = {}

    async def fake_provider_setup(
        *, fireworks: bool, model: str | None, api_key: str | None
    ) -> int:
        received.update(fireworks=fireworks, model=model, api_key=api_key)
        return 0

    monkeypatch.setattr(setup_module, "run_provider_setup", fake_provider_setup)
    result = _RUNNER.invoke(
        cli.app,
        [
            "--set",
            "--fireworks",
            "--model",
            "accounts/fireworks/models/minimax-m3",
            "--api-key",
            "fw-test-value",
        ],
    )

    assert result.exit_code == 0
    assert received == {
        "fireworks": True,
        "model": "accounts/fireworks/models/minimax-m3",
        "api_key": "fw-test-value",
    }


def test_provider_flags_require_set() -> None:
    # Rich may colorize option tokens inside the Error panel (e.g. FORCE_COLOR CI),
    # splitting "--set" across ANSI codes; assert on the stripped combined output.
    result = _RUNNER.invoke(cli.app, ["--fireworks"], env={"NO_COLOR": "1"})
    assert result.exit_code == 2
    assert "require --set" in _plain(result.output)


def test_provider_setup_success_reports_layers_without_ready_overclaim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.setup as setup_module

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready"}

    async def fake_interactive(
        service: dict[str, object],
        *,
        provider_choice: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del provider_choice, model, api_key
        return service, {"binding": "configured", "credential": "stored"}

    monkeypatch.setattr(setup_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)
    monkeypatch.setattr(setup_module, "_interactive_provider_setup", fake_interactive)

    result = _RUNNER.invoke(cli.app, ["--set", "--fireworks", "--model", "m"])
    plain = _plain(result.output)
    assert result.exit_code == 0
    assert "Yoetz is ready to use this provider." not in plain
    assert "Provider binding and vault credential storage succeeded" in plain
    assert "SDK extra (semantic-openai):" in plain
    assert "_semantic_not_configured" in plain
    assert "Privacy policy: not demonstrated" in plain
    assert "Transport probe: not demonstrated" in plain
    assert "Installed artifact evidence: not demonstrated" in plain
    assert "not proof of live provider dispatch or semantic review" in plain
