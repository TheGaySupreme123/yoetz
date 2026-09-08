"""Failure-matrix coverage for resuming one-use semantic disclosure authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from builders.privacy_policies import minimal_external_policy
from yoetz.application.egress import (
    PrivacyCoordinator,
    SemanticEgressAwaitingHuman,
    SemanticEgressBlocked,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ConsentSource,
    DisclosureProposal,
    PrivacyOutcome,
    PrivacyProfile,
    PrivacyReason,
    ProviderBinding,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyAuditReservation,
    PrivacyAuditState,
    PrivacyClassifierPort,
    PrivacyPolicyStorePort,
    RepositoryPrivacyAuthority,
)
from yoetz.ports.semantic import Deadline
from yoetz.protocol.ids import IdKind

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_INSTALLATION = "ins_54000000-0000-4000-8000-000000000001"
_TASK = "tsk_54000000-0000-4000-8000-000000000002"
_REQUEST = "req_54000000-0000-4000-8000-000000000003"
_PROPOSAL = "ppr_54000000-0000-4000-8000-000000000004"
_WORKSPACE = "hmac-sha256:" + "5" * 64
_CASE_DIGEST = "sha256:" + "6" * 64
_AUTHORITY_DIGEST = "sha256:" + "8" * 64


class _Clock:
    def __init__(self, now: datetime = _NOW) -> None:
        self.now = now

    def now_utc(self) -> datetime:
        return self.now

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids:
    def __init__(self) -> None:
        self.next_value = 0

    def new(self, kind: IdKind) -> str:
        self.next_value += 1
        prefix = "out_" if kind is IdKind.OUTBOUND_CASE else "egr_"
        return f"{prefix}54000000-0000-4000-8000-{self.next_value:012x}"


def _scope() -> AuthorizationScope:
    return AuthorizationScope(
        AuthorizationScopeKind.TASK,
        _INSTALLATION,
        _WORKSPACE,
        _TASK,
    )


def _binding() -> ProviderBinding:
    policy = minimal_external_policy()
    binding = next(
        channel.provider_binding
        for channel in policy.channel_policies
        if channel.provider_binding is not None
    )
    return binding


def _effective() -> EffectivePrivacyPolicy:
    base = minimal_external_policy()
    channels = tuple(
        replace(channel, preview_required=True) if channel.provider_binding is not None else channel
        for channel in base.channel_policies
    )
    policy = replace(
        base,
        profile=PrivacyProfile.CONFIRM_EVERY_REQUEST,
        effective_scope=_scope(),
        channel_policies=channels,
    )
    return EffectivePrivacyPolicy(policy, 2, policy.policy_digest)


def _proposal(*, expires_at: datetime) -> DisclosureProposal:
    policy = _effective().policy
    return DisclosureProposal(
        _PROPOSAL,
        _REQUEST,
        _TASK,
        ("sha256:" + "9" * 64,),
        b"{}",
        (),
        (),
        (),
        _CASE_DIGEST,
        _binding(),
        None,
        "semantic-review",
        _scope(),
        policy.version,
        policy.policy_digest,
        2,
        1,
        expires_at,
        "hmac-sha256:" + "a" * 64,
    )


class _Audit:
    def __init__(self, status: str, proposal: DisclosureProposal) -> None:
        self.status = status
        self.proposal = proposal

    async def load(self, request_id: str, subject_digest: str) -> PrivacyAuditState | None:
        assert (request_id, subject_digest) == (_REQUEST, _CASE_DIGEST)
        reservation = PrivacyAuditReservation(
            _PROPOSAL,
            _REQUEST,
            _CASE_DIGEST,
            self.status,
            2,
            _NOW,
        )
        return PrivacyAuditState(reservation, self.status)

    async def load_disclosure_proposal(self, proposal_id: str) -> DisclosureProposal | None:
        assert proposal_id == _PROPOSAL
        return self.proposal


class _Policies:
    def __init__(self, *, granted: bool) -> None:
        self.granted = granted
        self.effective = _effective()

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        assert scope == _scope()
        return self.effective

    async def repository_authority(self, scope: AuthorizationScope) -> RepositoryPrivacyAuthority:
        assert scope == _scope()
        policy = self.effective.policy
        grant_policy = replace(
            policy,
            effective_scope=AuthorizationScope(
                AuthorizationScopeKind.WORKSPACE,
                _INSTALLATION,
                _WORKSPACE,
            ),
        )
        return RepositoryPrivacyAuthority(
            scope,
            self.effective,
            _WORKSPACE,
            "granted" if self.granted else "missing",
            "not_applicable",
            _AUTHORITY_DIGEST,
            (),
            2 if self.granted else None,
            grant_policy.policy_digest if self.granted else None,
            grant_policy if self.granted else None,
        )


class _Gateway:
    def __init__(self) -> None:
        self.reconciliations = 0

    async def reconcile_repository_policy(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.reconciliations += 1
        return object()

    async def close(self) -> None:
        return None


def _coordinator(
    status: str,
    *,
    expires_at: datetime,
    granted: bool = True,
    clock: _Clock | None = None,
) -> PrivacyCoordinator:
    return PrivacyCoordinator(
        cast(PrivacyPolicyStorePort, _Policies(granted=granted)),
        cast(PrivacyClassifierPort, object()),
        cast(PrivacyAuditPort, _Audit(status, _proposal(expires_at=expires_at))),
        cast(OutboundGatewayPort, _Gateway()),
        cast(ClockPort, clock or _Clock()),
        cast(IdPort, _Ids()),
        human_authority=HumanAuthorityCapability(
            "established_passphrase",
            "sha256:" + "b" * 64,
            1,
            "passphrase",
            1,
            True,
        ),
    )


def _deadline() -> Deadline:
    return Deadline(_NOW + timedelta(minutes=10), 600.0)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ("reserved", "awaiting_human"))
async def test_unanswered_or_crash_before_wait_marking_remains_nonterminal(status: str) -> None:
    coordinator = _coordinator(status, expires_at=_NOW + timedelta(minutes=5))

    result = await coordinator.resume(_REQUEST, _CASE_DIGEST, _deadline())

    assert isinstance(result, SemanticEgressAwaitingHuman)
    assert result.privacy_proposal_id == _PROPOSAL
    assert result.expires_at == _NOW + timedelta(minutes=5)


@pytest.mark.anyio
async def test_expired_wait_terminalizes_without_dispatch() -> None:
    coordinator = _coordinator(
        "awaiting_human",
        expires_at=_NOW - timedelta(seconds=1),
    )

    result = await coordinator.resume(_REQUEST, _CASE_DIGEST, _deadline())

    assert isinstance(result, SemanticEgressBlocked)
    assert result.outcome is PrivacyOutcome.APPROVAL_EXPIRED
    assert result.reason is PrivacyReason.AUTHORIZATION_EXPIRED


@pytest.mark.anyio
async def test_recorded_denial_terminalizes_without_dispatch() -> None:
    coordinator = _coordinator(
        "decision_receipt_pending",
        expires_at=_NOW + timedelta(minutes=5),
    )

    result = await coordinator.resume(_REQUEST, _CASE_DIGEST, _deadline())

    assert isinstance(result, SemanticEgressBlocked)
    assert result.outcome is PrivacyOutcome.HUMAN_DENIED
    assert result.reason is PrivacyReason.HUMAN_DENIED


@pytest.mark.anyio
async def test_stale_repository_authority_fails_closed_before_dispatch() -> None:
    coordinator = _coordinator(
        "approved",
        expires_at=_NOW + timedelta(minutes=5),
        granted=False,
    )

    result = await coordinator.resume(_REQUEST, _CASE_DIGEST, _deadline())

    assert isinstance(result, SemanticEgressBlocked)
    assert result.outcome is PrivacyOutcome.BLOCKED_BY_POLICY
    assert result.reason is PrivacyReason.SCOPE_MISMATCH


@pytest.mark.anyio
async def test_approved_resume_preserves_per_request_human_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = _coordinator(
        "approved",
        expires_at=_NOW + timedelta(minutes=5),
    )
    captured: list[ConsentSource] = []

    async def dispatch(self: PrivacyCoordinator, *args: object, **kwargs: object) -> object:
        del self, kwargs
        captured.append(cast(ConsentSource, args[4]))
        return object()

    monkeypatch.setattr(PrivacyCoordinator, "_dispatch_approved", dispatch)

    result = await coordinator.resume(_REQUEST, _CASE_DIGEST, _deadline())

    assert result is not None
    assert captured == [ConsentSource.PER_REQUEST_LOCAL_HUMAN]
