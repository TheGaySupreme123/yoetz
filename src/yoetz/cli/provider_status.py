"""Read-only semantic readiness report for operator surfaces.

Reports the installation-local conditions that must all hold before external semantic review can
dispatch, without claiming a live provider smoke or writing any state. It separately reports the
registered Codex MCP route, because a strict agent route cannot dispatch semantic review even
when every installation condition holds.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, cast

from yoetz.config.load import load_config
from yoetz.config.models import ConfigError
from yoetz.config.paths import PathSafetyError, bundle_root
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.service.client import connect_service

__all__ = [
    "MachineScopeError",
    "credential_human_display",
    "host_admission_observation",
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
_INSTALLATION_MARKER_DOMAIN: Final = b"yoetz/installation-state/v1\x00"
_MAX_INSTALLATION_MARKER_BYTES: Final = 65_536
_INSTALLATION_MARKER_KEYS: Final = frozenset(
    {
        "schema_version",
        "installation_id",
        "vault_mode",
        "root_envelope_base64",
        "mode_binding_digest",
        "record_digest",
    }
)


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
    admission = value.get("host_admission")
    if isinstance(admission, Mapping):
        print(
            "host_admission: "
            + " ".join(
                f"{host}={_admission_state(admission.get(host))}"
                for host in ("claude", "codex", "cursor")
            )
        )
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


def _admission_state(value: object) -> str:
    if isinstance(value, Mapping):
        state = cast(Mapping[str, object], value).get("state")
        if type(state) is str:
            return state
    return "unknown"


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


async def mcp_route_observation(
    workspace_locator: Path | None = None,
) -> dict[str, JsonValue]:
    """Report exclusive external/plugin MCP ownership without inferring runtime success.

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
    from yoetz.adapters.integrations.portable_plugin import observe_plugin_managed_mcp
    from yoetz.application.harness_mcp import HarnessMcpService
    from yoetz.cli import setup as cli_setup
    from yoetz.ports.plugin_artifacts import McpOwnershipState

    try:
        # The registration-time authority stays owned by `setup`, so this reads it rather than
        # growing a second answer to the same question.
        configured: JsonValue = cli_setup.configured_mcp_route_profile()
    except Exception:
        configured = None
    external_state: str | None = None
    external_profile: str | None = None
    external_observed = False
    try:
        binaries = discover_codex_binaries()
        if binaries:
            observation = await HarnessMcpService(CodexMcpAdapter()).observe(binaries[0])
            external_state = observation.state.value
            external_profile = observation.route_profile
            external_observed = True
    except Exception:
        pass
    root = Path.cwd() if workspace_locator is None else workspace_locator
    plugin = observe_plugin_managed_mcp(root)

    external_owned = external_state == "yoetz_owned"
    external_foreign = external_state == "foreign_present"
    plugin_owned = plugin.ownership_state is McpOwnershipState.PLUGIN
    plugin_foreign = plugin.ownership_state is McpOwnershipState.FOREIGN
    plugin_ambiguous = plugin.ownership_state is McpOwnershipState.AMBIGUOUS
    if not external_observed or not plugin.observed or plugin_ambiguous:
        ownership = McpOwnershipState.AMBIGUOUS
        source: JsonValue = None
        route_profile: JsonValue = None
        observed = False
    elif external_owned and plugin_owned:
        ownership = McpOwnershipState.DUAL
        source = "dual"
        route_profile = None
        observed = True
    elif external_foreign or plugin_foreign:
        ownership = McpOwnershipState.FOREIGN
        source = "foreign"
        route_profile = None
        observed = True
    elif external_owned:
        ownership = McpOwnershipState.EXTERNAL
        source = "external_registration"
        route_profile = external_profile
        observed = True
    elif plugin_owned:
        ownership = McpOwnershipState.PLUGIN
        source = "plugin_managed"
        route_profile = plugin.route_profile
        observed = True
    else:
        ownership = McpOwnershipState.ABSENT
        source = None
        route_profile = None
        observed = True

    return {
        "registration_state": external_state,
        "registered_profile": route_profile,
        "configured_profile": configured,
        "observed": observed,
        "owner_source": source,
        "ownership_state": ownership.value,
        "external_registration_state": external_state,
        "plugin_managed_state": plugin.ownership_state.value,
    }


