"""Unit tests for the read-only semantic readiness report.

The r4 dogfood spent four runs against an installation that structurally could not dispatch
semantic review, because nothing surfaced readiness before a check returned
``provider_not_configured``. These tests pin the two ways such a report can lie: reporting a
condition it never actually read, and counting a credential that belongs to a different provider
than the bound endpoint.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from yoetz.cli import provider_status as module
from yoetz.config.models import ProviderProfileConfig, VerificationConfig, YoetzConfig

pytestmark = pytest.mark.anyio


def _provider(provider_id: str = "openai") -> ProviderProfileConfig:
    return ProviderProfileConfig(
        provider_id=provider_id,
        endpoint_profile_id="openai-responses",
        endpoint_profile_version="1.0.0",
        model="gpt-5.4",
        capability_profile="openai-responses-structured-1",
    )


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
    def __init__(self, capabilities: tuple[str, ...], policy: dict[str, object]) -> None:
        self._capabilities = capabilities
        self._policy = policy
        self.closed = False

    async def service_status(self) -> Any:
        class _State:
            value = "ready"

        class _Status:
            state = _State()
            capabilities = self._capabilities

        return _Status()

    async def privacy_get_effective(self, request: object) -> dict[str, object]:
        del request
        return self._policy

    async def close(self) -> None:
        self.closed = True


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    semantic: str = "optional",
    provider: ProviderProfileConfig | None = None,
    capabilities: tuple[str, ...] = ("external_provider",),
    llm_inference_enabled: bool = True,
) -> _Client:
    config = YoetzConfig(
        profile="strict-local" if provider is None else "local-openai",
        verification=VerificationConfig(semantic=cast(Any, semantic)),
        provider=provider,
    )
    client = _Client(capabilities, _policy(llm_inference_enabled=llm_inference_enabled))

    def _load(*_args: object) -> YoetzConfig:
        return config

    monkeypatch.setattr(module, "load_config", _load)

    async def _connect(_kind: object) -> _Client:
        return client

    monkeypatch.setattr(module, "connect_service", _connect)
    return client


async def test_all_four_conditions_met_reports_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, provider=_provider())

    report = await module.provider_status_report()

    assert report["semantic_enabled"] is True
    assert report["endpoint_bound"] is True
    assert report["credential_connected"] is True
    assert report["llm_inference_enabled"] is True
    assert report["semantic_ready"] is True
    assert report["blockers"] == ()
    assert client.closed is True


async def test_llm_inference_channel_is_actually_read_from_canonical_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled channel must read as disabled, not as unknown.

    The canonical policy names this list ``channel_policies``. Reading any other key returns None
    for every channel, which silently makes readiness unreachable on a correctly configured host.
    """

    _install(monkeypatch, provider=_provider(), llm_inference_enabled=False)

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service withholds external_provider unless the *configured* provider is connected."""

    _install(monkeypatch, provider=_provider("anthropic"), capabilities=())

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
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


async def test_report_never_claims_a_live_provider_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, provider=_provider())

    report = await module.provider_status_report()

    notes = " ".join(cast(tuple[str, ...], report["notes"]))
    assert "does not prove live provider dispatch" in notes


async def test_exit_code_is_nonzero_when_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, provider=_provider(), capabilities=())

    assert await module.run_provider_status(json_output=True) == 20


async def test_exit_code_is_zero_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, provider=_provider())

    assert await module.run_provider_status(json_output=True) == 0
