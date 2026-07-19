from __future__ import annotations

import pytest

from builders.policy_cases import clm, make_case
from yoetz.application.check import validate_semantic_judgment
from yoetz.domain.findings import (
    FindingKind,
    SamplingParams,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.ports.semantic import ReviewerChallenge, SemanticJudgment
from yoetz.protocol.models import SemanticReason, SemanticStatus

_DIGEST = "sha256:" + "a" * 64


def _provenance() -> SemanticProvenance:
    return SemanticProvenance(
        provider="fake",
        endpoint_profile_id="fake",
        endpoint_profile_version="1.0.0",
        model="fake/model",
        sdk_version="1.0.0",
        prompt_digest=_DIGEST,
        schema_digest=_DIGEST,
        policy_digest=_DIGEST,
        privacy_policy_digest=_DIGEST,
        sampling_params=SamplingParams(128),
        latency_ms=1,
        semantic_attempt_id="att_20000000-0000-4000-8000-000000000001",
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        privacy_receipt_id="egr_20000000-0000-4000-8000-000000000001",
        status=SemanticStatus.SUCCEEDED,
        reason=SemanticReason.SEMANTIC_COMPLETED,
        provider_request_id="fake-1",
        egress_authorization_id="aut_20000000-0000-4000-8000-000000000001",
        request_commitment="hmac-sha256:" + "b" * 64,
    )


def _challenge(*refs: str) -> ReviewerChallenge:
    return ReviewerChallenge(
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        "Evidence gap",
        tuple(sorted(refs)),
        "The claim lacks a recorded basis.",
        "The claim may remain unresolved.",
        "Main agent: provide evidence for the claim.",
        "provide_evidence",
        "The missing material may exist outside the case.",
    )


def test_semantic_judgment_accepts_only_frozen_refs_and_derives_policy() -> None:
    case = make_case(extra_refs=(clm(1),))
    judgment = SemanticJudgment("challenges_returned", (_challenge(str(clm(1))),))

    candidates = validate_semantic_judgment(
        case,
        (),
        judgment,
        _provenance(),
        expected_frontier=case.frontier,
    )

    assert len(candidates) == 1
    assert candidates[0].subject_refs == (clm(1),)
    assert candidates[0].policy_id == "work-integrity"
    assert candidates[0].policy_version == "0.1.0"


def test_invented_or_stale_semantic_refs_are_rejected() -> None:
    case = make_case(extra_refs=(clm(1),))
    invented = "clm_20000000-0000-4000-8000-000000000099"

    with pytest.raises(ValueError, match="semantic_ref_outside_case"):
        validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("challenges_returned", (_challenge(invented),)),
            _provenance(),
            expected_frontier=case.frontier,
        )
    with pytest.raises(ValueError, match="semantic_judgment_invalid"):
        validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("challenges_returned", (_challenge(str(clm(1))),)),
            _provenance(),
            expected_frontier=type(case.frontier)(99, "sha256:" + "c" * 64),
        )


def test_no_material_discrepancy_returns_no_semantic_candidate() -> None:
    case = make_case(extra_refs=(clm(1),))

    assert (
        validate_semantic_judgment(
            case,
            (),
            SemanticJudgment("no_material_discrepancy", ()),
            _provenance(),
            expected_frontier=case.frontier,
        )
        == ()
    )
