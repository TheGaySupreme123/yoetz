"""First-run setup wizard: harness, local service, and provider connection.

The wizard orchestrates only operations a human could already run by hand: it
discovers Codex binaries, previews and (after explicit confirmation) applies the
runbook's ``codex mcp get``/``codex mcp add`` sequence, checks whether the local
service is reachable, and—only on a local interactive terminal—runs the existing
vault and provider-credential ceremonies. Secret bytes remain inside the dedicated
hidden-input confidential helper and never enter wizard arguments, config, or MCP.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import anyio
import typer

from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
from yoetz.adapters.integrations.codex_plugin import PluginHookPresence, inspect_plugin
from yoetz.adapters.integrations.codex_skill import (
    CodexSkillIntegration,
    inspect_destination,
    load_packaged_skill_source,
)
from yoetz.application.codex_plugin import CodexPluginService
from yoetz.application.harness_mcp import HarnessMcpService, McpRegistrationConfirmation
from yoetz.application.observation_check_policy import load_observation_check_policy
from yoetz.config.load import load_config
from yoetz.config.paths import PathSafetyError, setup_marker_path
from yoetz.domain.values import RequestId, request_id
from yoetz.ports.harness_mcp import (
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationError,
    McpRegistrationState,
)
from yoetz.ports.integrations import (
    HarnessId,
    IntegrationAction,
    IntegrationError,
    IntegrationPreview,
    IntegrationScope,
    IntegrationTarget,
    SkillApplyCommand,
    SkillPreviewCommand,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, new_id

__all__ = [
    "SETUP_MARKER_SCHEMA",
    "apply_codex_integration",
    "check_policy_preview",
    "configured_mcp_route_profile",
    "integrate_mcp",
    "project_skill_preview",
    "run_provider_setup",
    "run_setup_wizard",
    "setup_marker_present",
    "setup_status",
    "should_offer_first_run",
    "write_setup_marker",
]

SETUP_MARKER_SCHEMA: Final = "yoetz.setup-wizard-marker/1"
_REPORT_SCHEMA: Final = "yoetz.setup-wizard-report/1"
_STATUS_SCHEMA: Final = "yoetz.setup-status/1"
_HARNESS_DISPLAY_NAMES: Final[dict[HarnessId, str]] = {HarnessId.CODEX: "Codex"}

_NEXT_SERVICE: Final = "run 'yoetz service run' under your selected user supervisor"
_NEXT_UNLOCK: Final = "run 'yoetz service unlock' from a local terminal if the vault is locked"
_NEXT_PRIVACY: Final = "run 'yoetz --privacy' to review or change the privacy policy"
_NEXT_PROVIDER_TOML: Final = (
    "run 'yoetz provider endpoint' (or edit config.toml) to choose a reviewed provider "
    "or an owner-declared HTTPS origin+model — never put API keys in TOML"
)
_NEXT_CREDENTIAL: Final = (
    "run 'yoetz provider credential set' from a local terminal to provision the "
    "provider credential through the confidential ceremony"
)
_NEXT_RESTART: Final = "restart the Yoetz service so the configured semantic evaluator is composed"


def _stdout_json(value: JsonValue) -> None:
    sys.stdout.buffer.write(canonical_encode(value) + b"\n")
    sys.stdout.buffer.flush()


def _emit(value: JsonValue, *, json_output: bool) -> None:
    if json_output or not sys.stdout.isatty():
        _stdout_json(value)
    else:
        typer.echo(canonical_encode(value).decode("utf-8"))


def _usage_failure(message: str) -> int:
    typer.echo(f"invalid_request: {message}", err=True)
    return 2


class _UsageExit(Exception):
    """Internal control flow for a selection failure with an already-printed message."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def setup_marker_present() -> bool:
    """Report whether the wizard completion marker exists, failing closed on unsafe paths."""

    try:
        return setup_marker_path().is_file()
    except PathSafetyError, OSError:
        # An unsafe or unreadable state directory must never trigger the wizard.
        return True


def should_offer_first_run() -> bool:
    """True only for an interactive terminal with no completion marker recorded."""

    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False
    return interactive and not setup_marker_present()


def _integration_layers() -> dict[str, JsonValue]:
    """Inspect skill, plugin, hook, and trust state without inferring activation."""

    source = None
    try:
        source = load_packaged_skill_source()
    except IntegrationError as error:
        tested_profiles: list[JsonValue] = []
        skill_source_state = error.reason.value
    else:
        tested_profiles = list(source.harness_tested_set)
        skill_source_state = "verified"
    skill_presence = "unknown"
    skill_digest: str | None = None
    skill_compatibility = "unsupported"
    if source is not None:
        try:
            skill_inspection = inspect_destination(
                IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(Path.cwd())),
                source,
            )
        except IntegrationError:
            skill_presence = "unknown"
        else:
            skill_presence = skill_inspection.state.value
            skill_digest = skill_inspection.installed_digest
            skill_compatibility = "supported" if tested_profiles else "unsupported"
    try:
        inspection = inspect_plugin(
            IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(Path.cwd()))
        )
    except IntegrationError as error:
        return {
            "hooks": {
                "presence": "unknown",
                "trust_observable": False,
                "trust_state": "unknown",
            },
            "plugin": {
                "digest": None,
                "presence": "unknown",
                "reason": error.reason.value,
            },
            "skill": {
                "automatic_activation_tested": False,
                "compatibility": skill_compatibility,
                "installed_digest": skill_digest,
                "presence": skill_presence,
                "source_state": skill_source_state,
                "tested_profiles": tested_profiles,
            },
        }
    return {
        "hooks": {
            "presence": inspection.presence.value,
            "trust_observable": inspection.trust_observable,
            "trust_state": "observable" if inspection.trust_observable else "unknown",
        },
        "plugin": {
            "digest": inspection.installed_digest,
            "presence": inspection.presence.value,
        },
        "skill": {
            "automatic_activation_tested": bool(tested_profiles),
            "compatibility": skill_compatibility,
            "installed_digest": skill_digest,
            "presence": skill_presence,
            "source_state": skill_source_state,
            "tested_profiles": tested_profiles,
        },
    }


def _is_interactive_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False


def _choose_review_mode() -> Literal["local_only", "semantic"]:
    """Let first run choose its complete privacy posture before registration."""

    typer.echo("")
    typer.echo("Choose how Yoetz should review work:")
    typer.echo("  1. Local only — deterministic checks; nothing leaves this computer")
    typer.echo("  2. Semantic review — configure a provider, API key, and privacy policy")
    while True:
        raw = typer.prompt("Review mode", default="1").strip()
        if raw == "1":
            return "local_only"
        if raw == "2":
            return "semantic"
        typer.echo("Please enter 1 or 2.")


def _write_setup_marker(outcome: str) -> bool:
    try:
        path = setup_marker_path()
        payload = canonical_encode({"outcome": outcome, "schema": SETUP_MARKER_SCHEMA}) + b"\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    except PathSafetyError, OSError:
        return False
    return True


def _binary_row(binary: HarnessBinary) -> dict[str, JsonValue]:
    return {
        "compatibility": binary.compatibility,
        "executable_path": binary.executable_path,
        "harness": binary.harness_id.value,
        "reported_version": binary.reported_version,
    }


def _explicit_binary(codex_path: str) -> HarnessBinary | None:
    candidate = Path(os.path.abspath(codex_path))
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return HarnessBinary(
        harness_id=HarnessId.CODEX,
        executable_path=str(candidate),
        reported_version=None,
        compatibility="untested",
    )


def _choose_harness(
    binaries: tuple[HarnessBinary, ...],
    *,
    codex_path: str | None,
    interactive: bool,
) -> HarnessId | None:
    """Choose one detected supported harness before selecting its installation."""

    if codex_path is not None:
        return HarnessId.CODEX
    if not binaries:
        return None
    if not interactive:
        return HarnessId.CODEX

    harness = HarnessId.CODEX
    count = len(binaries)
    suffix = "installation" if count == 1 else "installations"
    typer.echo("Automatically detected harnesses:")
    typer.echo(f"  1. {_HARNESS_DISPLAY_NAMES[harness]} ({count} {suffix})")
    raw = typer.prompt("Select a harness to connect to Yoetz", default="1")
    if raw != "1":
        raise _UsageExit(_usage_failure("the harness selection is not one of the listed numbers"))
    return harness


