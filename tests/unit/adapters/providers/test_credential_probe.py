"""What one credential probe is allowed to conclude.

The probe exists so a wrong API key is caught while the person who can fix it is still at the
terminal. The rule that makes it safe is the converse: it may only ever conclude "the provider
refused this credential". Every other failure -- offline, timeouts, outages, a model this profile
cannot answer for -- must leave the credential alone, because none of them establish the key is
wrong, and someone setting up on a flaky connection must not lose a good one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from yoetz.adapters.providers.credential_probe import (
    PROBE_PURPOSE,
    _classify,  # pyright: ignore[reportPrivateUsage]
    _destination,  # pyright: ignore[reportPrivateUsage]
    _probe_body,  # pyright: ignore[reportPrivateUsage]
    probe_provider_credential,
    probe_request_digest,
)
from yoetz.adapters.providers.openai_responses import OneAttemptCredentialTransport
from yoetz.config.write import (
    anthropic_provider,
    fireworks_provider,
    grok_provider,
    official_openai_provider,
)
from yoetz.domain.findings import SemanticFailureClass
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding
from yoetz.protocol.canonical import canonical_digest, strict_json_parse
from yoetz.protocol.ids import IdKind, new_id

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


class _CountingCredential:
    """Counts authorize calls so a double-consume would be visible."""

    def __init__(self) -> None:
        self.calls = 0

    async def authorize_attempt(self, binding: object, inject_and_start: object) -> object:
        del binding
        self.calls += 1
        view = memoryview(b"x" * 32)
        return await inject_and_start.inject_and_start(view)  # type: ignore[attr-defined]


class _FixedStatusTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int) -> None:
        self._status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self._status, request=request)


def _attempt_binding(provider: object, digest: str) -> ProviderAttemptAuthBinding:
    return ProviderAttemptAuthBinding(
        provider_id=provider.provider_id,  # type: ignore[attr-defined]
        model_id=provider.model,  # type: ignore[attr-defined]
        endpoint_profile_id=provider.endpoint_profile_id,  # type: ignore[attr-defined]
        endpoint_profile_version=provider.endpoint_profile_version,  # type: ignore[attr-defined]
        purpose=PROBE_PURPOSE,
        authorization_scope_digest="sha256:" + "a" * 64,
        purpose_digest=canonical_digest({"purpose": PROBE_PURPOSE}),
        dispatch_id=str(new_id(IdKind.EGRESS_DISPATCH)),
        request_body_digest=digest,
        service_generation=1,
        monotonic_deadline=1e12,
    )


def test_only_a_refused_credential_is_a_rejection() -> None:
    assert _classify(401).outcome == "rejected"
    assert _classify(401).failure_class is SemanticFailureClass.AUTHENTICATION
    assert _classify(403).outcome == "rejected"
    assert _classify(403).failure_class is SemanticFailureClass.AUTHORIZATION


def test_an_authenticated_rate_limit_accepts_the_credential() -> None:
    """429 means the key authenticated and the account is busy, which is not a bad key."""

    result = _classify(429)
    assert result.outcome == "accepted"
    assert result.failure_class is SemanticFailureClass.RATE_LIMITED


@pytest.mark.parametrize("status", [200, 201, 299])
def test_a_successful_answer_accepts_the_credential(status: int) -> None:
    assert _classify(status).outcome == "accepted"


@pytest.mark.parametrize("status", [400, 404, 409, 422, 500, 502, 503])
def test_everything_that_is_not_a_refusal_leaves_the_credential_unproven(status: int) -> None:
    """An unknown model or an outage says nothing about the key, so it must not withdraw it."""

    assert _classify(status).outcome == "unverified"


def test_the_probe_body_carries_no_task_content_and_one_token() -> None:
    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    body, _suffix = _probe_body(provider)
    payload = strict_json_parse(body.body)
    assert payload == {
        "input": "ping",
        "max_output_tokens": 1,
        "model": "accounts/fireworks/models/minimax-m3",
        "store": False,
    }
    assert probe_request_digest(provider) == body.body_sha256


def test_the_probe_purpose_is_a_valid_attempt_binding_token() -> None:
    """Underscores fail the purpose pattern; the probe must stay hyphenated."""

    assert PROBE_PURPOSE == "credential-probe"
    assert canonical_digest({"purpose": PROBE_PURPOSE}) == canonical_digest(
        {"purpose": "credential-probe"}
    )


def test_the_probe_digest_uses_the_transport_prefix() -> None:
    """Bare hex digests fail both binding validation and the transport's body check."""

    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    digest = probe_request_digest(provider)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_each_preset_probes_its_own_exact_destination() -> None:
    cases = (
        (
            fireworks_provider(model="accounts/fireworks/models/minimax-m3"),
            ("api.fireworks.ai", 443, "/inference/v1/responses"),
        ),
        (
            official_openai_provider(model="gpt-4.1-mini"),
            ("api.openai.com", 443, "/v1/responses"),
        ),
        (
            anthropic_provider(model="claude-sonnet-4-6"),
            ("api.anthropic.com", 443, "/v1/chat/completions"),
        ),
        (grok_provider(model="grok-4.5"), ("api.x.ai", 443, "/v1/chat/completions")),
    )
    for provider, expected in cases:
        assert _destination(provider, now=NOW) == expected


def test_a_chat_completions_preset_probes_with_a_chat_body() -> None:
    body, suffix = _probe_body(anthropic_provider(model="claude-sonnet-4-6"))
    assert suffix == "/chat/completions"
    assert strict_json_parse(body.body) == {
        "max_tokens": 1,
        "messages": [{"content": "ping", "role": "user"}],
        "model": "claude-sonnet-4-6",
    }


@pytest.mark.anyio
async def test_probe_rejects_only_through_one_authorize_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transport must own the single authorize call; a 401 then withdraws the key."""

    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    digest = probe_request_digest(provider)
    binding = _attempt_binding(provider, digest)
    credential = _CountingCredential()
    original_init = OneAttemptCredentialTransport.__init__

    def _init_with_fixed_inner(
        self: OneAttemptCredentialTransport,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(self, *args, **kwargs)
        cast(Any, self)._inner = _FixedStatusTransport(401)

    monkeypatch.setattr(OneAttemptCredentialTransport, "__init__", _init_with_fixed_inner)

    result = await probe_provider_credential(provider, credential, binding, now=NOW)

    assert credential.calls == 1
    assert result.outcome == "rejected"
    assert result.failure_class is SemanticFailureClass.AUTHENTICATION
    assert result.status_code == 401


@pytest.mark.anyio
async def test_probe_accepts_a_rate_limited_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = fireworks_provider(model="accounts/fireworks/models/minimax-m3")
    digest = probe_request_digest(provider)
    binding = _attempt_binding(provider, digest)
    credential = _CountingCredential()
    original_init = OneAttemptCredentialTransport.__init__

    def _init_with_fixed_inner(
        self: OneAttemptCredentialTransport,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(self, *args, **kwargs)
        cast(Any, self)._inner = _FixedStatusTransport(429)

    monkeypatch.setattr(OneAttemptCredentialTransport, "__init__", _init_with_fixed_inner)

    result = await probe_provider_credential(provider, credential, binding, now=NOW)

    assert credential.calls == 1
    assert result.outcome == "accepted"
    assert result.failure_class is SemanticFailureClass.RATE_LIMITED
