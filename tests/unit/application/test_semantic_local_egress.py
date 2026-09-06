from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

import yoetz.service.ready_composition as ready_composition
from builders.privacy_policies import local_only_policy
from yoetz.adapters.providers.fake import scripted_success
from yoetz.application.egress import (
    PrivacyCoordinator,
    SemanticEgressBlocked,
    SemanticEgressSuccess,
)
from yoetz.domain.findings import SemanticDispatchKind
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ConsentSource,
    DataClass,
    DisclosureProposal,
    EgressChannel,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    PrivacyOutcome,
    ProviderBinding,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    MinimizedDisclosure,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
)
from yoetz.ports.semantic import Deadline, SemanticJudgment
from yoetz.protocol.models import DataCategory, SemanticReason, SemanticStatus

_NOW = datetime(2026, 7, 26, tzinfo=UTC)
_REQUEST = "req_30000000-0000-4000-8000-000000000001"
_TASK = "tsk_30000000-0000-4000-8000-000000000002"
_PROPOSAL = "ppr_30000000-0000-4000-8000-000000000003"
_CASE_DIGEST = "sha256:" + "c" * 64
_SUBJECT_DIGEST = "sha256:" + "d" * 64
_ITEM_DIGEST = "sha256:" + "e" * 64


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


class _Audit:
    def __init__(self, *, persist: bool) -> None:
        self.persist = persist
        self.receipt: LocalDisclosureReceipt | None = None
        self.authorize_calls = 0

    async def authorize(self, *args: object) -> object:
        self.authorize_calls += 1
        raise AssertionError("local semantic dispatch must not mint network authority")

    async def complete_local_disclosure(
        self, reservation_id: str, receipt: LocalDisclosureReceipt
    ) -> None:
        assert reservation_id == _PROPOSAL
        if not self.persist:
            raise RuntimeError("receipt_write_failed")
        self.receipt = receipt

    async def load(self, request_id: str, subject_digest: str) -> object | None:
        assert (request_id, subject_digest) == (_REQUEST, _SUBJECT_DIGEST)
        if self.receipt is None:
            return None
        return SimpleNamespace(receipt_id=self.receipt.receipt_id)


class _Gateway:
    def __init__(self) -> None:
        self.result = scripted_success(SemanticJudgment("no_material_discrepancy", ())).result
        self.calls = 0

    async def dispatch_local_semantic(self, case: object, deadline: Deadline) -> object:
        del case, deadline
        self.calls += 1
        return self.result


def _binding() -> ProviderBinding:
    return ProviderBinding(
        "local-test",
        "local/test-model",
        "local-af-unix",
        "1.0.0",
        "local_af_unix",
    )


def _scope() -> AuthorizationScope:
    return AuthorizationScope(
        AuthorizationScopeKind.TASK,
        "ins_30000000-0000-4000-8000-000000000004",
        "hmac-sha256:" + "f" * 64,
        _TASK,
    )


def _effective(binding: ProviderBinding) -> EffectivePrivacyPolicy:
    policy = replace(
        local_only_policy(),
        local_model_enabled=True,
        local_model_binding=binding,
        local_model_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        local_model_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
    )
    return EffectivePrivacyPolicy(policy, 1, policy.policy_digest)


def _candidate(binding: ProviderBinding) -> CandidateContext:
    return CandidateContext(
        _REQUEST,
        EgressChannel.LLM_INFERENCE,
        None,
        "semantic-review",
        _scope(),
        _SUBJECT_DIGEST,
        binding,
        (),
    )


