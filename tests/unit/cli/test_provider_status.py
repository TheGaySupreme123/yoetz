"""Unit tests for the read-only semantic readiness report.

The r4 dogfood spent four runs against an installation that structurally could not dispatch
semantic review, because nothing surfaced readiness before a check returned
``provider_not_configured``. These tests pin the two ways such a report can lie: reporting a
condition it never actually read, and counting a credential that belongs to a different provider
than the bound endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from yoetz.cli import provider_status as module
from yoetz.config.models import ProviderProfileConfig, VerificationConfig, YoetzConfig
from yoetz.ports.control import ControlError

pytestmark = pytest.mark.anyio


def _provider(provider_id: str = "openai") -> ProviderProfileConfig:
    return ProviderProfileConfig(
        provider_id=provider_id,
        endpoint_profile_id="openai-responses",
        endpoint_profile_version="1.0.0",
        model="gpt-5.4",
        capability_profile="openai-responses-structured-1",
    )


def test_credential_human_display_uses_one_constant_mask() -> None:
    assert module.credential_human_display(True) == "********"
    assert module.credential_human_display(False) == "not stored"
    assert module.credential_human_display(None) == "unknown"
    assert module.credential_human_display("a-real-key-must-never-be-reflected") == "unknown"


def _policy(*, llm_inference_enabled: bool, profile: str = "local_only") -> dict[str, object]:
    return {
        "policy": {
            "profile": profile,
            "channel_policies": [
                {"channel": "capability_testing", "enabled": False},
                {"channel": "llm_inference", "enabled": llm_inference_enabled},
                {"channel": "update_checks", "enabled": False},
            ],
        }
    }


class _Client:
    def __init__(
        self,
        capabilities: tuple[str, ...],
        policy: dict[str, object],
        *,
        state: str = "ready",
        state_reason: str = "none",
    ) -> None:
        self._capabilities = capabilities
        self._policy = policy
        self._state = state
        self._state_reason = state_reason
        self.closed = False

    async def service_status(self) -> Any:
        return SimpleNamespace(
            state=SimpleNamespace(value=self._state),
            state_reason=self._state_reason,
            capabilities=self._capabilities,
        )

    async def privacy_get_effective(self, request: object) -> dict[str, object]:
        del request
        return self._policy

    async def close(self) -> None:
        self.closed = True


_INSTALLATION_ID = "ins_50000000-0000-4000-8000-000000000001"


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    semantic: str = "optional",
    provider: ProviderProfileConfig | None = None,
    capabilities: tuple[str, ...] = ("external_provider",),
    llm_inference_enabled: bool = True,
    installation_state: str | None = None,
    service_state: str = "ready",
    service_state_reason: str = "none",
) -> _Client:
    config = YoetzConfig(
        profile="strict-local" if provider is None else "local-openai",
        verification=VerificationConfig(semantic=cast(Any, semantic)),
        provider=provider,
    )
    client = _Client(
        capabilities,
        _policy(llm_inference_enabled=llm_inference_enabled),
        state=service_state,
        state_reason=service_state_reason,
    )

    def _load(*_args: object) -> YoetzConfig:
        return config

    monkeypatch.setattr(module, "load_config", _load)

    async def _connect(_kind: object) -> _Client:
        return client

    monkeypatch.setattr(module, "connect_service", _connect)

    # privacy_get_effective requires a scope, which is read from installation state; without
    # this the report degrades to "unknown" for every policy-derived condition.
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    if installation_state is None:
        installation_state = json.dumps({"installation_id": _INSTALLATION_ID})
    if installation_state:
        (state / "installation-state.json").write_text(installation_state)
    monkeypatch.setattr(module, "state_dir", lambda: state)
    return client


async def test_all_four_conditions_met_reports_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _install(monkeypatch, tmp_path, provider=_provider())

    report = await module.provider_status_report()

    assert report["semantic_enabled"] is True
    assert report["endpoint_bound"] is True
    assert report["credential_connected"] is True
    assert report["llm_inference_enabled"] is True
    assert report["semantic_ready"] is True
    assert report["blockers"] == ()
    assert client.closed is True


async def test_llm_inference_channel_is_actually_read_from_canonical_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A disabled channel must read as disabled, not as unknown.

    The canonical policy names this list ``channel_policies``. Reading any other key returns None
    for every channel, which silently makes readiness unreachable on a correctly configured host.
    """

    _install(monkeypatch, tmp_path, provider=_provider(), llm_inference_enabled=False)

    report = await module.provider_status_report()

    assert report["llm_inference_enabled"] is False
    assert report["semantic_ready"] is False
    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    channel_blockers = [
        item for item in blockers if item.get("condition") == "llm_inference_channel"
    ]
    assert len(channel_blockers) == 1
    # "disabled" is a read answer; "unknown" would mean the policy was never inspected.
    assert channel_blockers[0]["state"] == "disabled"
    assert channel_blockers[0]["next_command"] == "yoetz privacy setup"


