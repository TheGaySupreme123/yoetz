"""Full external provider factory dispatch table (Responses + Chat Completions).

Keeps :mod:`openai_responses_factory` Responses-only while mapping every owner-selectable
endpoint profile ID that ready composition may install. Unknown IDs yield an empty builder map
(fail closed to ``factory_unavailable`` at the gateway).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from yoetz.adapters.privacy.gateway import ExternalProviderFactory
from yoetz.adapters.providers.openai_chat_completions import (
    ChatCompletionsExternalFactory,
    chat_completions_profile_from_binding,
)
from yoetz.adapters.providers.openai_responses import (
    OpenAIProfile,
    owner_declared_data_use_profile,
)
from yoetz.adapters.providers.openai_responses_factory import (
    OpenAIResponsesExternalFactory,
    openai_profile_from_provider_config,
    provider_binding_from_config,
)
from yoetz.config.models import (
    OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID,
    OWNER_DECLARED_ENDPOINT_PROFILE_ID,
    ProviderProfileConfig,
)
from yoetz.domain.privacy import ProviderBinding
from yoetz.ports.clock import ClockPort
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "CHAT_COMPLETIONS_PROFILE_IDS",
    "RESPONSES_PROFILE_IDS",
    "SUPPORTED_ENDPOINT_PROFILE_IDS",
    "external_factory_builders_from_config",
]

RESPONSES_PROFILE_IDS: Final = frozenset(
    {
        OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID,
        "fireworks-responses",
        OWNER_DECLARED_ENDPOINT_PROFILE_ID,
        "vercel-ai-gateway-openai-responses",
    }
)

CHAT_COMPLETIONS_PROFILE_IDS: Final = frozenset(
    {
        "anthropic-openai-chat-completions",
        "google-gemini-openai-chat-completions",
        "openrouter-openai-chat-completions",
    }
)

SUPPORTED_ENDPOINT_PROFILE_IDS: Final = RESPONSES_PROFILE_IDS | CHAT_COMPLETIONS_PROFILE_IDS


def _vercel_profile(provider: ProviderProfileConfig, *, now: datetime) -> OpenAIProfile:
    return OpenAIProfile(
        provider_id=provider.provider_id,
        model=provider.model,
        endpoint_profile_id=provider.endpoint_profile_id,
        endpoint_profile_version=provider.endpoint_profile_version,
        timeout_seconds=provider.timeout_seconds,
        supports_structured_outputs=True,
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=now,
            expires_at=now + timedelta(days=30),
            evidence_digest=canonical_digest(
                {
                    "profile": provider.endpoint_profile_id,
                    "schema": "yoetz.provider-data-use/1",
                }
            ),
        ),
        host="ai-gateway.vercel.sh",
        base_path_prefix="/v1",
    )


def external_factory_builders_from_config(
    provider: ProviderProfileConfig | None, *, clock: ClockPort
) -> dict[ProviderBinding, object]:
    """Return builders for every supported endpoint profile ID; empty for unknown IDs."""

    if provider is None:
        return {}
    profile_id = provider.endpoint_profile_id
    if profile_id not in SUPPORTED_ENDPOINT_PROFILE_IDS:
        return {}

    binding = provider_binding_from_config(provider)
    now = clock.now_utc()

    if profile_id in CHAT_COMPLETIONS_PROFILE_IDS:

        def _chat_builder() -> ExternalProviderFactory:
            data_use = owner_declared_data_use_profile(
                reviewed_at=now,
                expires_at=now + timedelta(days=30),
                evidence_digest=canonical_digest(
                    {
                        "profile": profile_id,
                        "schema": "yoetz.provider-data-use/1",
                    }
                ),
            )
            profile = chat_completions_profile_from_binding(
                provider_id=provider.provider_id,
                model=provider.model,
                endpoint_profile_id=profile_id,
                endpoint_profile_version=provider.endpoint_profile_version,
                timeout_seconds=provider.timeout_seconds,
                data_use_profile=data_use,
            )
            return ChatCompletionsExternalFactory(profile, clock)  # pyright: ignore[reportReturnType]

        return {binding: _chat_builder}

    def _responses_builder() -> ExternalProviderFactory:
        if profile_id == "vercel-ai-gateway-openai-responses":
            profile = _vercel_profile(provider, now=now)
        else:
            profile = openai_profile_from_provider_config(provider, now=now)
        return OpenAIResponsesExternalFactory(profile, clock)

    return {binding: _responses_builder}
