"""The interface reports the agent route as its own verdict, never folded into readiness."""

from __future__ import annotations

from yoetz.tui.models import LayerState, PrivacyPosture, ProviderPosture
from yoetz.tui.runtime import YoetzRuntime


def _provider(**overrides: object) -> ProviderPosture:
    values: dict[str, object] = {
        "endpoint_bound": True,
        "provider_id": "openai",
        "model": "gpt-5",
        "endpoint_profile_id": "openai-responses",
        "credential_connected": True,
        "llm_inference_enabled": True,
        "semantic_enabled": True,
        "semantic_ready": True,
        "readiness_determinable": True,
    }
    values.update(overrides)
    return ProviderPosture(**values)  # type: ignore[arg-type]


_PRIVACY = PrivacyPosture(profile="assisted_review", llm_inference_enabled=True, readable=True)


def _layer(provider: ProviderPosture) -> LayerState:
    runtime = YoetzRuntime()
    layers = runtime._provider_layers(provider, _PRIVACY)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return next(layer for layer in layers if layer.key == "agent_route_review_ready").state


def _detail(provider: ProviderPosture) -> str:
    runtime = YoetzRuntime()
    layers = runtime._provider_layers(provider, _PRIVACY)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return next(layer for layer in layers if layer.key == "agent_route_review_ready").detail


def test_a_strict_registration_is_reported_without_calling_the_install_unready() -> None:
    """The installation stays ready; only the agent route is not.

    Collapsing the two would tell an operator with a deliberate strict registration that their
    installation is broken, and would contradict ADR-018: the route ceiling is process-local.
    """

    provider = _provider(agent_route_semantic_ready=False, registered_route_profile="strict")

    runtime = YoetzRuntime()
    layers = runtime._provider_layers(provider, _PRIVACY)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    by_key = {layer.key: layer.state for layer in layers}

    assert by_key["semantic_review_ready"] is LayerState.VERIFIED
    assert by_key["agent_route_review_ready"] is LayerState.NOT_CONFIGURED
    assert "yoetz integrate codex mcp preview" in _detail(provider)


def test_a_policy_registration_reports_the_route_as_ready() -> None:
    provider = _provider(agent_route_semantic_ready=True, registered_route_profile="policy")
    assert _layer(provider) is LayerState.VERIFIED
    assert _detail(provider) == ""


def test_an_unread_registration_is_unknown_rather_than_a_blocker() -> None:
    """Absent evidence is not evidence of a strict route."""

    provider = _provider(agent_route_semantic_ready=None, registered_route_profile=None)
    assert _layer(provider) is LayerState.UNKNOWN
    assert "could not be read" in _detail(provider)


def _admission_layer(provider: ProviderPosture) -> tuple[LayerState, str]:
    runtime = YoetzRuntime()
    layers = runtime._provider_layers(provider, _PRIVACY)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    layer = next(layer for layer in layers if layer.key == "host_admission")
    return layer.state, layer.detail


def test_host_admission_is_its_own_layer_and_names_each_host() -> None:
    """Issue #467: a ready installation with no admission is where auto-review hosts hold checks."""

    ready = _provider(agent_route_semantic_ready=True, registered_route_profile="policy")
    state, detail = _admission_layer(ready)
    assert state is LayerState.UNKNOWN
    assert "could not be read" in detail

    present = _provider(
        host_admission=(("claude", "present"), ("codex", "absent"), ("cursor", "absent"))
    )
    state, detail = _admission_layer(present)
    assert state is LayerState.VERIFIED
    assert detail == "claude present, codex absent, cursor absent"

    absent = _provider(
        host_admission=(("claude", "absent"), ("codex", "absent"), ("cursor", "absent"))
    )
    state, detail = _admission_layer(absent)
    assert state is LayerState.NOT_CONFIGURED
    assert "yoetz integrate <host> admission preview" in detail

    unreadable = _provider(host_admission=(("claude", "unknown"), ("codex", "absent")))
    assert _admission_layer(unreadable)[0] is LayerState.UNKNOWN

    drifted = _provider(
        host_admission=(("claude", "present"), ("codex", "absent"), ("cursor", "absent")),
        blockers=(("host_admission_drift", "present"),),
    )
    state, detail = _admission_layer(drifted)
    assert state is LayerState.NOT_CONFIGURED
    assert "revoke" in detail
