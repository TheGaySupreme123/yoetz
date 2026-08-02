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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

import httpx
from pydantic import TypeAdapter, ValidationError

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
from yoetz.protocol.models import (
    ProviderChallengeModel,
    ProviderJudgmentEnvelopeModel,
    ProviderJudgmentModel,
    SemanticStatus,
)

__all__ = [
    "CHALLENGE_FIELD_GLOSSARY",
    "FINDING_KIND_GLOSSARY",
    "JUDGMENT_JSON_SCHEMA",
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
    "RenderedRequest",
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
# Every destination the one-attempt transport may dispatch to. The Responses paths are this
# module's own; the Chat Completions paths belong to the sibling adapter, which reuses this
# transport rather than duplicating credential-injection code.
_ALLOWED_PATHS: Final = frozenset(
    {
        "/v1/responses",
        "/inference/v1/responses",
        "/v1/chat/completions",
        "/api/v1/chat/completions",
        "/v1beta/openai/chat/completions",
    }
)
_IDENTITY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.ASCII)
_MODEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", re.ASCII)
_HOSTNAME_PATTERN: Final = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
    re.ASCII,
)

_SYSTEM_INSTRUCTION: Final = (
    "You are a bounded reviewer helping the main agent complete the user's stated goal. Review "
    "only the supplied packet. Distinguish agent claims, deterministic observations, and "
    "unavailable content. Never say no code changed merely because no source excerpt was "
    "disclosed. Compare the completion claim with the goal, obligations, decisions, ordered "
    "timeline, deterministic finding bases, state/change observations, evidence freshness, "
    "failures, limitations, and selected excerpts. If a material discrepancy exists, address the "
    "main agent directly, explain the discrepancy and strongest plausible alternative, cite only "
    "supplied refs, and request the smallest resolving action or evidence. Every value in "
    "cited_refs must come from the packet's citable_refs array and nothing else: an item_id from "
    "items[] is not citable, and a challenge citing anything outside citable_refs is discarded "
    "unread. Do not invent repository facts, fetch more context, overrule deterministic results, "
    "waive findings, or claim stronger coverage than the packet."
)

_PROVIDER_JUDGMENT_ADAPTER: Final[TypeAdapter[ProviderJudgmentModel]] = TypeAdapter(
    ProviderJudgmentModel
)
_PROVIDER_JUDGMENT_ENVELOPE_ADAPTER: Final[TypeAdapter[ProviderJudgmentEnvelopeModel]] = (
    TypeAdapter(ProviderJudgmentEnvelopeModel)
)


def _rename_schema_defs(raw: dict[str, object]) -> dict[str, object]:
    """Strip the pydantic ``Model`` suffix from ``$defs`` anchors (matches schema generator)."""

    defs = raw.get("$defs")
    if type(defs) is not dict:
        return raw
    rename: dict[str, str] = {}
    for key in cast(dict[str, object], defs):
        new_key = key[: -len("Model")] if key.endswith("Model") and len(key) > len("Model") else key
        rename[f"#/$defs/{key}"] = f"#/$defs/{new_key}"

    def _walk(node: object) -> object:
        if type(node) is dict:
            source = cast(dict[str, object], node)
            result: dict[str, object] = {}
            for key, value in source.items():
                if key == "$ref" and type(value) is str and value in rename:
                    result[key] = rename[value]
                else:
                    result[key] = _walk(value)
            return result
        if type(node) is list:
            return [_walk(item) for item in cast(list[object], node)]
        return node

    renamed = cast(dict[str, object], _walk(raw))
    new_defs: dict[str, object] = {}
    for key, value in cast(dict[str, object], defs).items():
        target = rename[f"#/$defs/{key}"].removeprefix("#/$defs/")
        new_defs[target] = _walk(value)
    renamed["$defs"] = new_defs
    return renamed


