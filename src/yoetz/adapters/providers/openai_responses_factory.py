"""Credential-free ExternalProviderFactory for OpenAI Responses (and compatible hosts)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from yoetz.adapters.providers.openai_responses import (
    OneAttemptCredentialTransport,
    OpenAIProfile,
    OpenAIResponsesEvaluator,
    RenderedOpenAIRequest,
    owner_declared_data_use_profile,
    render_case,
)
from yoetz.config.models import (
    OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID,
    OWNER_DECLARED_ENDPOINT_PROFILE_ID,
    ProviderProfileConfig,
    parse_https_origin,
)
from yoetz.domain.privacy import ApprovedOutboundCase, ProviderBinding, ProviderDataUseProfile
from yoetz.ports.clock import ClockPort
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import SemanticEvaluatorPort
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "OpenAIResponsesExternalFactory",
    "external_factory_builders_from_config",
    "openai_profile_from_provider_config",
    "provider_binding_from_config",
]


def provider_binding_from_config(provider: ProviderProfileConfig) -> ProviderBinding:
    return ProviderBinding(
        provider.provider_id,
        provider.model,
        provider.endpoint_profile_id,
        provider.endpoint_profile_version,
        "external",
    )


def _official_data_use(now: datetime) -> ProviderDataUseProfile:
    return ProviderDataUseProfile(
        data_use_profile_id="openai-api-data-use",
        data_use_profile_version="1.0.0",
        customer_content_training="prohibited",
        retention="bounded",
        retention_days_ceiling=30,
        provider_human_access="restricted",
        reviewed_at=now,
        expires_at=now + timedelta(days=365),
        evidence_digest=canonical_digest(
            {"profile": "openai-api-data-use", "schema": "yoetz.provider-data-use/1"}
        ),
    )


def openai_profile_from_provider_config(
    provider: ProviderProfileConfig, *, now: datetime
) -> OpenAIProfile:
    """Build the exact nonsecret OpenAIProfile for an allowed config binding."""

    timeout = provider.timeout_seconds
    if provider.endpoint_profile_id == OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID:
        return OpenAIProfile(
            provider_id=provider.provider_id,
            model=provider.model,
            endpoint_profile_id=provider.endpoint_profile_id,
            endpoint_profile_version=provider.endpoint_profile_version,
            timeout_seconds=timeout,
            supports_structured_outputs=True,
            data_use_profile=_official_data_use(now),
        )
    if provider.endpoint_profile_id == "fireworks-responses":
        return OpenAIProfile(
            provider_id=provider.provider_id,
            model=provider.model,
            endpoint_profile_id=provider.endpoint_profile_id,
            endpoint_profile_version=provider.endpoint_profile_version,
            timeout_seconds=timeout,
            supports_structured_outputs=True,
            data_use_profile=owner_declared_data_use_profile(
                reviewed_at=now,
                expires_at=now + timedelta(days=30),
                evidence_digest=canonical_digest(
                    {"profile": "fireworks-responses", "schema": "yoetz.provider-data-use/1"}
                ),
            ),
            host="api.fireworks.ai",
            base_path_prefix="/inference/v1",
        )
    if provider.endpoint_profile_id == OWNER_DECLARED_ENDPOINT_PROFILE_ID:
        if provider.owner_declared_endpoint is None:
            raise ValueError("owner_declared_endpoint_required")
        host, port = parse_https_origin(provider.owner_declared_endpoint.https_origin)
        return OpenAIProfile(
            provider_id=provider.provider_id,
            model=provider.model,
            endpoint_profile_id=provider.endpoint_profile_id,
            endpoint_profile_version=provider.endpoint_profile_version,
            timeout_seconds=timeout,
            supports_structured_outputs=True,
            data_use_profile=owner_declared_data_use_profile(
                reviewed_at=now,
                expires_at=now + timedelta(days=30),
                evidence_digest=canonical_digest(
                    {
                        "host": host,
                        "port": port,
                        "profile": "owner-declared-openai-responses",
                        "schema": "yoetz.provider-data-use/1",
                    }
                ),
            ),
            host=host,
            port=port,
            base_path_prefix="/v1",
        )
    raise ValueError("provider_endpoint_profile_unsupported")


@dataclass
class OpenAIResponsesExternalFactory:
    """Render + one-attempt evaluator factory installed by ready composition."""

    profile: OpenAIProfile
    clock: ClockPort

    def __post_init__(self) -> None:
        self._last_rendered: RenderedOpenAIRequest | None = None

    def render(self, case: ApprovedOutboundCase) -> bytes:
        rendered = render_case(case)
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
            raise ValueError("openai_factory_render_required")
        transport = OneAttemptCredentialTransport(
            rendered=rendered,
            credential=credential,
            binding=binding,
            host=self.profile.host,
            port=self.profile.port,
            path=self.profile.path,
        )
        return OpenAIResponsesEvaluator(self.profile, transport, self.clock)


def external_factory_builders_from_config(
    provider: ProviderProfileConfig | None, *, clock: ClockPort
) -> dict[ProviderBinding, object]:
    """Dispatch to the full factory table (Responses + Chat Completions + Vercel).

    Prefer importing from :mod:`yoetz.adapters.providers.factory`. This wrapper remains so
    existing call sites and tests keep working.
    """

    from yoetz.adapters.providers.factory import (
        external_factory_builders_from_config as _dispatch,
    )

    return _dispatch(provider, clock=clock)
