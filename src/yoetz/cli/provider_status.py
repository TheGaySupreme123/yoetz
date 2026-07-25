"""Read-only semantic readiness report for operator surfaces.

Reports the four conditions that must all hold before external semantic review can
dispatch, without claiming a live provider smoke or writing any state.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Final, cast

from yoetz.config.load import load_config
from yoetz.config.paths import state_dir
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.service.client import connect_service

__all__ = ["machine_scope_request", "provider_status_report", "run_provider_status"]

_SCHEMA: Final = "yoetz.provider-status/1"


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
    print(f"credential_connected: {value.get('credential_connected')}")
    print(f"llm_inference_enabled: {value.get('llm_inference_enabled')}")
    print(f"semantic_ready: {value.get('semantic_ready')}")
    blockers = value.get("blockers")
    if isinstance(blockers, list | tuple) and blockers:
        print("blockers:")
        for item in blockers:
            if isinstance(item, Mapping):
                print(f"  - {item.get('condition')}: {item.get('next_command')}")
            else:
                print(f"  - {item}")
    next_steps = value.get("next_commands")
    if isinstance(next_steps, list | tuple) and next_steps:
        print("next commands:")
        for step in next_steps:
            print(f"  - {step}")


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


async def provider_status_report() -> dict[str, JsonValue]:
    """Compose a nonsecret readiness snapshot from config, service, and policy."""

    config = load_config({}, {}, None)
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
    credential_connected: bool | None = None
    llm_inference_enabled: bool | None = None
    policy_profile: str | None = None

    try:
        client = await connect_service(ControlClientKind.CLI)
        try:
            status = await client.service_status()
            service_state = status.state.value
            if status.state.value == "ready":
                credential_connected = "external_provider" in status.capabilities
                try:
                    effective = await client.privacy_get_effective(machine_scope_request())
                except Exception:
                    effective = None
                if isinstance(effective, Mapping):
                    plain = cast(Mapping[str, object], dict(effective))
                    policy = plain.get("policy")
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
        credential_connected = None
        llm_inference_enabled = None

    blockers: list[dict[str, JsonValue]] = []
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
    if credential_connected is not True:
        blockers.append(
            {
                "condition": "provider_credential",
                "state": "unknown" if credential_connected is None else "not_connected",
                "next_command": "yoetz provider credential set",
            }
        )
    if llm_inference_enabled is not True:
        blockers.append(
            {
                "condition": "llm_inference_channel",
                "state": "unknown" if llm_inference_enabled is None else "disabled",
                "next_command": "yoetz privacy setup",
            }
        )

    semantic_ready = (
        semantic_enabled
        and endpoint_bound
        and credential_connected is True
        and llm_inference_enabled is True
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
        "service_state": service_state,
        "semantic_ready": semantic_ready,
        "blockers": tuple(blockers),
        "next_commands": next_commands,
        "notes": (
            "semantic_ready is structural readiness only; it does not prove live provider dispatch.",
            "credential_connected reports the configured provider's credential, not any provider.",
            "A credential for a different provider than the bound endpoint does not count.",
        ),
    }


async def run_provider_status(*, json_output: bool) -> int:
    """Emit the readiness report and return a process exit code."""

    report = await provider_status_report()
    _emit(report, json_output=json_output)
    return 0 if report.get("semantic_ready") is True else 20
