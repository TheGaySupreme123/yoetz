"""Factory dispatch selects the correct adapter per endpoint profile ID."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from yoetz.adapters.privacy.gateway import ExternalProviderFactory
from yoetz.adapters.providers.factory import (
    CHAT_COMPLETIONS_PROFILE_IDS,
    RESPONSES_PROFILE_IDS,
    external_factory_builders_from_config,
)
from yoetz.adapters.providers.openai_chat_completions import ChatCompletionsExternalFactory
from yoetz.adapters.providers.openai_responses_factory import OpenAIResponsesExternalFactory
from yoetz.config.models import ProviderProfileConfig
from yoetz.config.write import (
    anthropic_provider,
    fireworks_provider,
    google_gemini_provider,
    official_openai_provider,
    openrouter_provider,
    owner_declared_openai_provider,
    vercel_ai_gateway_provider,
)


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 7, 24, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 0.0


def _builder(provider: ProviderProfileConfig) -> Callable[[], ExternalProviderFactory]:
    builders = external_factory_builders_from_config(provider, clock=_Clock())
    assert len(builders) == 1
    return cast(Callable[[], ExternalProviderFactory], next(iter(builders.values())))


def _assert_factory(provider: ProviderProfileConfig, factory_type: type) -> None:
    factory = _builder(provider)()
    assert type(factory) is factory_type
    if factory_type is ChatCompletionsExternalFactory:
        chat = cast(ChatCompletionsExternalFactory, factory)
        assert chat.profile.path.endswith("/chat/completions")
        assert chat.profile.endpoint_profile_id in CHAT_COMPLETIONS_PROFILE_IDS
    else:
        responses = cast(OpenAIResponsesExternalFactory, factory)
        profile = responses.profile
        assert profile.path.endswith("/responses")
        assert profile.endpoint_profile_id in RESPONSES_PROFILE_IDS


def test_responses_profiles_select_responses_factory() -> None:
    _assert_factory(official_openai_provider(model="gpt-4.1-mini"), OpenAIResponsesExternalFactory)
    _assert_factory(
        fireworks_provider(model="accounts/fireworks/models/qwen3-235b-a22b"),
        OpenAIResponsesExternalFactory,
    )
    _assert_factory(
        owner_declared_openai_provider(model="local-model", https_origin="https://example.test"),
        OpenAIResponsesExternalFactory,
    )
    _assert_factory(
        vercel_ai_gateway_provider(model="anthropic/claude-sonnet-4-6"),
        OpenAIResponsesExternalFactory,
    )
    vercel = vercel_ai_gateway_provider(model="anthropic/claude-sonnet-4-6")
    factory = cast(OpenAIResponsesExternalFactory, _builder(vercel)())
    profile = factory.profile
    assert profile.host == "ai-gateway.vercel.sh"
    assert profile.path == "/v1/responses"


def test_chat_completions_profiles_select_chat_factory() -> None:
    _assert_factory(anthropic_provider(model="claude-sonnet-4-6"), ChatCompletionsExternalFactory)
    _assert_factory(
        google_gemini_provider(model="gemini-3.5-flash"), ChatCompletionsExternalFactory
    )
    _assert_factory(openrouter_provider(model="openai/gpt-5.2"), ChatCompletionsExternalFactory)

    anthropic = anthropic_provider(model="claude-sonnet-4-6")
    factory = cast(ChatCompletionsExternalFactory, _builder(anthropic)())
    profile = factory.profile
    assert profile.host == "api.anthropic.com"
    assert profile.path == "/v1/chat/completions"
    assert profile.base_path_prefix == "/v1"

    gemini = google_gemini_provider(model="gemini-3.5-flash")
    factory = cast(ChatCompletionsExternalFactory, _builder(gemini)())
    profile = factory.profile
    assert profile.host == "generativelanguage.googleapis.com"
    assert profile.path == "/v1beta/openai/chat/completions"

    openrouter = openrouter_provider(model="openai/gpt-5.2")
    factory = cast(ChatCompletionsExternalFactory, _builder(openrouter)())
    profile = factory.profile
    assert profile.host == "openrouter.ai"
    assert profile.path == "/api/v1/chat/completions"


def test_unknown_profile_yields_empty_builders() -> None:
    provider = ProviderProfileConfig(
        provider_id="unknown",
        endpoint_profile_id="not-a-real-profile",
        endpoint_profile_version="1.0.0",
        model="x",
        capability_profile="x",
        timeout_seconds=60,
        max_retries=0,
    )
    assert external_factory_builders_from_config(provider, clock=_Clock()) == {}
    assert external_factory_builders_from_config(None, clock=_Clock()) == {}
