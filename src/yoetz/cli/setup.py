"""First-run setup wizard: harness, local service, and provider connection.

The wizard orchestrates only operations a human could already run by hand: it
discovers Codex binaries, previews and (after explicit confirmation) applies the
runbook's ``codex mcp get``/``codex mcp add`` sequence, checks whether the local
service is reachable, and—only on a local interactive terminal—runs the existing
vault and provider-credential ceremonies. Secret bytes remain inside the dedicated
hidden-input confidential helper and never enter wizard arguments, config, or MCP.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

import typer

from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
from yoetz.adapters.integrations.codex_plugin import PluginHookPresence, inspect_plugin
from yoetz.adapters.integrations.codex_skill import load_packaged_skill_source
from yoetz.application.codex_plugin import CodexPluginService
from yoetz.application.harness_mcp import HarnessMcpService, McpRegistrationConfirmation
from yoetz.application.observation_check_policy import load_observation_check_policy
from yoetz.config.paths import PathSafetyError, setup_marker_path
from yoetz.ports.harness_mcp import (
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationError,
    McpRegistrationState,
)
from yoetz.ports.integrations import (
    HarnessId,
    IntegrationError,
    IntegrationScope,
    IntegrationTarget,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode

__all__ = [
    "SETUP_MARKER_SCHEMA",
    "integrate_mcp",
    "run_provider_setup",
    "run_setup_wizard",
    "setup_marker_present",
    "setup_status",
    "should_offer_first_run",
]

SETUP_MARKER_SCHEMA: Final = "yoetz.setup-wizard-marker/1"
_REPORT_SCHEMA: Final = "yoetz.setup-wizard-report/1"
_STATUS_SCHEMA: Final = "yoetz.setup-status/1"
_HARNESS_DISPLAY_NAMES: Final[dict[HarnessId, str]] = {HarnessId.CODEX: "Codex"}

_NEXT_SERVICE: Final = "run 'yoetz service run' under your selected user supervisor"
_NEXT_UNLOCK: Final = "run 'yoetz service unlock' from a local terminal if the vault is locked"
_NEXT_PRIVACY: Final = "run 'yoetz privacy setup' to review recipes and provider binding"
_NEXT_PROVIDER_TOML: Final = (
    "run 'yoetz provider endpoint' (or edit config.toml) to choose Official OpenAI "
    "or an owner-declared HTTPS origin+model — never put API keys in TOML"
)
_NEXT_CREDENTIAL: Final = (
    "run 'yoetz provider credential set' from a local terminal to provision the "
    "provider credential through the confidential ceremony"
)


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

    try:
        source = load_packaged_skill_source()
    except IntegrationError as error:
        tested_profiles: list[JsonValue] = []
        skill_state = error.reason.value
    else:
        tested_profiles = list(source.harness_tested_set)
        skill_state = "verified"
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
                "source_state": skill_state,
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
            "source_state": skill_state,
            "tested_profiles": tested_profiles,
        },
    }


def _is_interactive_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False


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
    typer.echo("  - Plugin / guidance / hooks installation in this trusted project")
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
    binary: HarnessBinary, policy_preview: dict[str, JsonValue] | None = None
) -> None:
    typer.echo("Proposed change: complete Yoetz Codex project integration:")
    typer.echo("  1. Install plugin / guidance / hooks under .agents/plugins/yoetz")
    typer.echo("  2. Register the Yoetz MCP server with Codex")
    typer.echo("  MCP server name: yoetz")
    typer.echo("  Command: yoetz mcp serve")
    typer.echo(f"  Codex executable: {binary.executable_path}")
    if binary.reported_version is not None:
        typer.echo(f"  Codex version: {binary.reported_version}")
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


def _readiness_layers(
    *,
    binary: HarnessBinary | None,
    mcp_state: str | None,
    plugin_presence: str | None,
    hooks: dict[str, JsonValue],
    consent_outcome: str | None,
    service: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    consent_active = consent_outcome == "granted"
    service_routing = bool(service.get("reachable"))
    observation_ready = _plugin_verified(plugin_presence) and consent_active and service_routing
    return {
        "codex": None
        if binary is None
        else {
            "executable_path": binary.executable_path,
            "reported_version": binary.reported_version,
        },
        "mcp_registration": mcp_state,
        "plugin_installation": plugin_presence,
        "hooks": hooks,
        "consent": consent_outcome or "absent",
        "service_routing": {
            "reachable": service_routing,
            "state": service.get("state"),
        },
        "observation_ready": observation_ready,
        "semantic_advice_ready": False,
        "semantic_advice_note": "deterministic_only_until_provider_ready",
    }


async def _codex_integration_step(
    binary: HarnessBinary,
    *,
    interactive: bool,
    accept: bool,
    workspace: Path | None = None,
) -> dict[str, JsonValue]:
    """Preview and apply one Codex integration: plugin + MCP + consent."""

    mcp_service = HarnessMcpService(CodexMcpAdapter())
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
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    plugin_preview = plugin_service.preview(project)
    check_policy = _check_policy_preview(workspace)
    already_registered = mcp_preview.state_before is McpRegistrationState.YOETZ_OWNED
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
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    accepted = accept
    policy_digest_confirmed = False
    if interactive and not accepted:
        _emit_registration_preview(binary, check_policy)
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
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 1) Install and verify plugin/hooks (even when MCP is already registered).
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
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 2) Register and verify MCP (noop when already yoetz-owned).
    mcp_outcome = "already_registered" if already_registered else "registered"
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
                "observation_consent": {"outcome": "absent", "workspace_commitment": None},
            }
        mcp_state = result.state_after
        mcp_outcome = "registered"
    else:
        try:
            mcp_state = await mcp_service.status(binary)
        except McpRegistrationError as error:
            return {
                "outcome": "failed",
                "reason": error.reason.value,
                "state": mcp_preview.state_before.value,
                "plugin": plugin_report,
                "observation_consent": {"outcome": "absent", "workspace_commitment": None},
            }

    mcp_ok = mcp_state is McpRegistrationState.YOETZ_OWNED
    if not mcp_ok:
        return {
            "outcome": "failed",
            "reason": "mcp_verification_failed",
            "state": mcp_state.value,
            "plugin": plugin_report,
            "observation_consent": {"outcome": "absent", "workspace_commitment": None},
        }

    # 3) Consent only after both plugin and MCP verified.
    observation = _grant_observation_consent(workspace)
    check_policy = _activate_check_policy_trust(
        workspace,
        check_policy,
        exact_digest_confirmed=policy_digest_confirmed,
    )
    return {
        "outcome": mcp_outcome,
        "reason": None,
        "state": mcp_state.value,
        "plugin": plugin_report,
        "observation_consent": observation,
        "check_policy": check_policy,
    }


async def _register_step(
    binary: HarnessBinary,
    *,
    interactive: bool,
    accept: bool,
) -> dict[str, JsonValue]:
    """Backward-compatible name for the complete Codex integration step."""

    return await _codex_integration_step(binary, interactive=interactive, accept=accept)


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


async def _interactive_provider_setup(
    service: dict[str, JsonValue],
    *,
    provider_choice: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Run trusted local setup ceremonies while keeping secrets out of wizard state."""

    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore, OSKeyringError
    from yoetz.cli.provider_binding import (
        apply_provider_endpoint_choice,
        prompt_provider_endpoint_binding,
    )
    from yoetz.cli.unlock import (
        HumanCeremonyCliError,
        initialize_passphrase_vault,
        set_provider_credential,
        unlock_vault,
    )
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root
    from yoetz.service.confidential_protocol import ProviderCredentialTarget
    from yoetz.service.vault import provider_credential_profile_binding

    provider_report: dict[str, JsonValue] = {
        "binding": "skipped",
        "credential": "skipped",
    }
    auto_passphrase: bytearray | None = None

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
                if api_key is not None:
                    current_config = load_config({}, {}, None)
                    auto_store = AutoUnlockPassphraseStore(
                        bundle_root(_data_dir=current_config.storage.data_dir)
                    )
                    try:
                        auto_passphrase = auto_store.load_or_create()
                    except OSKeyringError:
                        typer.echo(
                            "Platform credential store unavailable; choose a vault passphrase"
                        )
                        await initialize_passphrase_vault()
                    else:
                        typer.echo("Secure vault setup (platform credential store auto-unlock)")
                        await initialize_passphrase_vault(bytearray(auto_passphrase))
                else:
                    typer.echo("Secure vault setup (hidden local-terminal input)")
                    await initialize_passphrase_vault()
            elif service.get("vault_mode") == "passphrase":
                typer.echo("")
                current_config = load_config({}, {}, None)
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

    if provider_choice == "fireworks":
        selected_model = model
        if selected_model is None:
            selected_model = typer.prompt("Fireworks model id").strip()
        try:
            written, _provider = apply_provider_endpoint_choice("fireworks", model=selected_model)
        except (OSError, ValueError) as error:
            provider_report["credential_reason"] = getattr(
                error, "reason_code", "provider_binding_invalid"
            )
            wipe_auto_passphrase()
            return service, provider_report
        typer.echo(f"Fireworks model: {selected_model}")
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
    if api_key is None:
        typer.echo("Provider API key (hidden local-terminal input; stored only in the Yoetz vault)")
    else:
        typer.echo("Provider API key supplied by --api-key; storing it only in the Yoetz vault")
    try:
        supplied_credential = None if api_key is None else bytearray(api_key.encode("utf-8"))
        if auto_passphrase is None and api_key is not None:
            current_config = load_config({}, {}, None)
            auto_passphrase = AutoUnlockPassphraseStore(
                bundle_root(_data_dir=current_config.storage.data_dir)
            ).load()
        result = await set_provider_credential(
            target,
            supplied_credential,
            None if auto_passphrase is None else bytearray(auto_passphrase),
        )
    except HumanCeremonyCliError as error:
        provider_report["credential"] = "failed"
        provider_report["credential_reason"] = error.reason
    else:
        provider_report["credential"] = result.activation_status
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


