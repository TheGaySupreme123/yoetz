"""The production human-effects seam must serve per-request disclosure ceremonies."""

# pyright: reportArgumentType=false, reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from builders.privacy_policies import minimal_external_policy
from yoetz.application.privacy_policy import PrivacyPolicyApplication
from yoetz.domain.privacy import DisclosureProposal, HumanPrivacyDecision
from yoetz.ports.privacy import EffectivePrivacyPolicy
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.models import DataCategory
from yoetz.service.confidential_protocol import (
    ClientOpenEnvelope,
    HumanCeremonyKind,
    PrivacyDecisionResult,
    PrivacyDisclosureDecisionPreview,
    PrivacyPendingTarget,
)
from yoetz.service.daemon import _LockedHumanEffects, _PrivacyPolicyAppRelay

_NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
_REQUEST_ID = "req_30000000-0000-4000-8000-000000000001"
_TASK_ID = "tsk_30000000-0000-4000-8000-000000000002"
_PROPOSAL_ID = "ppr_30000000-0000-4000-8000-000000000003"
_CASE_DIGEST = "sha256:" + "c" * 64
_COMMITMENT = "hmac-sha256:" + "d" * 64


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW


class _PolicyStore:
    def __init__(self) -> None:
        policy = minimal_external_policy()
        self.effective = EffectivePrivacyPolicy(policy, 7, policy.policy_digest)

    async def effective_policy(self, scope: object) -> EffectivePrivacyPolicy:
        assert scope == self.effective.policy.effective_scope
        return self.effective


class _Audit:
    def __init__(self, proposal: DisclosureProposal) -> None:
        self.proposal = proposal
        self.decisions: list[HumanPrivacyDecision] = []

    async def load_disclosure_proposal(self, proposal_id: str) -> DisclosureProposal | None:
        return self.proposal if proposal_id == self.proposal.privacy_proposal_id else None

    async def record_human_decision(
        self, proposal_id: str, decision: HumanPrivacyDecision
    ) -> None:
        assert proposal_id == self.proposal.privacy_proposal_id
        self.decisions.append(decision)


class _Mac:
    def mac(self, domain: bytes, value: bytes) -> str:
        assert domain == b"yoetz/privacy-audit/local-approval/v1\x00"
        assert value == _CASE_DIGEST.encode("ascii")
        return "hmac-sha256:" + "e" * 64


class _Vault:
    ready = True
    mode = SimpleNamespace(value="passphrase")

    def installation_mac_handle(self, purpose: object) -> _Mac:
        del purpose
        return _Mac()


def _effects() -> tuple[_LockedHumanEffects, _Audit]:
    store = _PolicyStore()
    policy = store.effective.policy
    binding = next(
        channel.provider_binding
        for channel in policy.channel_policies
        if channel.provider_binding is not None
    )
    proposal = DisclosureProposal(
        _PROPOSAL_ID,
        _REQUEST_ID,
        _TASK_ID,
        (),
        b'{"claim":"bounded"}',
        (DataCategory.CLAIM_TEXT,),
        (),
        (),
        _CASE_DIGEST,
        binding,
        None,
        "semantic-review",
        policy.effective_scope,
        policy.version,
        policy.policy_digest,
        19,
        5,
        _NOW + timedelta(minutes=5),
        _COMMITMENT,
    )
    audit = _Audit(proposal)
    app = PrivacyPolicyApplication(
        cast(object, store),
        cast(object, audit),
        cast(object, SimpleNamespace()),
        cast(object, _Clock()),
        cast(object, SimpleNamespace()),
        policy.effective_scope,
    )
    relay = _PrivacyPolicyAppRelay()
    relay.bind(lambda: app)
    return _LockedHumanEffects(cast(object, SimpleNamespace()), cast(object, _Vault()), relay), audit


@pytest.mark.anyio
async def test_disclosure_preview_and_approval_use_the_durable_proposal() -> None:
    effects, audit = _effects()
    target = PrivacyPendingTarget("disclosure", _PROPOSAL_ID)

    preview, target_digest, generation = await effects.prepare(
        ClientOpenEnvelope(
            "1" * 64,
            HumanCeremonyKind.PRIVACY_DISCLOSURE_DECISION,
            target,
        )
    )

    assert type(preview) is PrivacyDisclosureDecisionPreview
    assert preview.excerpt_preview == '{"claim":"bounded"}'
    assert preview.category == "claim-text"
    assert preview.excerpt_digest == _CASE_DIGEST
    assert generation == 7
    assert target_digest == canonical_digest(
        {"decision_kind": "disclosure", "kind": "privacy_pending", "pending_id": _PROPOSAL_ID}
    )

    result = await effects.decide_privacy(target, "approve", None, 1.0)

    assert result == PrivacyDecisionResult("committed", _CASE_DIGEST)
    assert len(audit.decisions) == 1
    assert audit.decisions[0].approved is True
    assert audit.decisions[0].accepted_diff_digest == _CASE_DIGEST
