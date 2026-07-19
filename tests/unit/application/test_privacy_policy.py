"""Provider-free privacy policy application tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from yoetz.application.privacy_policy import (
    GetPrivacyEffectiveRequest,
    GetPrivacyReceiptRequest,
    ListPrivacyReceiptsRequest,
    PrivacyPolicyApplication,
    ProposePrivacyPolicyRequest,
    TightenPrivacyPolicyRequest,
    privacy_get_effective,
    privacy_propose_policy,
    privacy_receipts_get,
    privacy_receipts_list,
    privacy_tighten_policy,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    DataClass,
    EgressChannel,
    PrivacyPolicy,
    PrivacyProfile,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    PolicyCommitResult,
    PreparedPolicyTransition,
    PrivacyReceiptAudience,
    PrivacyReceiptPage,
)
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import DataCategory

_NOW = datetime(2026, 7, 19, tzinfo=UTC)
_INSTALLATION = "ins_20000000-0000-4000-8000-000000000001"
_POLICY_ID = "pvy_20000000-0000-4000-8000-000000000002"
_PROPOSAL_ID = "ppr_20000000-0000-4000-8000-000000000003"
_RECEIPT_ID = "egr_20000000-0000-4000-8000-000000000004"
_DIGEST = f"sha256:{'3' * 64}"
_NEW_DIGEST = f"sha256:{'4' * 64}"


def _scope() -> AuthorizationScope:
    return AuthorizationScope(AuthorizationScopeKind.MACHINE, _INSTALLATION)


def _disabled(channel: EgressChannel) -> ChannelPolicy:
    return ChannelPolicy(
        channel,
        False,
        (),
        (),
        None,
        (),
        AuthorizationScopeKind.MACHINE,
        False,
        0,
        0,
        0,
    )


def _policy(
    *, digest: str = _DIGEST, agent_categories: tuple[DataCategory, ...] = ()
) -> PrivacyPolicy:
    return PrivacyPolicy(
        _POLICY_ID,
        1,
        digest,
        PrivacyProfile.LOCAL_ONLY,
        ReviewContextProfile.STRUCTURAL,
        ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        False,
        False,
        _scope(),
        tuple(_disabled(channel) for channel in sorted(EgressChannel, key=lambda c: c.value)),
        False,
        None,
        (),
        (),
        agent_categories,
        (DataClass.PUBLIC_STRUCTURAL,),
        tuple(DataCategory),
        (DataClass.ORDINARY_USER_CONTENT, DataClass.PUBLIC_STRUCTURAL),
        _NOW,
    )


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids:
    def new(self, kind: IdKind) -> str:
        assert kind is IdKind.PRIVACY_PROPOSAL
        return _PROPOSAL_ID


class _Store:
    def __init__(self, policy: PrivacyPolicy) -> None:
        self.effective = EffectivePrivacyPolicy(policy, 5, policy.policy_digest)
        self.prepared: object | None = None
        self.tightened: object | None = None

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        assert scope == self.effective.policy.effective_scope
        return self.effective

    async def prepare_transition(self, proposal: object) -> PreparedPolicyTransition:
        self.prepared = proposal
        return PreparedPolicyTransition(proposal, _NEW_DIGEST, _DIGEST, True)  # type: ignore[arg-type]

    async def tighten(
        self, scope: AuthorizationScope, overlay: object, expected_policy_digest: str
    ) -> PolicyCommitResult:
        self.tightened = (scope, overlay, expected_policy_digest)
        policy = replace(self.effective.policy, policy_digest=_NEW_DIGEST)
        return PolicyCommitResult(policy, 6, 2, 1)


class _Audit:
    def __init__(self) -> None:
        self.calls: list[tuple[object, PrivacyReceiptAudience]] = []

    async def list_receipts(
        self, query: object, audience: PrivacyReceiptAudience
    ) -> PrivacyReceiptPage:
        self.calls.append((query, audience))
        return PrivacyReceiptPage(1, (), None)

    async def get_receipt(self, receipt_id: str, audience: PrivacyReceiptAudience) -> None:
        self.calls.append((receipt_id, audience))
        return None


class _Gateway:
    def __init__(self) -> None:
        self.revoked: list[int] = []

    async def close_revoked(self, generation: int) -> None:
        self.revoked.append(generation)


def _app(policy: PrivacyPolicy) -> PrivacyPolicyApplication:
    return PrivacyPolicyApplication(
        _Store(policy),  # type: ignore[arg-type]
        _Audit(),  # type: ignore[arg-type]
        _Gateway(),  # type: ignore[arg-type]
        _Clock(),
        _Ids(),
        _scope(),
    )


def test_effective_and_receipt_inspection_are_read_only_port_delegations() -> None:
    app = _app(_policy())

    async def run() -> tuple[EffectivePrivacyPolicy, PrivacyReceiptPage, object]:
        effective = await privacy_get_effective(app, GetPrivacyEffectiveRequest(_scope()))
        page = await privacy_receipts_list(app, ListPrivacyReceiptsRequest())
        missing = await privacy_receipts_get(app, GetPrivacyReceiptRequest(_RECEIPT_ID))
        return effective, page, missing

    effective, page, missing = asyncio.run(run())

    assert effective.generation == 5
    assert page.receipts == () and missing is None
    assert app.audit.calls[0][1] is PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL  # type: ignore[attr-defined]


def test_policy_expansion_creates_typed_persisted_proposal() -> None:
    app = _app(_policy())
    expanded = _policy(
        digest=_NEW_DIGEST,
        agent_categories=(DataCategory.FINDING_SUMMARY,),
    )

    result = asyncio.run(
        privacy_propose_policy(app, ProposePrivacyPolicyRequest(_DIGEST, expanded))
    )

    assert result.privacy_proposal_id == _PROPOSAL_ID  # type: ignore[union-attr]
    assert app.policy_store.prepared is not None  # type: ignore[attr-defined]


def test_tightening_uses_expected_digest_cas_and_closes_old_generation() -> None:
    current = _policy(agent_categories=(DataCategory.FINDING_SUMMARY,))
    app = _app(current)
    tightened = _policy(digest=_NEW_DIGEST)

    result = asyncio.run(
        privacy_tighten_policy(app, TightenPrivacyPolicyRequest(_DIGEST, tightened))
    )

    assert result.generation == 6
    assert result.revoked_authorization_count == 2
    assert app.gateway.revoked == [5]  # type: ignore[attr-defined]