def _choose_binary(
    binaries: tuple[HarnessBinary, ...],
    *,
    codex_path: str | None,
    interactive: bool,
) -> HarnessBinary | None:
    """Return the selected binary or ``None`` when none exists; raise ``_UsageExit`` otherwise."""

    if codex_path is not None:
        explicit = _explicit_binary(codex_path)
        if explicit is None:
            raise _UsageExit(_usage_failure("the --codex-path executable was not found"))
        return explicit
    if not binaries:
        return None
    if len(binaries) == 1:
        return binaries[0]
    if not interactive:
        raise _UsageExit(
            _usage_failure("multiple codex executables found; select one with --codex-path")
        )
    typer.echo("Detected Codex installations:")
    for index, binary in enumerate(binaries, start=1):
        version = binary.reported_version or "unknown version"
        typer.echo(f"  {index}. {binary.executable_path} ({version})")
    raw = typer.prompt("Select the Codex installation to configure", default="1")
    if not raw.isdecimal() or not 1 <= int(raw) <= len(binaries):
        raise _UsageExit(_usage_failure("the harness selection is not one of the listed numbers"))
    return binaries[int(raw) - 1]


def _confirm_registration() -> bool:
    """Require an explicit capital-insensitive Y or N with no default answer."""

    while True:
        raw = typer.prompt("Apply this registration? [Y/N]", show_default=False)
        answer = raw.strip().upper()
        if answer == "Y":
            return True
        if answer == "N":
            return False
        typer.echo("Please enter Y or N.")


def _confirm_project_setup(*, include_observation: bool, policy_digest: str | None) -> bool:
    """One confirmed operation covering MCP/plugin/guidance/hooks/observation consent."""

    typer.echo("This confirmation covers:")
    typer.echo("  - Project skill plus plugin / hook source installation in this trusted project")
    typer.echo("  - MCP registration")
    if include_observation:
        typer.echo(
            "  - Observation consent for this workspace "
            "(structural Codex events only; never raw path logged)"
        )
        typer.echo("  - Advice readiness once observation evidence exists")
    if policy_digest is not None:
        typer.echo(f"  - Trust exact approved-check policy digest {policy_digest}")
    while True:
        raw = typer.prompt("Confirm Codex project setup? [Y/N]", show_default=False)
        answer = raw.strip().upper()
        if answer == "Y":
            return True
        if answer == "N":
            return False
        typer.echo("Please enter Y or N.")


def _grant_observation_consent(workspace: Path | None = None) -> dict[str, JsonValue]:
    """Record observation consent via private workspace commitment (never log raw path)."""

    try:
        from yoetz.adapters.integrations.observation_local import LocalObservationStore

        store = LocalObservationStore()
        root = (workspace if workspace is not None else Path.cwd()).resolve()
        commitment = store.workspace_commitment(str(root))
        store.grant_consent(commitment)
        return {"outcome": "granted", "workspace_commitment": commitment}
    except Exception as error:
        return {
            "outcome": "failed",
            "reason": type(error).__name__,
            "workspace_commitment": None,
        }


def _check_policy_preview(workspace: Path | None = None) -> dict[str, JsonValue]:
    """Return a path-free exact-byte policy preview; repository bytes grant no authority."""

    root = (workspace if workspace is not None else Path.cwd()).resolve()
    try:
        policy, _raw = load_observation_check_policy(root)
    except Exception:
        return {"outcome": "absent", "policy_digest": None, "check_ids": ()}
    return {
        "outcome": "proposed",
        "policy_digest": policy.raw_digest,
        "check_ids": tuple(item.approval_id for item in policy.checks),
    }


def _activate_check_policy_trust(
    workspace: Path | None,
    preview: dict[str, JsonValue],
    *,
    exact_digest_confirmed: bool,
) -> dict[str, JsonValue]:
    """Activate only the exact bytes shown to a trusted local human."""

    digest = preview.get("policy_digest")
    if type(digest) is not str:
        return preview
    if not exact_digest_confirmed:
        return {**preview, "outcome": "untrusted_confirmation_required"}
    try:
        from yoetz.adapters.integrations.observation_local import LocalObservationStore

        root = (workspace if workspace is not None else Path.cwd()).resolve()
        policy, _raw = load_observation_check_policy(root)
        if policy.raw_digest != digest:
            return {**preview, "outcome": "untrusted_digest_changed"}
        store = LocalObservationStore()
        commitment = store.workspace_commitment(str(root))
        store.trust_policy_digest(commitment, digest)
        return {**preview, "outcome": "trusted"}
    except Exception:
        return {**preview, "outcome": "untrusted_activation_failed"}


def _emit_registration_preview(
    binary: HarnessBinary,
    mcp_preview: object,
    policy_preview: dict[str, JsonValue] | None = None,
    skill_preview: IntegrationPreview | None = None,
) -> None:
    typer.echo("Proposed change: complete Yoetz Codex project integration:")
    typer.echo("  1. Install discoverable guidance under .agents/skills/yoetz")
    typer.echo("  2. Install structural plugin / hook sources under .agents/plugins/yoetz")
    typer.echo("  3. Register the Yoetz MCP server with Codex")
    typer.echo("  MCP server name: yoetz")
    serve_command = getattr(mcp_preview, "serve_command", ())
    typer.echo(f"  Command: {' '.join(serve_command)}")
    typer.echo(f"  Codex executable: {binary.executable_path}")
    if binary.reported_version is not None:
        typer.echo(f"  Codex version: {binary.reported_version}")
    if skill_preview is not None:
        typer.echo(f"  Project skill state: {skill_preview.state_before.value}")
        typer.echo(f"  Project skill compatibility: {skill_preview.compatibility}")
        typer.echo(f"  Project skill preview digest: {skill_preview.preview_digest}")
    digest = None if policy_preview is None else policy_preview.get("policy_digest")
    if type(digest) is str:
        typer.echo(f"  Approved-check policy digest: {digest}")
        check_ids = policy_preview.get("check_ids") if policy_preview is not None else None
        if isinstance(check_ids, tuple):
            typer.echo(f"  Proposed checks: {', '.join(str(item) for item in check_ids)}")
        typer.echo("  Repository policy bytes propose commands; this confirmation activates only")
        typer.echo("  the exact digest above. Any byte change suspends execution.")


def _plugin_verified(presence: str | None) -> bool:
    return presence == PluginHookPresence.INSTALLED.value


async def _install_project_skill(
    target: IntegrationTarget,
    *,
    adapter: CodexSkillIntegration,
    operation_id: RequestId,
    accepted_preview: IntegrationPreview,
) -> dict[str, JsonValue]:
    """Install the project-scoped skill without claiming Codex capability support.

    Project skill discovery is a separate, native Codex surface from plugin loading.  Setup may
    explicitly install the reviewed bytes for an unprofiled Codex release after the enclosing
    project-integration preview is accepted, but the returned compatibility remains unsupported
    until an exact capability cell is frozen.
    """

    try:
        result = await adapter.install_skill(
            HarnessId.CODEX,
            SkillApplyCommand(
                operation_id,
                target,
                IntegrationAction.INSTALL,
                accepted_preview.preview_digest,
                True,
                False,
            ),
        )
    except IntegrationError as error:
        return {
            "compatibility": "unsupported",
            "outcome": "failed",
            "presence": None,
            "reason": error.reason.value,
        }
    return {
        "compatibility": accepted_preview.compatibility,
        "installed_digest": result.installed_digest,
        "outcome": "already_installed" if result.action is IntegrationAction.NOOP else "installed",
        "presence": result.state_after.value,
        "reason": None,
    }


@dataclass(frozen=True, slots=True)
class _ProjectSkillPlan:
    adapter: CodexSkillIntegration
    operation_id: RequestId
    preview: IntegrationPreview


async def _preview_project_skill(target: IntegrationTarget) -> _ProjectSkillPlan:
    """Create the exact project-skill plan before any setup acceptance is collected."""

    adapter = CodexSkillIntegration(allow_untested=True)
    operation_id = request_id(new_id(IdKind.REQUEST))
    preview = await adapter.preview_skill(
        HarnessId.CODEX,
        SkillPreviewCommand(
            operation_id,
            target,
            IntegrationAction.INSTALL,
            False,
        ),
    )
    return _ProjectSkillPlan(adapter, operation_id, preview)