async def test_credential_for_a_different_provider_is_not_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The service withholds external_provider unless the *configured* provider is connected."""

    _install(monkeypatch, tmp_path, provider=_provider("anthropic"), capabilities=())

    report = await module.provider_status_report()

    assert report["endpoint_bound"] is True
    assert report["credential_connected"] is False
    assert report["semantic_ready"] is False
    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    credential = [item for item in blockers if item.get("condition") == "provider_credential"]
    assert len(credential) == 1
    assert credential[0]["state"] == "not_connected"
    assert credential[0]["next_command"] == "yoetz provider credential set"


async def test_unbound_endpoint_and_disabled_semantic_are_named_with_next_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        semantic="disabled",
        provider=None,
        capabilities=(),
        llm_inference_enabled=False,
    )

    report = await module.provider_status_report()

    assert report["semantic_ready"] is False
    conditions = tuple(
        cast(dict[str, object], item)["condition"]
        for item in cast(tuple[object, ...], report["blockers"])
    )
    assert conditions == (
        "verification.semantic",
        "provider_endpoint",
        "provider_credential",
        "llm_inference_channel",
    )
    # Every blocker carries an actionable next command; that is the whole point of the report.
    assert all(
        cast(dict[str, object], item)["next_command"]
        for item in cast(tuple[object, ...], report["blockers"])
    )
    assert len(cast(tuple[object, ...], report["next_commands"])) == 4


async def test_report_never_claims_a_live_provider_smoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, tmp_path, provider=_provider())

    report = await module.provider_status_report()

    notes = " ".join(cast(tuple[str, ...], report["notes"]))
    assert "does not prove live provider dispatch" in notes


async def test_exit_code_is_nonzero_when_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, tmp_path, provider=_provider(), capabilities=())

    assert await module.run_provider_status(json_output=True) == 20


async def test_exit_code_is_zero_when_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, tmp_path, provider=_provider())

    assert await module.run_provider_status(json_output=True) == 0


async def test_missing_installation_state_reports_unknown_not_a_false_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scope that cannot be built must degrade to "unknown", never to a claimed condition.

    ``privacy_get_effective`` requires a scope built from installation state. When that state is
    unreadable the policy is never inspected, so every policy-derived condition has to read as
    unknown and readiness has to be false — the report must not fill the gap with a guess.
    """

    _install(monkeypatch, tmp_path, provider=_provider(), installation_state="")

    report = await module.provider_status_report()

    assert report["llm_inference_enabled"] is None
    assert report["semantic_ready"] is False
    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    channel = [item for item in blockers if item.get("condition") == "llm_inference_channel"]
    assert len(channel) == 1
    assert channel[0]["state"] == "unknown"
    assert "next_command" not in channel[0]


async def test_locked_service_reports_real_blocker_first_without_false_remediation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        provider=_provider(),
        service_state="locked",
        service_state_reason="passphrase_required",
    )

    report = await module.provider_status_report()

    assert report["readiness_determinable"] is False
    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    assert blockers[0] == {
        "condition": "service_unlocked",
        "state": "locked",
        "reason": "passphrase_required",
        "next_command": "yoetz service unlock",
        "probed_lifecycle": "user_service_no_autostart",
        # A locked service is present, so MCP shares it rather than starting a new one.
        "mcp_local_composition": "shares_this_service",
    }
    unknown = tuple(item for item in blockers if item.get("state") == "unknown")
    assert {item["condition"] for item in unknown} == {
        "provider_credential",
        "llm_inference_channel",
    }
    assert all("next_command" not in item for item in unknown)
    assert report["next_commands"] == ("yoetz service unlock",)


async def test_absent_service_names_the_probed_lifecycle_and_the_mcp_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An absent service must not read as a contradiction of a working MCP session.

    The 2026-07-26 dogfood saw `service_unavailable` here while MCP-local work and live semantic
    dispatch succeeded, because the bridge starts the service on demand and this surface never
    does. The report has to say which lifecycle it probed.
    """

    _install(monkeypatch, tmp_path, provider=_provider())

    async def _refuse(_kind: object) -> object:
        raise ControlError("service_unavailable", retryable=True)

    monkeypatch.setattr(module, "connect_service", _refuse)

    report = await module.provider_status_report()

    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    assert blockers[0]["condition"] == "service_unlocked"
    assert blockers[0]["state"] == "service_unavailable"
    assert blockers[0]["probed_lifecycle"] == "user_service_no_autostart"
    assert blockers[0]["mcp_local_composition"] == "starts_on_demand"
    # The existing remediation for an operator who wants a persistent service is unchanged.
    assert blockers[0]["next_command"] == "yoetz service run"
    assert report["semantic_ready"] is False


async def test_stale_auto_unlock_points_to_repair_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        provider=_provider(),
        service_state="locked",
        service_state_reason="auto_unlock_stale",
    )

    report = await module.provider_status_report()

    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    assert blockers[0]["next_command"] == "yoetz service auto-unlock repair"
    assert report["next_commands"] == ("yoetz service auto-unlock repair",)


async def test_rejected_auto_unlock_points_to_repair_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        provider=_provider(),
        service_state="locked",
        service_state_reason="auto_unlock_rejected",
    )

    report = await module.provider_status_report()

    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    assert blockers[0]["next_command"] == "yoetz service auto-unlock repair"
    assert report["next_commands"] == ("yoetz service auto-unlock repair",)


async def test_uninitialized_vault_points_to_guided_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        provider=_provider(),
        service_state="locked",
        service_state_reason="vault_uninitialized",
    )

    report = await module.provider_status_report()

    blockers = cast(tuple[dict[str, object], ...], report["blockers"])
    assert blockers[0]["next_command"] == "yoetz setup"
    assert report["next_commands"] == ("yoetz setup",)
