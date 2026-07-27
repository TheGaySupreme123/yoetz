"""The pinned OpenAI SDK must preserve the audited Responses request body exactly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from yoetz.adapters.providers.openai_responses import (
    OneAttemptCredentialTransport,
    OpenAIProfile,
    OpenAIResponsesEvaluator,
    owner_declared_data_use_profile,
    render_case,
)
from yoetz.domain.privacy import ApprovedOutboundCase, DataCategory, ProviderBinding
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderAuthTransportCallback
from yoetz.ports.semantic import Deadline, SemanticResultSuccess
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse

_NOW = datetime(2026, 7, 25, tzinfo=UTC)
_DIGEST = "sha256:" + "c" * 64
_JUDGMENT = '{"conclusion":"no_material_discrepancy","reviewer_challenges":[]}'


class _CaptureTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.request_body: bytes | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_body = await request.aread()
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "resp_test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": _JUDGMENT}],
                    }
                ],
            },
        )


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 0.0


class _Credential:
    async def authorize_attempt[T](
        self,
        binding: ProviderAttemptAuthBinding,
        inject_and_start: ProviderAuthTransportCallback[T],
    ) -> T:
        del binding
        secret = bytearray(b"nonsecret-test-credential")
        view = memoryview(secret)
        try:
            return await inject_and_start.inject_and_start(view)
        finally:
            view.release()
            for index in range(len(secret)):
                secret[index] = 0


def _case() -> ApprovedOutboundCase:
    payload = canonical_encode({"schema": "yoetz.semantic-check-candidate/1"})
    return ApprovedOutboundCase(
        case_id="cas_60000000-0000-4000-8000-000000000001",
        request_id="req_60000000-0000-4000-8000-000000000001",
        payload=payload,
        media_type="application/json",
        schema_id="yoetz-semantic-case-1.0.0",
        included_item_ids=("case-packet",),
        approved_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        blocked_categories=(),
        byte_count=len(payload),
        token_count=8,
        provider_binding=ProviderBinding(
            "fireworks",
            "accounts/fireworks/models/minimax-m3",
            "fireworks-responses",
            "1.0.0",
            "external",
        ),
        purpose="semantic-review",
        authorization_id="aut_60000000-0000-4000-8000-000000000001",
        policy_digest=_DIGEST,
        case_digest="sha256:" + "d" * 64,
    )


@pytest.mark.anyio
async def test_pinned_sdk_preserves_the_rendered_responses_body() -> None:
    profile = OpenAIProfile(
        provider_id="fireworks",
        model="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        supports_structured_outputs=True,
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=_NOW,
            expires_at=_NOW + timedelta(days=30),
            evidence_digest=_DIGEST,
        ),
        host="api.fireworks.ai",
        base_path_prefix="/inference/v1",
    )
    case = _case()
    rendered = render_case(case)
    body = cast(dict[str, Any], strict_json_parse(rendered.body))
    import openai

    capture = _CaptureTransport()
    client = openai.AsyncOpenAI(
        api_key="nonsecret-test-sentinel",
        base_url=profile.base_url,
        http_client=httpx.AsyncClient(transport=capture),
    )
    try:
        await client.responses.create(**body)
    finally:
        await client.close()

    assert capture.request_body == rendered.body


@pytest.mark.anyio
async def test_evaluator_dispatches_the_audited_rendered_body_verbatim() -> None:
    profile = OpenAIProfile(
        provider_id="fireworks",
        model="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        supports_structured_outputs=True,
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=_NOW,
            expires_at=_NOW + timedelta(days=30),
            evidence_digest=_DIGEST,
        ),
        host="api.fireworks.ai",
        base_path_prefix="/inference/v1",
    )
    case = _case()
    rendered = render_case(case)
    binding = ProviderAttemptAuthBinding(
        provider_id="fireworks",
        model_id="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
        purpose="semantic-review",
        authorization_scope_digest=_DIGEST,
        purpose_digest=canonical_digest({"purpose": "semantic-review"}),
        dispatch_id="dsp_60000000-0000-4000-8000-000000000001",
        request_body_digest=rendered.body_sha256,
        service_generation=1,
        monotonic_deadline=30.0,
    )
    capture = _CaptureTransport()
    transport = OneAttemptCredentialTransport(
        rendered=rendered,
        credential=_Credential(),
        binding=binding,
        host=profile.host,
        port=profile.port,
        path=profile.path,
    )
    cast(Any, transport)._inner = capture
    result = await OpenAIResponsesEvaluator(profile, transport, _Clock()).evaluate(
        case,
        Deadline(_NOW + timedelta(seconds=30), 30.0),
    )

    assert type(result) is SemanticResultSuccess
    assert capture.request_body == rendered.body
    # The adapter reports the policy that authorized the dispatch, never a minted placeholder.
    assert result.provenance.policy_digest == case.policy_digest
    assert result.provenance.privacy_policy_digest == case.policy_digest
    assert result.provenance.policy_digest != "sha256:" + "0" * 64
