"""Read-only semantic readiness report for operator surfaces.

Reports the installation-local conditions that must all hold before external semantic review can
dispatch, without claiming a live provider smoke or writing any state. It separately reports the
registered Codex MCP route, because a strict agent route cannot dispatch semantic review even
when every installation condition holds.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from yoetz.config.load import load_config
from yoetz.config.paths import state_dir
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.service.client import connect_service

__all__ = [
    "credential_human_display",
    "machine_scope_request",
    "mcp_route_observation",
    "provider_status_report",
    "run_provider_status",
]

_SCHEMA: Final = "yoetz.provider-status/1"
_CREDENTIAL_MASK: Final = "********"
# This report probes the persistent user service over the fixed endpoint only. It never starts
# one, unlike the MCP bridge's connect-on-demand path.
_PROBED_LIFECYCLE: Final = "user_service_no_autostart"


def _stdout_json(value: JsonValue) -> None:
    sys.stdout.buffer.write(canonical_encode(value) + b"\n")
    sys.stdout.buffer.flush()


def _emit(value: Mapping[str, JsonValue], *, json_output: bool) -> None:
    if json_output or not sys.stdout.isatty():
        _stdout_json(dict(value))
        return
    print(f"schema: {value.get('schema')}")
    print(f"semantic_mode: {value.get('verification_semantic')}")
    print(f"endpoint_bound: {value.get('endpoint_bound')}")
    binding = value.get("endpoint")
    if isinstance(binding, Mapping):
        print(
            "  "
            f"provider_id={binding.get('provider_id')} "
            f"model={binding.get('model')} "
            f"profile={binding.get('endpoint_profile_id')}"
        )
    print(f"credential: {credential_human_display(value.get('credential_connected'))}")
    print(f"llm_inference_enabled: {value.get('llm_inference_enabled')}")
    print(f"repository_grant: {value.get('repository_grant_state')}")
    print(f"repository_migration: {value.get('repository_migration_state')}")
    print(f"semantic_ready: {value.get('semantic_ready')}")
    route = value.get("mcp_route")
    if isinstance(route, Mapping):
        print(
            "mcp_route: "
            f"registered={route.get('registered_profile')} "
            f"configured={route.get('configured_profile')} "
            f"observed={route.get('observed')}"
        )
    print(f"agent_route_semantic_ready: {value.get('agent_route_semantic_ready')}")
    blockers = value.get("blockers")
    if isinstance(blockers, list | tuple) and blockers:
        print("blockers:")
        for item in blockers:
            if isinstance(item, Mapping):
                print(f"  - {item.get('condition')}: {item.get('next_command')}")
                if item.get("mcp_local_composition") == "starts_on_demand":
                    print(
                        "    (this check probes the running user service and never starts one; "
                        "the MCP bridge starts it on demand, but this check does not probe "
                        "that path)"
                    )
            else:
                print(f"  - {item}")
    next_steps = value.get("next_commands")
    if isinstance(next_steps, list | tuple) and next_steps:
        print("next commands:")
        for step in next_steps:
            print(f"  - {step}")


def credential_human_display(value: object) -> str:
    """Render credential presence without reflecting any property of the stored secret."""

    if value is True:
        return _CREDENTIAL_MASK
    if value is False:
        return "not stored"
    return "unknown"


def _channel_enabled(policy: Mapping[str, object], channel: str) -> bool | None:
    """Read one channel's enabled flag from the canonical effective policy.

    The canonical policy names this list ``channel_policies``; reading any other key silently
    reports every channel as unknown and makes readiness unreachable.
    """

    channels = policy.get("channel_policies")
    if not isinstance(channels, list | tuple):
        return None
    for item in cast("list[object] | tuple[object, ...]", channels):
        if not isinstance(item, Mapping):
            continue
        entry = cast(Mapping[str, object], item)
        if entry.get("channel") == channel:
            return entry.get("enabled") is True
    return None


def _mcp_local_composition(service_state: str | None, *, service_observed: bool) -> str:
    """Say what the MCP-local path would find, only from what this probe actually established.

    A service that answered is shared by both surfaces. An absent one is started on demand by the
    bridge. Every other refusal — an untrusted peer, a protocol mismatch — proves neither, so it
    stays `unknown` instead of asserting a lifecycle this check never observed.
    """

    if service_observed:
        return "shares_this_service"
    if service_state in {None, "service_unavailable"}:
        return "starts_on_demand"
    return "unknown"


async def mcp_route_observation() -> dict[str, JsonValue]:
    """Report which Codex MCP route is registered, or say plainly that it was not read.

    A strict registration and a policy registration are both ``yoetz_owned``, so registration
    state alone cannot tell an operator whether the agent route can dispatch semantic review at
    all. This probe adds the missing fact.

    Fail-soft is deliberate and load-bearing: this report exists to stay readable when the
    installation is broken, so an absent Codex, an unreadable entry, or any registration error
    degrades to ``observed: false`` — the module's existing "unknown means unread" convention —
    rather than raising or moving the exit code.
    """

    # Imported here: `yoetz.cli.setup` imports this module lazily, and the discovery/adapter
    # stack is only needed on this one path.
    from yoetz.adapters.integrations.codex_discovery import discover_codex_binaries
    from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
    from yoetz.application.harness_mcp import HarnessMcpService
    from yoetz.cli import setup as cli_setup

    try:
        # The registration-time authority stays owned by `setup`, so this reads it rather than
        # growing a second answer to the same question.
        configured: JsonValue = cli_setup.configured_mcp_route_profile()
    except Exception:
        configured = None
    unread: dict[str, JsonValue] = {
        "registration_state": None,
        "registered_profile": None,
        "configured_profile": configured,
        "observed": False,
    }
    try:
        binaries = discover_codex_binaries()
        if not binaries:
            return unread
        observation = await HarnessMcpService(CodexMcpAdapter()).observe(binaries[0])
    except Exception:
        return unread
    return {
        "registration_state": observation.state.value,
        "registered_profile": observation.route_profile,
        "configured_profile": configured,
        "observed": True,
    }


def machine_scope_request() -> JsonObject:
    """Build the ``privacy_get_effective`` body for this installation's machine scope.

    ``scope`` is required by the frozen request schema, so an unreadable installation id is
    reported as a caller error here rather than sent as a body the service must reject.
    """

    try:
        state = json.loads((state_dir() / "installation-state.json").read_text())
        installation_id = state["installation_id"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ControlError("invalid_request") from exc
    if type(installation_id) is not str or not installation_id:
        raise ControlError("invalid_request")
    body: dict[str, JsonValue] = {
        "schema_version": "1.0.0",
        "scope": {"kind": "machine", "installation_id": installation_id},
    }
    return JsonObject(body)


async def provider_status_report(*, workspace_locator: Path | None = None) -> dict[str, JsonValue]:
    """Compose a nonsecret readiness snapshot from config, service, and policy."""

    config = load_config({}, os.environ, None)
    verification_semantic = config.verification.semantic
    semantic_enabled = verification_semantic != "disabled"
    endpoint_bound = config.provider is not None
    endpoint: dict[str, JsonValue] | None = None
    if config.provider is not None:
        endpoint = {
            "provider_id": config.provider.provider_id,
            "model": config.provider.model,
            "endpoint_profile_id": config.provider.endpoint_profile_id,
            "endpoint_profile_version": config.provider.endpoint_profile_version,
        }

    service_state: str | None = None
    service_state_reason: str | None = None
    credential_connected: bool | None = None
    llm_inference_enabled: bool | None = None
    policy_profile: str | None = None
    repository_grant_state: str | None = None
    repository_migration_state: str | None = None
    # Set only when a service actually answered, so presence is observed rather than inferred
    # from the shape of a refusal.
    service_observed = False

    try:
        client = await connect_service(
            ControlClientKind.CLI,
            workspace_locator=WorkspaceLocator(
                str(
                    (Path.cwd() if workspace_locator is None else workspace_locator).resolve(
                        strict=True
                    )
                )
            ),
        )
        try:
            status = await client.service_status()
            service_observed = True
            service_state = status.state.value
            service_state_reason = status.state_reason
            if status.state.value == "ready":
                credential_connected = "external_provider" in status.capabilities
                try:
                    effective = await client.privacy_get_setup(
                        JsonObject({"schema_version": "2.0.0"})
                    )
                except Exception:
                    effective = None
                if isinstance(effective, Mapping):
                    plain = cast(Mapping[str, object], dict(effective))
                    raw_grant = plain.get("grant_state")
                    raw_migration = plain.get("migration_state")
                    if raw_grant in {"granted", "missing"}:
                        repository_grant_state = cast(str, raw_grant)
                    if raw_migration in {
                        "not_applicable",
                        "legacy_route_available",
                        "first_repository_available",
                        "consumed",
                    }:
                        repository_migration_state = cast(str, raw_migration)
                    policy = plain.get("composed_policy")
                    if isinstance(policy, Mapping):
                        policy_map = cast(Mapping[str, object], policy)
                        profile = policy_map.get("profile")
                        if type(profile) is str:
                            policy_profile = profile
                        llm_inference_enabled = _channel_enabled(policy_map, "llm_inference")
            else:
                credential_connected = None
        finally:
            await client.close()
    except ControlError as error:
        service_state = error.reason
        service_state_reason = error.reason
        credential_connected = None
        llm_inference_enabled = None

    mcp_route = await mcp_route_observation()
    registered_profile = mcp_route["registered_profile"]

    blockers: list[dict[str, JsonValue]] = []
    if service_state != "ready":
        if service_state_reason in {"auto_unlock_rejected", "auto_unlock_stale"}:
            service_command = "yoetz service auto-unlock repair"
        elif service_state_reason == "vault_uninitialized":
            service_command = "yoetz setup"
        elif service_state in {None, "service_unavailable"}:
            service_command = "yoetz service run"
        else:
            service_command = "yoetz service unlock"
        blockers.append(
            {
                "condition": "service_unlocked",
                "state": service_state or "service_unavailable",
                "reason": service_state_reason,
                "next_command": service_command,
                # This surface deliberately connects without starting anything, so an absent
                # service reads as unavailable here while the MCP bridge — which connects on
                # demand — starts the same service and succeeds. Naming the probed lifecycle
                # keeps the two reports from looking like a contradiction.
                "probed_lifecycle": _PROBED_LIFECYCLE,
                "mcp_local_composition": _mcp_local_composition(
                    service_state, service_observed=service_observed
                ),
            }
        )
    if not semantic_enabled:
        blockers.append(
            {
                "condition": "verification.semantic",
                "state": verification_semantic,
                "next_command": "set [verification].semantic to optional|required in config.toml",
            }
        )
    if not endpoint_bound:
        blockers.append(
            {
                "condition": "provider_endpoint",
                "state": "unbound",
                "next_command": "yoetz provider endpoint --provider <preset> --model <model>",
            }
        )
    if credential_connected is None:
        blockers.append({"condition": "provider_credential", "state": "unknown"})
    elif credential_connected is False:
        blockers.append(
            {
                "condition": "provider_credential",
                "state": "not_connected",
                "next_command": "yoetz provider credential set",
            }
        )
    if llm_inference_enabled is None:
        blockers.append({"condition": "llm_inference_channel", "state": "unknown"})
    elif llm_inference_enabled is False:
        blockers.append(
            {
                "condition": "llm_inference_channel",
                "state": "disabled",
                "next_command": "yoetz --privacy",
            }
        )
    if repository_grant_state is None:
        blockers.append({"condition": "repository_privacy_grant", "state": "unknown"})
    elif repository_grant_state == "missing":
        blockers.append(
            {
                "condition": "repository_privacy_grant",
                "state": "missing",
                "next_command": "yoetz --privacy",
            }
        )
    if registered_profile == "strict":
        # Scoped to the agent route on purpose. ADR-018 decision 2 makes the route ceiling
        # process-local, so a strict Codex registration does not stop a CLI or terminal check
        # from dispatching semantic review — it is not an installation blocker.
        blockers.append(
            {
                "condition": "mcp_route_profile",
                "state": "strict",
                "scope": "agent_route",
                "next_command": "yoetz integrate codex mcp preview",
            }
        )

    readiness_determinable = (
        service_state == "ready"
        and credential_connected is not None
        and llm_inference_enabled is not None
        and repository_grant_state is not None
    )
    semantic_ready = (
        readiness_determinable
        and semantic_enabled
        and endpoint_bound
        and credential_connected is True
        and llm_inference_enabled is True
        and repository_grant_state == "granted"
    )
    next_commands = tuple(
        cast(str, item["next_command"])
        for item in blockers
        if type(item.get("next_command")) is str
    )

    return {
        "schema": _SCHEMA,
        "verification_semantic": verification_semantic,
        "semantic_enabled": semantic_enabled,
        "endpoint_bound": endpoint_bound,
        "endpoint": endpoint,
        "credential_connected": credential_connected,
        "llm_inference_enabled": llm_inference_enabled,
        "privacy_profile": policy_profile,
        "repository_grant_state": repository_grant_state,
        "repository_migration_state": repository_migration_state,
        "service_state": service_state,
        "service_state_reason": service_state_reason,
        "readiness_determinable": readiness_determinable,
        "semantic_ready": semantic_ready,
        "mcp_route": mcp_route,
        "agent_route_semantic_ready": semantic_ready and registered_profile == "policy",
        "blockers": tuple(blockers),
        "next_commands": next_commands,
        "notes": (
            "semantic_ready is structural readiness only; it does not prove live provider dispatch.",
            "credential_connected reports the configured provider's credential, not any provider.",
            "A credential for a different provider than the bound endpoint does not count.",
            "unknown means the service could not be read, not that the step is incomplete.",
            "agent_route_semantic_ready describes the registered Codex MCP route only; "
            "semantic_ready describes this repository-bound installation view. Neither "
            "substitutes for the other.",
            "A strict registered route does not make this installation not-ready: CLI and "
            "terminal checks still dispatch, only the strict agent route cannot.",
            "mcp_route.observed false means the route was not read, not that none is registered.",
        ),
    }


async def run_provider_status(*, json_output: bool) -> int:
    """Emit the readiness report and return a process exit code."""

    report = await provider_status_report()
    _emit(report, json_output=json_output)
    return 0 if report.get("semantic_ready") is True else 20