def _emit_provider_setup_layer_report() -> None:
    """Honestly separate binding/credential storage from undemonstrated runtime layers."""

    typer.echo("Provider binding and vault credential storage succeeded; that layer is supported.")
    typer.echo("Other layers (ready vs not demonstrated by this path):")
    typer.echo(f"  SDK extra (semantic-openai): {_semantic_openai_extra_state()}")
    typer.echo(
        "  Semantic evaluator: not composed "
        "(_semantic_not_configured in ready composition; not demonstrated)"
    )
    typer.echo("  Privacy policy: not demonstrated")
    typer.echo("  Transport probe: not demonstrated")
    typer.echo("  Installed artifact evidence: not demonstrated")
    typer.echo(
        "Stored binding/credential is not proof of live provider dispatch or semantic review."
    )


async def run_provider_setup(
    *,
    fireworks: bool = False,
    model: str | None = None,
    api_key: str | None = None,
) -> int:
    """Run only the simple local provider setup path used by ``yoetz --set``."""

    fully_supplied = fireworks and model is not None and api_key is not None
    if not _is_interactive_terminal() and not fully_supplied:
        return _usage_failure(
            "--set requires a local terminal unless --fireworks, --model, and --api-key "
            "are all supplied"
        )
    typer.echo("Yoetz LLM setup")
    typer.echo("The API key is entered with hidden input and stored only in the local vault.")
    service = await _service_reachability(start_if_absent=True)
    if not service.get("reachable"):
        typer.echo("provider_setup_failed: service_unavailable", err=True)
        return 20
    service, provider = await _interactive_provider_setup(
        service,
        provider_choice="fireworks" if fireworks else None,
        model=model,
        api_key=api_key,
    )
    binding = provider.get("binding")
    credential = provider.get("credential")
    typer.echo("")
    typer.echo(f"Provider binding: {binding}")
    typer.echo(f"API key: {credential}")
    if binding != "configured" or credential != "stored":
        reason = provider.get("credential_reason")
        if type(reason) is str:
            typer.echo(f"Reason: {reason}")
        return 20
    _emit_provider_setup_layer_report()
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


