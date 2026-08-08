"""Provider-free privacy policy application tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from yoetz.adapters.memory.privacy import MemoryPrivacyCatalogState, MemoryPrivacyPolicyStore
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
_REPOSITORY = f"hmac-sha256:{'5' * 64}"
_REPOSITORY_POLICY_ID = "pvy_20000000-0000-4000-8000-000000000005"


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
        if kind is IdKind.PRIVACY_PROPOSAL:
            return _PROPOSAL_ID
        if kind is IdKind.PRIVACY_POLICY:
            return _REPOSITORY_POLICY_ID
        raise AssertionError(kind)


class _Store:
    def __init__(self, policy: PrivacyPolicy) -> None:
        self.effective = EffectivePrivacyPolicy(policy, 5, policy.policy_digest)
        self.prepared: object | None = None
        self.tightened: object | None = None
        self.events: list[str] = []

    async def effective_policy(self, scope: AuthorizationScope) -> EffectivePrivacyPolicy:
        assert scope == self.effective.policy.effective_scope
        return self.effective

    async def prepare_transition(self, proposal: object) -> PreparedPolicyTransition:
        self.prepared = proposal
        return PreparedPolicyTransition(proposal, _NEW_DIGEST, _DIGEST, True)  # type: ignore[arg-type]

    async def tighten(
        self, scope: AuthorizationScope, overlay: object, expected_policy_digest: str
    ) -> PolicyCommitResult:
        self.events.append("store_tighten")
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
    def __init__(self, events: list[str]) -> None:
        self.revoked: list[int] = []
        self._events = events

    async def close_revoked(self, generation: int) -> None:
        self._events.append("gateway_close")
        self.revoked.append(generation)

    @asynccontextmanager
    async def authority_mutation_fence(self) -> AsyncGenerator[None]:
        self._events.append("gateway_fence_enter")
        try:
            yield
        finally:
            self._events.append("gateway_fence_exit")


def _app(policy: PrivacyPolicy) -> PrivacyPolicyApplication:
    store = _Store(policy)
    return PrivacyPolicyApplication(
        store,  # type: ignore[arg-type]
        _Audit(),  # type: ignore[arg-type]
        _Gateway(store.events),  # type: ignore[arg-type]
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
    assert app.gateway.revoked == []  # type: ignore[attr-defined]
    assert app.policy_store.events == [  # type: ignore[attr-defined]
        "gateway_fence_enter",
        "store_tighten",
        "gateway_fence_exit",
    ]


def test_missing_repository_private_candidate_inserts_without_human_ceremony() -> None:
    state = MemoryPrivacyCatalogState()
    store = MemoryPrivacyPolicyStore(state, _Clock())
    repository_scope = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE, _INSTALLATION, _REPOSITORY
    )
    app = PrivacyPolicyApplication(
        store,
        _Audit(),  # type: ignore[arg-type]
        _Gateway([]),  # type: ignore[arg-type]
        _Clock(),
        _Ids(),
        _scope(),
    )

    async def run() -> tuple[object, object]:
        await store.seed_if_absent(_policy())
        authority = await store.repository_authority(repository_scope)
        result = await privacy_propose_policy(
            app,
            ProposePrivacyPolicyRequest(
                authority.effective.effective_digest,
                _policy(),
                authority.authority_digest,
                repository_scope,
            ),
        )
        return result, await store.repository_authority(repository_scope)

    result, authority = asyncio.run(run())
    assert result.generation == 2  # type: ignore[union-attr]
    assert authority.grant_state == "granted"  # type: ignore[attr-defined]
    assert state.transitions == {}


def test_v1_workspace_candidate_requires_trusted_repository_context() -> None:
    workspace_candidate = replace(
        _policy(digest=_NEW_DIGEST),
        effective_scope=AuthorizationScope(
            AuthorizationScopeKind.WORKSPACE, _INSTALLATION, _REPOSITORY
        ),
    )

    with pytest.raises(ValueError, match="repository_privacy_context_required"):
        asyncio.run(
            privacy_propose_policy(
                _app(_policy()), ProposePrivacyPolicyRequest(_DIGEST, workspace_candidate)
            )
        )


def test_v1_workspace_tightening_requires_trusted_repository_context() -> None:
    workspace_candidate = replace(
        _policy(digest=_NEW_DIGEST),
        effective_scope=AuthorizationScope(
            AuthorizationScopeKind.WORKSPACE, _INSTALLATION, _REPOSITORY
        ),
    )

    with pytest.raises(ValueError, match="repository_privacy_context_required"):
        asyncio.run(
            privacy_tighten_policy(
                _app(_policy()), TightenPrivacyPolicyRequest(_DIGEST, workspace_candidate)
            )
        )
