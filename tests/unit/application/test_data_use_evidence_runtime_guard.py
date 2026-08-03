"""RT-privacy-egress-2: require_current_provider_data_use_evidence is a runtime egress guard."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from builders.privacy_policies import INSTALLATION_ID, minimal_external_policy
from builders.privacy_widenings import llm_channel
from yoetz.adapters.providers.openai_responses import owner_declared_data_use_profile
from yoetz.application.egress import PrivacyCoordinator, SemanticEgressBlocked
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    CandidateContextItem,
    ClassifiedContext,
    ClassifiedContextItem,
    DataClass,
    EgressChannel,
    PrivacyDecision,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyReason,
    ProviderBinding,
    ProviderDataUseProfile,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    MinimizedDisclosure,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyAuditReservation,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
)
from yoetz.ports.semantic import Deadline
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import DataCategory

_NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _deadline() -> Deadline:
    return Deadline(_NOW + timedelta(minutes=1), 60.0)


_REQUEST = "req_80000000-0000-4000-8000-000000000002"
_TASK = "tsk_80000000-0000-4000-8000-000000000003"
_SUBJECT = "sha256:" + "d" * 64


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new(self, kind: IdKind) -> str:
        self._n += 1
        prefix = {
            IdKind.PRIVACY_PROPOSAL: "ppr",
            IdKind.EGRESS_RECEIPT: "egr",
            IdKind.EGRESS_AUTHORIZATION: "aut",
            IdKind.EGRESS_DISPATCH: "dsp",
        }.get(kind, "ppr")
        return f"{prefix}_80000000-0000-4000-8000-{self._n:012d}"


class _Store:
    def __init__(self, policy: PrivacyPolicy) -> None:
        self._policy = policy

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        del scope
        return EffectivePrivacyPolicy(self._policy, 1, self._policy.policy_digest)


class _Classifier:
    def classify(
        self, candidate: CandidateContext, effective: EffectivePrivacyPolicy
    ) -> ClassifiedContext:
        del effective
        return ClassifiedContext(
            candidate,
            tuple(
                ClassifiedContextItem(item, DataClass.PUBLIC_STRUCTURAL, (), True, "1.0.0")
                for item in candidate.items
            ),
        )

    def minimize_and_scan(
        self, classified: ClassifiedContext, decision: PrivacyDecision
    ) -> MinimizedDisclosure:
        del decision
        return MinimizedDisclosure(
            prepared_bytes=b"{}",
            included_item_ids=tuple(item.candidate.item_id for item in classified.items),
            source_item_digests=("sha256:" + "e" * 64,),
            approved_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
            blocked_categories=(),
            transformation_summary=(),
            byte_count=2,
            token_count=1,
            case_digest="sha256:" + "c" * 64,
            scanner_registry_version="test",
            scanner_profile_digest="sha256:" + "f" * 64,
            forbidden_findings=(),
        )


class _Audit:
    def __init__(self) -> None:
        self.prepared = 0

    async def reserve(self, subject: object) -> PrivacyAuditReservation:
        proposal_id = getattr(
            subject, "privacy_proposal_id", "ppr_80000000-0000-4000-8000-000000000001"
        )
        request_id = getattr(subject, "request_id", _REQUEST)
        return PrivacyAuditReservation(
            proposal_id,
            request_id,
            _SUBJECT,
            "reserved",
            1,
            _NOW,
        )

    async def complete_decision(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def prepare_disclosure_proposal(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.prepared += 1
        raise RuntimeError("stop_after_prepare")


class _Gateway:
    async def close(self) -> None:
        return None


def _policy_requiring_data_use() -> PrivacyPolicy:
    return replace(minimal_external_policy(), require_current_provider_data_use_evidence=True)


def _candidate(binding: ProviderBinding) -> CandidateContext:
    scope = AuthorizationScope(
        AuthorizationScopeKind.TASK,
        INSTALLATION_ID,
        f"hmac-sha256:{'8' * 64}",
        _TASK,
    )
    return CandidateContext(
        _REQUEST,
        EgressChannel.LLM_INFERENCE,
        None,
        "semantic-review",
        scope,
        _SUBJECT,
        binding,
        (
            CandidateContextItem(
                "item-1",
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                scope,
                "origin-1",
                b"{}",
            ),
        ),
    )


@pytest.mark.anyio
async def test_semantic_pipeline_blocks_when_flag_true_and_data_use_ineligible() -> None:
    policy = _policy_requiring_data_use()
    binding = llm_channel(policy).provider_binding
    assert binding is not None
    ineligible = owner_declared_data_use_profile(
        reviewed_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=30),
        evidence_digest=canonical_digest({"schema": "yoetz.provider-data-use/1", "k": "x"}),
    )
    assert not ineligible.recommendation_eligible(_NOW)

    audit = _Audit()
    coordinator = PrivacyCoordinator(
        cast(PrivacyPolicyStorePort, _Store(policy)),
        cast(PrivacyClassifierPort, _Classifier()),
        cast(PrivacyAuditPort, audit),
        cast(OutboundGatewayPort, _Gateway()),
        cast(ClockPort, _Clock()),
        cast(IdPort, _Ids()),
        data_use_resolver=lambda _binding: ineligible,
    )
    result = await coordinator.evaluate_semantic(_candidate(binding), _deadline())
    assert isinstance(result, SemanticEgressBlocked)
    assert result.outcome is PrivacyOutcome.BLOCKED_BY_POLICY
    assert result.reason is PrivacyReason.POLICY_DENIED
    assert audit.prepared == 0


@pytest.mark.anyio
async def test_semantic_pipeline_blocks_when_flag_true_and_resolver_missing() -> None:
    policy = _policy_requiring_data_use()
    binding = llm_channel(policy).provider_binding
    assert binding is not None
    audit = _Audit()
    coordinator = PrivacyCoordinator(
        cast(PrivacyPolicyStorePort, _Store(policy)),
        cast(PrivacyClassifierPort, _Classifier()),
        cast(PrivacyAuditPort, audit),
        cast(OutboundGatewayPort, _Gateway()),
        cast(ClockPort, _Clock()),
        cast(IdPort, _Ids()),
        data_use_resolver=None,
    )
    result = await coordinator.evaluate_semantic(_candidate(binding), _deadline())
    assert isinstance(result, SemanticEgressBlocked)
    assert result.reason is PrivacyReason.POLICY_DENIED
    assert audit.prepared == 0


@pytest.mark.anyio
async def test_semantic_pipeline_allows_when_flag_true_and_eligible() -> None:
    policy = _policy_requiring_data_use()
    binding = llm_channel(policy).provider_binding
    assert binding is not None
    eligible = ProviderDataUseProfile(
        data_use_profile_id="openai-api-data-use",
        data_use_profile_version="1.0.0",
        customer_content_training="prohibited",
        retention="bounded",
        retention_days_ceiling=30,
        provider_human_access="restricted",
        reviewed_at=_NOW - timedelta(days=1),
        expires_at=_NOW + timedelta(days=30),
        evidence_digest=canonical_digest({"schema": "yoetz.provider-data-use/1", "k": "ok"}),
    )
    assert eligible.recommendation_eligible(_NOW)

    audit = _Audit()
    coordinator = PrivacyCoordinator(
        cast(PrivacyPolicyStorePort, _Store(policy)),
        cast(PrivacyClassifierPort, _Classifier()),
        cast(PrivacyAuditPort, audit),
        cast(OutboundGatewayPort, _Gateway()),
        cast(ClockPort, _Clock()),
        cast(IdPort, _Ids()),
        data_use_resolver=lambda _binding: eligible,
    )
    result = await coordinator.evaluate_semantic(_candidate(binding), _deadline())
    assert audit.prepared == 1, f"eligible data-use must reach prepare; got {result!r}"
    assert isinstance(result, SemanticEgressBlocked)
    assert result.outcome is PrivacyOutcome.AUDIT_FAILED
