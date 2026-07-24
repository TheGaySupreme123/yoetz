"""OpenAI-compatible Chat Completions semantic adapter (Anthropic/Gemini/OpenRouter paths).

Distinct from :mod:`openai_responses`: ``OpenAIProfile`` pins Responses paths. This module owns
``ChatCompletionsProfile`` and the ``/chat/completions`` request shape. Credential transport reuses
:class:`~yoetz.adapters.providers.openai_responses.OneAttemptCredentialTransport` (Bearer, one
attempt). Judgment normalization reuses the Responses closed judgment helpers.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

import httpx

from yoetz.adapters.providers.openai_responses import (
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MAX_RESPONSE_BODY_BYTES,
    OneAttemptCredentialTransport,
    RenderedOpenAIRequest,
    normalize_judgment,
)
from yoetz.domain.findings import SamplingParams, SemanticFailureClass
from yoetz.domain.privacy import ApprovedOutboundCase, ApprovedProviderCase, ProviderDataUseProfile
from yoetz.ports.clock import ClockPort
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import (
    Deadline,
    ProviderAttemptProvenance,
    SemanticResult,
    SemanticResultInvalid,
    SemanticResultLate,
    SemanticResultSuccess,
    SemanticResultTimeout,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.models import MAX_REVIEW_CHALLENGES, SemanticStatus

__all__ = [
    "CHAT_COMPLETIONS_ALLOWED_PREFIXES",
    "ChatCompletionsExternalFactory",
    "ChatCompletionsProfile",
    "chat_completions_profile_from_binding",
    "normalize_chat_completions_response",
    "render_chat_completions_case",
]

CHAT_COMPLETIONS_ALLOWED_PREFIXES: Final = frozenset({"/v1", "/api/v1", "/v1beta/openai"})

_IDENTITY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", re.ASCII)
_HOSTNAME_PATTERN: Final = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
    re.ASCII,
)

# Keep instruction text identical to Responses so prompt digests stay comparable across styles.
_SYSTEM_INSTRUCTION: Final = (
    "You are a bounded reviewer helping the main agent complete the user's stated goal. Review "
    "only the supplied packet. Distinguish agent claims, deterministic observations, and "
    "unavailable content. Never say no code changed merely because no source excerpt was "
    "disclosed. Compare the completion claim with the goal, obligations, decisions, ordered "
    "timeline, deterministic finding bases, state/change observations, evidence freshness, "
    "failures, limitations, and selected excerpts. If a material discrepancy exists, address the "
    "main agent directly, explain the discrepancy and strongest plausible alternative, cite only "
    "supplied refs, and request the smallest resolving action or evidence. Do not invent "
    "repository facts, fetch more context, overrule deterministic results, waive findings, or "
    "claim stronger coverage than the packet."
)

_CHALLENGE_JSON_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "finding_kind",
        "summary",
        "cited_refs",
        "discrepancy",
        "alternative_interpretation",
        "message_to_main_agent",
        "requested_next_step",
        "uncertainty",
    ],
    "properties": {
        "finding_kind": {"type": "string"},
        "summary": {"type": "string"},
        "cited_refs": {"type": "array", "items": {"type": "string"}},
        "discrepancy": {"type": "string"},
        "alternative_interpretation": {"type": "string"},
        "message_to_main_agent": {"type": "string"},
        "requested_next_step": {
            "type": "string",
            "enum": [
                "act",
                "provide_evidence",
                "revise_claim",
                "dispute_with_evidence",
                "state_unresolved_limitation",
            ],
        },
        "uncertainty": {"type": "string"},
    },
}

_JUDGMENT_JSON_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conclusion", "reviewer_challenges"],
    "properties": {
        "conclusion": {
            "type": "string",
            "enum": ["no_material_discrepancy", "challenges_returned", "insufficient_packet"],
        },
        "reviewer_challenges": {
            "type": "array",
            "maxItems": MAX_REVIEW_CHALLENGES,
            "items": _CHALLENGE_JSON_SCHEMA,
        },
    },
}

_PROMPT_DIGEST: Final = "sha256:" + hashlib.sha256(_SYSTEM_INSTRUCTION.encode("utf-8")).hexdigest()
_SCHEMA_DIGEST: Final = canonical_digest(_JUDGMENT_JSON_SCHEMA)

# Host facts for bundled presets (nonsecret).
_PRESET_HOSTS: Final[Mapping[str, tuple[str, str, int]]] = {
    "anthropic-openai-chat-completions": ("api.anthropic.com", "/v1", 443),
    "google-gemini-openai-chat-completions": (
        "generativelanguage.googleapis.com",
        "/v1beta/openai",
        443,
    ),
    "openrouter-openai-chat-completions": ("openrouter.ai", "/api/v1", 443),
}


@dataclass(frozen=True, slots=True)
class ChatCompletionsProfile:
    """Frozen nonsecret identity/capability profile for an OpenAI-compatible chat endpoint."""

    provider_id: str
    model: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    timeout_seconds: int
    supports_structured_outputs: bool
    data_use_profile: ProviderDataUseProfile
    host: str
    port: int = 443
    base_path_prefix: str = "/v1"

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not str
            or _IDENTITY_PATTERN.fullmatch(self.provider_id) is None
        ):
            raise ValueError("chat_completions_profile_provider_invalid")
        if type(self.model) is not str or _MODEL_PATTERN.fullmatch(self.model) is None:
            raise ValueError("chat_completions_profile_model_invalid")
        if (
            type(self.endpoint_profile_id) is not str
            or _IDENTITY_PATTERN.fullmatch(self.endpoint_profile_id) is None
        ):
            raise ValueError("chat_completions_profile_endpoint_invalid")
        if type(self.endpoint_profile_version) is not str or not self.endpoint_profile_version:
            raise ValueError("chat_completions_profile_version_invalid")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("chat_completions_profile_timeout_invalid")
        if (
            type(self.supports_structured_outputs) is not bool
            or not self.supports_structured_outputs
        ):
            raise ValueError("chat_completions_profile_capability_invalid")
        if type(self.data_use_profile) is not ProviderDataUseProfile:
            raise ValueError("chat_completions_profile_data_use_invalid")
        if type(self.host) is not str or _HOSTNAME_PATTERN.fullmatch(self.host) is None:
            raise ValueError("chat_completions_profile_host_invalid")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("chat_completions_profile_port_invalid")
        if self.base_path_prefix not in CHAT_COMPLETIONS_ALLOWED_PREFIXES:
            raise ValueError("chat_completions_profile_path_invalid")

    @property
    def path(self) -> str:
        return f"{self.base_path_prefix}/chat/completions"

    @property
    def base_url(self) -> str:
        if self.port == 443:
            return f"https://{self.host}{self.base_path_prefix}"
        return f"https://{self.host}:{self.port}{self.base_path_prefix}"


def _build_body_object(case: ApprovedOutboundCase) -> dict[str, JsonValue]:
    try:
        payload_value = strict_json_parse(case.payload)
    except Exception as exc:
        raise ValueError("chat_completions_case_payload_invalid") from exc
    return {
        "model": case.provider_binding.model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": payload_value},
        ],
        "max_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "yoetz_semantic_judgment",
                "strict": True,
                "schema": _JUDGMENT_JSON_SCHEMA,
            },
        },
    }


def render_chat_completions_case(case: ApprovedOutboundCase) -> RenderedOpenAIRequest:
    """Deterministically convert an approved external case into a chat-completions body."""

    if type(case) is not ApprovedOutboundCase:
        raise TypeError("chat_completions_case_invalid")
    if case.provider_binding.transport != "external":
        raise ValueError("chat_completions_case_binding_invalid")

    body_object = _build_body_object(case)
    body = canonical_encode(body_object)
    if len(body) > OPENAI_MAX_RESPONSE_BODY_BYTES:
        raise ValueError("chat_completions_rendered_body_too_large")
    body_digest = "sha256:" + hashlib.sha256(body).hexdigest()

    return RenderedOpenAIRequest(
        body=body,
        body_sha256=body_digest,
        provider_id=case.provider_binding.provider_id,
        model=case.provider_binding.model_id,
        endpoint_profile_id=case.provider_binding.endpoint_profile_id,
        endpoint_profile_version=case.provider_binding.endpoint_profile_version,
        prompt_digest=_PROMPT_DIGEST,
        schema_digest=_SCHEMA_DIGEST,
    )


def _provenance(
    profile: ChatCompletionsProfile,
    status: SemanticStatus,
    *,
    latency_ms: int,
    provider_request_id: str | None = None,
    failure_class: SemanticFailureClass | None = None,
) -> ProviderAttemptProvenance:
    return ProviderAttemptProvenance(
        provider=profile.provider_id,
        endpoint_profile_id=profile.endpoint_profile_id,
        endpoint_profile_version=profile.endpoint_profile_version,
        model=profile.model,
        sdk_version="2.46.0",
        prompt_digest=_PROMPT_DIGEST,
        schema_digest=_SCHEMA_DIGEST,
        policy_digest="sha256:" + "0" * 64,
        privacy_policy_digest="sha256:" + "0" * 64,
        sampling_params=SamplingParams(OPENAI_MAX_OUTPUT_TOKENS),
        latency_ms=latency_ms,
        status=status,
        provider_request_id=provider_request_id,
        failure_class=failure_class,
    )


def _mapping_content(source: Mapping[object, object]) -> str | None:
    raw_choices = source.get("choices")
    if type(raw_choices) is not list or not cast(list[object], raw_choices):
        return None
    first: object = cast(list[object], raw_choices)[0]
    if not isinstance(first, Mapping):
        return None
    first_map = cast(Mapping[object, object], first)
    message = first_map.get("message")
    if not isinstance(message, Mapping):
        return None
    message_map = cast(Mapping[object, object], message)
    content = message_map.get("content")
    return content if type(content) is str else None


def _content_text(response: object) -> str | None:
    choices = getattr(response, "choices", None)
    if type(choices) is not list or not cast(list[object], choices):
        if isinstance(response, Mapping):
            return _mapping_content(cast(Mapping[object, object], response))
        return None
    first_choice: object = cast(list[object], choices)[0]
    message: object | None = getattr(first_choice, "message", None)
    if message is None and isinstance(first_choice, Mapping):
        message = cast(Mapping[object, object], first_choice).get("message")
    if message is None:
        return None
    content: object | None = getattr(message, "content", None)
    if content is None and isinstance(message, Mapping):
        content = cast(Mapping[object, object], message).get("content")
    return content if type(content) is str else None


def normalize_chat_completions_response(
    response: object,
    profile: ChatCompletionsProfile,
    *,
    latency_ms: int,
    late: bool = False,
) -> SemanticResult:
    """Classify one chat-completions response into the closed semantic-result union."""

    provider_request_id: str | None
    attr_id = getattr(response, "id", None)
    if type(attr_id) is str:
        provider_request_id = attr_id
    elif isinstance(response, Mapping):
        raw_id = cast(Mapping[object, object], response).get("id")
        provider_request_id = raw_id if type(raw_id) is str else None
    else:
        provider_request_id = None

    raw_text = _content_text(cast(object, response))
    if type(raw_text) is not str or not raw_text:
        return SemanticResultInvalid(
            _provenance(
                profile,
                SemanticStatus.INVALID,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.RESPONSE_SCHEMA,
            ),
            raw_size=len(raw_text) if type(raw_text) is str else 0,
        )
    raw_bytes = raw_text.encode("utf-8")
    if len(raw_bytes) > OPENAI_MAX_RESPONSE_BODY_BYTES:
        return SemanticResultInvalid(
            _provenance(
                profile,
                SemanticStatus.INVALID,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.RESPONSE_SCHEMA,
            ),
            raw_size=OPENAI_MAX_RESPONSE_BODY_BYTES + 1,
        )
    try:
        parsed = strict_json_parse(raw_bytes)
        judgment = normalize_judgment(parsed)
    except ValueError, TypeError, LookupError:
        return SemanticResultInvalid(
            _provenance(
                profile,
                SemanticStatus.INVALID,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.RESPONSE_SCHEMA,
            ),
            raw_size=len(raw_bytes),
        )

    if late:
        return SemanticResultLate(
            _provenance(
                profile,
                SemanticStatus.LATE,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
            )
        )
    return SemanticResultSuccess(
        judgment,
        _provenance(
            profile,
            SemanticStatus.SUCCEEDED,
            latency_ms=latency_ms,
            provider_request_id=provider_request_id,
        ),
    )


class ChatCompletionsEvaluator:
    """One-attempt chat-completions evaluator (ADR-006 decision 5)."""

    __slots__ = ("_clock", "_profile", "_safety_margin_seconds", "_transport")

    def __init__(
        self,
        profile: ChatCompletionsProfile,
        transport: OneAttemptCredentialTransport,
        clock: ClockPort,
        *,
        safety_margin_seconds: float = 0.0,
    ) -> None:
        if type(profile) is not ChatCompletionsProfile:
            raise TypeError("chat_completions_profile_invalid")
        if type(transport) is not OneAttemptCredentialTransport:
            raise TypeError("chat_completions_transport_invalid")
        if safety_margin_seconds < 0.0:
            raise ValueError("chat_completions_safety_margin_invalid")
        self._profile = profile
        self._transport = transport
        self._clock = clock
        self._safety_margin_seconds = safety_margin_seconds

    async def evaluate(self, case: ApprovedProviderCase, deadline: Deadline) -> SemanticResult:
        if type(case) is not ApprovedOutboundCase:
            raise TypeError("chat_completions_case_invalid")
        if type(deadline) is not Deadline:
            raise TypeError("chat_completions_deadline_invalid")

        now_monotonic = self._clock.monotonic_seconds()
        remaining = deadline.remaining_seconds(now_monotonic) - self._safety_margin_seconds
        if deadline.expired(now_monotonic) or remaining <= 0.0:
            return SemanticResultTimeout(
                _provenance(
                    self._profile,
                    SemanticStatus.TIMEOUT,
                    latency_ms=0,
                    failure_class=SemanticFailureClass.TIMEOUT,
                )
            )

        body_object = _build_body_object(case)

        try:
            openai_module = importlib.import_module("openai")
        except ImportError:
            return SemanticResultUnavailable(
                _provenance(
                    self._profile,
                    SemanticStatus.UNAVAILABLE,
                    latency_ms=0,
                    failure_class=SemanticFailureClass.UNSUPPORTED_PROFILE,
                )
            )

        http_client = httpx.AsyncClient(transport=self._transport, trust_env=False)
        client: Any = openai_module.AsyncOpenAI(
            api_key="yoetz-fixed-nonsecret-sentinel",
            base_url=self._profile.base_url,
            timeout=remaining,
            max_retries=0,
            http_client=http_client,
        )
        try:
            response = await client.chat.completions.create(
                model=body_object["model"],
                messages=body_object["messages"],
                max_tokens=OPENAI_MAX_OUTPUT_TOKENS,
                response_format=body_object["response_format"],
            )
        except Exception as exc:  # noqa: BLE001 - classified below, never re-raised raw
            elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
            return _classify_chat_failure(exc, self._profile, latency_ms=elapsed_ms)
        finally:
            await client.close()
            await http_client.aclose()

        elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
        return normalize_chat_completions_response(response, self._profile, latency_ms=elapsed_ms)


def _classify_chat_failure(
    error: BaseException, profile: ChatCompletionsProfile, *, latency_ms: int
) -> SemanticResult:
    if isinstance(error, httpx.TimeoutException):
        return SemanticResultTimeout(
            _provenance(
                profile,
                SemanticStatus.TIMEOUT,
                latency_ms=latency_ms,
                failure_class=SemanticFailureClass.TIMEOUT,
            )
        )
    if isinstance(error, httpx.TransportError):
        return SemanticResultUnavailable(
            _provenance(
                profile,
                SemanticStatus.UNAVAILABLE,
                latency_ms=latency_ms,
                failure_class=SemanticFailureClass.TRANSPORT,
            )
        )

    status_code = getattr(error, "status_code", None)
    if type(status_code) is not int:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)

    failure_class = SemanticFailureClass.TRANSPORT
    if status_code == 401:
        failure_class = SemanticFailureClass.AUTHENTICATION
    elif status_code == 403:
        failure_class = SemanticFailureClass.AUTHORIZATION
    elif status_code == 429:
        failure_class = SemanticFailureClass.RATE_LIMITED
    elif type(status_code) is int and status_code >= 500:
        failure_class = SemanticFailureClass.PROVIDER_OUTAGE

    return SemanticResultUnavailable(
        _provenance(
            profile,
            SemanticStatus.UNAVAILABLE,
            latency_ms=latency_ms,
            failure_class=failure_class,
        )
    )


@dataclass
class ChatCompletionsExternalFactory:
    """Render + one-attempt evaluator factory for chat-completions profiles."""

    profile: ChatCompletionsProfile
    clock: ClockPort

    def __post_init__(self) -> None:
        self._last_rendered: RenderedOpenAIRequest | None = None

    def render(self, case: ApprovedOutboundCase) -> bytes:
        rendered = render_chat_completions_case(case)
        self._last_rendered = rendered
        return rendered.body

    def build_evaluator(
        self,
        binding: ProviderAttemptAuthBinding,
        credential: ProviderCredentialHandle,
        request_commitment: object,
    ) -> ChatCompletionsEvaluator:
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


def chat_completions_profile_from_binding(
    *,
    provider_id: str,
    model: str,
    endpoint_profile_id: str,
    endpoint_profile_version: str,
    timeout_seconds: int,
    data_use_profile: ProviderDataUseProfile,
    host: str | None = None,
    port: int = 443,
    base_path_prefix: str | None = None,
) -> ChatCompletionsProfile:
    """Build a chat-completions profile from config or preset host facts."""

    if host is None or base_path_prefix is None:
        preset = _PRESET_HOSTS.get(endpoint_profile_id)
        if preset is None:
            raise ValueError("chat_completions_endpoint_profile_unsupported")
        host, base_path_prefix, port = preset
    return ChatCompletionsProfile(
        provider_id=provider_id,
        model=model,
        endpoint_profile_id=endpoint_profile_id,
        endpoint_profile_version=endpoint_profile_version,
        timeout_seconds=timeout_seconds,
        supports_structured_outputs=True,
        data_use_profile=data_use_profile,
        host=host,
        port=port,
        base_path_prefix=base_path_prefix,
    )
