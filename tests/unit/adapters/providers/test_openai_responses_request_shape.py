"""The pinned OpenAI SDK must preserve the audited Responses request body exactly."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from yoetz.adapters.providers.openai_responses import (
    OPENAI_MAX_RESPONSE_BODY_BYTES,
    OneAttemptCredentialTransport,
    OpenAIProfile,
    OpenAIResponsesEvaluator,
    owner_declared_data_use_profile,
    render_case,
)
from yoetz.domain.findings import SemanticFailureClass
from yoetz.domain.privacy import ApprovedOutboundCase, DataCategory, ProviderBinding
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderAuthTransportCallback
from yoetz.ports.semantic import (
    Deadline,
    SemanticResultSuccess,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.models import SemanticStatus

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


class _ChunkedBodyStream(httpx.AsyncByteStream):
    """Async multi-chunk body used to exercise the raw response-byte cap."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False
        self.chunks_started = 0

    async def __aiter__(self):  # noqa: ANN204 - httpx AsyncByteStream contract
        for chunk in self._chunks:
            if self.closed:
                return
            self.chunks_started += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _ScriptedStreamTransport(httpx.AsyncBaseTransport):
    """Return a header-controlled streamed response and record the request body."""

    def __init__(
        self,
        *,
        chunks: list[bytes],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.request_body: bytes | None = None
        self.stream = _ChunkedBodyStream(chunks)
        self._headers = headers

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_body = await request.aread()
        headers = {"content-type": "application/json"}
        if self._headers is not None:
            headers.update(self._headers)
        return httpx.Response(
            200,
            request=request,
            headers=headers,
            stream=self.stream,
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


def _profile() -> OpenAIProfile:
    return OpenAIProfile(
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


def _binding(rendered_body_sha256: str) -> ProviderAttemptAuthBinding:
    return ProviderAttemptAuthBinding(
        provider_id="fireworks",
        model_id="accounts/fireworks/models/minimax-m3",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version="1.0.0",
        purpose="semantic-review",
        authorization_scope_digest=_DIGEST,
        purpose_digest=canonical_digest({"purpose": "semantic-review"}),
        dispatch_id="dsp_60000000-0000-4000-8000-000000000001",
        request_body_digest=rendered_body_sha256,
        service_generation=1,
        monotonic_deadline=30.0,
    )


def _success_body() -> bytes:
    return json.dumps(
        {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": _JUDGMENT}],
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _padded_body(target_size: int) -> bytes:
    """Build a valid-looking Responses JSON body of exactly ``target_size`` bytes."""

    base = {
        "id": "resp_test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": _JUDGMENT}],
            }
        ],
        "padding": "",
    }
    encoded = json.dumps(base, separators=(",", ":")).encode("utf-8")
    if len(encoded) > target_size:
        raise AssertionError("base body already exceeds target size")
    pad_len = target_size - len(encoded)
    base["padding"] = "x" * pad_len
    body = json.dumps(base, separators=(",", ":")).encode("utf-8")
    # json.dumps may expand escaping; pad with raw ASCII so length is exact.
    if len(body) != target_size:
        # Recompute with exact pad after measuring overhead of the padding field.
        overhead = len(body) - pad_len
        if overhead > target_size:
            raise AssertionError("unable to build body of target size")
        base["padding"] = "x" * (target_size - overhead)
        body = json.dumps(base, separators=(",", ":")).encode("utf-8")
    if len(body) != target_size:
        raise AssertionError(f"expected {target_size} bytes, got {len(body)}")
    return body


async def _evaluate_with_inner(
    inner: httpx.AsyncBaseTransport,
) -> tuple[Any, OpenAIProfile, ApprovedOutboundCase]:
    profile = _profile()
    case = _case()
    rendered = render_case(case)
    transport = OneAttemptCredentialTransport(
        rendered=rendered,
        credential=_Credential(),
        binding=_binding(rendered.body_sha256),
        host=profile.host,
        port=profile.port,
        path=profile.path,
    )
    cast(Any, transport)._inner = inner
    result = await OpenAIResponsesEvaluator(profile, transport, _Clock()).evaluate(
        case,
        Deadline(_NOW + timedelta(seconds=30), 30.0),
    )
    return result, profile, case


@pytest.mark.anyio
async def test_pinned_sdk_preserves_the_rendered_responses_body() -> None:
    profile = _profile()
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
    profile = _profile()
    case = _case()
    rendered = render_case(case)
    capture = _CaptureTransport()
    transport = OneAttemptCredentialTransport(
        rendered=rendered,
        credential=_Credential(),
        binding=_binding(rendered.body_sha256),
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


@pytest.mark.anyio
async def test_headerless_body_over_cap_is_rejected_before_parsing() -> None:
    """Chunked/headerless body of cap+1 fails closed as transport-unavailable."""

    oversize = _padded_body(OPENAI_MAX_RESPONSE_BODY_BYTES + 1)
    # Split so the overflow is only visible after the first chunk has been accepted.
    split_at = OPENAI_MAX_RESPONSE_BODY_BYTES // 2
    inner = _ScriptedStreamTransport(chunks=[oversize[:split_at], oversize[split_at:]])
    result, _profile_unused, _case_unused = await _evaluate_with_inner(inner)
    del _profile_unused, _case_unused

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.status is SemanticStatus.UNAVAILABLE
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert inner.stream.closed is True
    # Stream iteration starts, but the body is closed as soon as the cap is exceeded.
    assert inner.stream.chunks_started >= 1


@pytest.mark.anyio
async def test_misleading_small_content_length_still_enforces_stream_cap() -> None:
    oversize = _padded_body(OPENAI_MAX_RESPONSE_BODY_BYTES + 1)
    split_at = OPENAI_MAX_RESPONSE_BODY_BYTES // 2
    inner = _ScriptedStreamTransport(
        chunks=[oversize[:split_at], oversize[split_at:]],
        headers={"content-length": "16"},
    )
    result, _, _ = await _evaluate_with_inner(inner)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert inner.stream.closed is True


@pytest.mark.anyio
async def test_declared_content_length_above_cap_closes_without_reading() -> None:
    body = _success_body()
    inner = _ScriptedStreamTransport(
        chunks=[body],
        headers={"content-length": str(OPENAI_MAX_RESPONSE_BODY_BYTES + 1)},
    )
    result, _, _ = await _evaluate_with_inner(inner)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert inner.stream.closed is True
    assert inner.stream.chunks_started == 0


@pytest.mark.anyio
async def test_non_identity_content_encoding_is_rejected_fail_closed() -> None:
    body = _success_body()
    inner = _ScriptedStreamTransport(
        chunks=[body],
        headers={"content-encoding": "gzip"},
    )
    result, _, _ = await _evaluate_with_inner(inner)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert inner.stream.closed is True
    assert inner.stream.chunks_started == 0


@pytest.mark.anyio
async def test_headerless_body_exactly_at_cap_is_accepted_by_transport() -> None:
    """Identity streaming that totals the cap remains supported."""

    exact = b"a" * OPENAI_MAX_RESPONSE_BODY_BYTES
    split_at = OPENAI_MAX_RESPONSE_BODY_BYTES // 3
    chunks = [exact[:split_at], exact[split_at : 2 * split_at], exact[2 * split_at :]]
    assert sum(len(chunk) for chunk in chunks) == OPENAI_MAX_RESPONSE_BODY_BYTES
    inner = _ScriptedStreamTransport(chunks=chunks)

    profile = _profile()
    case = _case()
    rendered = render_case(case)
    transport = OneAttemptCredentialTransport(
        rendered=rendered,
        credential=_Credential(),
        binding=_binding(rendered.body_sha256),
        host=profile.host,
        port=profile.port,
        path=profile.path,
    )
    cast(Any, transport)._inner = inner

    # Drive the one-attempt path the same way the SDK does, then read the body.
    request = httpx.Request(
        "POST",
        f"https://{profile.host}{profile.path}",
        content=rendered.body,
        headers={"content-type": "application/json"},
    )
    response = await transport.handle_async_request(request)
    body = await response.aread()

    assert body == exact
    assert inner.stream.closed is True
    assert inner.stream.chunks_started == 3
