"""One provider judgment contract: schema generation, normalization, and failure classes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from yoetz.adapters.providers.openai_chat_completions import (
    ChatCompletionsProfile,
)
from yoetz.adapters.providers.openai_chat_completions import (
    normalize_response as normalize_chat_response,
)
from yoetz.adapters.providers.openai_responses import (
    JUDGMENT_JSON_SCHEMA,
    OpenAIProfile,
    build_judgment_json_schema,
    classify_provider_failure,
    normalize_judgment,
    owner_declared_data_use_profile,
)
from yoetz.adapters.providers.openai_responses import (
    normalize_response as normalize_responses_response,
)
from yoetz.domain.findings import FindingKind, SemanticFailureClass
from yoetz.ports.semantic import (
    SemanticResultInvalid,
    SemanticResultRefused,
    SemanticResultSuccess,
    SemanticResultTimeout,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, strict_json_parse
from yoetz.protocol.models import ProviderJudgmentModel

_NOW = datetime(2026, 7, 28, tzinfo=UTC)
_DIGEST = "sha256:" + "c" * 64
_REPO = Path(__file__).resolve().parents[4]
_FROZEN_SCHEMA = _REPO / "schemas" / "findings" / "provider-judgment-1.0.0.schema.json"

_REF_A = "clm_20000000-0000-4000-8000-000000000001"
_REF_B = "act_10000000-0000-4000-8000-000000000001"
_REF_C = "evd_30000000-0000-4000-8000-000000000001"


def _challenge(
    *,
    kind: str = "claim_without_admissible_evidence",
    refs: list[str] | None = None,
    summary: str = "Evidence gap",
) -> dict[str, JsonValue]:
    return {
        "finding_kind": kind,
        "summary": summary,
        "cited_refs": cast(list[JsonValue], refs if refs is not None else [_REF_A]),
        "discrepancy": "The claim lacks a recorded basis.",
        "alternative_interpretation": "The claim may remain unresolved.",
        "message_to_main_agent": "Main agent: provide evidence for the claim.",
        "requested_next_step": "provide_evidence",
        "uncertainty": "The missing material may exist outside the case.",
    }


def _judgment(
    conclusion: str = "no_material_discrepancy",
    challenges: list[dict[str, JsonValue]] | None = None,
) -> dict[str, JsonValue]:
    return {
        "conclusion": conclusion,
        "reviewer_challenges": cast(
            list[JsonValue], [] if challenges is None else challenges
        ),
    }


def _responses_profile() -> OpenAIProfile:
    return OpenAIProfile(
        provider_id="openai",
        model="gpt-test",
        endpoint_profile_id="openai-responses",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        supports_structured_outputs=True,
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=_NOW,
            expires_at=_NOW + timedelta(days=30),
            evidence_digest=_DIGEST,
        ),
    )


def _chat_profile() -> ChatCompletionsProfile:
    return ChatCompletionsProfile(
        provider_id="xai",
        model="grok-test",
        endpoint_profile_id="xai-openai-chat-completions",
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        structured_output_enforcement="provider_enforced",
        data_use_profile=owner_declared_data_use_profile(
            reviewed_at=_NOW,
            expires_at=_NOW + timedelta(days=30),
            evidence_digest=_DIGEST,
        ),
        host="api.x.ai",
    )


class _ResponsesResponse:
    def __init__(
        self,
        *,
        output_text: str | None = None,
        status: str = "completed",
        refusal: str | None = None,
        response_id: str = "resp_test",
    ) -> None:
        self.output_text = output_text
        self.status = status
        self.refusal = refusal
        self.id = response_id


class _ChatMessage:
    def __init__(self, *, content: str | None = None, refusal: str | None = None) -> None:
        self.content = content
        self.refusal = refusal


class _ChatChoice:
    def __init__(self, message: _ChatMessage, finish_reason: str | None = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _ChatResponse:
    def __init__(self, choice: _ChatChoice | None = None, response_id: str = "chatcmpl-1") -> None:
        self.choices = () if choice is None else (choice,)
        self.id = response_id


def test_generated_schema_matches_owning_model_and_frozen_artifact() -> None:
    rebuilt = build_judgment_json_schema()
    assert rebuilt == JUDGMENT_JSON_SCHEMA
    assert canonical_digest(rebuilt) == canonical_digest(JUDGMENT_JSON_SCHEMA)

    frozen = strict_json_parse(_FROZEN_SCHEMA.read_bytes())
    assert type(frozen) is dict
    frozen_doc = cast(dict[str, Any], frozen)
    # Frozen catalog adds $id/$schema/root title; after dropping catalog chrome and nested titles,
    # the constrained-output body matches the runtime request schema byte-for-byte.
    assert frozen_doc["$id"].endswith("provider-judgment-1.0.0.schema.json")

    def _strip_titles(node: object) -> object:
        if type(node) is dict:
            return {
                key: _strip_titles(value)
                for key, value in cast(dict[str, object], node).items()
                if key != "title"
            }
        if type(node) is list:
            return [_strip_titles(item) for item in cast(list[object], node)]
        return node

    frozen_body = {
        key: value
        for key, value in cast(dict[str, object], _strip_titles(frozen_doc)).items()
        if key not in {"$id", "$schema"}
    }
    assert frozen_body == cast(dict[str, object], JUDGMENT_JSON_SCHEMA)
    kinds = cast(
        list[str],
        cast(
            dict[str, Any], cast(dict[str, Any], JUDGMENT_JSON_SCHEMA["$defs"])["FindingKindWire"]
        )["enum"],
    )
    assert set(kinds) == {kind.value for kind in FindingKind}


def test_every_finding_kind_is_admitted_and_near_misses_are_rejected() -> None:
    for kind in FindingKind:
        judgment = normalize_judgment(
            _judgment("challenges_returned", [_challenge(kind=kind.value)])
        )
        assert judgment.challenges[0].finding_kind is kind

    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(
            _judgment("challenges_returned", [_challenge(kind="claim_without_evidence")])
        )
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("challenges_returned", [_challenge(kind="not_a_kind")]))


def test_challenge_refs_enforce_pattern_count_uniqueness_and_canonicalize_order() -> None:
    # Valid unsorted refs normalize into ASCII order.
    judgment = normalize_judgment(
        _judgment(
            "challenges_returned",
            [_challenge(refs=[_REF_C, _REF_A, _REF_B])],
        )
    )
    assert judgment.challenges[0].cited_refs == tuple(sorted((_REF_A, _REF_B, _REF_C)))

    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("challenges_returned", [_challenge(refs=[])]))
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("challenges_returned", [_challenge(refs=[_REF_A, _REF_A])]))
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(
            _judgment("challenges_returned", [_challenge(refs=["task_not_a_subject_id"])])
        )
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(
            _judgment(
                "challenges_returned",
                [_challenge(refs=["tsk_20000000-0000-4000-8000-000000000001"])],
            )
        )
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(
            _judgment(
                "challenges_returned",
                [_challenge(refs=["prose citation to the claim"])],
            )
        )


def test_conclusion_challenge_coupling_is_enforced() -> None:
    normalize_judgment(_judgment("no_material_discrepancy", []))
    normalize_judgment(_judgment("insufficient_packet", []))
    normalize_judgment(_judgment("challenges_returned", [_challenge()]))

    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("challenges_returned", []))
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("insufficient_packet", [_challenge()]))
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("no_material_discrepancy", [_challenge()]))


def test_empty_review_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(_judgment("challenges_returned", [_challenge(summary="")]))


def test_provider_model_and_normalize_share_rejection_surface() -> None:
    adapter: TypeAdapter[ProviderJudgmentModel] = TypeAdapter(ProviderJudgmentModel)
    bad = _judgment("challenges_returned", [_challenge(kind="invented_kind")])
    with pytest.raises(ValidationError):
        adapter.validate_python(bad)
    with pytest.raises(ValueError, match="openai_judgment_shape_invalid"):
        normalize_judgment(bad)


@pytest.mark.parametrize(
    ("raw", "failure_class"),
    (
        ("", SemanticFailureClass.RESPONSE_SCHEMA),
        ("```json\n{}\n```", SemanticFailureClass.RESPONSE_SCHEMA),
        (
            'prefix {"conclusion":"no_material_discrepancy","reviewer_challenges":[]}',
            SemanticFailureClass.RESPONSE_SCHEMA,
        ),
        (
            '{"conclusion":"no_material_discrepancy","reviewer_challenges":[],"conclusion":"x"}',
            SemanticFailureClass.RESPONSE_SCHEMA,
        ),
        (
            '{"conclusion":"no_material_discrepancy","reviewer_challenges":[],"x":1.5}',
            SemanticFailureClass.RESPONSE_SCHEMA,
        ),
    ),
)
def test_malformed_output_keeps_schema_failure_class_without_plaintext(
    raw: str, failure_class: SemanticFailureClass
) -> None:
    result = normalize_responses_response(
        _ResponsesResponse(output_text=raw),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=5,
    )
    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is failure_class
    # No diagnostic field retains provider plaintext (slots-only structural facts).
    assert result.raw_size == len(raw.encode("utf-8"))
    for value in (
        result.provenance.provider,
        result.provenance.model,
        result.provenance.provider_request_id,
        result.provenance.failure_class,
    ):
        if type(value) is str:
            assert "conclusion" not in value
            assert "```" not in value
            assert "prefix" not in value


def test_oversized_output_is_content_invalid_not_timeout() -> None:
    huge = "x" * (1_048_576 + 8)
    result = normalize_responses_response(
        _ResponsesResponse(output_text=huge),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=5,
    )
    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_CONTENT


def test_incomplete_is_content_invalid_not_timeout() -> None:
    result = normalize_responses_response(
        _ResponsesResponse(output_text='{"conclusion":', status="incomplete"),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=16_000,
    )
    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_CONTENT
    assert result.provenance.provider_request_id == "resp_test"
    assert type(result) is not SemanticResultTimeout


def test_cancelled_is_refused_not_timeout() -> None:
    result = normalize_responses_response(
        _ResponsesResponse(status="cancelled"),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=5,
    )
    assert type(result) is SemanticResultRefused


def test_chat_length_finish_reason_is_content_invalid() -> None:
    result = normalize_chat_response(
        _ChatResponse(_ChatChoice(_ChatMessage(content='{"conclusion":'), finish_reason="length")),
        _chat_profile(),
        policy_digest=_DIGEST,
        latency_ms=5,
    )
    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_CONTENT


def test_transport_timeout_still_maps_to_provider_timeout() -> None:
    result = classify_provider_failure(
        httpx.TimeoutException("deadline"),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=60_000,
    )
    assert type(result) is SemanticResultTimeout
    assert result.provenance.failure_class is SemanticFailureClass.TIMEOUT


def test_conforming_challenge_response_succeeds_on_first_parse() -> None:
    body = json.dumps(
        _judgment(
            "challenges_returned",
            [_challenge(refs=[_REF_B, _REF_A])],
        ),
        separators=(",", ":"),
    )
    result = normalize_responses_response(
        _ResponsesResponse(output_text=body),
        _responses_profile(),
        policy_digest=_DIGEST,
        latency_ms=12,
    )
    assert type(result) is SemanticResultSuccess
    assert result.judgment.conclusion == "challenges_returned"
    assert result.judgment.challenges[0].cited_refs == (
        _REF_B,
        _REF_A,
    ) or result.judgment.challenges[0].cited_refs == tuple(sorted((_REF_A, _REF_B)))