async def project_skill_preview(workspace: Path | None = None) -> IntegrationPreview:
    """Return the exact setup skill preview for a non-prompt front end."""

    root = (workspace if workspace is not None else Path.cwd()).resolve()
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(root))
    return (await _preview_project_skill(target)).preview


def _configured_mcp_route_profile() -> Literal["policy", "strict"]:
    """Choose the registration-time route posture from structural local configuration."""

    try:
        config = load_config({}, os.environ, None)
    except Exception:
        return "strict"
    if config.verification.semantic == "disabled":
        return "strict"
    return "strict" if config.provider is None and config.local_model is None else "policy"


def configured_mcp_route_profile() -> Literal["policy", "strict"]:
    """Public registration-time route posture for front ends with no answer of their own.

    A caller that has just asked the human which review posture they want passes that answer
    instead: the reply is the authority, and configuration is only the fallback for surfaces
    (post-setup ``/connect``, ``integrate codex mcp``) that ask nothing.
    """

    return _configured_mcp_route_profile()


def _mcp_adapter(
    route_profile: Literal["policy", "strict"] | None = None,
) -> CodexMcpAdapter:
    return CodexMcpAdapter(
        route_profile=_configured_mcp_route_profile() if route_profile is None else route_profile
    )


def _installed_hooks_declare_workspace_binding(workspace: Path | None = None) -> bool:
    """True when the installed plugin hooks render ``--workspace .`` for observe."""

    root = (workspace if workspace is not None else Path.cwd()).resolve()
    hooks_path = root / ".agents" / "plugins" / "yoetz" / "hooks" / "hooks.json"
    try:
        raw = hooks_path.read_bytes()
    except OSError:
        return False
    return b"--workspace ." in raw and b"yoetz hooks observe" in raw


def _observation_hook_probe(*, workspace: Path | None = None) -> dict[str, JsonValue]:
    """Prove project binding + durable envelope enqueue via the installed observe path.

    Runs a synthetic SessionStart through ``handle_observe`` with ``--workspace .``
    semantics and ``skip_service=True`` so the probe stays local. Success requires an
    active consent commitment, a Codex-session→workspace bind, and a durable outbox
    entry (or acknowledged drain) for that session. Never logs or returns plaintext paths.
    """

    from yoetz.adapters.integrations.observation_local import LocalObservationStore
    from yoetz.cli.observe_hooks import handle_observe
    from yoetz.protocol.canonical import canonical_encode

    root = (workspace if workspace is not None else Path.cwd()).resolve()
    if not _installed_hooks_declare_workspace_binding(root):
        return {"ok": False, "reason": "hooks_missing_workspace_binding"}
    store = LocalObservationStore()
    commitment = store.workspace_commitment(str(root))
    consent = store.consent_for(commitment)
    if consent is None or not consent.active:
        return {"ok": False, "reason": "consent_inactive"}
    probe_session = "yoetz-setup-probe-session"
    payload = canonical_encode(
        {
            "session_id": probe_session,
            "hook_event_name": "SessionStart",
            "cwd": ".",
        }
    )
    with contextlib.redirect_stderr(io.StringIO()):
        code = handle_observe(
            event_name="SessionStart",
            stdin_bytes=payload,
            stdout=io.BytesIO(),
            workspace=".",
            skip_service=True,
        )
    if code != 0:
        return {"ok": False, "reason": "observe_exit_nonzero"}
    bound = store.find_workspace_for_codex_session(probe_session)
    if bound != commitment:
        return {"ok": False, "reason": "binding_missing"}
    pending = store.list_pending_outbox(commitment)
    if not pending:
        # SessionStart may have been drained/acked in a prior probe; binding alone
        # plus hooks workspace declaration still proves project routing.
        return {"ok": True, "reason": "bound_without_pending"}
    return {"ok": True, "reason": "envelope_enqueued"}


def _readiness_layers(
    *,
    binary: HarnessBinary | None,
    mcp_state: str | None,
    plugin_presence: str | None,
    skill_presence: str | None,
    hooks: dict[str, JsonValue],
    consent_outcome: str | None,
    service: dict[str, JsonValue],
    workspace: Path | None = None,
) -> dict[str, JsonValue]:
    consent_active = consent_outcome == "granted"
    service_routing = bool(service.get("reachable"))
    probe: dict[str, JsonValue] = {"ok": False, "reason": "not_attempted"}
    if _plugin_verified(plugin_presence) and consent_active:
        try:
            probe = _observation_hook_probe(workspace=workspace)
        except Exception as error:
            probe = {"ok": False, "reason": type(error).__name__}
    observation_ready = (
        _plugin_verified(plugin_presence)
        and consent_active
        and service_routing
        and bool(probe.get("ok"))
    )
    return {
        "codex": None
        if binary is None
        else {
            "executable_path": binary.executable_path,
            "reported_version": binary.reported_version,
        },
        "mcp_registration": mcp_state,
        "plugin_installation": plugin_presence,
        "project_skill_installation": skill_presence,
        "hooks": hooks,
        "consent": consent_outcome or "absent",
        "service_routing": {
            "reachable": service_routing,
            "state": service.get("state"),
        },
        "observation_hook_probe": probe,
        "observation_ready": observation_ready,
        "semantic_advice_ready": False,
        "semantic_advice_note": "deterministic_only_until_provider_ready",
    }