def _sort_schema_lists(node: object) -> object:
    if type(node) is dict:
        source = cast(dict[str, object], node)
        result: dict[str, object] = {}
        for key in tuple(source.keys()):
            child: object = source[key]
            handled = False
            if key == "required" and type(child) is list:
                result[key] = sorted(
                    [str(item) for item in cast(list[object], child)],
                    key=lambda item: item.encode("utf-8"),
                )
                handled = True
            elif key == "enum" and type(child) is list:
                enum_items = cast(list[object], child)
                if all(type(item) is str for item in enum_items):
                    result[key] = sorted(
                        [str(item) for item in enum_items],
                        key=lambda item: item.encode("utf-8"),
                    )
                    handled = True
            if not handled:
                # Re-bind through object so list-narrowing does not leak into recursion.
                pass_through: object = source[key]
                result[key] = _sort_schema_lists(pass_through)
        return result
    if type(node) is list:
        list_items = cast(list[object], node)
        sorted_items: list[object] = []
        for index in range(len(list_items)):
            element: object = list_items[index]
            sorted_items.append(_sort_schema_lists(element))
        return sorted_items
    return node


_SCHEMA_ANNOTATION_KEYS: Final = frozenset({"title", "description"})


def _strip_schema_titles(node: object) -> object:
    """Drop pydantic title/description metadata so the wire schema carries shape only.

    Docstrings are developer commentary about *why* the contract is shaped this way; they are not
    instructions for the reviewer and must not be dispatched to a provider as schema descriptions.
    """

    if type(node) is dict:
        source = cast(dict[str, object], node)
        return {
            key: _strip_schema_titles(value)
            for key, value in source.items()
            if key not in _SCHEMA_ANNOTATION_KEYS
        }
    if type(node) is list:
        return [_strip_schema_titles(item) for item in cast(list[object], node)]
    return node


# Reviewer-facing definitions, written deliberately for the model that receives this schema.
#
# These are not docstrings and must never be sourced from one: a docstring explains the contract to
# a maintainer, while these tell a reviewer what the word means when it has to pick one. Stripping
# every annotation left the model choosing among fourteen bare enum strings and eight bare field
# names with no gloss anywhere in the request, which is not a fair question to ask it.
FINDING_KIND_GLOSSARY: Final[dict[str, str]] = {
    "action_without_result": "an action was taken but no outcome for it was ever recorded",
    "claim_without_admissible_evidence": (
        "a claim of fact or completion rests on no evidence the packet actually contains"
    ),
    "completion_with_open_obligations": (
        "work is presented as finished while obligations it was meant to satisfy remain open"
    ),
    "contradictory_claims_unresolved": (
        "two claims in the packet cannot both be true and neither has been withdrawn or reconciled"
    ),
    "diff_does_not_match_account": (
        "the described change and the change actually shown in the packet differ in substance"
    ),
    "evidence_does_not_support_claim": (
        "evidence is cited for a claim but does not establish what the claim asserts"
    ),
    "failed_work_omitted": (
        "a recorded failure, error, or abandoned attempt is missing from the account given"
    ),
    "ledger_stale_or_incomplete": (
        "the record itself is behind or missing entries, so the packet cannot settle the question"
    ),
    "material_limitation_omitted": (
        "a limitation that changes how the result should be read was not disclosed"
    ),
    "questionable_finding_rejection": (
        "a deterministic finding was dismissed or explained away without adequate grounds"
    ),
    "requested_item_never_attempted": (
        "something the user or an obligation asked for was never worked on at all"
    ),
    "result_without_action": "an outcome is recorded with no action in the packet that produced it",
    "stale_evidence_for_changed_state": (
        "the cited evidence predates a change to what it describes, so it no longer speaks to it"
    ),
    "weak_or_stale_response": (
        "the answer given is thin or out of date relative to what the packet supports"
    ),
}

