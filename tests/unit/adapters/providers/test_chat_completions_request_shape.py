"""Chat Completions request shape, capability handling, and honest response classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest

from yoetz.adapters.providers.openai_chat_completions import (
    ChatCompletionsProfile,
    StructuredOutputEnforcement,
    classify_provider_failure,
    normalize_response,
    render_case,
)
from yoetz.adapters.providers.openai_responses import owner_declared_data_use_profile
from yoetz.domain.findings import SemanticFailureClass
from yoetz.domain.privacy import ApprovedOutboundCase, DataCategory, ProviderBinding
from yoetz.ports.semantic import (
    SemanticResultInvalid,
    SemanticResultRefused,
    SemanticResultSuccess,
    SemanticResultTimeout,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse

_NOW = datetime(2026, 7, 24, tzinfo=UTC)
_DIGEST = "sha256:" + "c" * 64
_CREDENTIAL = "sk-live-never-appears-anywhere"

_JUDGMENT = (
    '{"conclusion":"no_material_discrepancy","reviewer_challenges":[]}'  # exact judgment shape
)


def _profile(
    enforcement: StructuredOutputEnforcement = "provider_enforced",
    *,
    host: str = "openrouter.ai",
    prefix: str = "/api/v1",
) -> ChatCompletionsProfile:
    return ChatCompletionsProfile(
        provider_id="openrouter",
        model="openai/gpt-5.2",
        endpoint_profile_id="openrouter-openai-chat-completions",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        structured_output_enforcement=enforcement,
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=_NOW, expires_at=_NOW + timedelta(days=30), evidence_digest=_DIGEST
        ),
        host=host,
        base_path_prefix=prefix,
    )


def _case(payload: bytes | None = None) -> ApprovedOutboundCase:
    body = (
        payload
        if payload is not None
        else canonical_encode(
            cast(JsonValue, {"claims": [], "goal": "ship the adapter", "obligations": []})
        )
    )
    return ApprovedOutboundCase(
        case_id="cas_70000000-0000-4000-8000-000000000001",
        request_id="req_70000000-0000-4000-8000-000000000001",
        payload=body,
        media_type="application/json",
        schema_id="yoetz-semantic-case-1.0.0",
        included_item_ids=("goal-1",),
        approved_categories=(DataCategory.TASK_DESCRIPTION,),
        blocked_categories=(),
        byte_count=len(body),
        token_count=16,
        provider_binding=ProviderBinding(
            provider_id="openrouter",
            model_id="openai/gpt-5.2",
            endpoint_profile_id="openrouter-openai-chat-completions",
            endpoint_profile_version="1.0.0",
            transport="external",
        ),
        purpose="semantic-review",
        authorization_id="aut_70000000-0000-4000-8000-000000000001",
        policy_digest=_DIGEST,
        case_digest="sha256:" + "d" * 64,
    )


class _Message:
    def __init__(self, content: object = None, refusal: object = None) -> None:
        self.content = content
        self.refusal = refusal


class _Choice:
    def __init__(self, message: _Message, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, *choices: _Choice, response_id: str = "chatcmpl-1") -> None:
        self.choices = list(choices)
        self.id = response_id


def test_message_content_is_the_approved_payload_as_text() -> None:
    """Chat Completions content is text: a nested JSON object is rejected before any review."""

    case = _case()
    rendered = render_case(case, _profile())
    body = cast(dict[str, JsonValue], strict_json_parse(rendered.body))

    messages = cast(list[JsonValue], body["messages"])
    system, user = cast(dict[str, JsonValue], messages[0]), cast(dict[str, JsonValue], messages[1])
    assert system["role"] == "system" and type(system["content"]) is str
    assert user["role"] == "user"
    assert user["content"] == case.payload.decode("utf-8")
    # The approved bytes travel verbatim: the adapter selects, summarizes, and adds nothing.
    assert strict_json_parse(cast(str, user["content"]).encode("utf-8")) == strict_json_parse(
        case.payload
    )
    assert body["model"] == "openai/gpt-5.2"
    assert body["max_tokens"] == 2048


def test_response_format_follows_the_recorded_endpoint_capability() -> None:
    """A host documented to ignore response_format is not sent one; the shape goes in the prompt."""

    enforced = cast(dict[str, JsonValue], strict_json_parse(render_case(_case(), _profile()).body))
    prompt_only = cast(
        dict[str, JsonValue],
        strict_json_parse(render_case(_case(), _profile("prompt_only")).body),
    )

    response_format = cast(dict[str, JsonValue], enforced["response_format"])
    assert response_format["type"] == "json_schema"
    schema = cast(dict[str, JsonValue], response_format["json_schema"])
    assert schema["strict"] is True
    assert "response_format" not in prompt_only
    system = cast(dict[str, JsonValue], cast(list[JsonValue], prompt_only["messages"])[0])
    instruction = cast(str, system["content"])
    assert "one JSON object and nothing else" in instruction
    assert "reviewer_challenges" in instruction


def test_rendered_body_is_deterministic_and_digest_bound() -> None:
    first = render_case(_case(), _profile())
    second = render_case(_case(), _profile())

    assert first.body == second.body
    assert first.body_sha256 == second.body_sha256
    assert first.prompt_digest.startswith("sha256:")
    assert first.schema_digest.startswith("sha256:")
    # Nothing credential-shaped can reach the profile or the rendered request.
    assert _CREDENTIAL.encode("ascii") not in first.body
    assert _CREDENTIAL not in repr(_profile())


def test_prompt_only_and_enforced_bodies_differ_by_exactly_the_response_format() -> None:
    enforced = cast(dict[str, JsonValue], strict_json_parse(render_case(_case(), _profile()).body))
    prompt_only = cast(
        dict[str, JsonValue],
        strict_json_parse(render_case(_case(), _profile("prompt_only")).body),
    )

    del enforced["response_format"]
    assert enforced == prompt_only


def test_a_non_external_binding_cannot_reach_the_adapter_at_all() -> None:
    """The external request shape is unreachable for a local binding by construction."""

    with pytest.raises(ValueError, match="invalid_privacy_value"):
        ProviderBinding(
            provider_id="local",
            model_id="local-model",
            endpoint_profile_id="local-openai-compatible",
            endpoint_profile_version="1.0.0",
            transport=cast(Literal["external"], "local_model"),
        )
    with pytest.raises(TypeError, match="chat_completions_case_invalid"):
        render_case(cast(ApprovedOutboundCase, object()), _profile())
    with pytest.raises(TypeError, match="chat_completions_profile_invalid"):
        render_case(_case(), cast(ChatCompletionsProfile, object()))


def test_exact_judgment_shape_succeeds() -> None:
    response = _Response(_Choice(_Message(content=_JUDGMENT)))

    result = normalize_response(response, _profile(), latency_ms=12)

    assert type(result) is SemanticResultSuccess
    assert result.judgment.conclusion == "no_material_discrepancy"
    assert result.provenance.provider_request_id == "chatcmpl-1"


def test_prose_answer_degrades_to_invalid_rather_than_a_fabricated_pass() -> None:
    """A host that ignored the requested structure must never read as a clean review."""

    response = _Response(_Choice(_Message(content="Looks good to me! No issues found.")))

    result = normalize_response(response, _profile("prompt_only"), latency_ms=12)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_SCHEMA


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        (_Response(_Choice(_Message(refusal="I cannot help"))), SemanticResultRefused),
        (
            _Response(_Choice(_Message(content=_JUDGMENT), finish_reason="content_filter")),
            SemanticResultRefused,
        ),
        (
            _Response(_Choice(_Message(content='{"conclusion":'), finish_reason="length")),
            SemanticResultTimeout,
        ),
        (_Response(_Choice(_Message(content=""))), SemanticResultInvalid),
        (_Response(), SemanticResultInvalid),
    ),
)
def test_non_judgment_answers_map_to_their_exact_terminal(
    response: _Response, expected: type[object]
) -> None:
    assert type(normalize_response(response, _profile(), latency_ms=5)) is expected


def test_transport_and_status_failures_keep_their_public_class() -> None:
    import httpx

    timeout = classify_provider_failure(httpx.TimeoutException("x"), _profile(), latency_ms=1)
    transport = classify_provider_failure(httpx.ConnectError("x"), _profile(), latency_ms=1)

    assert type(timeout) is SemanticResultTimeout
    assert type(transport) is SemanticResultUnavailable
    assert transport.provenance.failure_class is SemanticFailureClass.TRANSPORT

    for status, failure_class in (
        (401, SemanticFailureClass.AUTHENTICATION),
        (403, SemanticFailureClass.AUTHORIZATION),
        (429, SemanticFailureClass.RATE_LIMITED),
        (404, SemanticFailureClass.UNSUPPORTED_PROFILE),
        (503, SemanticFailureClass.PROVIDER_OUTAGE),
    ):
        error = RuntimeError("provider said no")
        error.status_code = status  # pyright: ignore[reportAttributeAccessIssue]
        result = classify_provider_failure(error, _profile(), latency_ms=1)
        assert type(result) is SemanticResultUnavailable
        assert result.provenance.failure_class is failure_class


def test_profile_rejects_an_unlisted_base_path_prefix() -> None:
    with pytest.raises(ValueError, match="chat_completions_profile_path_invalid"):
        _profile(prefix="/inference/v1")
