"""Setup wizard CLI: discovery reporting, confirmation gates, marker, and help fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli
from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter, CommandOutput
from yoetz.application.harness_mcp import HarnessMcpService
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import (
    HarnessId,
    IntegrationError,
    IntegrationReason,
    IntegrationState,
    IntegrationTarget,
    SkillSource,
)
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


def _yoetz_entry(
    route_profile: Literal["policy", "strict"] = "strict",
) -> CommandOutput:
    args = ["mcp", "serve"]
    if route_profile == "strict":
        args.extend(["--semantic", "off"])
    return CommandOutput(0, json.dumps({"command": "yoetz", "args": args}).encode())


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

    def fake_adapter(
        *,
        route_profile: Literal["policy", "strict"] = "policy",
    ) -> CodexMcpAdapter:
        runner = _ScriptedRunner(cast(list[CommandOutput], state["outputs"]))
        cast(list[list[tuple[str, ...]]], state["calls"]).append(runner.calls)
        return CodexMcpAdapter(runner, route_profile=route_profile)

    async def unreachable_client() -> object:
        raise ControlError("service_unavailable")

    async def unreachable_on_demand(_kind: ControlClientKind) -> ServiceClient:
        raise ControlError("service_unavailable")

    async def fake_install_project_skill(_target: object, **_kwargs: object) -> dict[str, object]:
        return {
            "compatibility": "unsupported",
            "installed_digest": "sha256:" + "c" * 64,
            "outcome": "installed",
            "presence": "installed_exact",
            "reason": None,
        }

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
    monkeypatch.setattr(setup_module, "_configured_mcp_route_profile", lambda: "strict")
    monkeypatch.setattr(setup_module, "CodexPluginService", _FakePluginService)

    def absent_skill_destination(
        _target: IntegrationTarget, _source: SkillSource
    ) -> SimpleNamespace:
        return SimpleNamespace(
            state=IntegrationState.ABSENT,
            installed_digest=None,
        )

    monkeypatch.setattr(
        setup_module,
        "inspect_destination",
        absent_skill_destination,
    )
    from yoetz.adapters.integrations.codex_plugin import (
        PluginHookPresence,
        PluginInspection,
    )

    def absent_plugin(_target: object) -> PluginInspection:
        return PluginInspection(
            PluginHookPresence.ABSENT,
            False,
            None,
            ("codex_hook_trust_not_observable_from_installation_state",),
        )

    monkeypatch.setattr(
        setup_module,
        "inspect_plugin",
        absent_plugin,
    )
    monkeypatch.setattr(setup_module, "setup_marker_path", lambda: marker)
    monkeypatch.setattr(
        setup_module,
        "_grant_observation_consent",
        fake_grant_observation_consent,
    )
    monkeypatch.setattr(setup_module, "_install_project_skill", fake_install_project_skill)
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
    assert "yoetz --privacy" in steps
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
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["registration"]["outcome"] == "registered"
    assert report["registration"]["state"] == "yoetz_owned"
    assert report["registration"]["observation_consent"] == {
        "outcome": "granted",
        "workspace_commitment": "hmac-sha256:" + "a" * 64,
    }
    assert report["registration"]["plugin"]["outcome"] == "installed"
    assert report["registration"]["plugin"]["presence"] == "installed"
    assert report["registration"]["skill"]["outcome"] == "installed"
    assert report["registration"]["skill"]["presence"] == "installed_exact"
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
    assert report["registration"]["skill"]["outcome"] == "installed"
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
    assert report["registration"]["skill"]["outcome"] == "skipped"
    # No mutating `mcp add` ever ran.
    for calls in cast(list[list[tuple[str, ...]]], wizard_env["calls"]):
        assert all(call[1:3] == ("mcp", "get") for call in calls)


@pytest.mark.anyio
async def test_tui_apply_refuses_a_skill_preview_digest_not_shown_to_the_user(
    wizard_env: dict[str, object],
) -> None:
    import yoetz.cli.setup as setup_module

    preview_runner = _ScriptedRunner([_yoetz_entry()])
    mcp_preview = await HarnessMcpService(
        CodexMcpAdapter(preview_runner, route_profile="strict")
    ).preview(_binary())
    workspace = cast(Path, wizard_env["marker"]).parent
    wizard_env["outputs"] = [_yoetz_entry()]

    report = await setup_module.apply_codex_integration(
        _binary(),
        workspace=workspace,
        approved_preview_digest=mcp_preview.preview_digest,
        approved_skill_preview_digest="sha256:" + "0" * 64,
    )

    assert report["outcome"] == "failed"
    assert report["reason"] == "preview_stale"
    skill_report = report["skill"]
    assert isinstance(skill_report, dict)
    assert skill_report["reason"] == "preview_stale"
    assert not (workspace / ".agents").exists()


@pytest.mark.anyio
async def test_a_preview_and_apply_that_disagree_on_the_route_refuse_as_stale(
    wizard_env: dict[str, object],
) -> None:
    """The terminal interface previews and applies in two calls, so the route must survive both.

    This is the shape of a defect the terminal interface used to have: it previewed with the
    adapter default (``policy``) and applied on the configured route, which resolves ``strict``
    with no provider bound, so first run's first action failed as stale. The route now travels
    on the approved plan, and this pins the gate that made the mismatch visible rather than
    silent. Only the route differs here -- the skill digest is the real one, so nothing else
    can explain the refusal.
    """

    import yoetz.cli.setup as setup_module

    workspace = cast(Path, wizard_env["marker"]).parent
    skill_preview = await setup_module.project_skill_preview(workspace)

    # Exactly what the TUI does today: no route_profile, so the class default (policy) applies.
    preview_runner = _ScriptedRunner([CommandOutput(1, b"")])
    mcp_preview = await HarnessMcpService(CodexMcpAdapter(preview_runner)).preview(_binary())

    # And exactly what apply does today: the configured route, strict with no provider bound.
    wizard_env["outputs"] = [CommandOutput(1, b"")]
    report = await setup_module.apply_codex_integration(
        _binary(),
        workspace=workspace,
        approved_preview_digest=mcp_preview.preview_digest,
        approved_skill_preview_digest=skill_preview.preview_digest,
    )

    assert report["outcome"] == "failed"
    assert report["reason"] == "preview_stale"
    assert not (workspace / ".agents").exists()


@pytest.mark.anyio
async def test_a_preview_and_apply_on_the_same_route_register(
    wizard_env: dict[str, object],
) -> None:
    """Control for the refusal above: the route is the cause, not the skill or plugin previews.

    Identical to that case in every input except the previewing adapter's route, which here
    matches what apply resolves. Without this, a refusal for some unrelated reason would read
    as proof of a route problem.
    """

    import yoetz.cli.setup as setup_module

    workspace = cast(Path, wizard_env["marker"]).parent
    skill_preview = await setup_module.project_skill_preview(workspace)

    preview_runner = _ScriptedRunner([CommandOutput(1, b"")])
    mcp_preview = await HarnessMcpService(
        CodexMcpAdapter(preview_runner, route_profile="strict")
    ).preview(_binary())

    wizard_env["outputs"] = [
        CommandOutput(1, b""),  # step preview get: absent
        CommandOutput(1, b""),  # adapter install re-preview get: absent
        CommandOutput(0, b""),  # add
        _yoetz_entry("strict"),  # verify get
    ]
    report = await setup_module.apply_codex_integration(
        _binary(),
        workspace=workspace,
        approved_preview_digest=mcp_preview.preview_digest,
        approved_skill_preview_digest=skill_preview.preview_digest,
    )

    assert report["reason"] is None
    assert report["outcome"] == "registered"


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

    # harness 1, installation 2, review mode 2 (local only), a rejected y/n, then confirm.
    result = _RUNNER.invoke(cli.app, ["setup", "run"], input="1\n2\n2\nmaybe\nY\n")

    assert result.exit_code == 0
    assert "Automatically detected harnesses:" in result.stdout
    assert "1. Codex (2 installations)" in result.stdout
    assert "Select a harness to connect to Yoetz" in result.stdout
    assert "Detected Codex installations:" in result.stdout
    assert "Select the Codex installation to configure" in result.stdout
    assert "Choose how Yoetz should review work:" in result.stdout
    assert "complete Yoetz Codex project integration" in result.stdout
    assert "MCP server name: yoetz" in result.stdout
    assert "Command: yoetz mcp serve --semantic off" in result.stdout
    assert "Codex executable: /b/codex" in result.stdout
    assert "Confirm Codex project setup? [Y/N]" in result.stdout
    assert "Observation consent for this workspace" in result.stdout
    assert "Please enter Y or N." in result.stdout
    assert "MCP registration: registered; automatic activation not tested" in result.stdout
    assert "Skill support: no tested capability profile; automatic activation not tested" in (
        result.stdout
    )
    assert "Plugin source files:" in result.stdout
    assert "Project skill installation:" in result.stdout
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

    result = _RUNNER.invoke(cli.app, ["setup", "run"], input="1\n1\nN\n")

    assert result.exit_code == 0
    assert "Confirm Codex project setup? [Y/N]" in result.stdout
    assert "MCP registration: declined" in result.stdout
    assert "Skill support: no tested capability profile; automatic activation not tested" in (
        result.stdout
    )
    for calls in cast(list[list[tuple[str, ...]]], wizard_env["calls"]):
        assert all(call[1:3] == ("mcp", "get") for call in calls)


def test_semantic_first_run_suggests_and_selects_assisted_privacy_draft(
    wizard_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as privacy_setup_module
    import yoetz.cli.provider_status as provider_status_module
    import yoetz.cli.setup as setup_module

    wizard_env["outputs"] = [
        CommandOutput(1, b""),  # preview get: absent
        CommandOutput(1, b""),  # apply re-preview get: absent
        CommandOutput(0, b""),  # add
        _yoetz_entry("policy"),  # verify get
    ]
    privacy_calls: list[tuple[str | None, bool, bool]] = []

    async def ready(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    async def provider_setup(
        service: dict[str, object],
        *,
        provider_choice: str | None = None,
        model: str | None = None,
        before_credential: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del provider_choice, model
        assert before_credential is not None
        assert await before_credential() is True
        return service, {"binding": "configured", "credential": "stored"}

    async def privacy_setup(
        *,
        recipe_hint: str | None = None,
        offer_recommended: bool = False,
        credential_probe_authorized: bool = False,
    ) -> SimpleNamespace:
        privacy_calls.append((recipe_hint, offer_recommended, credential_probe_authorized))
        return SimpleNamespace(
            outcome="configured",
            profile="confirm_every_request",
            proposal_id="pvp_1",
            reason=None,
        )

    async def provider_status() -> dict[str, object]:
        return {"semantic_ready": True}

    monkeypatch.setattr(setup_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(setup_module, "_service_reachability", ready)
    monkeypatch.setattr(setup_module, "_interactive_provider_setup", provider_setup)
    monkeypatch.setattr(setup_module, "_restart_service_for_semantic_composition", ready)
    monkeypatch.setattr(privacy_setup_module, "run_privacy_setup", privacy_setup)
    monkeypatch.setattr(provider_status_module, "provider_status_report", provider_status)

    # Harness 1, semantic review, registration confirmation, then credential-probe consent.
    result = _RUNNER.invoke(cli.app, ["setup", "run"], input="1\n1\nY\nY\n")

    assert result.exit_code == 0
    assert privacy_calls == [("assisted_review", True, True)]
    assert "Privacy: configured (confirm_every_request)" in result.stdout


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
    # `yoetz_owned` reads the same for both serve commands, so the row has to name the route.
    assert report["discovered"][0]["registered_route_profile"] == "strict"
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
            "compatibility": "unsupported",
            "installed_digest": None,
            "presence": "absent",
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
        "compatibility": "unsupported",
        "installed_digest": None,
        "presence": "unknown",
        "source_state": "source_invalid",
        "tested_profiles": [],
    }


def test_installed_hooks_use_nested_plugin_hook_path(tmp_path: Path) -> None:
    import yoetz.cli.setup as setup_module

    hooks_path = tmp_path / ".agents" / "plugins" / "yoetz" / "hooks" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        '{"hooks":{"SessionStart":[{"command":"yoetz hooks observe '
        '--event SessionStart --workspace ."}]}}'
    )

    assert (
        setup_module._installed_hooks_declare_workspace_binding(tmp_path) is True  # pyright: ignore[reportPrivateUsage]
    )


def test_integrate_mcp_status_preview_install(wizard_env: dict[str, object]) -> None:
    wizard_env["outputs"] = [CommandOutput(1, b"")]
    status = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "status", "--json"])
    assert status.exit_code == 0
    absent_status = json.loads(status.stdout)
    assert absent_status["state"] == "absent"
    # An absent entry has no Yoetz route to describe; the field is present and null, never guessed.
    assert absent_status["route_profile"] is None

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


def test_integrate_mcp_status_names_the_registered_route(
    wizard_env: dict[str, object],
) -> None:
    """Status has to distinguish the two owned registrations, or #132's conflation stays.

    Both serve commands classify as ``yoetz_owned``, so an operator reading only the state
    cannot tell whether the agent route can request semantic review at all.
    """

    for route_profile in ("strict", "policy"):
        wizard_env["outputs"] = [_yoetz_entry(route_profile)]
        result = _RUNNER.invoke(cli.app, ["integrate", "codex", "mcp", "status", "--json"])
        assert result.exit_code == 0
        body = json.loads(result.stdout)
        assert body["state"] == "yoetz_owned"
        assert body["route_profile"] == route_profile


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
    for args in (
        ["--help"],
        ["setup", "run", "--help"],
        ["setup", "status", "--help"],
    ):
        result = _RUNNER.invoke(cli.app, args)
        assert result.exit_code == 0
        lowered = result.stdout.lower()
        for token in ("api-key", "apikey", "password"):
            assert token not in lowered, (args, token)


def test_bare_invocation_without_tty_prints_help() -> None:
    result = _RUNNER.invoke(cli.app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_root_privacy_shortcut_dispatches_guided_privacy_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def privacy_setup() -> int:
        calls.append("privacy")
        return 0

    monkeypatch.setattr(cli, "_run_privacy_setup_command", privacy_setup)

    result = _RUNNER.invoke(cli.app, ["--privacy"])

    assert result.exit_code == 0
    assert calls == ["privacy"]


def test_root_privacy_shortcut_rejects_provider_setup_options() -> None:
    result = _RUNNER.invoke(cli.app, ["--privacy", "--set"], env={"NO_COLOR": "1"})

    assert result.exit_code == 2
    assert "--privacy cannot be combined" in _plain(result.output)


def test_root_set_fireworks_dispatches_simple_provider_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.setup as setup_module

    received: dict[str, object] = {}

    async def fake_provider_setup(*, fireworks: bool, model: str | None) -> int:
        received.update(fireworks=fireworks, model=model)
        return 0

    monkeypatch.setattr(setup_module, "run_provider_setup", fake_provider_setup)
    result = _RUNNER.invoke(
        cli.app,
        [
            "--set",
            "--fireworks",
            "--model",
            "accounts/fireworks/models/minimax-m3",
        ],
    )

    assert result.exit_code == 0
    assert received == {
        "fireworks": True,
        "model": "accounts/fireworks/models/minimax-m3",
    }


def test_root_set_dispatches_new_provider_setup_without_secret_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.setup as setup_module

    received: dict[str, object] = {}

    async def fake_provider_setup(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr(setup_module, "run_provider_setup", fake_provider_setup)
    result = _RUNNER.invoke(
        cli.app,
        ["--set", "--provider", "anthropic", "--model", "claude-sonnet-4-6"],
    )

    assert result.exit_code == 0
    assert received == {
        "fireworks": False,
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    }


def test_root_set_grok_dispatches_simple_provider_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.setup as setup_module

    received: dict[str, object] = {}

    async def fake_provider_setup(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr(setup_module, "run_provider_setup", fake_provider_setup)
    result = _RUNNER.invoke(cli.app, ["--set", "--grok", "--model", "grok-4.5"])

    assert result.exit_code == 0
    assert received == {"fireworks": False, "model": "grok-4.5", "grok": True}


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
    from yoetz.cli.privacy_setup import PrivacySetupReport

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready"}

    async def fake_interactive(
        service: dict[str, object],
        *,
        provider_choice: str | None = None,
        model: str | None = None,
        before_credential: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del provider_choice, model
        assert before_credential is not None
        assert await before_credential() is True
        return service, {"binding": "configured", "credential": "stored"}

    async def fake_privacy_setup(**_kwargs: object) -> PrivacySetupReport:
        return PrivacySetupReport("configured", "trusted_provider")

    monkeypatch.setattr(setup_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)
    monkeypatch.setattr(setup_module, "_interactive_provider_setup", fake_interactive)
    monkeypatch.setattr("yoetz.cli.privacy_setup.run_privacy_setup", fake_privacy_setup)

    result = _RUNNER.invoke(cli.app, ["--set", "--fireworks", "--model", "m"], input="Y\n")
    plain = _plain(result.output)
    assert result.exit_code == 0
    assert "Yoetz is ready to use this provider." not in plain
    assert "Provider binding and vault credential storage succeeded" in plain
    assert "SDK extra (semantic-openai):" in plain
    assert "_semantic_not_configured" in plain
    assert "Privacy policy: configured" in plain
    assert "Transport probe: not demonstrated" in plain
    assert "Installed artifact evidence: not demonstrated" in plain
    assert "not proof of live provider dispatch or semantic review" in plain


@pytest.mark.parametrize(
    "choice",
    [
        "official_openai",
        "fireworks",
        "anthropic",
        "google_gemini",
        "openrouter",
        "grok",
        "vercel_ai_gateway",
    ],
)
def test_secure_set_paths_share_provider_model_picker(
    choice: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.setup as setup_module
    import yoetz.config.load as config_module

    picked: list[str] = []
    applied: list[tuple[str, str]] = []

    def fake_pick(selected_choice: str) -> str:
        picked.append(selected_choice)
        return "selected-model"

    def fake_apply(selected_choice: str, *, model: str) -> tuple[Path, object]:
        applied.append((selected_choice, model))
        return tmp_path / "config.toml", object()

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(provider=None)

    monkeypatch.setattr(binding_module, "prompt_provider_model", fake_pick)
    monkeypatch.setattr(binding_module, "apply_provider_endpoint_choice", fake_apply)
    monkeypatch.setattr(config_module, "load_config", fake_load_config)

    _service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "unavailable"},
            provider_choice=choice,
        )
    )

    assert picked == [choice]
    assert applied == [(choice, "selected-model")]
    assert report["binding"] == "configured"
    assert report["credential_reason"] == "service_not_ready"


def test_uninitialized_provider_setup_provisions_auto_unlock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wizard must keep the auto-unlock write side reachable in product code."""

    import yoetz.adapters.keys.os_keyring as keyring_module
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.paths as paths_module

    calls: list[str] = []
    supplied: list[bytes] = []
    loaded_env: list[object] = []

    def fake_create_for_initialization(_store: object) -> bytearray:
        calls.append("create_for_initialization")
        return bytearray(b"a" * 48)

    async def fake_initialize(passphrase: bytearray | None = None) -> object:
        supplied.append(bytes(passphrase or b""))
        return object()

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    def fake_bundle_root(*, _data_dir: Path | None = None, _probe: object | None = None) -> Path:
        del _data_dir, _probe
        return tmp_path.resolve()

    def fake_load_config(overrides: object, env: object, config_path: object) -> SimpleNamespace:
        del overrides, config_path
        loaded_env.append(env)
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path))

    monkeypatch.setattr(
        keyring_module.AutoUnlockPassphraseStore,
        "create_for_initialization",
        fake_create_for_initialization,
    )
    monkeypatch.setattr(unlock_module, "initialize_passphrase_vault", fake_initialize)
    monkeypatch.setattr(binding_module, "prompt_provider_endpoint_binding", lambda: None)
    monkeypatch.setattr(config_module, "load_config", fake_load_config)
    monkeypatch.setattr(paths_module, "bundle_root", fake_bundle_root)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)

    service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "locked", "vault_mode": "uninitialized"}
        )
    )

    assert calls == ["create_for_initialization"]
    assert loaded_env == [os.environ]
    assert supplied == [b"a" * 48]
    assert service["state"] == "ready"
    assert report["binding"] == "skipped"


