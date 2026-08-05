"""The Assisted recommendation must be grounded in fixed, route-specific evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from yoetz.adapters.providers.data_use_catalog import (
    data_use_record_for_endpoint,
    endpoint_profile_data_use_recommendation_eligible,
)


def test_openai_record_is_route_specific_and_has_fixed_evidence_lifetime() -> None:
    record = data_use_record_for_endpoint("openai-responses")

    assert record.route_qualifier.startswith("OpenAI API Responses")
    assert record.official_source_urls
    assert record.profile.reviewed_at == datetime(2026, 8, 4, tzinfo=UTC)
    assert record.profile.expires_at == datetime(2026, 11, 4, tzinfo=UTC)
    assert endpoint_profile_data_use_recommendation_eligible(
        "openai-responses", now=datetime(2026, 8, 4, tzinfo=UTC)
    )
    assert not endpoint_profile_data_use_recommendation_eligible(
        "openai-responses", now=datetime(2026, 11, 4, tzinfo=UTC)
    )


def test_unreviewed_route_never_inherits_another_provider_posture() -> None:
    record = data_use_record_for_endpoint("openrouter-openai-chat-completions")

    assert record.profile.customer_content_training == "unknown"
    assert not endpoint_profile_data_use_recommendation_eligible(
        "openrouter-openai-chat-completions", now=datetime(2026, 8, 4, tzinfo=UTC)
    )


def test_fireworks_store_false_and_xai_direct_routes_are_recommendation_eligible() -> None:
    now = datetime(2026, 8, 5, tzinfo=UTC)

    fireworks = data_use_record_for_endpoint("fireworks-responses")
    assert fireworks.profile.customer_content_training == "prohibited"
    assert fireworks.profile.retention == "none"
    assert "store=false" in fireworks.route_qualifier
    assert endpoint_profile_data_use_recommendation_eligible("fireworks-responses", now=now)

    xai = data_use_record_for_endpoint("xai-openai-chat-completions")
    assert xai.profile.retention_days_ceiling == 30
    assert endpoint_profile_data_use_recommendation_eligible(
        "xai-openai-chat-completions", now=now
    )


def test_all_exposed_ambiguous_routes_have_explicit_conservative_records() -> None:
    for endpoint_profile_id in (
        "google-gemini-openai-chat-completions",
        "openrouter-openai-chat-completions",
        "vercel-ai-gateway-openai-responses",
        "owner-declared-openai-responses",
    ):
        record = data_use_record_for_endpoint(endpoint_profile_id)
        assert record.endpoint_profile_id == endpoint_profile_id
        assert record.profile.data_use_profile_id != "unreviewed-provider-route"
        assert not endpoint_profile_data_use_recommendation_eligible(
            endpoint_profile_id, now=datetime(2026, 8, 5, tzinfo=UTC)
        )