async def run_setup_wizard(
    *,
    non_interactive: bool,
    codex_path: str | None,
    accept: bool,
    json_output: bool,
) -> int:
    """Run the guided first-run setup and report each step honestly."""

    interactive = not non_interactive and _is_interactive_terminal()
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

    if chosen is None:
        registration = _registration_report(None, outcome="skipped", reason="codex_not_found")
    else:
        registration = await _register_step(chosen, interactive=interactive, accept=accept)

    service = await _service_reachability(start_if_absent=interactive)
    provider: dict[str, JsonValue] = {
        "binding": "skipped",
        "credential": "skipped",
    }
    if interactive:
        service, provider = await _interactive_provider_setup(service)

    next_steps: list[JsonValue] = []
    if not service["reachable"]:
        next_steps.append(_NEXT_SERVICE)
    if service.get("state") != "ready":
        next_steps.append(_NEXT_UNLOCK)
    next_steps.append(_NEXT_PRIVACY)
    if provider.get("binding") != "configured":
        next_steps.append(_NEXT_PROVIDER_TOML)
    if provider.get("credential") != "stored":
        next_steps.append(_NEXT_CREDENTIAL)

    mutating_run = interactive or accept
    marker_written = _write_setup_marker(str(registration["outcome"])) if mutating_run else False
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
    hooks_raw = integration.get("hooks")
    hooks = (
        hooks_raw
        if isinstance(hooks_raw, dict)
        else {}
    )
    readiness = _readiness_layers(
        binary=chosen,
        mcp_state=cast(str | None, registration.get("state")),
        plugin_presence=plugin_presence,
        hooks=hooks,
        consent_outcome=consent,
        service=service,
    )

    report: dict[str, JsonValue] = {
        "discovered": [_binary_row(binary) for binary in binaries],
        "integration": integration,
        "marker_written": marker_written,
        "next_steps": next_steps,
        "provider": provider,
        "readiness": readiness,
        "registration": registration,
        "schema": _REPORT_SCHEMA,
        "selected": None if chosen is None else _binary_row(chosen),
        "service": service,
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
    integration = report["integration"]
    readiness = report.get("readiness")
    typer.echo("Setup summary:")
    if isinstance(registration, dict):
        outcome = registration.get("outcome")
        reason = registration.get("reason")
        # MCP registration alone is not product readiness or automatic activation.
        if outcome in {"registered", "already_registered"}:
            line = f"  MCP registration: {outcome}; automatic activation not tested"
        else:
            line = f"  MCP registration: {outcome}"
        if reason:
            line += f" ({reason})"
        typer.echo(line)
        plugin = registration.get("plugin")
        if isinstance(plugin, dict):
            typer.echo(
                "  Plugin installation: "
                f"{plugin.get('outcome') or 'unknown'} "
                f"(presence={plugin.get('presence') or 'absent'})"
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
            typer.echo(
                "  Approved-check policy: "
                f"{check_policy.get('outcome') or 'absent'}"
            )
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
        typer.echo(f"  Provider credential: {provider.get('credential')}")
    steps = report["next_steps"]
    if isinstance(steps, list) and steps:
        typer.echo("Next steps:")
        for step in steps:
            typer.echo(f"  - {step}")


async def setup_status(*, json_output: bool) -> int:
    """Read-only setup posture: discovery, registration state, service, marker."""

    binaries = discover_codex_binaries()
    service_port = CodexMcpAdapter()
    rows: list[JsonValue] = []
    for binary in binaries:
        row = _binary_row(binary)
        try:
            state = await HarnessMcpService(service_port).status(binary)
            row["registration_state"] = state.value
        except McpRegistrationError as error:
            row["registration_state"] = None
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

    service = HarnessMcpService(CodexMcpAdapter())
    try:
        if action == "status":
            state = await service.status(chosen)
            _emit({"harness": harness, "state": state.value}, json_output=json_output)
            return 0
        preview = await service.preview(chosen)
        if action == "preview":
            _emit(
                {
                    "action": preview.action.value,
                    "harness": harness,
                    "preview_digest": preview.preview_digest,
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
            _emit_registration_preview(chosen)
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