CHALLENGE_FIELD_GLOSSARY: Final[dict[str, str]] = {
    "finding_kind": (
        "Which kind of discrepancy this is; pick the one that fits best. "
        + "; ".join(f"{kind}: {gloss}" for kind, gloss in sorted(FINDING_KIND_GLOSSARY.items()))
        + "."
    ),
    "summary": "One short line naming the discrepancy, readable on its own.",
    "cited_refs": (
        "The refs this challenge rests on. Every value must appear in the packet's citable_refs "
        "array; an items[].item_id is not a ref. A challenge citing anything else is discarded, "
        "so cite the specific claim, obligation, event, or finding the discrepancy is about."
    ),
    "discrepancy": "What the packet shows that does not fit, stated as fact rather than suspicion.",
    "alternative_interpretation": (
        "The strongest honest reading under which there is no problem here. Write the case against "
        "your own challenge, not a weak version of it."
    ),
    "message_to_main_agent": (
        "What you are telling the agent, addressed to it directly, including the smallest action "
        "or piece of evidence that would resolve this."
    ),
    "requested_next_step": (
        "The single kind of response you are asking the agent for. act: do the missing work; "
        "provide_evidence: record evidence that already exists; revise_claim: correct or withdraw "
        "what was claimed; dispute_with_evidence: rebut this challenge if you believe it is wrong; "
        "state_unresolved_limitation: say plainly that this cannot be settled here."
    ),
    "uncertainty": (
        "What you could not determine from the packet and what would settle it. Say so plainly "
        "rather than hedging the challenge itself."
    ),
}


