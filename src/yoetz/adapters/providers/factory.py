"""The one external-provider factory dispatch table ready composition installs.

Every configurable endpoint profile resolves here to exactly one runtime factory. A profile the
setup surface can write but this table cannot build is the failure this module exists to prevent:
the gateway reports `factory_unavailable` and the agent's semantic review silently never runs.

Responses-style profiles are built by the Responses factory and Chat Completions profiles by the
Chat Completions factory, so neither adapter learns the other's protocol. Data-use facts are per
profile and stay `unknown` where no reviewed record exists, which keeps those bindings out of the
assisted-eligible path (ADR-006 decision 14).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from yoetz.adapters.privacy.gateway import ExternalProviderFactory
from yoetz.adapters.providers.data_use_catalog import data_use_record_for_endpoint
from yoetz.adapters.providers.openai_chat_completions import (
    ChatCompletionsEvaluator,
    ChatCompletionsProfile,
    RenderedChatCompletionsRequest,
    StructuredOutputEnforcement,
    render_case,
)
from yoetz.adapters.providers.openai_responses import OneAttemptCredentialTransport
from yoetz.adapters.providers.openai_responses_factory import (
    OpenAIResponsesExternalFactory,
    openai_profile_from_provider_config,
    provider_binding_from_config,
)
from yoetz.adapters.providers.openai_responses_factory import (
    external_factory_builders_from_config as responses_factory_builders_from_config,
)
from yoetz.config.models import ProviderProfileConfig
from yoetz.domain.privacy import ApprovedOutboundCase, ProviderBinding
from yoetz.ports.clock import ClockPort
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import SemanticEvaluatorPort

__all__ = [
    "CHAT_COMPLETIONS_ENDPOINT_PROFILES",
    "RESPONSES_ENDPOINT_PROFILE_IDS",
    "ChatCompletionsEndpointFacts",
    "ChatCompletionsExternalFactory",
    # Re-exported so one import site answers "which factory does this profile ID select?".
    "OpenAIResponsesExternalFactory",
    "chat_completions_profile_from_provider_config",
    "external_factory_builders_from_config",
    "openai_profile_from_provider_config",
]


@dataclass(frozen=True, slots=True)
class ChatCompletionsEndpointFacts:
    """The exact nonsecret endpoint facts one bundled Chat Completions profile ID stands for."""

    host: str
    base_path_prefix: str
    structured_output_enforcement: StructuredOutputEnforcement


# Hosts publishing an OpenAI-compatible Chat Completions surface. `structured_output_enforcement`
# records what each vendor documents about `response_format`: Anthropic's compatibility layer
# documents ignoring it, so that profile carries the judgment shape in the instruction instead and
# an off-shape answer degrades to an honest invalid result. All three are E-007 capability claims
# until a live smoke run records evidence.
CHAT_COMPLETIONS_ENDPOINT_PROFILES: Final[Mapping[str, ChatCompletionsEndpointFacts]] = (
    MappingProxyType(
        {
            "anthropic-openai-chat-completions": ChatCompletionsEndpointFacts(
                "api.anthropic.com", "/v1", "prompt_only"
            ),
            "google-gemini-openai-chat-completions": ChatCompletionsEndpointFacts(
                "generativelanguage.googleapis.com", "/v1beta/openai", "provider_enforced"
            ),
            "openrouter-openai-chat-completions": ChatCompletionsEndpointFacts(
                "openrouter.ai", "/api/v1", "provider_enforced"
            ),
            "xai-openai-chat-completions": ChatCompletionsEndpointFacts(
                "api.x.ai", "/v1", "provider_enforced"
            ),
        }
    )
)

# Profiles the Responses factory builds. Vercel's AI Gateway is an OpenAI Responses surface on a
# different host, so it needs no adapter of its own.
RESPONSES_ENDPOINT_PROFILE_IDS: Final = frozenset(
    {
        "openai-responses",
        "fireworks-responses",
        "owner-declared-openai-responses",
        "vercel-ai-gateway-openai-responses",
    }
)


def chat_completions_profile_from_provider_config(
    provider: ProviderProfileConfig, *, now: datetime
) -> ChatCompletionsProfile:
    """Build the exact nonsecret ChatCompletionsProfile for an allowed config binding."""

    facts = CHAT_COMPLETIONS_ENDPOINT_PROFILES.get(provider.endpoint_profile_id)
    if facts is None:
        raise ValueError("provider_endpoint_profile_unsupported")
    return ChatCompletionsProfile(
        provider_id=provider.provider_id,
        model=provider.model,
        endpoint_profile_id=provider.endpoint_profile_id,
        endpoint_profile_version=provider.endpoint_profile_version,
        timeout_seconds=provider.timeout_seconds,
        structured_output_enforcement=facts.structured_output_enforcement,
        data_use_profile=data_use_record_for_endpoint(provider.endpoint_profile_id).profile,
        host=facts.host,
        base_path_prefix=facts.base_path_prefix,
    )


@dataclass
class ChatCompletionsExternalFactory:
    """Render + one-attempt evaluator factory installed by ready composition."""

    profile: ChatCompletionsProfile
    clock: ClockPort

    def __post_init__(self) -> None:
        self._last_rendered: RenderedChatCompletionsRequest | None = None

    def render(self, case: ApprovedOutboundCase) -> bytes:
        rendered = render_case(case, self.profile)
        self._last_rendered = rendered
        return rendered.body

    def build_evaluator(
        self,
        binding: ProviderAttemptAuthBinding,
        credential: ProviderCredentialHandle,
        request_commitment: object,
    ) -> SemanticEvaluatorPort:
        del request_commitment
        rendered = self._last_rendered
        if rendered is None or binding.request_body_digest != rendered.body_sha256:
            raise ValueError("chat_completions_factory_render_required")
        transport = OneAttemptCredentialTransport(
            rendered=rendered,
            credential=credential,
            binding=binding,
            host=self.profile.host,
            port=self.profile.port,
            path=self.profile.path,
        )
        return ChatCompletionsEvaluator(self.profile, transport, self.clock)


def external_factory_builders_from_config(
    provider: ProviderProfileConfig | None, *, clock: ClockPort
) -> dict[ProviderBinding, object]:
    """Return the one runtime factory builder for a configured provider, or nothing."""

    if provider is None:
        return {}
    if provider.endpoint_profile_id in RESPONSES_ENDPOINT_PROFILE_IDS:
        return responses_factory_builders_from_config(provider, clock=clock)
    if provider.endpoint_profile_id not in CHAT_COMPLETIONS_ENDPOINT_PROFILES:
        return {}
    binding = provider_binding_from_config(provider)
    now = clock.now_utc()

    def _builder() -> ExternalProviderFactory:
        profile = chat_completions_profile_from_provider_config(provider, now=now)
        return ChatCompletionsExternalFactory(profile, clock)

    return {binding: _builder}