def _proposal(binding: ProviderBinding) -> DisclosureProposal:
    return DisclosureProposal(
        _PROPOSAL,
        _REQUEST,
        _TASK,
        (_ITEM_DIGEST,),
        b"{}",
        (DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        (),
        (),
        _CASE_DIGEST,
        None,
        LocalDisclosureSink.LOCAL_MODEL,
        "semantic-review",
        _scope(),
        1,
        local_only_policy().policy_digest,
        2,
        1,
        _NOW + timedelta(minutes=1),
        "hmac-sha256:" + "a" * 64,
    )


def _minimized() -> MinimizedDisclosure:
    return MinimizedDisclosure(
        b"{}",
        ("case-packet",),
        (_ITEM_DIGEST,),
        (DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        (),
        (),
        2,
        1,
        _CASE_DIGEST,
        "semantic-local-test-v1",
        "sha256:" + "b" * 64,
        (),
    )


async def _dispatch(
    *, persist: bool, dispatch_guard: Callable[[], Awaitable[bool] | bool] | None = None
) -> tuple[object, _Audit, _Gateway]:
    binding = _binding()
    audit = _Audit(persist=persist)
    gateway = _Gateway()
    coordinator = PrivacyCoordinator(
        cast(PrivacyPolicyStorePort, object()),
        cast(PrivacyClassifierPort, object()),
        cast(PrivacyAuditPort, audit),
        cast(OutboundGatewayPort, gateway),
        cast(ClockPort, _Clock()),
        ready_composition.IdPort(),
    )
    coordinator._semantic_dispatch_guard = dispatch_guard  # pyright: ignore[reportPrivateUsage]
    result = await coordinator._dispatch_approved(  # pyright: ignore[reportPrivateUsage]
        _candidate(binding),
        _effective(binding),
        _proposal(binding),
        _minimized(),
        ConsentSource.BASELINE_POLICY,
        Deadline(_NOW + timedelta(minutes=1), 60.0),
        subject_digest=_SUBJECT_DIGEST,
    )
    return result, audit, gateway


@pytest.mark.anyio
async def test_local_semantic_success_persists_receipt_and_finalizes_local_provenance() -> None:
    result, audit, _gateway = await _dispatch(persist=True)

    assert isinstance(result, SemanticEgressSuccess)
    assert result.authorization_id is None
    assert result.dispatch_kind is SemanticDispatchKind.LOCAL_MODEL
    assert result.request_commitment is None
    assert audit.authorize_calls == 0
    assert audit.receipt is not None
    assert audit.receipt.sink is LocalDisclosureSink.LOCAL_MODEL
    assert audit.receipt.outcome is PrivacyOutcome.COMPLETED
    final = ready_composition._map_egress_to_final(  # pyright: ignore[reportPrivateUsage]
        result, ready_composition.IdPort()
    )
    assert final.status is SemanticStatus.SUCCEEDED
    assert final.provenance is not None
    assert final.provenance.dispatch_kind is SemanticDispatchKind.LOCAL_MODEL
    assert final.provenance.privacy_receipt_id == audit.receipt.receipt_id
    assert final.provenance.local_disclosure_reservation_id == _PROPOSAL
    assert final.provenance.egress_authorization_id is None
    assert final.provenance.request_commitment is None


@pytest.mark.anyio
async def test_local_semantic_success_fails_closed_when_receipt_is_not_durable() -> None:
    result, audit, _gateway = await _dispatch(persist=False)

    assert isinstance(result, SemanticEgressSuccess)
    assert audit.authorize_calls == 0
    final = ready_composition._map_egress_to_final(  # pyright: ignore[reportPrivateUsage]
        result, ready_composition.IdPort()
    )
    assert final.status is SemanticStatus.UNAVAILABLE
    assert final.reason is SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
    assert final.provenance is None


@pytest.mark.anyio
async def test_external_semantic_success_still_requires_authority_and_request_commitment() -> None:
    local, _audit, _gateway = await _dispatch(persist=True)
    assert isinstance(local, SemanticEgressSuccess)
    external = replace(
        local,
        authorization_id="aut_30000000-0000-4000-8000-000000000005",
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        request_commitment="hmac-sha256:" + "6" * 64,
    )

    final = ready_composition._map_egress_to_final(  # pyright: ignore[reportPrivateUsage]
        external, ready_composition.IdPort()
    )
    assert final.status is SemanticStatus.SUCCEEDED
    assert final.provenance is not None
    assert final.provenance.dispatch_kind is SemanticDispatchKind.EXTERNAL
    assert final.provenance.egress_authorization_id == external.authorization_id
    assert final.provenance.request_commitment == external.request_commitment
    assert final.provenance.local_disclosure_reservation_id is None

    unbound = ready_composition._map_egress_to_final(  # pyright: ignore[reportPrivateUsage]
        replace(external, request_commitment=None), ready_composition.IdPort()
    )
    assert unbound.status is SemanticStatus.UNAVAILABLE
    assert unbound.reason is SemanticReason.RECEIPT_PERSISTENCE_UNKNOWN
    assert unbound.provenance is None


@pytest.mark.anyio
async def test_local_semantic_rechecks_content_fence_before_provider_call() -> None:
    result, audit, gateway = await _dispatch(persist=True, dispatch_guard=lambda: False)

    assert isinstance(result, SemanticEgressBlocked)
    assert result.reason.value == "scope_mismatch"
    assert audit.receipt is None
    assert gateway.calls == 0