def _apply_reviewer_glossary(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Attach the curated reviewer definitions to the stripped schema.

    Applied after stripping rather than by not stripping, so the only text that can reach a
    provider is text written above for that purpose.
    """

    defs = schema.get("$defs")
    if type(defs) is not dict:
        raise RuntimeError("provider_judgment_schema_invalid")
    definitions = cast(dict[str, JsonValue], defs)
    kinds = definitions.get("FindingKindWire")
    challenge = definitions.get("ProviderChallenge")
    if type(kinds) is not dict or type(challenge) is not dict:
        raise RuntimeError("provider_judgment_schema_invalid")
    properties = cast(dict[str, JsonValue], challenge).get("properties")
    if type(properties) is not dict:
        raise RuntimeError("provider_judgment_schema_invalid")
    challenge_properties = cast(dict[str, JsonValue], properties)
    # The glossary must describe exactly the shape the model owns. A field added or renamed there
    # without a gloss fails the build rather than shipping a silently undefined field.
    if set(challenge_properties) != set(CHALLENGE_FIELD_GLOSSARY):
        raise RuntimeError("provider_judgment_schema_invalid")
    enum_values = cast(dict[str, JsonValue], kinds).get("enum")
    if type(enum_values) is not list or set(cast(list[JsonValue], enum_values)) != set(
        FINDING_KIND_GLOSSARY
    ):
        raise RuntimeError("provider_judgment_schema_invalid")
    for name, gloss in CHALLENGE_FIELD_GLOSSARY.items():
        target = challenge_properties[name]
        if type(target) is not dict:
            raise RuntimeError("provider_judgment_schema_invalid")
        source = cast(dict[str, JsonValue], target)
        # A property that is a bare ``$ref`` carries its gloss on the referenced definition:
        # annotations beside ``$ref`` are legal in 2020-12 but not uniformly honored, and the
        # definition is the one place every use of that vocabulary sees it.
        reference = source.get("$ref")
        if type(reference) is str:
            anchor = reference.removeprefix("#/$defs/")
            referenced = definitions.get(anchor)
            if type(referenced) is not dict:
                raise RuntimeError("provider_judgment_schema_invalid")
            definitions[anchor] = cast(
                JsonValue, {**cast(dict[str, JsonValue], referenced), "description": gloss}
            )
            continue
        challenge_properties[name] = cast(JsonValue, {**source, "description": gloss})
    return schema


def build_judgment_json_schema() -> dict[str, JsonValue]:
    """Generate the constrained-output schema from the single owning provider judgment model.

    The schema is generated from :data:`ProviderJudgmentEnvelopeModel`, which nests the same
    :data:`ProviderJudgmentModel` used by :func:`normalize_judgment` under a required ``judgment``
    property. The nesting is load-bearing: constrained-output requests are sent with
    ``strict: true``, and a provider rejects a schema whose root is a union rather than an object
    before generation starts, which surfaces only as an opaque transport failure. The generated
    document expresses closed enums, ref pattern and counts, non-empty bounded text, challenge
    cardinality, conclusion/challenge coupling through explicit union branches, and
    ``additionalProperties: false``. Normalization matches the frozen catalog shape (def rename +
    enum/required sort) so runtime and
    ``schemas/findings/provider-judgment-1.0.0.schema.json`` stay shape-equivalent.
    """

    raw = cast(dict[str, object], _PROVIDER_JUDGMENT_ENVELOPE_ADAPTER.json_schema())
    cleaned = _strip_schema_titles(_sort_schema_lists(_rename_schema_defs(raw)))
    if type(cleaned) is not dict:
        raise RuntimeError("provider_judgment_schema_invalid")
    return _apply_reviewer_glossary(cast(dict[str, JsonValue], cleaned))


JUDGMENT_JSON_SCHEMA: Final[dict[str, JsonValue]] = build_judgment_json_schema()


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
    base_path_prefix: str = "/v1"

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
        if self.base_path_prefix not in {"/v1", "/inference/v1"}:
            raise ValueError("openai_profile_path_invalid")

    @property
    def path(self) -> str:
        return f"{self.base_path_prefix}/responses"

    @property
    def base_url(self) -> str:
        # OpenAI Python SDK appends `/responses` to base_url; include `/v1` so the
        # wire path matches the transport-enforced `/v1/responses` destination.
        if self.port == 443:
            return f"https://{self.host}{self.base_path_prefix}"
        return f"https://{self.host}:{self.port}{self.base_path_prefix}"


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


class RenderedRequest(Protocol):
    """The two body facts the one-attempt transport binds itself to.

    Stated as a protocol so the sibling Chat Completions adapter can reuse this transport with its
    own rendered type instead of duplicating credential-injection code.
    """

    @property
    def body(self) -> bytes: ...

    @property
    def body_sha256(self) -> str: ...


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
                "schema": JUDGMENT_JSON_SCHEMA,
            }
        },
    }


_PROMPT_DIGEST: Final = "sha256:" + hashlib.sha256(_SYSTEM_INSTRUCTION.encode("utf-8")).hexdigest()
_SCHEMA_DIGEST: Final = canonical_digest(JUDGMENT_JSON_SCHEMA)


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
    policy_digest: str,
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
        policy_digest=policy_digest,
        privacy_policy_digest=policy_digest,
        sampling_params=SamplingParams(OPENAI_MAX_OUTPUT_TOKENS),
        latency_ms=latency_ms,
        status=status,
        provider_request_id=provider_request_id,
        failure_class=failure_class,
    )


def _challenge_from_model(challenge: ProviderChallengeModel) -> ReviewerChallenge:
    return ReviewerChallenge(
        FindingKind(challenge.finding_kind),
        challenge.summary,
        challenge.cited_refs,
        challenge.discrepancy,
        challenge.alternative_interpretation,
        challenge.message_to_main_agent,
        challenge.requested_next_step,
        challenge.uncertainty,
    )


def normalize_judgment(parsed: JsonValue) -> SemanticJudgment:
    """Validate a parsed judgment against the single provider judgment contract.

    Validation runs through :data:`ProviderJudgmentModel`, which
    :data:`ProviderJudgmentEnvelopeModel` nests to generate :data:`JUDGMENT_JSON_SCHEMA`, so any
    output that satisfies the machine-enforced provider schema can enter domain construction. Cited
    refs are then ASCII-canonicalized; invalid IDs, invented enums, empty prose, duplicates, and
    conclusion/challenge contradictions are never normalized into acceptance.
    """

    # The request schema asks for the envelope, so that is tried first. A bare judgment is also
    # accepted because the two shapes are unambiguous (``judgment`` versus ``conclusion``) and a
    # provider that flattens the wrapper is still returning output the contract can admit.
    model: ProviderJudgmentModel
    if type(parsed) is dict and "judgment" in parsed:
        try:
            model = _PROVIDER_JUDGMENT_ENVELOPE_ADAPTER.validate_python(parsed).judgment
        except ValidationError as exc:
            raise ValueError("openai_judgment_shape_invalid") from exc
    else:
        try:
            model = _PROVIDER_JUDGMENT_ADAPTER.validate_python(parsed)
        except ValidationError as exc:
            raise ValueError("openai_judgment_shape_invalid") from exc
    challenges = tuple(_challenge_from_model(item) for item in model.reviewer_challenges)
    return SemanticJudgment(model.conclusion, challenges)


def normalize_response(
    response: object,
    profile: OpenAIProfile,
    *,
    policy_digest: str,
    latency_ms: int,
    late: bool = False,
) -> SemanticResult:
    """Classify one provider response into the closed semantic-result union.

    Inspection order is fixed: explicit refusal surface first, deadline/cancellation next,
    parse/schema validity next, and late-arrival state last.

    ``policy_digest`` is the policy digest that authorized this dispatch, carried by the approved
    case. The adapter never mints one of its own; the outbound gateway rebinds it authoritatively
    after this returns.
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
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
            )
        )

    status = getattr(response, "status", None)
    if status == "cancelled":
        # Provider/client cancellation is not a transport deadline timeout.
        return SemanticResultRefused(
            _provenance(
                profile,
                SemanticStatus.REFUSED,
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
            )
        )
    if status == "incomplete":
        # Output-limit truncation (hard max_output_tokens) is content invalidity, not timeout.
        incomplete_text = getattr(response, "output_text", None)
        incomplete_size = (
            len(incomplete_text.encode("utf-8")) if type(incomplete_text) is str else 0
        )
        return SemanticResultInvalid(
            _provenance(
                profile,
                SemanticStatus.INVALID,
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.RESPONSE_CONTENT,
            ),
            raw_size=incomplete_size,
        )

    raw_text = getattr(response, "output_text", None)
    if type(raw_text) is not str or not raw_text:
        return SemanticResultInvalid(
            _provenance(
                profile,
                SemanticStatus.INVALID,
                policy_digest=policy_digest,
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
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
                failure_class=SemanticFailureClass.RESPONSE_CONTENT,
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
                policy_digest=policy_digest,
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
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                provider_request_id=provider_request_id,
            )
        )
    return SemanticResultSuccess(
        judgment,
        _provenance(
            profile,
            SemanticStatus.SUCCEEDED,
            policy_digest=policy_digest,
            latency_ms=latency_ms,
            provider_request_id=provider_request_id,
        ),
    )