def _admission_repository_root(workspace_locator: Path | None) -> Path:
    """Resolve the locator to the repository root that owns the host admission files.

    The hosts honor their project-scoped admission files at the repository root
    (``.claude/settings.local.json``, ``.codex/config.toml``, ``.cursor/*``), so an observation
    started in a subdirectory must walk up to that root or it reports ``absent`` for files it
    never looked at. The result is absolute — the adapter refuses a relative root as
    ``TARGET_UNSAFE`` — and the final symlink is deliberately not resolved: a symlinked root
    must keep reading as ``unknown`` rather than silently observing the symlink's target.
    """

    start = Path.cwd() if workspace_locator is None else workspace_locator.absolute()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def host_admission_observation(
    workspace_locator: Path | None = None,
    *,
    codex_owner: str | None = None,
) -> dict[str, JsonValue]:
    """Report each host's project-scoped admission entry for ``check`` without inferring intent.

    Reads only the three hosts' own files under the repository root resolved from the workspace
    locator (issue #467). An unreadable file is ``unknown``, never ``absent``: the report must
    not tell an operator that a host holds no admission when it simply could not read the host's
    rule file.
    """

    from yoetz.adapters.integrations.host_admission import (
        ADMISSION_HOSTS,
        HostAdmissionError,
        observe_host_admission,
    )

    root = _admission_repository_root(workspace_locator)
    owner = codex_owner if codex_owner in {"external", "plugin"} else None
    report: dict[str, JsonValue] = {}
    for host in ADMISSION_HOSTS:
        try:
            observation = observe_host_admission(
                host,
                root,
                owner=cast('Literal["external", "plugin"] | None', owner)
                if host == "codex"
                else None,
            )
        except HostAdmissionError as error:
            report[host] = {
                "entries": [],
                "host": host,
                "observed": False,
                "reason": error.reason.value,
                "state": "unknown",
            }
            continue
        report[host] = observation.as_json()
    return report


# Closed remediation set: each machine-scope construction failure names exactly one trusted next
# command. Values are fixed strings — no paths, no marker content, no user-controlled text.
_MACHINE_SCOPE_REMEDIATIONS: Final[Mapping[str, str]] = {
    "installation_bundle_unavailable": (
        "the configured storage bundle could not be resolved; "
        "fix [storage].data_dir in config.toml, then retry"
    ),
    "installation_marker_invalid": (
        "the installation marker could not be read; "
        "run 'yoetz service recovery status' for the trusted repair path, then retry"
    ),
    "installation_marker_missing": (
        "this installation is not initialized; run 'yoetz setup', then retry"
    ),
}


class MachineScopeError(Exception):
    """A bounded local machine-scope construction failure; never a service result.

    Raised before any service request when this installation's identity cannot be resolved from
    the configured storage bundle. The reason set is closed so every caller renders one valid
    actionable diagnostic instead of constructing an error the control vocabulary does not admit
    (issue #517: an inadmissible ``ControlError`` reason surfaced as generic ``internal_error``).
    """

    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or reason not in _MACHINE_SCOPE_REMEDIATIONS:
            raise ValueError("machine_scope_reason_invalid")
        self.reason = reason
        super().__init__(reason)

    @property
    def remediation(self) -> str:
        """One fixed actionable next step for this reason; safe for human stderr output."""

        return _MACHINE_SCOPE_REMEDIATIONS[self.reason]


