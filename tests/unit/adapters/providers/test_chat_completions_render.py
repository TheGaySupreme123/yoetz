"""Credential-safe chat-completions request shape goldens."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from yoetz.adapters.providers.openai_chat_completions import (
    chat_completions_profile_from_binding,
    render_chat_completions_case,
)
from yoetz.adapters.providers.openai_responses import owner_declared_data_use_profile
from yoetz.domain.privacy import ApprovedOutboundCase, DataCategory, ProviderBinding
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)


def _case(profile_id: str, model: str, provider_id: str) -> ApprovedOutboundCase:
    payload = canonical_encode({"goal": "test", "claims": []})
    return ApprovedOutboundCase(
        case_id="cas_70000000-0000-4000-8000-000000000001",
        request_id="req_70000000-0000-4000-8000-000000000001",
        payload=payload,
        media_type="application/json",
        schema_id="yoetz-semantic-case-1.0.0",
        included_item_ids=("goal-1",),
        approved_categories=(DataCategory.TASK_DESCRIPTION,),
        blocked_categories=(),
        byte_count=len(payload),
        token_count=16,
        provider_binding=ProviderBinding(
            provider_id=provider_id,
            model_id=model,
            endpoint_profile_id=profile_id,
            endpoint_profile_version="1.0.0",
            transport="external",
        ),
        purpose="semantic-review",
        authorization_id="aut_70000000-0000-4000-8000-000000000001",
        policy_digest="sha256:" + "b" * 64,
        case_digest="sha256:" + "d" * 64,
    )


def test_render_case_body_is_canonical_and_credential_free() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    data_use = owner_declared_data_use_profile(
        reviewed_at=now,
        expires_at=now + timedelta(days=30),
        evidence_digest=canonical_digest({"profile": "test"}),
    )
    profile = chat_completions_profile_from_binding(
        provider_id="anthropic",
        model="claude-sonnet-4-6",
        endpoint_profile_id="anthropic-openai-chat-completions",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        data_use_profile=data_use,
    )
    case = _case("anthropic-openai-chat-completions", "claude-sonnet-4-6", "anthropic")
    rendered = render_chat_completions_case(case)
    assert rendered.body_sha256 == "sha256:" + hashlib.sha256(rendered.body).hexdigest()
    body_value = strict_json_parse(rendered.body)
    assert isinstance(body_value, dict)
    body: dict[str, JsonValue] = body_value
    assert body["model"] == "claude-sonnet-4-6"
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    messages = body["messages"]
    assert isinstance(messages, list) and len(messages) == 2
    first = messages[0]
    second = messages[1]
    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["role"] == "system"
    assert second["role"] == "user"
    encoded = rendered.body.decode("ascii")
    assert "Authorization" not in encoded
    assert "api_key" not in encoded
    assert "Bearer" not in encoded
    assert "sk-" not in encoded
    assert profile.path == "/v1/chat/completions"