def classify_provider_failure(
    error: BaseException, profile: OpenAIProfile, *, policy_digest: str, latency_ms: int
) -> SemanticResult:
    """Map a native provider/transport failure to the public taxonomy without leaking its text."""

    if isinstance(error, httpx.TimeoutException):
        return SemanticResultTimeout(
            _provenance(
                profile,
                SemanticStatus.TIMEOUT,
                policy_digest=policy_digest,
                latency_ms=latency_ms,
                failure_class=SemanticFailureClass.TIMEOUT,
            )
        )
    if isinstance(error, httpx.TransportError):
        return SemanticResultUnavailable(
            _provenance(
                profile,
                SemanticStatus.UNAVAILABLE,
                policy_digest=policy_digest,
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
            policy_digest=policy_digest,
            latency_ms=latency_ms,
            failure_class=failure_class,
        )
    )


class _BoundedResponseByteStream(httpx.AsyncByteStream):
    """Count raw response bytes and refuse anything above the provider body cap.

    ``Content-Length`` is only an early-rejection hint; chunked or headerless bodies still
    stream through this wrapper so the cap is enforced on the bytes the SDK actually reads.
    The overflowing chunk is never yielded: the underlying stream is closed first, then a
    private size failure is raised for classification as generic transport unavailability.
    """

    __slots__ = ("_closed", "_inner", "_received")

    def __init__(self, inner: httpx.AsyncByteStream) -> None:
        self._inner = inner
        self._received = 0
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            size = len(chunk)
            if self._received + size > OPENAI_MAX_RESPONSE_BODY_BYTES:
                await self.aclose()
                raise ValueError("openai_response_body_too_large")
            self._received += size
            yield chunk

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._inner.aclose()