def machine_scope_request() -> JsonObject:
    """Build the ``privacy_get_effective`` body for this installation's machine scope.

    The installation marker lives in the configured storage bundle — the same root the service
    resolves via ``bundle_root(_data_dir=config.storage.data_dir)`` — not the fixed platform
    state directory, so an explicit ``storage.data_dir`` is honored here too (issue #517).
    ``scope`` is required by the frozen request schema, so a scope that cannot be constructed
    locally is reported as a bounded :class:`MachineScopeError` before any service request rather
    than sent as a body the service must reject.
    """

    try:
        config = load_config({}, os.environ, None)
        root = bundle_root(_data_dir=config.storage.data_dir)
    except (ConfigError, OSError, PathSafetyError) as exc:
        raise MachineScopeError("installation_bundle_unavailable") from exc
    marker = root / "installation-state.json"
    try:
        facts = marker.lstat()
        encoded = marker.read_bytes()
    except FileNotFoundError as exc:
        raise MachineScopeError("installation_marker_missing") from exc
    except OSError as exc:
        raise MachineScopeError("installation_marker_invalid") from exc
    try:
        if (
            not stat.S_ISREG(facts.st_mode)
            or stat.S_ISLNK(facts.st_mode)
            or facts.st_nlink != 1
            or len(encoded) > _MAX_INSTALLATION_MARKER_BYTES
            or not encoded.endswith(b"\n")
            or encoded.endswith(b"\n\n")
        ):
            raise ValueError
        if os.name == "posix" and stat.S_IMODE(facts.st_mode) & 0o077:
            raise ValueError
        state: object = strict_json_parse(encoded[:-1])
        if type(state) is not dict or canonical_encode(cast(JsonValue, state)) != encoded[:-1]:
            raise ValueError
        source = cast("dict[str, JsonValue]", state)
        if frozenset(source) != _INSTALLATION_MARKER_KEYS or source["schema_version"] != "1":
            raise ValueError
        body = dict(source)
        record_digest = body.pop("record_digest")
        if type(record_digest) is not str:
            raise ValueError
        expected = (
            "sha256:"
            + hashlib.sha256(_INSTALLATION_MARKER_DOMAIN + canonical_encode(body)).hexdigest()
        )
        if not hmac.compare_digest(record_digest, expected):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise MachineScopeError("installation_marker_invalid") from exc
    installation_id: object = source["installation_id"]
    if type(installation_id) is not str:
        raise MachineScopeError("installation_marker_invalid")
    try:
        validate_id(IdKind.INSTALLATION, installation_id)
    except (TypeError, ValueError) as exc:
        raise MachineScopeError("installation_marker_invalid") from exc
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
    endpoint_bound = config.provider is not None or config.external_runtime is not None
    endpoint: dict[str, JsonValue] | None = None
    if config.provider is not None:
        endpoint = {
            "provider_id": config.provider.provider_id,
            "model": config.provider.model,
            "endpoint_profile_id": config.provider.endpoint_profile_id,
            "endpoint_profile_version": config.provider.endpoint_profile_version,
            "credential_authority": "yoetz_vault_api_credential",
        }
    elif config.external_runtime is not None:
        runtime = config.external_runtime
        endpoint = {
            "provider_id": runtime.provider_id,
            "model": runtime.model,
            "endpoint_profile_id": runtime.endpoint_profile_id,
            "endpoint_profile_version": runtime.endpoint_profile_version,
            "credential_authority": runtime.credential_authority,
            "runtime_version": runtime.runtime_version,
            "capability_profile": runtime.capability_profile,
            "upstream_body_observability": "unavailable",
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

    mcp_route = await mcp_route_observation(workspace_locator)
    registered_profile = mcp_route.get("registered_profile")
    ownership_state = mcp_route.get("ownership_state")
    if ownership_state is None and mcp_route.get("registration_state") == "yoetz_owned":
        ownership_state = "external"
    route_observed = mcp_route.get("observed") is True
    host_admission = host_admission_observation(
        workspace_locator,
        codex_owner=ownership_state if type(ownership_state) is str else None,
    )
    # Admission is a repository fact derived from the grant; it drifts when the grant no
    # longer permits external review or (Codex) the registered route is strict, and a present
    # entry then admits a call Yoetz will refuse or hold anyway. Reported, never auto-removed
    # here: this surface is read-only.
    grant_permits_review = llm_inference_enabled is True and repository_grant_state == "granted"
    grant_known = llm_inference_enabled is not None and repository_grant_state is not None

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
                "next_command": (
                    "yoetz provider endpoint --provider <preset> --model <model> OR "
                    "yoetz provider codex-subscription setup --executable <path>"
                ),
            }
        )
    if credential_connected is None:
        blockers.append({"condition": "provider_credential", "state": "unknown"})
    elif credential_connected is False:
        credential_command = (
            "yoetz provider codex-subscription status"
            if config.external_runtime is not None
            else "yoetz provider credential set"
        )
        blockers.append(
            {
                "condition": "provider_credential",
                "state": "not_connected",
                "next_command": credential_command,
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
    if route_observed and ownership_state in {"dual", "foreign", "ambiguous"}:
        blockers.append(
            {
                "condition": "mcp_ownership_state",
                "state": ownership_state or "ambiguous",
                "scope": "agent_route",
            }
        )
    for host, admission in host_admission.items():
        state = admission.get("state") if isinstance(admission, Mapping) else None
        if state not in {"present", "partial"}:
            continue
        drifted = (grant_known and not grant_permits_review) or (
            host == "codex" and route_observed and registered_profile == "strict"
        )
        if drifted:
            blockers.append(
                {
                    "condition": "host_admission_drift",
                    "state": state,
                    "scope": "agent_route",
                    "host": host,
                    "next_command": (
                        f"yoetz integrate {host} admission preview --action revoke --project-root ."
                    ),
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
        "host_admission": host_admission,
        "agent_route_semantic_ready": (
            semantic_ready
            and route_observed
            and ownership_state in {"external", "plugin"}
            and registered_profile == "policy"
        ),
        "blockers": tuple(blockers),
        "next_commands": next_commands,
        "notes": (
            "semantic_ready is structural readiness only; it does not prove live provider dispatch.",
            "credential_connected reports the configured provider's credential, not any provider.",
            "For external_runtime_oauth, READY credential presence is the exact binding, digest, "
            "and dedicated home; ChatGPT login and model availability are proven inside evaluate() "
            "or by 'yoetz provider codex-subscription status'. Yoetz never reads the credential.",
            "A credential for a different provider than the bound endpoint does not count.",
            "unknown means the service could not be read, not that the step is incomplete.",
            "agent_route_semantic_ready describes one exclusively observed Codex MCP owner; "
            "semantic_ready describes this repository-bound installation view. Neither "
            "substitutes for the other.",
            "A strict registered route does not make this installation not-ready: CLI and "
            "terminal checks still dispatch, only the strict agent route cannot.",
            "mcp_route.observed false means ownership was not read unambiguously, not that no "
            "route exists.",
            "host_admission reports each host's own project-scoped rule admitting the "
            "semantic check past its automatic reviewer; it is host tool-call authorization "
            "and proves neither dispatch nor a Yoetz decision. unknown means the host file "
            "could not be read.",
        ),
    }


async def run_provider_status(*, json_output: bool) -> int:
    """Emit the readiness report and return a process exit code."""

    report = await provider_status_report()
    _emit(report, json_output=json_output)
    return 0 if report.get("semantic_ready") is True else 20