def test_uninitialized_setup_refuses_preexisting_auto_unlock_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RT-vault-secrets-1 / ADR-015: pre-existing scoped entry must block vault init.

    Interactive setup must use create_for_initialization (like elevated bootstrap)
    and must never submit a pre-planted auto-unlock secret to vault_initialize.
    """

    import base64
    import hashlib

    import yoetz.adapters.keys.os_keyring as keyring_module
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.paths as paths_module

    # Known secret that must not become the vault root passphrase.
    planted = bytearray(b"attacker-known-vault-secret-0123456789abcdef!!!!")
    assert 32 <= len(planted) <= 128

    class _AtomicBackend:
        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.values.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.values[(service, username)] = password

    backend = _AtomicBackend()
    store = keyring_module.AutoUnlockPassphraseStore(tmp_path.resolve(), backend=backend)
    store._backend_id = (  # pyright: ignore[reportPrivateUsage]
        "keyring.backends.macOS.Keyring"
    )
    store.save(bytearray(planted))
    encoded = base64.urlsafe_b64encode(planted).rstrip(b"=").decode("ascii")
    username = (
        "bundle-" + hashlib.sha256(os.fsencode(os.path.abspath(tmp_path.resolve()))).hexdigest()
    )
    assert backend.values[("yoetz.auto-unlock.v1", username)] == encoded
    with pytest.raises(keyring_module.OSKeyringError) as elevated_exc:
        store.create_for_initialization()
    assert elevated_exc.value.reason == "entry_exists"

    supplied: list[bytes] = []

    async def fake_initialize(passphrase: bytearray | None = None) -> object:
        supplied.append(bytes(passphrase or b""))
        return object()

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    def fake_store(_path: Path) -> keyring_module.AutoUnlockPassphraseStore:
        return store

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path))

    def fake_bundle_root(*, _data_dir: Path | None = None, _probe: object | None = None) -> Path:
        del _data_dir, _probe
        return tmp_path.resolve()

    monkeypatch.setattr(keyring_module, "AutoUnlockPassphraseStore", fake_store)
    monkeypatch.setattr(unlock_module, "initialize_passphrase_vault", fake_initialize)
    monkeypatch.setattr(binding_module, "prompt_provider_endpoint_binding", lambda: None)
    monkeypatch.setattr(config_module, "load_config", fake_load_config)
    monkeypatch.setattr(paths_module, "bundle_root", fake_bundle_root)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)

    service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "locked", "vault_mode": "uninitialized"}
        )
    )

    # Fail-closed: no vault_initialize with the planted secret; entry unchanged.
    assert supplied == []
    assert service == {"reachable": True, "state": "locked", "vault_mode": "uninitialized"}
    assert report["credential_reason"] == "auto_unlock_entry_exists"
    assert report["binding"] == "skipped"
    assert backend.values[("yoetz.auto-unlock.v1", username)] == encoded

    # Blocking is only useful if the operator can find and clear the entry that blocked them,
    # and the message must never echo the secret it refused to adopt.
    stderr = capsys.readouterr().err
    assert "yoetz.auto-unlock.v1" in stderr
    assert username in stderr
    assert "yoetz setup" in stderr
    assert encoded not in stderr
    assert planted.decode("ascii") not in stderr


def test_uninitialized_setup_stops_after_write_with_failed_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ambiguous committed entry must never trigger a different manual passphrase."""

    import yoetz.adapters.keys.os_keyring as keyring_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.paths as paths_module

    class _WriteThenUnreadable:
        def __init__(self) -> None:
            self.written: str | None = None

        def get_password(self, service: str, username: str) -> None:
            del service, username
            return None

        def set_password(self, service: str, username: str, password: str) -> None:
            del service, username
            self.written = password

    backend = _WriteThenUnreadable()
    store = keyring_module.AutoUnlockPassphraseStore(tmp_path.resolve(), backend=backend)
    store._backend_id = (  # pyright: ignore[reportPrivateUsage]
        "keyring.backends.macOS.Keyring"
    )
    initialized: list[bytes] = []

    async def fake_initialize(passphrase: bytearray | None = None) -> object:
        initialized.append(bytes(passphrase or b""))
        return object()

    def fake_store(_path: Path) -> keyring_module.AutoUnlockPassphraseStore:
        return store

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path))

    def fake_bundle_root(*, _data_dir: Path | None = None) -> Path:
        del _data_dir
        return tmp_path.resolve()

    monkeypatch.setattr(keyring_module, "AutoUnlockPassphraseStore", fake_store)
    monkeypatch.setattr(unlock_module, "initialize_passphrase_vault", fake_initialize)
    monkeypatch.setattr(config_module, "load_config", fake_load_config)
    monkeypatch.setattr(paths_module, "bundle_root", fake_bundle_root)

    service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "locked", "vault_mode": "uninitialized"}
        )
    )

    assert backend.written is not None
    assert initialized == []
    assert service == {"reachable": True, "state": "locked", "vault_mode": "uninitialized"}
    assert report["credential_reason"] == "auto_unlock_unverified"