async def _codex_integration_step(
    binary: HarnessBinary,
    *,
    interactive: bool,
    accept: bool,
    route_profile: Literal["policy", "strict"] | None = None,
    workspace: Path | None = None,
    approved_preview_digest: str | None = None,
    approved_skill_preview_digest: str | None = None,
    approved_policy_digest: str | None = None,
) -> dict[str, JsonValue]:
    """Preview and apply one Codex integration: skill + plugin sources + MCP + consent.

    ``approved_preview_digest``/``approved_skill_preview_digest``/``approved_policy_digest``
    let a caller that has already shown a human the exact previews echo all digests back. They are a
    *stricter* gate than ``accept``, not a softer one: the step re-previews and
    refuses as stale if either digest has moved since the approval was shown,
    and only an explicitly echoed policy digest activates the policy trust.
    """

    mcp_service = HarnessMcpService(_mcp_adapter(route_profile))
    plugin_service = CodexPluginService()
    project = IntegrationTarget(
        IntegrationScope.TRUSTED_PROJECT,
        str((workspace if workspace is not None else Path.cwd()).resolve()),
    )
    try:
        mcp_preview = await mcp_service.preview(binary)
    except McpRegistrationError as error:
        return {
            "outcome": "failed",
            "reason": error.reason.value,
            "state": None,
            "plugin": {"outcome": "skipped", "presence": None},
            "skill": {"outcome": "skipped", "presence": None},
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    plugin_preview = plugin_service.preview(project)
    check_policy = _check_policy_preview(workspace)
    already_registered = mcp_preview.action is McpRegistrationAction.NOOP
    foreign = mcp_preview.state_before is McpRegistrationState.FOREIGN_PRESENT

    if foreign:
        inspection = plugin_service.inspect(project)
        return {
            "outcome": "skipped",
            "reason": "foreign_entry_present",
            "state": mcp_preview.state_before.value,
            "plugin": {
                "outcome": "skipped",
                "presence": inspection.presence.value,
                "reason": "mcp_foreign_entry",
            },
            "skill": {"outcome": "skipped", "presence": None},
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    try:
        skill_plan = await _preview_project_skill(project)
    except IntegrationError as error:
        return {
            "outcome": "failed",
            "reason": "skill_preview_failed",
            "state": mcp_preview.state_before.value,
            "plugin": {"outcome": "skipped", "presence": plugin_preview.presence_before.value},
            "skill": {
                "outcome": "failed",
                "presence": None,
                "reason": error.reason.value,
            },
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    external_approval = (
        approved_preview_digest is not None or approved_skill_preview_digest is not None
    )
    if external_approval and (
        approved_preview_digest != mcp_preview.preview_digest
        or approved_skill_preview_digest != skill_plan.preview.preview_digest
    ):
        return {
            "outcome": "failed",
            "reason": "preview_stale",
            "state": mcp_preview.state_before.value,
            "plugin": {"outcome": "skipped", "presence": plugin_preview.presence_before.value},
            "skill": {
                "outcome": "skipped",
                "presence": skill_plan.preview.state_before.value,
                "reason": "preview_stale",
            },
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    accepted = accept
    policy_digest_confirmed = False
    if external_approval:
        # The caller displayed both exact previews and collected an explicit yes.
        accepted = True
        shown = check_policy.get("policy_digest")
        policy_digest_confirmed = (
            approved_policy_digest is not None
            and isinstance(shown, str)
            and approved_policy_digest == shown
        )
    if interactive and not accepted:
        _emit_registration_preview(binary, mcp_preview, check_policy, skill_plan.preview)
        typer.echo(
            f"  Plugin presence before apply: {plugin_preview.presence_before.value} "
            f"({plugin_preview.planned_file_count} managed files)"
        )
        if already_registered:
            typer.echo("  MCP is already registered; setup will still install/verify the plugin.")
        accepted = _confirm_project_setup(
            include_observation=True,
            policy_digest=cast(str | None, check_policy.get("policy_digest")),
        )
        policy_digest_confirmed = accepted and type(check_policy.get("policy_digest")) is str
    if not accepted:
        return {
            "outcome": "declined",
            "reason": None,
            "state": mcp_preview.state_before.value,
            "plugin": {
                "outcome": "declined",
                "presence": plugin_preview.presence_before.value,
            },
            "skill": {
                "outcome": "declined",
                "presence": skill_plan.preview.state_before.value,
            },
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 1) Install the project-scoped skill Codex actually discovers without a marketplace.
    skill_report = await _install_project_skill(
        project,
        adapter=skill_plan.adapter,
        operation_id=skill_plan.operation_id,
        accepted_preview=skill_plan.preview,
    )
    if skill_report.get("outcome") == "failed":
        return {
            "outcome": "failed",
            "reason": "skill_install_failed",
            "state": mcp_preview.state_before.value,
            "plugin": {"outcome": "skipped", "presence": plugin_preview.presence_before.value},
            "skill": skill_report,
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 2) Install and verify structural plugin/hook sources (even when MCP is registered).
    plugin_report: dict[str, JsonValue]
    try:
        inspection = plugin_service.install(project, allow_untested=True)
        plugin_report = {
            "outcome": "installed",
            "presence": inspection.presence.value,
            "trust_observable": inspection.trust_observable,
            "digest": inspection.installed_digest,
        }
    except IntegrationError as error:
        plugin_report = {
            "outcome": "failed",
            "presence": plugin_preview.presence_before.value,
            "reason": error.reason.value,
        }
        return {
            "outcome": "failed",
            "reason": "plugin_install_failed",
            "state": mcp_preview.state_before.value,
            "plugin": plugin_report,
            "skill": skill_report,
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    plugin_ok = inspection.presence is PluginHookPresence.INSTALLED
    if not plugin_ok:
        plugin_report["outcome"] = "unverified"
        return {
            "outcome": "failed",
            "reason": "plugin_verification_failed",
            "state": mcp_preview.state_before.value,
            "plugin": plugin_report,
            "skill": skill_report,
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 3) Register and verify MCP (noop when already yoetz-owned).
    mcp_outcome = (
        "already_registered"
        if already_registered
        else (
            "reregistered"
            if mcp_preview.action is McpRegistrationAction.REREGISTER
            else "registered"
        )
    )
    mcp_state = mcp_preview.state_before
    if not already_registered:
        try:
            result = await mcp_service.register(
                binary,
                McpRegistrationConfirmation(
                    mcp_preview.preview_digest,
                    True,
                    "interactive" if interactive else "noninteractive_flag",
                ),
            )
        except McpRegistrationError as error:
            return {
                "outcome": "failed",
                "reason": error.reason.value,
                "state": mcp_preview.state_before.value,
                "plugin": plugin_report,
                "skill": skill_report,
                "observation_consent": {"outcome": "absent", "workspace_commitment": None},
            }
        mcp_state = result.state_after
        mcp_outcome = (
            "reregistered" if result.action is McpRegistrationAction.REREGISTER else "registered"
        )
    else:
        try:
            mcp_state = await mcp_service.status(binary)
        except McpRegistrationError as error:
            return {
                "outcome": "failed",
                "reason": error.reason.value,
                "state": mcp_preview.state_before.value,
                "plugin": plugin_report,
                "skill": skill_report,
                "observation_consent": {"outcome": "absent", "workspace_commitment": None},
            }

    mcp_ok = mcp_state is McpRegistrationState.YOETZ_OWNED
    if not mcp_ok:
        return {
            "outcome": "failed",
            "reason": "mcp_verification_failed",
            "state": mcp_state.value,
            "plugin": plugin_report,
            "skill": skill_report,
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 4) Consent only after skill sources, plugin sources, and MCP are verified.
    observation = _grant_observation_consent(workspace)
    check_policy = _activate_check_policy_trust(
        workspace,
        check_policy,
        exact_digest_confirmed=policy_digest_confirmed,
    )
    return {
        "outcome": mcp_outcome,
        "reason": None,
        "route_profile": mcp_preview.route_profile,
        "serve_command": list(mcp_preview.serve_command),
        "state": mcp_state.value,
        "plugin": plugin_report,
        "skill": skill_report,
        "observation_consent": observation,
        "check_policy": check_policy,
    }


async def _register_step(
    binary: HarnessBinary,
    *,
    interactive: bool,
    accept: bool,
    route_profile: Literal["policy", "strict"] | None = None,
) -> dict[str, JsonValue]:
    """Backward-compatible name for the complete Codex integration step."""

    return await _codex_integration_step(
        binary,
        interactive=interactive,
        accept=accept,
        route_profile=route_profile,
    )


async def apply_codex_integration(
    binary: HarnessBinary,
    *,
    route_profile: Literal["policy", "strict"] | None = None,
    workspace: Path | None = None,
    approved_preview_digest: str,
    approved_skill_preview_digest: str,
    approved_policy_digest: str | None = None,
) -> dict[str, JsonValue]:
    """Apply the exact integration a caller already previewed and got approved.

    This exists so a non-prompt front end (the terminal UI) can reuse the whole
    skill → plugin sources → MCP → consent → policy-trust sequence with its gates
    intact instead
    of reassembling it. It never prompts, and it refuses rather than proceed when
    the preview it is handed no longer matches what the services would propose.

    ``route_profile`` must be the same one the approved preview was built from. The serve
    command is inside the preview digest, so a caller that previews on one route and applies
    on another is refused as stale -- correctly, but with a reason that describes the digest
    rather than the disagreement underneath it. Pass the route explicitly and the two halves
    cannot drift.
    """

    return await _codex_integration_step(
        binary,
        interactive=False,
        accept=False,
        route_profile=route_profile,
        workspace=workspace,
        approved_preview_digest=approved_preview_digest,
        approved_skill_preview_digest=approved_skill_preview_digest,
        approved_policy_digest=approved_policy_digest,
    )


def check_policy_preview(workspace: Path | None = None) -> dict[str, JsonValue]:
    """Public path-free approved-check policy preview for non-prompt front ends."""

    return _check_policy_preview(workspace)


def write_setup_marker(outcome: str) -> bool:
    """Record first-run completion; shared by the wizard and the terminal UI."""

    return _write_setup_marker(outcome)


async def _service_reachability(*, start_if_absent: bool = False) -> dict[str, JsonValue]:
    from yoetz.cli.app import build_service_client
    from yoetz.ports.control import ControlClientKind, ControlError
    from yoetz.service.client import connect_service_on_demand

    try:
        client = (
            await connect_service_on_demand(ControlClientKind.CLI)
            if start_if_absent
            else await build_service_client()
        )
        try:
            status = await client.service_status()
        finally:
            await client.close()
    except ControlError:
        return {"reachable": False, "state": None, "vault_mode": None}
    vault_mode = getattr(status, "vault_mode", None)
    state = getattr(getattr(status, "state", None), "value", getattr(status, "state", None))
    return {
        "reachable": True,
        "state": state if type(state) is str else None,
        "vault_mode": vault_mode if type(vault_mode) is str else None,
    }


# A draining service unlinks its endpoints and releases singleton authority before the
# successor can bind, so a flat retry window reports an unreachable service that was only slow.
_RESTART_BACKOFF_SECONDS: Final = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2)


async def _restart_service_for_semantic_composition() -> dict[str, JsonValue]:
    """Restart after provider config changes, then verify the new singleton answered."""

    from yoetz.cli.app import build_service_client
    from yoetz.ports.control import ControlClientKind, ControlError
    from yoetz.service.client import connect_service_on_demand

    try:
        client = await build_service_client()
        try:
            await client.stop()
        finally:
            await client.close()
    except ControlError:
        return {"reachable": False, "state": None, "vault_mode": None}
    for delay in _RESTART_BACKOFF_SECONDS:
        await anyio.sleep(delay)
        try:
            client = await connect_service_on_demand(ControlClientKind.CLI)
            try:
                status = await client.service_status()
            finally:
                await client.close()
        except ControlError:
            continue
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", None))
        vault_mode = getattr(status, "vault_mode", None)
        return {
            "reachable": True,
            "state": state if type(state) is str else None,
            "vault_mode": vault_mode if type(vault_mode) is str else None,
        }
    return {"reachable": False, "state": None, "vault_mode": None}


async def _interactive_provider_setup(
    service: dict[str, JsonValue],
    *,
    provider_choice: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Run trusted local setup ceremonies while keeping secrets out of wizard state."""

    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore, OSKeyringError
    from yoetz.cli.provider_binding import (
        ProviderEndpointChoice,
        apply_provider_endpoint_choice,
        prompt_provider_endpoint_binding,
        prompt_provider_model,
    )
    from yoetz.cli.unlock import (
        HumanCeremonyCliError,
        initialize_passphrase_vault,
        set_provider_credential,
        unlock_vault,
    )
    from yoetz.config.load import load_config
    from yoetz.config.models import ConfigError
    from yoetz.config.paths import bundle_root
    from yoetz.config.write import provider_preset
    from yoetz.service.confidential_client import ConfidentialClientError
    from yoetz.service.confidential_protocol import ProviderCredentialTarget
    from yoetz.service.vault import provider_credential_profile_binding

    provider_report: dict[str, JsonValue] = {
        "binding": "skipped",
        "credential": "skipped",
    }
    auto_passphrase: bytearray | None = None
    replacing_stored_credential = False

    def wipe_auto_passphrase() -> None:
        nonlocal auto_passphrase
        if auto_passphrase is not None:
            for index in range(len(auto_passphrase)):
                auto_passphrase[index] = 0
            auto_passphrase = None

    try:
        if service.get("reachable") and service.get("state") == "locked":
            if service.get("vault_mode") == "uninitialized":
                typer.echo("")
                current_config = load_config({}, os.environ, None)
                auto_store = AutoUnlockPassphraseStore(
                    bundle_root(_data_dir=current_config.storage.data_dir)
                )
                try:
                    # ADR-015 decision 5 / ADR-008: generate a fresh scoped secret for
                    # vault_initialize. Never adopt a pre-existing entry (load_or_create).
                    # Matches elevated create_for_initialization fail-closed on entry_exists.
                    auto_passphrase = auto_store.create_for_initialization()
                except OSKeyringError as error:
                    if error.reason != "unsupported":
                        provider_report["credential_reason"] = f"auto_unlock_{error.reason}"
                        if error.reason == "entry_exists":
                            entry_service, entry_account = auto_store.entry_identity
                            typer.echo(
                                "A pre-existing platform credential entry blocks vault "
                                "initialization, because adopting it would make an already "
                                "known value the vault root passphrase.",
                                err=True,
                            )
                            typer.echo(f"  Credential store service: {entry_service}", err=True)
                            typer.echo(f"  Account: {entry_account}", err=True)
                            typer.echo(
                                "Delete that entry (macOS: Keychain Access; Linux: "
                                "'secret-tool clear service " + entry_service + "'), then "
                                "rerun 'yoetz setup'. Keep it only if this install already has "
                                "a vault, in which case setup did not need to initialize one.",
                                err=True,
                            )
                        else:
                            typer.echo(
                                "Platform credential entry could not be verified; vault "
                                "initialization stopped to avoid a conflicting passphrase. "
                                "Restore credential-store access, then rerun 'yoetz setup'.",
                                err=True,
                            )
                        return service, provider_report
                    typer.echo("Platform credential store unavailable; choose a vault passphrase")
                    typer.echo("Secure vault setup (hidden local-terminal input)")
                    await initialize_passphrase_vault()
                else:
                    typer.echo("Secure vault setup (platform credential store auto-unlock)")
                    await initialize_passphrase_vault(bytearray(auto_passphrase))
            elif service.get("vault_mode") == "passphrase":
                typer.echo("")
                current_config = load_config({}, os.environ, None)
                auto_store = AutoUnlockPassphraseStore(
                    bundle_root(_data_dir=current_config.storage.data_dir)
                )
                auto_passphrase = auto_store.load()
                if auto_passphrase is None:
                    typer.echo(
                        "Unlock Yoetz to finish provider setup (hidden local-terminal input)"
                    )
                    await unlock_vault()
                else:
                    typer.echo("Unlocking Yoetz from the platform credential store")
                    await unlock_vault(bytearray(auto_passphrase))
            service = await _service_reachability()
    except HumanCeremonyCliError as error:
        provider_report["credential_reason"] = error.reason

    if provider_choice is not None:
        selected_model = model
        try:
            preset = provider_preset(provider_choice)
            choice = cast(ProviderEndpointChoice, preset.choice)
            if selected_model is None:
                selected_model = prompt_provider_model(preset.choice)
            if not selected_model:
                provider_report["credential_reason"] = "model_selection_invalid"
                wipe_auto_passphrase()
                return service, provider_report
            written, _provider = apply_provider_endpoint_choice(choice, model=selected_model)
        except (ConfigError, OSError, ValueError) as error:
            provider_report["credential_reason"] = getattr(
                error, "reason_code", "provider_binding_invalid"
            )
            wipe_auto_passphrase()
            return service, provider_report
        typer.echo(f"{preset.provider_id} model: {selected_model}")
    else:
        written = prompt_provider_endpoint_binding()
    if written is None:
        wipe_auto_passphrase()
        return service, provider_report
    provider_report["binding"] = "configured"
    config = load_config({}, {}, written)
    provider = config.provider
    if provider is None or service.get("state") != "ready":
        provider_report.setdefault("credential_reason", "service_not_ready")
        wipe_auto_passphrase()
        return service, provider_report

    # The service snapshots its configured provider and credential capability at composition
    # time. Refresh it after writing the binding before deciding whether this exact profile
    # already has a credential. This also makes a repeated setup run idempotent: an existing
    # credential is shown as present and is never requested again.
    service = await _restart_service_for_semantic_composition()
    credential_before: bool | None = None
    if service.get("state") == "ready":
        from yoetz.cli.provider_status import provider_status_report

        try:
            status_before = await provider_status_report()
        except OSError, ValueError:
            status_before = {}
        observed_before = status_before.get("credential_connected")
        credential_before = observed_before if type(observed_before) is bool else None
    if credential_before is True:
        from yoetz.cli.provider_status import credential_human_display

        typer.echo(f"  Credential: {credential_human_display(True)} (already stored)")
        if typer.confirm("Use the stored credential for this provider and model?", default=True):
            provider_report["credential"] = "stored"
            provider_report["credential_display"] = credential_human_display(True)
            wipe_auto_passphrase()
            return service, provider_report
        replacing_stored_credential = True
        typer.echo("Enter a new credential to replace the stored value.")

    # A Keychain-provisioned passphrase vault is already ready without the
    # human knowing its generated passphrase. Load that same scoped secret
    # only for the one provider-reauthentication ceremony, so setup asks for
    # the provider key but never unexpectedly asks the user for a passphrase.
    if auto_passphrase is None and service.get("vault_mode") == "passphrase":
        current_config = load_config({}, os.environ, None)
        auto_passphrase = AutoUnlockPassphraseStore(
            bundle_root(_data_dir=current_config.storage.data_dir)
        ).load()

    storage = provider_credential_profile_binding(
        provider.provider_id,
        provider.model,
        provider.endpoint_profile_id,
        provider.endpoint_profile_version,
    )
    target = ProviderCredentialTarget(
        action="set",
        provider_id=storage.provider_id,
        model_id=storage.model_id,
        endpoint_profile_id=storage.endpoint_profile_id,
        endpoint_profile_version=storage.endpoint_profile_version,
        purpose=storage.purpose,
        scope_digest=storage.authorization_scope_digest,
        purpose_digest=storage.purpose_digest,
    )
    typer.echo("")
    typer.echo("Provider API key (hidden local-terminal input; stored only in the Yoetz vault)")
    try:
        result = await set_provider_credential(
            target,
            None,
            None if auto_passphrase is None else bytearray(auto_passphrase),
        )
    except HumanCeremonyCliError as error:
        provider_report["credential"] = "failed"
        provider_report["credential_reason"] = error.reason
    except ConfidentialClientError as error:
        # An initial vault write may have committed before the result/close frame was lost.
        # Recompose and inspect only the exact configured profile's presence bit; never retry with
        # a wiped buffer and never turn an unreadable state into a success claim.
        if replacing_stored_credential:
            # Presence was already true before the write, so it cannot distinguish the old value
            # from a committed replacement. Preserve that uncertainty instead of claiming success.
            provider_report["credential"] = "failed"
            provider_report["credential_reason"] = f"credential_{error.reason}"
            return await _service_reachability(), provider_report
        service = await _restart_service_for_semantic_composition()
        credential_after: bool | None = None
        if service.get("state") == "ready":
            from yoetz.cli.provider_status import provider_status_report

            try:
                status_after = await provider_status_report()
            except OSError, ValueError:
                status_after = {}
            observed_after = status_after.get("credential_connected")
            credential_after = observed_after if type(observed_after) is bool else None
        if credential_after is True:
            from yoetz.cli.provider_status import credential_human_display

            provider_report["credential"] = "stored"
            provider_report["credential_display"] = credential_human_display(True)
            provider_report["credential_reason"] = "stored_result_recovered"
        else:
            provider_report["credential"] = "failed"
            provider_report["credential_reason"] = f"credential_{error.reason}"
    else:
        provider_report["credential"] = result.activation_status
        if result.activation_status == "stored":
            from yoetz.cli.provider_status import credential_human_display

            provider_report["credential_display"] = credential_human_display(True)
    finally:
        wipe_auto_passphrase()
    return await _service_reachability(), provider_report


def _semantic_openai_extra_state() -> str:
    """Report whether the optional ``semantic-openai`` import surface is present.

    Presence is a structural import fact only. It does not prove wire dispatch, auth, or a
    successful semantic review.
    """

    if importlib.util.find_spec("openai") is None:
        return "absent (not demonstrated)"
    return "present (importable; wire dispatch not demonstrated)"


def _emit_provider_setup_layer_report(*, privacy_outcome: str) -> None:
    """Honestly separate binding/credential storage from undemonstrated runtime layers."""

    typer.echo("Provider binding and vault credential storage succeeded; that layer is supported.")
    typer.echo("Other layers (ready vs not demonstrated by this path):")
    typer.echo(f"  SDK extra (semantic-openai): {_semantic_openai_extra_state()}")
    typer.echo(
        "  Semantic evaluator: not composed "
        "(_semantic_not_configured in ready composition; not demonstrated)"
    )
    typer.echo(f"  Privacy policy: {privacy_outcome}")
    typer.echo("  Transport probe: not demonstrated")
    typer.echo("  Installed artifact evidence: not demonstrated")
    typer.echo(
        "Stored binding/credential is not proof of live provider dispatch or semantic review."
    )


async def run_provider_setup(
    *,
    fireworks: bool = False,
    grok: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Run only the simple local provider setup path used by ``yoetz --set``."""

    if (fireworks or grok) and provider is not None:
        return _usage_failure("provider shortcuts and --provider are mutually exclusive")
    if fireworks and grok:
        return _usage_failure("--fireworks and --grok are mutually exclusive")
    provider_choice = "fireworks" if fireworks else ("grok" if grok else provider)
    if provider_choice is not None:
        from yoetz.config.models import ConfigError
        from yoetz.config.write import provider_preset

        try:
            provider_choice = provider_preset(provider_choice).choice
        except ConfigError, TypeError:
            return _usage_failure("--provider must name a reviewed provider preset")
    if not _is_interactive_terminal():
        return _usage_failure("--set requires a local terminal for hidden credential input")
    typer.echo("Yoetz LLM setup")
    typer.echo("The API key is entered with hidden input and stored only in the local vault.")
    service = await _service_reachability(start_if_absent=True)
    if not service.get("reachable"):
        typer.echo("provider_setup_failed: service_unavailable", err=True)
        return 20
    service, provider_report = await _interactive_provider_setup(
        service,
        provider_choice=provider_choice,
        model=model,
    )
    binding = provider_report.get("binding")
    credential = provider_report.get("credential")
    typer.echo("")
    typer.echo(f"Provider binding: {binding}")
    from yoetz.cli.provider_status import credential_human_display

    typer.echo(
        "Credential: "
        + credential_human_display(
            True if credential == "stored" else (False if credential == "failed" else None)
        )
    )
    if binding != "configured" or credential != "stored":
        reason = provider_report.get("credential_reason")
        if type(reason) is str:
            typer.echo(f"Reason: {reason}")
        return 20
    from yoetz.cli.privacy_setup import run_privacy_setup
    from yoetz.cli.unlock import HumanCeremonyCliError
    from yoetz.ports.control import ControlError

    typer.echo("")
    typer.echo("Provider credential stored. Now choose the exact disclosure policy it may use.")
    try:
        privacy = await run_privacy_setup(
            recipe_hint="assisted_review",
            offer_recommended=True,
        )
    except (ControlError, HumanCeremonyCliError, OSError, ValueError) as error:
        typer.echo(
            f"privacy_setup_failed: {type(error).__name__}; the credential remains stored but inert",
            err=True,
        )
        return 20
    if privacy.outcome in {"cancelled", "failed"}:
        typer.echo(
            "No standing disclosure policy was approved; the credential remains stored but inert."
        )
    _emit_provider_setup_layer_report(privacy_outcome=privacy.outcome)
    if privacy.outcome == "failed":
        return 20
    typer.echo(
        "Preview Codex MCP registration again to review the policy route command now that "
        "semantic configuration changed."
    )
    return 0


def _registration_report(
    state: McpRegistrationState | None,
    *,
    outcome: str,
    reason: str | None = None,
) -> dict[str, JsonValue]:
    return {
        "outcome": outcome,
        "reason": reason,
        "state": None if state is None else state.value,
    }


async def _resolve_setup_package_update(
    *,
    interactive: bool,
) -> dict[str, JsonValue]:
    """Structural package-update advisory for setup reports (never auto-upgrades)."""

    from yoetz.application.package_update import (
        package_update_report_fields,
        resolve_package_update_advisory,
    )

    # Noninteractive setup never opens the network for surprise side effects when the
    # service/policy is not already available; interactive may check when policy allows.
    try:
        from yoetz.cli.app import build_service_client
        from yoetz.cli.provider_status import machine_scope_request

        client = await build_service_client()
        try:
            raw = await client.privacy_get_effective(machine_scope_request())
        finally:
            await client.close()
        raw_map = cast(Mapping[str, object], raw)
        policy_obj = raw_map.get("policy")
        if not isinstance(policy_obj, Mapping):
            advisory = await resolve_package_update_advisory(policy=None, allow_network=False)
        else:
            # Prefer structural bits over full re-decode when wire shape is partial.
            policy_map = cast(Mapping[str, object], policy_obj)
            network = policy_map.get("network_egress_permitted")
            enabled = False
            channels = policy_map.get("channel_policies")
            if isinstance(channels, (list, tuple)):
                for entry_obj in cast(tuple[object, ...] | list[object], channels):
                    if not isinstance(entry_obj, Mapping):
                        continue
                    row = cast(Mapping[str, object], entry_obj)
                    if row.get("channel") == "update_checks" and row.get("enabled") is True:
                        enabled = True
                        break
            advisory = await resolve_package_update_advisory(
                network_egress_permitted=network if type(network) is bool else None,
                update_checks_enabled=enabled,
                allow_network=interactive,
            )
    except Exception:
        advisory = await resolve_package_update_advisory(policy=None, allow_network=False)
    fields = dict(package_update_report_fields(advisory))
    return cast(dict[str, JsonValue], fields)


async def _offer_interactive_package_upgrade(package_update: Mapping[str, JsonValue]) -> bool:
    """Offer upgrade-over-reinstall when a newer package is known. Returns False to stop setup."""

    if package_update.get("is_newer") is not True:
        return True
    installed = package_update.get("installed_version")
    latest = package_update.get("latest_version")
    command = package_update.get("upgrade_command")
    if type(installed) is not str or type(latest) is not str or type(command) is not str:
        return True
    typer.echo("")
    typer.echo(f"A newer Yoetz package is available ({installed} → {latest}).")
    typer.echo(f"Upgrade command: {command}")
    upgrade = typer.confirm(
        "Upgrade the package first (exit and re-run yoetz after upgrade)?",
        default=False,
    )
    if upgrade:
        typer.echo("Exit this process, run the upgrade command, then re-run yoetz setup.")
        return False
    typer.echo("Continuing with this package version; harness add/repair does not reinstall it.")
    return True


async def run_setup_wizard(
    *,
    non_interactive: bool,
    codex_path: str | None,
    accept: bool,
    json_output: bool,
) -> int:
    """Run the guided first-run setup and report each step honestly."""

    interactive = not non_interactive and _is_interactive_terminal()
    package_update = await _resolve_setup_package_update(interactive=interactive)
    if interactive and not await _offer_interactive_package_upgrade(package_update):
        report: dict[str, JsonValue] = {
            "package_update": package_update,
            "schema": _REPORT_SCHEMA,
            "outcome": "cancelled",
            "reason": "package_upgrade_deferred",
        }
        _emit(report, json_output=json_output)
        return 0

    binaries = discover_codex_binaries()
    try:
        harness = _choose_harness(
            binaries,
            codex_path=codex_path,
            interactive=interactive,
        )
        chosen = (
            None
            if harness is None
            else _choose_binary(binaries, codex_path=codex_path, interactive=interactive)
        )
    except _UsageExit as failure:
        return failure.code

    review_mode: Literal["local_only", "semantic", "deferred"] = (
        _choose_review_mode() if interactive else "deferred"
    )
    if chosen is None:
        registration = _registration_report(None, outcome="skipped", reason="codex_not_found")
    else:
        registration = await _register_step(
            chosen,
            interactive=interactive,
            accept=accept,
            route_profile="policy" if review_mode == "semantic" else "strict",
        )

    service = await _service_reachability(start_if_absent=interactive)
    provider: dict[str, JsonValue] = {
        "binding": "skipped",
        "credential": "skipped",
    }
    # Choosing local-only review means no widening was requested; it does not observe what the
    # durable policy already is, so the report never names a profile it did not read.
    privacy: dict[str, JsonValue] = {
        "outcome": "deferred" if not interactive else "not_changed",
        "profile": "unknown",
        "reason": None,
    }
    semantic_status: dict[str, JsonValue] | None = None
    if interactive and review_mode == "semantic":
        service, provider = await _interactive_provider_setup(service)
        if (
            service.get("state") == "ready"
            and provider.get("binding") == "configured"
            and provider.get("credential") == "stored"
        ):
            from yoetz.cli.privacy_setup import run_privacy_setup
            from yoetz.cli.unlock import HumanCeremonyCliError
            from yoetz.ports.control import ControlError

            try:
                privacy_result = await run_privacy_setup(
                    recipe_hint="assisted_review",
                    offer_recommended=True,
                )
            except (ControlError, HumanCeremonyCliError, OSError, ValueError) as error:
                reason = getattr(error, "reason", None)
                if type(error) is ValueError:
                    value_error_reason = str(error)
                    reason = (
                        value_error_reason
                        if value_error_reason.startswith("privacy_setup_")
                        else "privacy_setup_failed"
                    )
                privacy = {
                    "outcome": "failed",
                    "profile": "unknown",
                    "reason": reason if type(reason) is str else "privacy_setup_failed",
                }
            else:
                privacy = {
                    "outcome": privacy_result.outcome,
                    "profile": privacy_result.profile,
                    "proposal_id": privacy_result.proposal_id,
                    "reason": privacy_result.reason,
                }
                if privacy_result.outcome in {"configured", "unchanged"}:
                    service = await _restart_service_for_semantic_composition()
                    if service.get("state") == "ready":
                        from yoetz.cli.provider_status import provider_status_report

                        semantic_status = await provider_status_report()
        else:
            privacy = {
                "outcome": "failed",
                "profile": "unknown",
                "reason": "provider_setup_incomplete",
            }

    next_steps: list[JsonValue] = []
    if not service["reachable"]:
        next_steps.append(_NEXT_SERVICE)
    if service.get("state") != "ready":
        next_steps.append(_NEXT_UNLOCK)
    if review_mode == "semantic" and (
        semantic_status is None or semantic_status.get("semantic_ready") is not True
    ):
        next_steps.append(_NEXT_RESTART)
    if privacy.get("outcome") not in {"configured", "unchanged", "not_changed"}:
        next_steps.append(_NEXT_PRIVACY)
    if review_mode != "local_only" and provider.get("binding") != "configured":
        next_steps.append(_NEXT_PROVIDER_TOML)
    if review_mode != "local_only" and provider.get("credential") != "stored":
        next_steps.append(_NEXT_CREDENTIAL)
    if (
        chosen is not None
        and provider.get("binding") == "configured"
        and registration.get("route_profile") == "strict"
    ):
        next_steps.append(
            "run 'yoetz integrate codex mcp preview' and explicitly accept re-registration "
            "if you want the policy route to permit configured semantic review"
        )

    mutating_run = interactive or accept
    setup_complete = (
        not interactive
        or review_mode == "local_only"
        or (semantic_status is not None and semantic_status.get("semantic_ready") is True)
    )
    marker_written = (
        _write_setup_marker(str(registration["outcome"]))
        if mutating_run and setup_complete
        else False
    )
    integration = _integration_layers()
    consent: str | None = None
    observation = registration.get("observation_consent")
    if isinstance(observation, Mapping):
        raw_consent = observation.get("outcome")
        consent = raw_consent if type(raw_consent) is str else None
    plugin_block = registration.get("plugin")
    plugin_presence = None
    if isinstance(plugin_block, Mapping):
        raw_presence = plugin_block.get("presence")
        plugin_presence = raw_presence if type(raw_presence) is str else None
    else:
        plugin = integration.get("plugin")
        if isinstance(plugin, Mapping):
            raw_presence = plugin.get("presence")
            plugin_presence = raw_presence if type(raw_presence) is str else None
    skill_block = registration.get("skill")
    skill_presence = None
    if isinstance(skill_block, Mapping):
        raw_presence = skill_block.get("presence")
        skill_presence = raw_presence if type(raw_presence) is str else None
    else:
        skill = integration.get("skill")
        if isinstance(skill, Mapping):
            raw_presence = skill.get("presence")
            skill_presence = raw_presence if type(raw_presence) is str else None
    hooks_raw = integration.get("hooks")
    hooks = hooks_raw if isinstance(hooks_raw, dict) else {}
    readiness = _readiness_layers(
        binary=chosen,
        mcp_state=cast(str | None, registration.get("state")),
        plugin_presence=plugin_presence,
        skill_presence=skill_presence,
        hooks=hooks,
        consent_outcome=consent,
        service=service,
        workspace=Path.cwd(),
    )
    if semantic_status is not None:
        semantic_ready = semantic_status.get("semantic_ready") is True
        readiness["semantic_advice_ready"] = semantic_ready
        readiness["semantic_advice_note"] = (
            "configured_and_composed; live_provider_dispatch_not_tested"
            if semantic_ready
            else "semantic_configuration_incomplete"
        )

    report: dict[str, JsonValue] = {
        "discovered": [_binary_row(binary) for binary in binaries],
        "integration": integration,
        "marker_written": marker_written,
        "next_steps": next_steps,
        "package_update": package_update,
        "privacy": privacy,
        "provider": provider,
        "readiness": readiness,
        "registration": registration,
        "schema": _REPORT_SCHEMA,
        "selected": None if chosen is None else _binary_row(chosen),
        "semantic_status": semantic_status,
        "service": service,
        "review_mode": review_mode,
    }
    if interactive:
        _emit_human_report(report)
    else:
        _emit(report, json_output=json_output)
    return 0


def _emit_human_report(report: dict[str, JsonValue]) -> None:
    registration = report["registration"]
    service = report["service"]
    provider = report["provider"]
    privacy = report.get("privacy")
    integration = report["integration"]
    readiness = report.get("readiness")
    typer.echo("Setup summary:")
    if isinstance(registration, dict):
        outcome = registration.get("outcome")
        reason = registration.get("reason")
        # MCP registration alone is not product readiness or automatic activation.
        if outcome in {"registered", "reregistered", "already_registered"}:
            line = f"  MCP registration: {outcome}; automatic activation not tested"
        else:
            line = f"  MCP registration: {outcome}"
        if reason:
            line += f" ({reason})"
        typer.echo(line)
        plugin = registration.get("plugin")
        if isinstance(plugin, dict):
            typer.echo(
                "  Plugin source files: "
                f"{plugin.get('outcome') or 'unknown'} "
                f"(presence={plugin.get('presence') or 'absent'}; "
                "Codex discovery remains unverified)"
            )
        skill = registration.get("skill")
        if isinstance(skill, dict):
            typer.echo(
                "  Project skill installation: "
                f"{skill.get('outcome') or 'unknown'} "
                f"(presence={skill.get('presence') or 'absent'}; "
                f"compatibility={skill.get('compatibility') or 'unsupported'})"
            )
    if isinstance(integration, dict):
        skill = integration.get("skill")
        hooks = integration.get("hooks")
        if isinstance(skill, dict) and skill.get("source_state") != "verified":
            typer.echo("  Skill support: packaged source invalid; automatic activation not tested")
        elif (
            isinstance(skill, dict)
            and isinstance(skill.get("tested_profiles"), list)
            and bool(skill.get("tested_profiles"))
        ):
            tested_profiles = cast(list[JsonValue], skill["tested_profiles"])
            profiles = ", ".join(str(item) for item in tested_profiles)
            typer.echo(f"  Skill support: tested profiles: {profiles}")
        else:
            typer.echo(
                "  Skill support: no tested capability profile; automatic activation not tested"
            )
        if isinstance(hooks, dict):
            typer.echo(
                "  Hook installation: "
                f"{hooks.get('presence') or 'absent'}; "
                f"trust {hooks.get('trust_state') or 'unknown'}"
            )
    if isinstance(registration, dict):
        observation = registration.get("observation_consent")
        if isinstance(observation, dict):
            outcome = observation.get("outcome")
            if outcome == "granted":
                typer.echo(
                    "  Observation consent: granted "
                    "(structural events only; coverage requires real evidence)"
                )
            elif outcome is not None:
                typer.echo(f"  Observation consent: {outcome}")
        check_policy = registration.get("check_policy")
        if isinstance(check_policy, dict):
            typer.echo(f"  Approved-check policy: {check_policy.get('outcome') or 'absent'}")
            digest = check_policy.get("policy_digest")
            if type(digest) is str:
                typer.echo(f"  Approved-check policy digest: {digest}")
    if isinstance(service, dict):
        reachable = service.get("reachable")
        typer.echo(f"  Local service reachable: {'yes' if reachable else 'no'}")
        typer.echo(f"  Local service state: {service.get('state') or 'unavailable'}")
    if isinstance(readiness, dict):
        typer.echo(
            "  Observation readiness: "
            + ("ready to observe" if readiness.get("observation_ready") else "not ready")
        )
        typer.echo(
            "  Semantic-advice readiness: "
            + (
                "ready"
                if readiness.get("semantic_advice_ready")
                else str(readiness.get("semantic_advice_note") or "not demonstrated")
            )
        )
    if isinstance(provider, dict):
        typer.echo(f"  Provider binding: {provider.get('binding')}")
        from yoetz.cli.provider_status import credential_human_display

        credential = provider.get("credential")
        typer.echo(
            "  Credential: "
            + credential_human_display(
                True if credential == "stored" else (False if credential == "failed" else None)
            )
        )
    if isinstance(privacy, dict):
        line = f"  Privacy: {privacy.get('outcome')} ({privacy.get('profile')})"
        if privacy.get("reason"):
            line += f" — {privacy.get('reason')}"
        typer.echo(line)
    steps = report["next_steps"]
    if isinstance(steps, list) and steps:
        typer.echo("Next steps:")
        for step in steps:
            typer.echo(f"  - {step}")


async def setup_status(*, json_output: bool) -> int:
    """Read-only setup posture: discovery, registration state, service, marker."""

    binaries = discover_codex_binaries()
    service_port = _mcp_adapter()
    rows: list[JsonValue] = []
    for binary in binaries:
        row = _binary_row(binary)
        try:
            observation = await HarnessMcpService(service_port).observe(binary)
            row["registration_state"] = observation.state.value
            # A strict and a policy registration are both yoetz_owned; only this distinguishes them.
            row["registered_route_profile"] = observation.route_profile
        except McpRegistrationError as error:
            row["registration_state"] = None
            row["registered_route_profile"] = None
            row["registration_error"] = error.reason.value
        rows.append(row)
    report: dict[str, JsonValue] = {
        "discovered": rows,
        "integration": _integration_layers(),
        "marker_present": setup_marker_present(),
        "schema": _STATUS_SCHEMA,
        "service": await _service_reachability(),
    }
    _emit(report, json_output=json_output)
    return 0


_MCP_EXIT_USAGE: Final = frozenset(
    {"confirmation_required", "preview_stale", "foreign_entry_present"}
)


def _mcp_error_exit(reason: str) -> int:
    typer.echo(f"mcp_registration_{reason}", err=True)
    return 2 if reason in _MCP_EXIT_USAGE else 20


async def integrate_mcp(
    action: str,
    harness: str,
    *,
    codex_path: str | None,
    accept: bool,
    preview_digest: str | None,
    json_output: bool,
) -> int:
    """Client-local ``integrate <harness> mcp status|preview|install`` commands."""

    if harness != "codex" or action not in {"status", "preview", "install"}:
        return _usage_failure("the harness or action is not supported")
    interactive = _is_interactive_terminal()
    binaries = discover_codex_binaries()
    try:
        chosen = _choose_binary(binaries, codex_path=codex_path, interactive=False)
    except _UsageExit as failure:
        return failure.code
    if chosen is None:
        return _usage_failure("no codex executable was found on PATH")

    service = HarnessMcpService(_mcp_adapter())
    try:
        if action == "status":
            observation = await service.observe(chosen)
            _emit(
                {
                    "harness": harness,
                    "route_profile": observation.route_profile,
                    "state": observation.state.value,
                },
                json_output=json_output,
            )
            return 0
        preview = await service.preview(chosen)
        if action == "preview":
            _emit(
                {
                    "action": preview.action.value,
                    "harness": harness,
                    "preview_digest": preview.preview_digest,
                    "route_profile": preview.route_profile,
                    "serve_command": list(preview.serve_command),
                    "state_before": preview.state_before.value,
                    "warnings": list(preview.warnings),
                },
                json_output=json_output,
            )
            return 0
        if preview_digest is not None and preview_digest != preview.preview_digest:
            return _mcp_error_exit("preview_stale")
        if preview.state_before is McpRegistrationState.FOREIGN_PRESENT:
            return _mcp_error_exit("foreign_entry_present")
        accepted = accept
        if interactive and not accepted:
            _emit_registration_preview(chosen, preview)
            accepted = _confirm_registration()
        if not accepted:
            return _mcp_error_exit("confirmation_required")
        if preview.action is McpRegistrationAction.NOOP:
            _emit(
                {
                    "action": "noop",
                    "harness": harness,
                    "state_after": preview.state_before.value,
                    "state_before": preview.state_before.value,
                },
                json_output=json_output,
            )
            return 0
        result = await service.register(
            chosen,
            McpRegistrationConfirmation(
                preview.preview_digest,
                True,
                "interactive" if interactive else "noninteractive_flag",
            ),
        )
    except McpRegistrationError as error:
        return _mcp_error_exit(error.reason.value)
    _emit(
        {
            "action": result.action.value,
            "harness": harness,
            "state_after": result.state_after.value,
            "state_before": result.state_before.value,
        },
        json_output=json_output,
    )
    return 0