class OneAttemptCredentialTransport(httpx.AsyncBaseTransport):
    """Adapter-private, one-attempt custom HTTP transport bound to one credential handle.

    It inspects the prepared request before any DNS/connect/write and rejects a byte/digest,
    method, destination, or encoding mismatch. It ignores poisoned proxy/netrc/environment
    configuration (``trust_env=False``), strips any SDK-fixed ``Authorization`` placeholder, and
    injects the real credential only inside :meth:`inject_and_start`, which the credential handle
    invokes exactly once. Response bodies are capped at :data:`OPENAI_MAX_RESPONSE_BODY_BYTES`
    whether or not ``Content-Length`` is present; non-identity ``Content-Encoding`` is refused
    so decompression cannot bypass the raw-stream cap.
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
        rendered: RenderedRequest,
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
        if path not in _ALLOWED_PATHS:
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
        content_encoding = response.headers.get("content-encoding")
        if content_encoding is not None and content_encoding.strip().lower() != "identity":
            await response.aclose()
            raise ValueError("openai_response_encoding_forbidden")
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except TypeError, ValueError:
                # Provider-controlled header text must not appear in structural errors.
                await response.aclose()
                raise ValueError("openai_response_content_length_invalid") from None
            if declared_size > OPENAI_MAX_RESPONSE_BODY_BYTES:
                await response.aclose()
                raise ValueError("openai_response_body_too_large")
        stream = response.stream
        if not isinstance(stream, httpx.AsyncByteStream):
            # Fail closed: never skip the byte cap for sync/missing streams.
            # Sync streams raise RuntimeError on aclose; use the matching close path.
            response.close()
            raise ValueError("openai_response_stream_invalid")
        response.stream = _BoundedResponseByteStream(stream)
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
                    policy_digest=case.policy_digest,
                    latency_ms=0,
                    failure_class=SemanticFailureClass.TIMEOUT,
                )
            )

        # The one-attempt transport compares the SDK's exact serialized bytes to the
        # privacy gateway's audited body.  Reconstructing keyword arguments here can
        # preserve a different insertion order from the canonical rendering, which
        # correctly fails closed as a body mismatch before credential injection.
        # Decode the canonical rendering and pass that mapping through unchanged.
        rendered_body = strict_json_parse(render_case(case).body)
        if type(rendered_body) is not dict:
            raise ValueError("openai_rendered_body_invalid")
        body_object = cast(dict[str, Any], rendered_body)

        try:
            openai_module = importlib.import_module("openai")
        except ImportError:
            return SemanticResultUnavailable(
                _provenance(
                    self._profile,
                    SemanticStatus.UNAVAILABLE,
                    policy_digest=case.policy_digest,
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
            response = await client.responses.create(**body_object)
        except Exception as exc:  # noqa: BLE001 - classified below, never re-raised raw
            elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
            return classify_provider_failure(
                exc, self._profile, policy_digest=case.policy_digest, latency_ms=elapsed_ms
            )
        finally:
            await client.close()
            await http_client.aclose()

        elapsed_ms = max(0, int((self._clock.monotonic_seconds() - now_monotonic) * 1_000))
        return normalize_response(
            response, self._profile, policy_digest=case.policy_digest, latency_ms=elapsed_ms
        )