def test_ready_auto_unlock_vault_reuses_scoped_secret_for_provider_reauthentication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fresh Keychain-backed setup must ask only for the provider credential."""

    import yoetz.adapters.keys.os_keyring as keyring_module
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.paths as paths_module
    import yoetz.config.write as write_module

    loaded: list[bytes] = []
    supplied_reauthentication: list[bytes | None] = []

    def fake_load(_store: object) -> bytearray:
        loaded.append(b"a" * 48)
        return bytearray(b"a" * 48)

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            storage=SimpleNamespace(data_dir=tmp_path),
            provider=SimpleNamespace(
                provider_id="fireworks",
                model="accounts/fireworks/models/minimax-m3",
                endpoint_profile_id="fireworks-responses",
                endpoint_profile_version="1.0.0",
            ),
        )

    def fake_bundle_root(*, _data_dir: Path | None = None) -> Path:
        del _data_dir
        return tmp_path.resolve()

    def fake_write(_choice: str, *, model: str) -> tuple[Path, object]:
        assert _choice == "fireworks"
        assert model == "accounts/fireworks/models/minimax-m3"
        return tmp_path / "config.toml", object()

    async def fake_set(
        _target: object,
        _credential: bytearray | None,
        reauthentication: bytearray | None,
    ) -> SimpleNamespace:
        supplied_reauthentication.append(
            None if reauthentication is None else bytes(reauthentication)
        )
        return SimpleNamespace(activation_status="stored")

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    async def fake_restart() -> dict[str, object]:
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    async def fake_provider_status() -> dict[str, object]:
        return {"credential_connected": False}

    def fake_provider_preset(_provider: str) -> SimpleNamespace:
        return SimpleNamespace(choice="fireworks", provider_id="fireworks")

    monkeypatch.setattr(keyring_module.AutoUnlockPassphraseStore, "load", fake_load)
    monkeypatch.setattr(config_module, "load_config", fake_load_config)
    monkeypatch.setattr(paths_module, "bundle_root", fake_bundle_root)
    monkeypatch.setattr(binding_module, "apply_provider_endpoint_choice", fake_write)
    monkeypatch.setattr(unlock_module, "set_provider_credential", fake_set)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)
    monkeypatch.setattr(setup_module, "_restart_service_for_semantic_composition", fake_restart)
    import yoetz.cli.provider_status as provider_status_module

    monkeypatch.setattr(provider_status_module, "provider_status_report", fake_provider_status)
    monkeypatch.setattr(
        write_module,
        "provider_preset",
        fake_provider_preset,
    )

    _service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "ready", "vault_mode": "passphrase"},
            provider_choice="fireworks",
            model="accounts/fireworks/models/minimax-m3",
        )
    )

    assert loaded == [b"a" * 48]
    assert supplied_reauthentication == [b"a" * 48]
    assert report["binding"] == "configured"
    assert report["credential"] == "stored"
    assert report["credential_display"] == "********"


def test_existing_bound_credential_can_be_reused_without_requesting_it_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.provider_status as provider_status_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.write as write_module

    provider = SimpleNamespace(
        provider_id="fireworks",
        model="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
    )

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path), provider=provider)

    def fake_write(*_args: object, **_kwargs: object) -> tuple[Path, object]:
        return tmp_path / "config.toml", object()

    def fake_preset(_provider: object) -> SimpleNamespace:
        return SimpleNamespace(choice="fireworks", provider_id="fireworks")

    monkeypatch.setattr(
        config_module,
        "load_config",
        fake_load_config,
    )
    monkeypatch.setattr(
        binding_module,
        "apply_provider_endpoint_choice",
        fake_write,
    )
    monkeypatch.setattr(write_module, "provider_preset", fake_preset)

    async def fake_restart() -> dict[str, object]:
        return {"reachable": True, "state": "ready", "vault_mode": "passphrase"}

    async def fake_status() -> dict[str, object]:
        return {"credential_connected": True}

    async def forbidden_set(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stored credential must not be requested again")

    monkeypatch.setattr(setup_module, "_restart_service_for_semantic_composition", fake_restart)
    monkeypatch.setattr(provider_status_module, "provider_status_report", fake_status)
    monkeypatch.setattr(unlock_module, "set_provider_credential", forbidden_set)

    def reuse(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(setup_module.typer, "confirm", reuse)

    _service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "ready", "vault_mode": "passphrase"},
            provider_choice="fireworks",
            model="accounts/fireworks/models/minimax-m3",
        )
    )

    assert report["credential"] == "stored"
    assert report["credential_display"] == "********"


@pytest.mark.parametrize(
    ("result_lost", "expected_credential"),
    [(False, "stored"), (True, "failed")],
)
def test_existing_bound_credential_replacement_does_not_inherit_old_presence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result_lost: bool,
    expected_credential: str,
) -> None:
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.provider_status as provider_status_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.write as write_module
    from yoetz.service.confidential_client import ConfidentialClientError

    provider = SimpleNamespace(
        provider_id="fireworks",
        model="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
    )
    replacements: list[object] = []

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path), provider=provider)

    def fake_write(*_args: object, **_kwargs: object) -> tuple[Path, object]:
        return tmp_path / "config.toml", object()

    def fake_preset(_provider: object) -> SimpleNamespace:
        return SimpleNamespace(choice="fireworks", provider_id="fireworks")

    async def fake_restart() -> dict[str, object]:
        return {"reachable": True, "state": "ready", "vault_mode": "os_managed"}

    async def fake_status() -> dict[str, object]:
        return {"credential_connected": True}

    async def replace(target: object, *_args: object, **_kwargs: object) -> SimpleNamespace:
        replacements.append(target)
        if result_lost:
            raise ConfidentialClientError("ambiguous")
        return SimpleNamespace(activation_status="stored")

    async def fake_reachability(*, start_if_absent: bool = False) -> dict[str, object]:
        del start_if_absent
        return {"reachable": True, "state": "ready", "vault_mode": "os_managed"}

    monkeypatch.setattr(config_module, "load_config", fake_load_config)
    monkeypatch.setattr(binding_module, "apply_provider_endpoint_choice", fake_write)
    monkeypatch.setattr(write_module, "provider_preset", fake_preset)
    monkeypatch.setattr(setup_module, "_restart_service_for_semantic_composition", fake_restart)
    monkeypatch.setattr(provider_status_module, "provider_status_report", fake_status)
    monkeypatch.setattr(unlock_module, "set_provider_credential", replace)
    monkeypatch.setattr(setup_module, "_service_reachability", fake_reachability)

    def replace_stored(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(setup_module.typer, "confirm", replace_stored)

    _service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "ready", "vault_mode": "os_managed"},
            provider_choice="fireworks",
            model="accounts/fireworks/models/minimax-m3",
        )
    )

    assert len(replacements) == 1
    assert report["credential"] == expected_credential
    if result_lost:
        assert report["credential_reason"] == "credential_ambiguous"
        assert "credential_display" not in report
    else:
        assert report["credential_display"] == "********"


def test_lost_credential_result_recovers_from_recomposed_presence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yoetz.cli.provider_binding as binding_module
    import yoetz.cli.provider_status as provider_status_module
    import yoetz.cli.setup as setup_module
    import yoetz.cli.unlock as unlock_module
    import yoetz.config.load as config_module
    import yoetz.config.write as write_module
    from yoetz.service.confidential_client import ConfidentialClientError

    provider = SimpleNamespace(
        provider_id="fireworks",
        model="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
    )
    status_reads = iter((False, True))

    def fake_load_config(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(storage=SimpleNamespace(data_dir=tmp_path), provider=provider)

    def fake_write(*_args: object, **_kwargs: object) -> tuple[Path, object]:
        return tmp_path / "config.toml", object()

    def fake_preset(_provider: object) -> SimpleNamespace:
        return SimpleNamespace(choice="fireworks", provider_id="fireworks")

    monkeypatch.setattr(
        config_module,
        "load_config",
        fake_load_config,
    )
    monkeypatch.setattr(
        binding_module,
        "apply_provider_endpoint_choice",
        fake_write,
    )
    monkeypatch.setattr(write_module, "provider_preset", fake_preset)

    async def fake_restart() -> dict[str, object]:
        return {"reachable": True, "state": "ready", "vault_mode": "os_managed"}

    async def fake_status() -> dict[str, object]:
        return {"credential_connected": next(status_reads)}

    async def lost_result(*_args: object, **_kwargs: object) -> object:
        raise ConfidentialClientError("ambiguous")

    monkeypatch.setattr(setup_module, "_restart_service_for_semantic_composition", fake_restart)
    monkeypatch.setattr(provider_status_module, "provider_status_report", fake_status)
    monkeypatch.setattr(unlock_module, "set_provider_credential", lost_result)

    _service, report = asyncio.run(
        setup_module._interactive_provider_setup(  # pyright: ignore[reportPrivateUsage]
            {"reachable": True, "state": "ready", "vault_mode": "os_managed"},
            provider_choice="fireworks",
            model="accounts/fireworks/models/minimax-m3",
        )
    )

    assert report["credential"] == "stored"
    assert report["credential_display"] == "********"
    assert report["credential_reason"] == "stored_result_recovered"
