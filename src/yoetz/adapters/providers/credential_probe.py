"""One bounded authenticated request that answers whether a stored credential works.

A credential is the one setup step whose correctness nothing else can infer. An endpoint that
resolves, a model that exists, and a policy that permits egress all look identical with a stale
API key, and the first evidence otherwise arrives much later as a failed check whose public
reason cannot name authentication. This dispatches one minimal request so that evidence arrives
while the person who can fix it is still standing at the terminal.

Three properties are deliberate:

* **It carries no task content.** The body is a fixed literal and a one-token ceiling. This is an
  authentication probe, not a review, and it must never become a second path that sends work.
* **It reuses the hardened one-attempt transport**, so the destination allowlist, the
  body-digest binding, the `trust_env=False` isolation, and the response byte cap all apply
  exactly as they do to a real semantic attempt.
* **Only a provider's refusal of the credential is a rejection.** A timeout, a socket failure, a
  provider outage, or an unrecognized model leaves the answer `unverified`. Someone configuring
  Yoetz on a flaky connection must not lose a key that was never actually refused.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

import httpx

from yoetz.adapters.providers.factory import (
    CHAT_COMPLETIONS_ENDPOINT_PROFILES,
    RESPONSES_ENDPOINT_PROFILE_IDS,
)
from yoetz.adapters.providers.openai_responses import OneAttemptCredentialTransport
from yoetz.adapters.providers.openai_responses_factory import (
    openai_profile_from_provider_config,
)
from yoetz.config.models import ProviderProfileConfig
from yoetz.domain.findings import SemanticFailureClass
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle

__all__ = [
    "CredentialProbeResult",
    "PROBE_PURPOSE",
    "probe_provider_credential",
    "probe_request_digest",
]

# Hyphenated: ProviderAttemptAuthBinding purpose tokens only allow [a-z0-9-].
# Not a review purpose — nothing about this request is derived from a task.
PROBE_PURPOSE: Final = "credential-probe"

_PROBE_TEXT: Final = "ping"
_PROBE_TIMEOUT_SECONDS: Final = 20.0

ProbeOutcome = Literal["accepted", "rejected", "unverified"]


@dataclass(frozen=True, slots=True)
class CredentialProbeResult:
    """What one probe established, and nothing more.

    ``accepted`` means the provider authenticated the credential -- including a rate-limited
    answer, which proves the key is good and the account merely busy. ``rejected`` means the
    provider refused the credential itself. ``unverified`` means the question was not answered.
    """

    outcome: ProbeOutcome
    failure_class: SemanticFailureClass | None = None
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class _ProbeBody:
    """Satisfies the transport's ``RenderedRequest`` protocol for a body we build here."""

    body: bytes
    body_sha256: str


def _probe_body(provider: ProviderProfileConfig) -> tuple[_ProbeBody, str]:
    """Return the smallest well-formed request for this endpoint profile, and its path suffix."""

    if provider.endpoint_profile_id in RESPONSES_ENDPOINT_PROFILE_IDS:
        payload: dict[str, object] = {
            "input": _PROBE_TEXT,
            "max_output_tokens": 1,
            "model": provider.model,
            "store": False,
        }
        suffix = "/responses"
    elif provider.endpoint_profile_id in CHAT_COMPLETIONS_ENDPOINT_PROFILES:
        payload = {
            "max_tokens": 1,
            "messages": [{"content": _PROBE_TEXT, "role": "user"}],
            "model": provider.model,
        }
        suffix = "/chat/completions"
    else:
        raise ValueError("provider_endpoint_profile_unsupported")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # Same wire form as every real rendered request: the transport and the attempt binding
    # both require the sha256: prefix, not a bare hex digest.
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    return _ProbeBody(body, digest), suffix


def probe_request_digest(provider: ProviderProfileConfig) -> str:
    """The probe body's digest, so a caller can bind an attempt to it before dispatching."""

    return _probe_body(provider)[0].body_sha256


def _destination(provider: ProviderProfileConfig, *, now: datetime) -> tuple[str, int, str]:
    """Resolve the exact host, port, and path this binding would dispatch to."""

    suffix = _probe_body(provider)[1]
    if provider.endpoint_profile_id in RESPONSES_ENDPOINT_PROFILE_IDS:
        profile = openai_profile_from_provider_config(provider, now=now)
        return profile.host, profile.port, profile.path
    facts = CHAT_COMPLETIONS_ENDPOINT_PROFILES[provider.endpoint_profile_id]
    return facts.host, 443, f"{facts.base_path_prefix}{suffix}"


def _classify(status_code: int) -> CredentialProbeResult:
    """Map one provider status to the narrowest honest answer about the credential.

    Only 401 and 403 say anything about the credential. A 429 authenticated first and then hit a
    limit, so it is an acceptance. Everything else -- an unknown model, a malformed body for this
    profile, an outage -- leaves the credential unproven rather than refuted.
    """

    if status_code == 401:
        return CredentialProbeResult("rejected", SemanticFailureClass.AUTHENTICATION, status_code)
    if status_code == 403:
        return CredentialProbeResult("rejected", SemanticFailureClass.AUTHORIZATION, status_code)
    if status_code == 429:
        return CredentialProbeResult("accepted", SemanticFailureClass.RATE_LIMITED, status_code)
    if 200 <= status_code < 300:
        return CredentialProbeResult("accepted", None, status_code)
    if status_code >= 500:
        return CredentialProbeResult(
            "unverified", SemanticFailureClass.PROVIDER_OUTAGE, status_code
        )
    return CredentialProbeResult(
        "unverified", SemanticFailureClass.UNSUPPORTED_PROFILE, status_code
    )


async def probe_provider_credential(
    provider: ProviderProfileConfig,
    credential: ProviderCredentialHandle,
    binding: ProviderAttemptAuthBinding,
    *,
    now: datetime,
) -> CredentialProbeResult:
    """Dispatch one minimal authenticated request and report only what it established.

    The hardened transport owns the one-shot authorize callback: we post through it and let
    ``handle_async_request`` call ``authorize_attempt`` exactly once. Calling authorize first
    and then posting would double-consume the handle and never leave process memory with a key.

    Every failure that is not the provider refusing the credential returns ``unverified``. The
    caller decides what to do with that; this function never turns "could not ask" into "no".
    """

    try:
        rendered, _suffix = _probe_body(provider)
        host, port, path = _destination(provider, now=now)
        transport = OneAttemptCredentialTransport(
            rendered=rendered,
            credential=credential,
            binding=binding,
            host=host,
            port=port,
            path=path,
        )
    except KeyError, ValueError:
        return CredentialProbeResult("unverified", SemanticFailureClass.UNSUPPORTED_PROFILE)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"https://{host}:{port}{path}",
                content=rendered.body,
                headers={
                    # Placeholder only: the transport strips Authorization and injects the real
                    # credential inside the one-shot authorize callback.
                    "authorization": "Bearer placeholder",
                    "content-type": "application/json",
                },
            )
            return _classify(response.status_code)
    except httpx.TimeoutException:
        return CredentialProbeResult("unverified", SemanticFailureClass.TIMEOUT)
    except httpx.TransportError:
        return CredentialProbeResult("unverified", SemanticFailureClass.TRANSPORT)
    except Exception:
        # A probe must never be the reason a ceremony fails. Anything unexpected here leaves the
        # credential unproven, which the caller already handles as "stored, not verified".
        return CredentialProbeResult("unverified", None)
