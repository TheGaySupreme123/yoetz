"""Versioned, package-owned provider data-use evidence.

The catalog is deliberately static. A daemon start may determine whether a record has
expired, but it must never create a newer review date or extend an evidence lifetime.
Each entry is bound to one endpoint profile; gateways and owner-declared endpoints never
inherit another provider's posture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Literal

from yoetz.domain.privacy import ProviderDataUseProfile
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "ProviderDataUseRecord",
    "data_use_record_for_endpoint",
    "endpoint_profile_data_use_recommendation_eligible",
]


@dataclass(frozen=True, slots=True)
class ProviderDataUseRecord:
    """One reviewed, route-specific provider data-use record."""

    endpoint_profile_id: str
    route_qualifier: str
    official_source_urls: tuple[str, ...]
    caveats: tuple[str, ...]
    profile: ProviderDataUseProfile


_REVIEWED_AT: Final = datetime(2026, 8, 4, tzinfo=UTC)
_EXPIRES_AT: Final = datetime(2026, 11, 4, tzinfo=UTC)


def _profile(
    profile_id: str,
    *,
    training: Literal["prohibited", "permitted", "unknown"],
    retention: Literal["none", "bounded", "unbounded", "unknown"],
    retention_days: int | None,
    human_access: Literal["prohibited", "restricted", "permitted", "unknown"],
    sources: tuple[str, ...],
    route_qualifier: str,
    caveats: tuple[str, ...],
) -> ProviderDataUseProfile:
    return ProviderDataUseProfile(
        data_use_profile_id=profile_id,
        data_use_profile_version="2026.08.04",
        customer_content_training=training,
        retention=retention,
        retention_days_ceiling=retention_days,
        provider_human_access=human_access,
        reviewed_at=_REVIEWED_AT,
        expires_at=_EXPIRES_AT,
        evidence_digest=canonical_digest(
            {
                "profile": profile_id,
                "reviewed_at": _REVIEWED_AT.isoformat(),
                "expires_at": _EXPIRES_AT.isoformat(),
                "customer_content_training": training,
                "retention": retention,
                "retention_days_ceiling": retention_days,
                "provider_human_access": human_access,
                "route_qualifier": route_qualifier,
                "caveats": list(caveats),
                "sources": list(sources),
            }
        ),
    )


_OPENAI_SOURCES: Final = (
    "https://platform.openai.com/docs/models/default-usage-policies-by-endpoint",
    "https://help.openai.com/en/articles/5722486-api-data-usage-policies",
)
_ANTHROPIC_SOURCES: Final = (
    "https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training",
    "https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data",
)
_FIREWORKS_SOURCES: Final = ("https://docs.fireworks.ai/guides/security_compliance/data_handling",)
_XAI_SOURCES: Final = ("https://docs.x.ai/developers/faq/security",)
_GEMINI_SOURCES: Final = (
    "https://ai.google.dev/gemini-api/terms",
    "https://ai.google.dev/gemini-api/docs/zdr",
)
_OPENROUTER_SOURCES: Final = (
    "https://openrouter.ai/docs/guides/privacy/data-collection",
    "https://openrouter.ai/docs/guides/features/zdr",
)
_VERCEL_SOURCES: Final = (
    "https://vercel.com/docs/ai-gateway/models-and-providers/provider-options",
    "https://vercel.com/docs/ai-gateway/byok",
)

_CATALOG: Final = MappingProxyType(
    {
        "openai-responses": ProviderDataUseRecord(
            "openai-responses",
            "OpenAI API Responses endpoint with renderer-fixed store=false; account-level data "
            "controls are outside this binding.",
            _OPENAI_SOURCES,
            (
                "Abuse-monitoring, legal, safety, and support exceptions may permit retained data "
                "or restricted authorized access.",
                "The renderer sets store=false; organization opt-ins and separately stored API "
                "features are outside this route claim.",
            ),
            _profile(
                "openai-api-responses",
                training="prohibited",
                retention="bounded",
                retention_days=30,
                human_access="restricted",
                sources=_OPENAI_SOURCES,
                route_qualifier="OpenAI API Responses endpoint with renderer-fixed store=false; "
                "account-level data controls are outside this binding.",
                caveats=(
                    "Abuse-monitoring, legal, safety, and support exceptions may permit retained "
                    "data or restricted authorized access.",
                    "Organization opt-ins and separately stored API features are outside this "
                    "route claim.",
                ),
            ),
        ),
        "anthropic-openai-chat-completions": ProviderDataUseRecord(
            "anthropic-openai-chat-completions",
            "Anthropic commercial API compatibility endpoint; consumer and opted-in routes excluded.",
            _ANTHROPIC_SOURCES,
            (
                "Abuse-policy, legal, safety, and support exceptions may permit longer retention "
                "or restricted authorized access.",
                "Consumer, feedback-opt-in, and third-party cloud routes are excluded.",
            ),
            _profile(
                "anthropic-commercial-api",
                training="prohibited",
                retention="bounded",
                retention_days=30,
                human_access="restricted",
                sources=_ANTHROPIC_SOURCES,
                route_qualifier="Anthropic commercial API compatibility endpoint; consumer and "
                "opted-in routes excluded.",
                caveats=(
                    "Abuse-policy, legal, safety, and support exceptions may permit longer "
                    "retention or restricted authorized access.",
                    "Consumer, feedback-opt-in, and third-party cloud routes are excluded.",
                ),
            ),
        ),
        "fireworks-responses": ProviderDataUseRecord(
            "fireworks-responses",
            "Fireworks Responses API only, with the renderer-fixed store=false opt-out.",
            _FIREWORKS_SOURCES,
            (
                "Operational metadata is retained; safety, legal, and support handling may still "
                "involve restricted authorized access.",
                "Opt-in logging, training products, and other stored features are excluded.",
            ),
            _profile(
                "fireworks-responses-store-false",
                training="prohibited",
                retention="none",
                retention_days=None,
                human_access="restricted",
                sources=_FIREWORKS_SOURCES,
                route_qualifier="Fireworks Responses API only, with the renderer-fixed store=false "
                "opt-out.",
                caveats=(
                    "Operational metadata is retained; safety, legal, and support handling may "
                    "still involve restricted authorized access.",
                    "Opt-in logging, training products, and other stored features are excluded.",
                ),
            ),
        ),
        "xai-openai-chat-completions": ProviderDataUseRecord(
            "xai-openai-chat-completions",
            "Direct xAI API compatibility endpoint; ordinary 30-day abuse-audit retention, "
            "without a team-level ZDR claim.",
            _XAI_SOURCES,
            (
                "The 30-day window is for abuse/misuse auditing; legal and safety exceptions may "
                "apply and authorized access is restricted rather than impossible.",
                "Files, collections, stateful features, consumer Grok, and team ZDR are excluded.",
            ),
            _profile(
                "xai-direct-api-default",
                training="prohibited",
                retention="bounded",
                retention_days=30,
                human_access="restricted",
                sources=_XAI_SOURCES,
                route_qualifier="Direct xAI API compatibility endpoint; ordinary 30-day "
                "abuse-audit retention, without a team-level ZDR claim.",
                caveats=(
                    "Legal and safety exceptions may apply and authorized access is restricted "
                    "rather than impossible.",
                    "Files, collections, stateful features, consumer Grok, and team ZDR are "
                    "excluded.",
                ),
            ),
        ),
        "google-gemini-openai-chat-completions": ProviderDataUseRecord(
            "google-gemini-openai-chat-completions",
            "Gemini Developer API compatibility endpoint; this binding does not establish "
            "paid-service billing status, region-specific terms, or ZDR eligibility.",
            _GEMINI_SOURCES,
            (
                "Paid and unpaid Gemini Developer API use have materially different training and "
                "retention terms.",
                "Possession of an API key does not prove an active paid Cloud Billing project.",
            ),
            _profile(
                "gemini-developer-api-tier-unverified",
                training="unknown",
                retention="unknown",
                retention_days=None,
                human_access="unknown",
                sources=_GEMINI_SOURCES,
                route_qualifier="Gemini Developer API compatibility endpoint; this binding does "
                "not establish paid-service billing status, region-specific terms, or ZDR eligibility.",
                caveats=(
                    "Paid and unpaid use have materially different training and retention terms.",
                    "Possession of an API key does not prove an active paid Cloud Billing project.",
                ),
            ),
        ),
        "openrouter-openai-chat-completions": ProviderDataUseRecord(
            "openrouter-openai-chat-completions",
            "OpenRouter gateway route; the binding does not constrain the downstream provider, "
            "fallback set, endpoint-level ZDR posture, or account logging settings.",
            _OPENROUTER_SOURCES,
            (
                "Gateway defaults and account logging settings do not establish downstream "
                "provider behavior.",
                "Standing authority is unavailable until downstream/fallback constraints and "
                "actual-route receipts are represented.",
            ),
            _profile(
                "openrouter-downstream-unconstrained",
                training="unknown",
                retention="unknown",
                retention_days=None,
                human_access="unknown",
                sources=_OPENROUTER_SOURCES,
                route_qualifier="OpenRouter gateway route; the binding does not constrain the "
                "downstream provider, fallback set, endpoint-level ZDR posture, or account settings.",
                caveats=(
                    "Gateway defaults do not establish downstream provider behavior.",
                    "Standing authority is unavailable until downstream/fallback constraints and "
                    "actual-route receipts are represented.",
                ),
            ),
        ),
        "vercel-ai-gateway-openai-responses": ProviderDataUseRecord(
            "vercel-ai-gateway-openai-responses",
            "Vercel AI Gateway route; the binding does not constrain the downstream provider or "
            "fallback set and cannot inherit a gateway-level posture as provider assurance.",
            _VERCEL_SOURCES,
            (
                "Gateway-layer deletion does not establish downstream provider retention or training.",
                "Standing authority is unavailable until downstream/fallback constraints and "
                "actual-route receipts are represented.",
            ),
            _profile(
                "vercel-ai-gateway-downstream-unconstrained",
                training="unknown",
                retention="unknown",
                retention_days=None,
                human_access="unknown",
                sources=_VERCEL_SOURCES,
                route_qualifier="Vercel AI Gateway route; the binding does not constrain the "
                "downstream provider or fallback set.",
                caveats=(
                    "Gateway-layer deletion does not establish downstream provider retention or training.",
                    "Standing authority is unavailable until downstream/fallback constraints and "
                    "actual-route receipts are represented.",
                ),
            ),
        ),
        "owner-declared-openai-responses": ProviderDataUseRecord(
            "owner-declared-openai-responses",
            "Owner-declared Responses-compatible endpoint; host ownership and downstream data-use "
            "posture are not established by this endpoint profile.",
            (),
            ("No packaged provider-policy evidence exists for this owner-declared route.",),
            _profile(
                "owner-declared-route-unreviewed",
                training="unknown",
                retention="unknown",
                retention_days=None,
                human_access="unknown",
                sources=(),
                route_qualifier="Owner-declared Responses-compatible endpoint; host ownership and "
                "downstream data-use posture are not established.",
                caveats=("No packaged provider-policy evidence exists for this route.",),
            ),
        ),
    }
)

_UNKNOWN = ProviderDataUseRecord(
    "unknown",
    "No reviewed exact-route/account-tier evidence is packaged for this endpoint profile.",
    (),
    ("No packaged exact-route evidence exists for this endpoint profile.",),
    _profile(
        "unreviewed-provider-route",
        training="unknown",
        retention="unknown",
        retention_days=None,
        human_access="unknown",
        sources=(),
        route_qualifier="No reviewed exact-route/account-tier evidence is packaged for this endpoint profile.",
        caveats=("No packaged exact-route evidence exists for this endpoint profile.",),
    ),
)


def data_use_record_for_endpoint(endpoint_profile_id: str) -> ProviderDataUseRecord:
    """Return one exact-profile record, never a host-level fallback."""

    return _CATALOG.get(endpoint_profile_id, _UNKNOWN)


def endpoint_profile_data_use_recommendation_eligible(
    endpoint_profile_id: str, *, now: datetime
) -> bool:
    """Whether current, route-specific evidence supports Assisted as the recommendation."""

    current = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    return data_use_record_for_endpoint(endpoint_profile_id).profile.recommendation_eligible(
        current
    )
