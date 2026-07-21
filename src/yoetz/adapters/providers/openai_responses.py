"""Native OpenAI Responses semantic-evaluation adapter for the approved external profile.

This module is the live provider bridge: it turns an already-approved outbound case into a
structured judgment and normalizes the provider's answer into Yoetz's closed semantic-result union
with provisional :class:`~yoetz.ports.semantic.ProviderAttemptProvenance`. It never manufactures
final receipt-bound provenance, never retries internally, and never lets a real credential enter a
reusable client, header, log, or exception. The ``openai``/``httpx`` SDK dependency is optional
(the ``semantic-openai`` extra); the module never imports ``openai`` at module scope so it keeps
importing cleanly when the extra is not installed, and resolves it lazily, once per physical
attempt, only inside :meth:`OpenAIResponsesEvaluator.evaluate`.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

import httpx

from yoetz.domain.findings import FindingKind, SamplingParams, SemanticFailureClass
from yoetz.domain.privacy import ApprovedOutboundCase, ApprovedProviderCase, ProviderDataUseProfile
from yoetz.domain.values import validate_sha256_digest
from yoetz.ports.clock import ClockPort
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import (
    Deadline,
    ProviderAttemptProvenance,
    ReviewerChallenge,
    SemanticJudgment,
    SemanticResult,
    SemanticResultInvalid,
    SemanticResultLate,
    SemanticResultRefused,
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
    "OFFICIAL_OPENAI_HOST",
    "OFFICIAL_OPENAI_PATH",
    "OFFICIAL_OPENAI_PORT",
    "OPENAI_CREDENTIAL_MAX_BYTES",
    "OPENAI_CREDENTIAL_MIN_BYTES",
    "OPENAI_MAX_OUTPUT_TOKENS",
    "OPENAI_MAX_RESPONSE_BODY_BYTES",
    "OneAttemptCredentialTransport",
    "OpenAIProfile",
    "OpenAIResponsesEvaluator",
    "ProviderDataUseProfile",
    "RenderedOpenAIRequest",
    "classify_provider_failure",
    "normalize_judgment",
    "normalize_response",
    "owner_declared_data_use_profile",
    "render_case",
    "validate_openai_credential",
]

OPENAI_CREDENTIAL_MIN_BYTES: Final = 16
OPENAI_CREDENTIAL_MAX_BYTES: Final = 512
OPENAI_MAX_OUTPUT_TOKENS: Final = 2_048
OPENAI_MAX_RESPONSE_BODY_BYTES: Final = 1_048_576

_TOKEN68_BODY_BYTES: Final = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~+/"
)
OFFICIAL_OPENAI_HOST: Final = "api.openai.com"
OFFICIAL_OPENAI_PORT: Final = 443
OFFICIAL_OPENAI_PATH: Final = "/v1/responses"
_HOST: Final = OFFICIAL_OPENAI_HOST
_PORT: Final = OFFICIAL_OPENAI_PORT
_PATH: Final = OFFICIAL_OPENAI_PATH
_IDENTITY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", re.ASCII)
_HOSTNAME_PATTERN: Final = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
    re.ASCII,
)

type _Conclusion = Literal["no_material_discrepancy", "challenges_returned", "insufficient_packet"]
type _NextStep = Literal[
    "act",
    "provide_evidence",
    "revise_claim",
    "dispute_with_evidence",
    "state_unresolved_limitation",
]

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


def validate_openai_credential(view: memoryview) -> None:
    """Byte-exact, non-normalizing, offline token68 validator for the OpenAI credential profile.

    Scans the protected view without converting it to ``str``/``bytes``, trimming, Unicode
    decoding/normalization, case changing, prefix repair, or logging. It returns no transformed
    value and never exposes length, invalid offset/byte, prefix, or input on failure.
    """

    if type(view) is not memoryview:
        raise TypeError("credential_invalid")
    length = len(view)
    if not OPENAI_CREDENTIAL_MIN_BYTES <= length <= OPENAI_CREDENTIAL_MAX_BYTES:
        raise ValueError("credential_invalid")
    scan = view if view.format == "B" else view.cast("B")
    equals_start = length
    index = length - 1
    while index >= 0 and scan[index] == 0x3D:
        equals_start = index
        index -= 1
    if equals_start == 0:
        raise ValueError("credential_invalid")
    for offset in range(equals_start):
        if scan[offset] not in _TOKEN68_BODY_BYTES:
            raise ValueError("credential_invalid")


@dataclass(frozen=True, slots=True)
class OpenAIProfile:
    """Frozen, exact, nonsecret identity/capability profile for a Responses endpoint."""

    provider_id: str
    model: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    timeout_seconds: int
    supports_structured_outputs: bool
    data_use_profile: ProviderDataUseProfile
    host: str = _HOST
    port: int = _PORT

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not str
            or _IDENTITY_PATTERN.fullmatch(self.provider_id) is None
        ):
            raise ValueError("openai_profile_provider_invalid")
        if type(self.model) is not str or _MODEL_PATTERN.fullmatch(self.model) is None:
            raise ValueError("openai_profile_model_invalid")
        if (
            type(self.endpoint_profile_id) is not str
            or _IDENTITY_PATTERN.fullmatch(self.endpoint_profile_id) is None
        ):
            raise ValueError("openai_profile_endpoint_invalid")
        if type(self.endpoint_profile_version) is not str or not self.endpoint_profile_version:
            raise ValueError("openai_profile_version_invalid")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("openai_profile_timeout_invalid")
        if (
            type(self.supports_structured_outputs) is not bool
            or not self.supports_structured_outputs
        ):
            raise ValueError("openai_profile_capability_invalid")
        if type(self.data_use_profile) is not ProviderDataUseProfile:
            raise ValueError("openai_profile_data_use_invalid")
        if type(self.host) is not str or _HOSTNAME_PATTERN.fullmatch(self.host) is None:
            raise ValueError("openai_profile_host_invalid")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("openai_profile_port_invalid")

    @property
    def path(self) -> str:
        return _PATH

    @property
    def base_url(self) -> str:
        if self.port == 443:
            return f"https://{self.host}"
        return f"https://{self.host}:{self.port}"


def owner_declared_data_use_profile(
    *,
    reviewed_at: object,
    expires_at: object,
    evidence_digest: str,
) -> ProviderDataUseProfile:
    """Unknown data-use facts for owner-declared hosts (never assisted-eligible)."""

    from datetime import datetime

    if type(reviewed_at) is not datetime or type(expires_at) is not datetime:
        raise TypeError("openai_data_use_time_invalid")
    return ProviderDataUseProfile(
        data_use_profile_id="owner-declared-unknown",
        data_use_profile_version="1.0.0",
        customer_content_training="unknown",
        retention="unknown",
        retention_days_ceiling=None,
        provider_human_access="unknown",
        reviewed_at=reviewed_at,
        expires_at=expires_at,
        evidence_digest=evidence_digest,
    )


@dataclass(frozen=True, slots=True)
class RenderedOpenAIRequest:
    """The exact final application JSON body plus its digest and nonsecret dispatch binding."""

    body: bytes
    body_sha256: str
    provider_id: str
    model: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    prompt_digest: str
    schema_digest: str

    def __post_init__(self) -> None:
        if type(self.body) is not bytes or not 0 < len(self.body) <= OPENAI_MAX_RESPONSE_BODY_BYTES:
            raise ValueError("openai_rendered_body_invalid")
        expected = "sha256:" + hashlib.sha256(self.body).hexdigest()
        if self.body_sha256 != expected:
            raise ValueError("openai_rendered_digest_mismatch")
        validate_sha256_digest(self.prompt_digest)
        validate_sha256_digest(self.schema_digest)


def _build_body_object(case: ApprovedOutboundCase) -> dict[str, JsonValue]:
    try:
        payload_value = strict_json_parse(case.payload)
    except Exception as exc:
        raise ValueError("openai_case_payload_invalid") from exc
    return {
        "model": case.provider_binding.model_id,
        "input": [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {"role": "user", "content": payload_value},
        ],
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "yoetz_semantic_judgment",
                "strict": True,
                "schema": _JUDGMENT_JSON_SCHEMA,
            }
        },
    }


_PROMPT_DIGEST: Final = "sha256:" + hashlib.sha256(_SYSTEM_INSTRUCTION.encode("utf-8")).hexdigest()
_SCHEMA_DIGEST: Final = canonical_digest(_JUDGMENT_JSON_SCHEMA)


def render_case(case: ApprovedOutboundCase) -> RenderedOpenAIRequest:
    """Deterministically convert an approved external case into a rendered request.

    Only the already-approved canonical payload bytes are copied into the request; this function
    selects, minimizes, summarizes, redacts, or adds nothing. It recomputes an independent
    reference body/digest so the one-attempt transport can refuse to dispatch anything the pinned
    SDK serializes differently.
    """

    if type(case) is not ApprovedOutboundCase:
        raise TypeError("openai_case_invalid")
    if case.provider_binding.transport != "external":
        raise ValueError("openai_case_binding_invalid")

    body_object = _build_body_object(case)
    body = canonical_encode(body_object)
    if len(body) > OPENAI_MAX_RESPONSE_BODY_BYTES:
        raise ValueError("openai_rendered_body_too_large")
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
    profile: OpenAIProfile,
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


def _text(source: Mapping[str, JsonValue], key: str) -> str:
    value = source.get(key)
    if type(value) is not str:
        raise ValueError("openai_judgment_field_invalid")
    return value


def _challenge_from_json(raw: JsonValue) -> ReviewerChallenge:
    if type(raw) is not dict:
        raise TypeError("openai_challenge_shape_invalid")
    source = cast(Mapping[str, JsonValue], raw)
    cited_raw = source.get("cited_refs")
    if type(cited_raw) is not list:
        raise ValueError("openai_challenge_shape_invalid")
    cited_items = cast(list[JsonValue], cited_raw)
    if any(type(item) is not str for item in cited_items):
        raise ValueError("openai_challenge_shape_invalid")
    return ReviewerChallenge(
        FindingKind(_text(source, "finding_kind")),
        _text(source, "summary"),
        tuple(cast(list[str], cited_items)),
        _text(source, "discrepancy"),
        _text(source, "alternative_interpretation"),
        _text(source, "message_to_main_agent"),
        cast(_NextStep, _text(source, "requested_next_step")),
        _text(source, "uncertainty"),
    )


def normalize_judgment(parsed: JsonValue) -> SemanticJudgment:
    """Validate a parsed judgment against Yoetz's closed judgment shape.

    Structural bounds (allowed finding kinds, bounded summaries/details, allowed cited-ID shape,
    conservative coverage, no novel conclusion vocabulary, no overlong arrays) are enforced by the
    :class:`SemanticJudgment`/:class:`ReviewerChallenge` constructors themselves; this function only
    maps untrusted JSON into those constructors and never relaxes their checks.
    """

    if type(parsed) is not dict:
        raise TypeError("openai_judgment_shape_invalid")
    source = cast(Mapping[str, JsonValue], parsed)
    conclusion = cast(_Conclusion, _text(source, "conclusion"))
    challenges_raw = source.get("reviewer_challenges", [])
    if type(challenges_raw) is not list:
        raise ValueError("openai_judgment_shape_invalid")
    challenges = tuple(_challenge_from_json(item) for item in cast(list[JsonValue], challenges_raw))
    return SemanticJudgment(conclusion, challenges)


def normalize_response(
    response: object,
    profile: OpenAIProfile,
    *,
    latency_ms: int,
    late: bool = False,
) -> SemanticResult:
    """Classify one provider response into the closed semantic-result union.

    Inspection order is fixed: explicit refusal surface first, deadline/cancellation next,
    parse/schema validity next, and late-arrival state last.
    """

    provider_request_id = getattr(response, "id", None)
    if type(provider_request_id) is not str:
        provider_request_id = None

    refusal = getattr(response, "refusal", None)
    if type(refusal) is str and refusal:
        return SemanticResultRefused(
            _provenance(
                profile,
                SemanticStatus.REFUSED,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
            )
        )

    status = getattr(response, "status", None)
    if status in {"cancelled", "incomplete"}:
        return SemanticResultTimeout(
            _provenance(
                profile,
                SemanticStatus.TIMEOUT,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.TIMEOUT,
            )
        )

    raw_text = getattr(response, "output_text", None)
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


def classify_provider_failure(
    error: BaseException, profile: OpenAIProfile, *, latency_ms: int
) -> SemanticResult:
    """Map a native provider/transport failure to the public taxonomy without leaking its text."""

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


class OneAttemptCredentialTransport(httpx.AsyncBaseTransport):
    """Adapter-private, one-attempt custom HTTP transport bound to one credential handle.

    It inspects the prepared request before any DNS/connect/write and rejects a byte/digest,
    method, destination, or encoding mismatch. It ignores poisoned proxy/netrc/environment
    configuration (``trust_env=False``), strips any SDK-fixed ``Authorization`` placeholder, and
    injects the real credential only inside :meth:`inject_and_start`, which the credential handle
    invokes exactly once.
    """

    __slots__ = (
        "_binding",
        "_body",
        "_body_sha256",
        "_consumed",
        "_credential",
        "_host",
        "_inner",
        "_path",
        "_pending_request",
        "_port",
    )

    def __init__(
        self,
        *,
        rendered: RenderedOpenAIRequest,
        credential: ProviderCredentialHandle,
        binding: ProviderAttemptAuthBinding,
        host: str = _HOST,
        port: int = _PORT,
        path: str = _PATH,
    ) -> None:
        if binding.request_body_digest != rendered.body_sha256:
            raise ValueError("openai_transport_binding_mismatch")
        if type(host) is not str or _HOSTNAME_PATTERN.fullmatch(host) is None:
            raise ValueError("openai_transport_host_invalid")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("openai_transport_port_invalid")
        if path != _PATH:
            raise ValueError("openai_transport_path_invalid")
        self._body = rendered.body
        self._body_sha256 = rendered.body_sha256
        self._credential = credential
        self._binding = binding
        self._host = host
        self._port = port
        self._path = path
        self._consumed = False
        self._inner = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
        self._pending_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._consumed:
            raise RuntimeError("openai_transport_already_consumed")
        self._consumed = True

        url = request.url
        if (
            request.method != "POST"
            or url.scheme != "https"
            or url.host != self._host
            or (url.port or self._port) != self._port
            or url.path != self._path
        ):
            raise ValueError("openai_transport_destination_mismatch")
        if request.headers.get("content-encoding"):
            raise ValueError("openai_transport_encoding_forbidden")

        body = await request.aread()
        if body != self._body:
            raise ValueError("openai_transport_body_mismatch")
        if "sha256:" + hashlib.sha256(body).hexdigest() != self._body_sha256:
            raise ValueError("openai_transport_digest_mismatch")

        request.headers.pop("authorization", None)
        request.headers["accept-encoding"] = "identity"
        self._pending_request = request
        try:
            return await self._credential.authorize_attempt(self._binding, self)
        finally:
            self._pending_request = None

    async def inject_and_start(self, credential_view: memoryview) -> httpx.Response:
        request = self._pending_request
        if request is None:
            raise RuntimeError("openai_transport_no_pending_request")
        token = bytes(credential_view).decode("ascii")
        request.headers["authorization"] = f"Bearer {token}"
        response = await self._inner.handle_async_request(request)
        content_length = response.headers.get("content-length")
        if content_length is not None and int(content_length) > OPENAI_MAX_RESPONSE_BODY_BYTES:
            raise ValueError("openai_response_body_too_large")
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class OpenAIResponsesEvaluator:
    """``SemanticEvaluatorPort`` implementation for the approved native OpenAI profile.

    Constructed only behind the privacy gateway for one physical attempt: the gateway supplies the
    approved case, an injected :class:`ClockPort`, and a :class:`OneAttemptCredentialTransport`
    already bound to a fresh credential handle and the precomputed final-body digest. This class
    makes exactly one physical provider call per :meth:`evaluate` invocation and never retries.
    """

    __slots__ = ("_clock", "_profile", "_safety_margin_seconds", "_transport")

    def __init__(
        self,
        profile: OpenAIProfile,
        transport: OneAttemptCredentialTransport,
        clock: ClockPort,
        *,
        safety_margin_seconds: float = 0.0,
    ) -> None:
        if type(profile) is not OpenAIProfile:
            raise TypeError("openai_profile_invalid")
        if type(transport) is not OneAttemptCredentialTransport:
            raise TypeError("openai_transport_invalid")
        if safety_margin_seconds < 0.0:
            raise ValueError("openai_safety_margin_invalid")
        self._profile = profile
        self._transport = transport
        self._clock = clock
        self._safety_margin_seconds = safety_margin_seconds

    async def evaluate(self, case: ApprovedProviderCase, deadline: Deadline) -> SemanticResult:
        if type(case) is not ApprovedOutboundCase:
            raise TypeError("openai_case_invalid")
        if type(deadline) is not Deadline:
            raise TypeError("openai_deadline_invalid")

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
            response = await client.responses.create(
                model=body_object["model"],
                input=body_object["input"],
                max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
                text=body_object["text"],
            )
        except Exception as exc:  # noqa: BLE001 - classified below, never re-raised raw
            elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
            return classify_provider_failure(exc, self._profile, latency_ms=elapsed_ms)
        finally:
            await client.close()
            await http_client.aclose()

        elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
        return normalize_response(response, self._profile, latency_ms=elapsed_ms)
