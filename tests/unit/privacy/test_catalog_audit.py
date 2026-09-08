"""Durable provider-free privacy catalog tests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import apsw
import pytest

from builders.privacy_policies import minimal_external_policy
from yoetz.adapters.memory.privacy import (
    MemoryPrivacyAudit,
    MemoryPrivacyCatalogState,
    MemoryPrivacyPolicyStore,
)
from yoetz.adapters.privacy.catalog import CatalogPrivacyAudit, CatalogPrivacyPolicyStore
from yoetz.adapters.privacy.local_enforcer import LocalPrivacyEnforcer
from yoetz.application.egress import PrivacyCoordinator
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ChannelPolicy,
    ConsentSource,
    DataClass,
    EgressChannel,
    LocalDisclosureApproved,
    LocalDisclosureReceipt,
    LocalDisclosureSink,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    ProjectionAuditContext,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import format_rfc3339_millis
from yoetz.ports.objects import (
    ObjectMetadata,
    ObjectRef,
    ObjectSource,
    ObjectStorePort,
    StagedObject,
)
from yoetz.ports.privacy import (
    AgentProjectionRequest,
    CompletedAgentProjection,
    DisclosureProposalRequest,
    HumanPolicyDecision,
    MinimizedDisclosure,
    PolicyOverlay,
    PolicyTransitionMember,
    PolicyTransitionProposal,
    PrivacyReceiptAudience,
    PrivacyReceiptPage,
    PrivacyReceiptQuery,
    PrivacyReceiptView,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import DataCategory

_NOW = datetime(2026, 7, 19, tzinfo=UTC)
_INSTALLATION = "ins_30000000-0000-4000-8000-000000000001"
_REQUEST = "req_30000000-0000-4000-8000-000000000002"
_RPC = "rpc_30000000-0000-4000-8000-000000000003"
_SERVICE = "svc_30000000-0000-4000-8000-000000000004"
_PROPOSAL = "ppr_30000000-0000-4000-8000-000000000005"
_POLICY = "pvy_30000000-0000-4000-8000-000000000006"
_RECEIPT = "egr_30000000-0000-4000-8000-000000000007"
_DIGEST = f"sha256:{'3' * 64}"
_REQUEST_2 = "req_30000000-0000-4000-8000-000000000008"
_PROPOSAL_2 = "ppr_30000000-0000-4000-8000-000000000009"
_RECEIPT_2 = "egr_30000000-0000-4000-8000-00000000000a"
_TASK = "tsk_30000000-0000-4000-8000-00000000000b"
_SESSION = "ses_30000000-0000-4000-8000-00000000000c"
_OBJECT = "obj_30000000-0000-4000-8000-00000000000d"
_ROUTE_DIGEST = f"sha256:{'5' * 64}"
_WORKSPACE = f"hmac-sha256:{'6' * 64}"


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


class _Key:
    def mac(self, domain: bytes, message: bytes) -> str:
        digest = hmac.new(b"k" * 32, domain + message, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"


class _UnusedObjects:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"object store used by objectless projection: {name}")


class _Objects:
    async def stage(self, source: ObjectSource, metadata: ObjectMetadata) -> StagedObject:
        assert source.data is not None
        return StagedObject(
            _OBJECT,
            len(source.data),
            f"hmac-sha256:{'7' * 64}",
            f"sha256:{'8' * 64}",
            "yoetz-object/1",
            "privacy-slot",
            metadata,
            object(),
        )

    async def finalize(self, staged: StagedObject) -> ObjectRef:
        return ObjectRef(
            staged.object_id,
            staged.plaintext_size,
            staged.commitment,
            staged.envelope_digest,
            staged.encryption_format,
            staged.key_slot,
            staged.metadata,
        )


class _Ids:
    def new(self, kind: IdKind) -> str:
        return _PROPOSAL if kind is IdKind.PRIVACY_PROPOSAL else _RECEIPT


class _Gateway:
    async def close(self) -> None:
        return None


def _database() -> apsw.Connection:
    db = apsw.Connection(":memory:")
    for version in ("0001", "0002", "0003"):
        db.execute(Path(f"migrations/catalog/{version}.sql").read_text(encoding="utf-8"))
    return db


def _scope() -> AuthorizationScope:
    return AuthorizationScope(AuthorizationScopeKind.MACHINE, _INSTALLATION)


def _task_scope() -> AuthorizationScope:
    return AuthorizationScope(AuthorizationScopeKind.TASK, _INSTALLATION, _WORKSPACE, _TASK)


def _receipt() -> LocalDisclosureReceipt:
    scope = _scope()
    return LocalDisclosureReceipt(
        "1.0.0",
        _RECEIPT,
        _REQUEST,
        _PROPOSAL,
        LocalDisclosureSink.AGENT_CONTEXT,
        PrivacyOutcome.COMPLETED,
        _NOW,
        scope,
        "client_result_projection",
        ReceiptPolicyBinding(_POLICY, 1, _DIGEST, canonical_digest({"scope": "machine"})),
        ConsentSource.BASELINE_POLICY,
        (),
        (),
        ReceiptCounts(0, 0, 0, 0, 0, 0, 2, 0, None),
        ReceiptTransformations(0, 0, 0),
        ReceiptSecretScan("scanner-v1", _DIGEST, 0, True),
        None,
        1,
    )


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


def _policy(*, version: int = 1, digest: str = _DIGEST) -> PrivacyPolicy:
    return PrivacyPolicy(
        _POLICY,
        version,
        digest,
        PrivacyProfile.LOCAL_ONLY,
        ReviewContextProfile.STRUCTURAL,
        ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        False,
        False,
        _scope(),
        tuple(_disabled(channel) for channel in sorted(EgressChannel, key=lambda item: item.value)),
        False,
        None,
        (),
        (),
        (DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        (DataClass.PUBLIC_STRUCTURAL,),
        tuple(DataCategory),
        (DataClass.ORDINARY_USER_CONTENT, DataClass.PUBLIC_STRUCTURAL),
        _NOW,
        None if version == 1 else _DIGEST,
    )


def _projection_request(
    proposal_id: str = _PROPOSAL, request_id: str = _REQUEST
) -> AgentProjectionRequest:
    empty = canonical_encode({})
    return AgentProjectionRequest(
        proposal_id,
        request_id,
        _RPC,
        "check",
        _SERVICE,
        1,
        None,
        empty,
        _scope(),
        None,
        None,
        _POLICY,
        1,
        1,
        _DIGEST,
        LocalDisclosureSink.AGENT_CONTEXT,
        (),
        empty,
        empty,
        (),
        0,
        0,
        0,
        _NOW,
    )


def test_objectless_projection_is_atomically_completed_and_queryable() -> None:
    db = _database()
    audit = CatalogPrivacyAudit(db, _UnusedObjects(), _Key(), _Clock())  # type: ignore[arg-type]
    request = _projection_request()

    async def run() -> tuple[
        CompletedAgentProjection, PrivacyReceiptView | None, PrivacyReceiptPage
    ]:
        completed = await audit.complete_agent_projection(request, _receipt())
        fetched = await audit.get_receipt(_RECEIPT, PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL)
        page = await audit.list_receipts(
            PrivacyReceiptQuery(), PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
        )
        return completed, fetched, page

    completed, fetched, page = asyncio.run(run())

    assert completed.subject.projection_commitment.startswith("hmac-sha256:")
    assert fetched is not None
    assert page.receipts == (fetched,)
    row = db.execute(
        "SELECT state, content_object_id, receipt_id FROM privacy_audit_records"
    ).fetchone()
    assert row == ("local_disclosure_completed", None, _RECEIPT)


def test_policy_store_seed_effective_and_tightening_are_generation_cas() -> None:
    db = _database()
    store = CatalogPrivacyPolicyStore(db, _Clock())
    seed = _policy()
    tightened = _policy(version=2, digest=f"sha256:{'4' * 64}")
    overlay = PolicyOverlay(
        tightened.effective_scope,
        tightened.review_selection,
        tightened.require_current_provider_data_use_evidence,
        tightened.channel_policies,
        tightened.local_model_categories,
        tightened.local_model_data_classes,
        tightened.agent_context_categories,
        tightened.agent_context_data_classes,
        tightened,
    )

    async def run() -> tuple[object, object, object]:
        seeded = await store.seed_if_absent(seed)
        effective = await store.effective_policy(_scope())
        committed = await store.tighten(_scope(), overlay, seed.policy_digest)
        return seeded, effective, committed

    seeded, effective, committed = asyncio.run(run())

    assert seeded == seed
    assert effective.policy == seed and effective.generation == 1  # type: ignore[attr-defined]
    assert committed.policy == tightened and committed.generation == 2  # type: ignore[attr-defined]
    assert db.execute(
        "SELECT state FROM privacy_policy_versions ORDER BY policy_generation"
    ).fetchall() == [("superseded",), ("current",)]


@pytest.mark.parametrize("approved", [False, True])
def test_insert_only_repository_transition_load_commit_and_replay_are_exact(
    approved: bool,
) -> None:
    db = _database()
    store = CatalogPrivacyPolicyStore(db, _Clock())
    machine = _policy()
    repository_scope = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE, _INSTALLATION, _WORKSPACE
    )
    repository = replace(
        machine,
        policy_id="pvy_30000000-0000-4000-8000-000000000010",
        policy_digest=f"sha256:{'7' * 64}",
        effective_scope=repository_scope,
    )

    async def run() -> tuple[object, object, object]:
        await store.seed_if_absent(machine)
        authority = await store.repository_authority(repository_scope)
        proposal = PolicyTransitionProposal(
            repository_scope,
            authority.effective.generation,
            repository,
            canonical_digest({"candidate": repository.policy_digest}),
            _NOW,
            _NOW + timedelta(seconds=60),
            _PROPOSAL,
            authority.effective.effective_digest,
            authority.authority_digest,
            (PolicyTransitionMember("insert", repository_scope, repository, None, None),),
        )
        prepared = await store.prepare_transition(proposal)
        assert await store.prepare_transition(proposal) == prepared
        loaded = await store.load_pending_transition(_PROPOSAL)
        decision = HumanPolicyDecision(
            prepared.prepared_digest,
            approved,
            _NOW + timedelta(seconds=1),
            f"hmac-sha256:{'8' * 64}",
        )
        committed = await store.commit_transition(loaded, decision)
        restarted = CatalogPrivacyPolicyStore(db, _Clock())
        terminal = await restarted.load_transition(_PROPOSAL)
        replayed = await restarted.commit_transition(
            terminal,
            replace(decision, decided_at=decision.decided_at + timedelta(seconds=1)),
        )
        with pytest.raises(ValueError, match="privacy_policy_decision_mismatch"):
            await restarted.commit_transition(terminal, replace(decision, approved=not approved))
        return loaded, committed, replayed

    loaded, committed, replayed = asyncio.run(run())
    assert loaded.proposal.members[0].action == "insert"  # type: ignore[attr-defined]
    assert committed == replayed
    assert replayed.replayed is True  # type: ignore[attr-defined]
    assert committed.policy == (repository if approved else machine)  # type: ignore[attr-defined]
    transition = db.execute(
        "SELECT state, terminal_result_digest FROM privacy_policy_transitions"
    ).fetchone()
    assert transition is not None and transition[0] == ("committed" if approved else "denied")
    assert type(transition[1]) is str

    db.execute(
        "UPDATE privacy_policy_transitions SET terminal_result_canonical = ? WHERE proposal_id = ?",
        (b"{}", _PROPOSAL),
    )
    with pytest.raises(ValueError, match="privacy_policy_terminal_result_corrupt"):
        asyncio.run(
            CatalogPrivacyPolicyStore(db, _Clock()).commit_transition(
                asyncio.run(CatalogPrivacyPolicyStore(db, _Clock()).load_transition(_PROPOSAL)),
                HumanPolicyDecision(
                    loaded.prepared_digest,  # type: ignore[attr-defined]
                    approved,
                    _NOW + timedelta(seconds=3),
                    f"hmac-sha256:{'8' * 64}",
                ),
            )
        )


def test_v2_terminal_transition_migrates_to_stable_replay_identity() -> None:
    db = _database()
    store = CatalogPrivacyPolicyStore(db, _Clock())
    machine = _policy()
    repository_scope = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE, _INSTALLATION, _WORKSPACE
    )
    repository = replace(
        machine,
        policy_id="pvy_30000000-0000-4000-8000-000000000010",
        policy_digest=f"sha256:{'7' * 64}",
        effective_scope=repository_scope,
    )
    original_at = _NOW + timedelta(seconds=1)

    async def commit() -> HumanPolicyDecision:
        await store.seed_if_absent(machine)
        authority = await store.repository_authority(repository_scope)
        proposal = PolicyTransitionProposal(
            repository_scope,
            authority.effective.generation,
            repository,
            canonical_digest({"candidate": repository.policy_digest}),
            _NOW,
            _NOW + timedelta(seconds=60),
            _PROPOSAL,
            authority.effective.effective_digest,
            authority.authority_digest,
            (PolicyTransitionMember("insert", repository_scope, repository, None, None),),
        )
        prepared = await store.prepare_transition(proposal)
        decision = HumanPolicyDecision(
            prepared.prepared_digest,
            True,
            original_at,
            f"hmac-sha256:{'8' * 64}",
        )
        await store.commit_transition(prepared, decision)
        return decision

    decision = asyncio.run(commit())
    legacy_digest = canonical_digest(
        {
            "approved": True,
            "authority_commitment": decision.authority_commitment,
            "decided_at": format_rfc3339_millis(original_at),
            "prepared_digest": decision.prepared_digest,
        }
    )
    db.execute(
        "UPDATE privacy_policy_transitions SET decision_digest = ?, decision_at = NULL, "
        "terminal_result_version = NULL, terminal_at = ? WHERE proposal_id = ?",
        (legacy_digest, format_rfc3339_millis(original_at), _PROPOSAL),
    )

    async def replay() -> object:
        restarted = CatalogPrivacyPolicyStore(db, _Clock())
        loaded = await restarted.load_transition(_PROPOSAL)
        return await restarted.commit_transition(
            loaded, replace(decision, decided_at=original_at + timedelta(seconds=10))
        )

    replayed = asyncio.run(replay())
    assert replayed.replayed is True  # type: ignore[attr-defined]
    migrated = db.execute(
        "SELECT terminal_result_version, decision_at FROM privacy_policy_transitions "
        "WHERE proposal_id = ?",
        (_PROPOSAL,),
    ).fetchone()
    assert migrated == (1, format_rfc3339_millis(original_at))


def test_memory_and_sqlite_projection_replay_and_cursor_behavior_match() -> None:
    db = _database()
    unused_objects = cast(ObjectStorePort, _UnusedObjects())
    sqlite = CatalogPrivacyAudit(db, unused_objects, _Key(), _Clock())
    memory = MemoryPrivacyAudit(
        MemoryPrivacyCatalogState(),
        unused_objects,
        _Key(),
        _Clock(),  # type: ignore[arg-type]
    )
    second_request = _projection_request(_PROPOSAL_2, _REQUEST_2)
    second_receipt = replace(
        _receipt(),
        privacy_proposal_id=_PROPOSAL_2,
        request_id=_REQUEST_2,
        receipt_id=_RECEIPT_2,
    )

    async def run() -> tuple[tuple[str, ...], tuple[str, ...]]:
        for audit in (sqlite, memory):
            await audit.complete_agent_projection(_projection_request(), _receipt())
            replay = await audit.complete_agent_projection(_projection_request(), _receipt())
            assert replay.subject.projection_commitment.startswith("hmac-sha256:")
            contradiction = replace(
                _receipt(),
                counts=ReceiptCounts(0, 0, 0, 0, 0, 0, 3, 0, None),
            )
            with pytest.raises(ValueError, match="privacy_projection_replay_conflict"):
                await audit.complete_agent_projection(_projection_request(), contradiction)
            await audit.complete_agent_projection(second_request, second_receipt)
        sqlite_first = await sqlite.list_receipts(
            PrivacyReceiptQuery(limit=1), PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
        )
        memory_first = await memory.list_receipts(
            PrivacyReceiptQuery(limit=1), PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
        )
        assert sqlite_first.next_cursor is not None and memory_first.next_cursor is not None
        for audit, cursor in (
            (sqlite, sqlite_first.next_cursor),
            (memory, memory_first.next_cursor),
        ):
            tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
            with pytest.raises(ValueError, match="privacy_receipt_cursor_invalid"):
                await audit.list_receipts(
                    PrivacyReceiptQuery(limit=1, cursor=tampered),
                    PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL,
                )
        sqlite_second = await sqlite.list_receipts(
            PrivacyReceiptQuery(limit=1, cursor=sqlite_first.next_cursor),
            PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL,
        )
        memory_second = await memory.list_receipts(
            PrivacyReceiptQuery(limit=1, cursor=memory_first.next_cursor),
            PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL,
        )
        return (
            tuple(
                view.receipt.receipt_id for view in sqlite_first.receipts + sqlite_second.receipts
            ),
            tuple(
                view.receipt.receipt_id for view in memory_first.receipts + memory_second.receipts
            ),
        )

    sqlite_ids, memory_ids = asyncio.run(run())

    assert sqlite_ids == memory_ids == (_RECEIPT_2, _RECEIPT)


def test_memory_and_sqlite_policy_generation_results_match() -> None:
    sqlite = CatalogPrivacyPolicyStore(_database(), _Clock())
    memory = MemoryPrivacyPolicyStore(MemoryPrivacyCatalogState(), _Clock())
    seed = _policy()

    async def run() -> tuple[tuple[int, PrivacyPolicy], tuple[int, PrivacyPolicy]]:
        await sqlite.seed_if_absent(seed)
        await memory.seed_if_absent(seed)
        sqlite_effective = await sqlite.effective_policy(_scope())
        memory_effective = await memory.effective_policy(_scope())
        return (
            (sqlite_effective.generation, sqlite_effective.policy),
            (memory_effective.generation, memory_effective.policy),
        )

    assert asyncio.run(run()) == ((1, seed), (1, seed))


def test_first_repository_carry_forward_provenance_is_exact_not_installation_wide() -> None:
    db = _database()
    store = CatalogPrivacyPolicyStore(db, _Clock())
    machine = minimal_external_policy()
    repository_a = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE, _INSTALLATION, "hmac-sha256:" + "1" * 64
    )
    repository_b = AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE, _INSTALLATION, "hmac-sha256:" + "2" * 64
    )

    async def run() -> tuple[object, object, object]:
        await store.seed_if_absent(machine)
        available = await store.repository_authority(repository_a)
        carried = await store.carry_forward_repository_authority(repository_a)
        explicit = replace(
            machine,
            policy_id="pvy_30000000-0000-4000-8000-000000000011",
            policy_digest="sha256:" + "9" * 64,
            version=1,
            effective_scope=repository_b,
            supersedes_policy_digest=None,
        )
        await store.seed_if_absent(explicit)
        later = await store.repository_authority(repository_b)
        return available, carried, later

    available, carried, later = asyncio.run(run())
    assert available.migration_state == "first_repository_available"  # type: ignore[attr-defined]
    assert carried.migration_state == "consumed"  # type: ignore[attr-defined]
    assert later.grant_state == "granted"  # type: ignore[attr-defined]
    assert later.migration_state == "not_applicable"  # type: ignore[attr-defined]


def test_memory_and_sqlite_content_proposal_root_sets_match() -> None:
    db = _database()
    db.execute(
        """INSERT INTO task_routes (
               task_id, workspace_ref_commitment, external_ref_commitment,
               active_session_id, bundle_relpath, route_generation,
               active_route_identity_digest, state, quarantine_code, created_at, updated_at
           ) VALUES (?, NULL, NULL, ?, ?, 1, ?, 'active', NULL, ?, ?)""",
        (
            _TASK,
            _SESSION,
            f"tasks/{_TASK}",
            _ROUTE_DIGEST,
            _NOW.isoformat().replace("+00:00", "Z"),
            _NOW.isoformat().replace("+00:00", "Z"),
        ),
    )
    memory_state = MemoryPrivacyCatalogState(routes={_TASK: _ROUTE_DIGEST})
    sqlite = CatalogPrivacyAudit(db, _Objects(), _Key(), _Clock())  # type: ignore[arg-type]
    memory = MemoryPrivacyAudit(memory_state, _Objects(), _Key(), _Clock())  # type: ignore[arg-type]
    prepared = canonical_encode({"items": []})
    minimized = MinimizedDisclosure(
        prepared,
        (),
        (),
        (),
        (),
        (("removed_items", 0),),
        len(prepared),
        0,
        canonical_digest({"items": []}),
        "scanner-v1",
        _DIGEST,
        (),
    )
    request = DisclosureProposalRequest(
        _PROPOSAL,
        _REQUEST,
        _TASK,
        minimized,
        None,
        LocalDisclosureSink.TRUSTED_HUMAN_CONTROL,
        "trusted-preview",
        _task_scope(),
        _POLICY,
        1,
        1,
        _DIGEST,
        len(prepared),
        0,
        _NOW + timedelta(minutes=1),
    )

    async def run() -> tuple[tuple[object, ...], tuple[object, ...]]:
        results: list[tuple[object, ...]] = []
        for audit in (sqlite, memory):
            reserved = await audit.prepare_disclosure_proposal(request)
            roots = await audit.live_object_roots(_TASK, _ROUTE_DIGEST)
            results.append(
                (
                    reserved.proposal.proposal_commitment,
                    roots.privacy_root_generation,
                    roots.root_set_digest,
                    tuple(ref.object_id for ref in roots.object_refs),
                )
            )
        return results[0], results[1]

    sqlite_result, memory_result = asyncio.run(run())

    assert sqlite_result == memory_result
    assert sqlite_result[3] == (_OBJECT,)


def test_memory_and_sqlite_agree_on_which_disclosures_are_still_decidable() -> None:
    """Both audit implementations list the same waiting proposals, and exclude expired ones.

    An expired proposal is refused by the decision path, so listing it would advertise a
    ceremony that cannot succeed -- the opposite of what a caller reaching for this needs.
    """

    db = _database()
    db.execute(
        """INSERT INTO task_routes (
               task_id, workspace_ref_commitment, external_ref_commitment,
               active_session_id, bundle_relpath, route_generation,
               active_route_identity_digest, state, quarantine_code, created_at, updated_at
           ) VALUES (?, NULL, NULL, ?, ?, 1, ?, 'active', NULL, ?, ?)""",
        (
            _TASK,
            _SESSION,
            f"tasks/{_TASK}",
            _ROUTE_DIGEST,
            _NOW.isoformat().replace("+00:00", "Z"),
            _NOW.isoformat().replace("+00:00", "Z"),
        ),
    )
    memory_state = MemoryPrivacyCatalogState(routes={_TASK: _ROUTE_DIGEST})
    sqlite = CatalogPrivacyAudit(db, _Objects(), _Key(), _Clock())  # type: ignore[arg-type]
    memory = MemoryPrivacyAudit(memory_state, _Objects(), _Key(), _Clock())  # type: ignore[arg-type]
    prepared = canonical_encode({"items": []})
    minimized = MinimizedDisclosure(
        prepared,
        (),
        (),
        (),
        (),
        (("removed_items", 0),),
        len(prepared),
        0,
        canonical_digest({"items": []}),
        "scanner-v1",
        _DIGEST,
        (),
    )
    live = DisclosureProposalRequest(
        _PROPOSAL,
        _REQUEST,
        _TASK,
        minimized,
        None,
        LocalDisclosureSink.TRUSTED_HUMAN_CONTROL,
        "trusted-preview",
        _task_scope(),
        _POLICY,
        1,
        1,
        _DIGEST,
        len(prepared),
        0,
        _NOW + timedelta(minutes=1),
    )

    # `_Clock` is fixed at _NOW. A second pair reading five minutes later sees the same stored
    # proposal past its one-minute expiry, so the clock is the only difference between the two
    # observations.
    class _LateClock:
        def now_utc(self) -> datetime:
            return _NOW + timedelta(minutes=5)

    late = (
        CatalogPrivacyAudit(db, _Objects(), _Key(), _LateClock()),  # type: ignore[arg-type]
        MemoryPrivacyAudit(memory_state, _Objects(), _Key(), _LateClock()),  # type: ignore[arg-type]
    )

    async def listed(audit: object) -> tuple[str, ...]:
        page = await cast(CatalogPrivacyAudit, audit).list_pending_disclosures(
            PrivacyReceiptAudience.TRUSTED_LOCAL_CONTROL
        )
        return tuple(entry.pending_id for entry in page.pending)

    async def run() -> tuple[tuple[str, ...], ...]:
        for audit in (sqlite, memory):
            await audit.prepare_disclosure_proposal(live)
        return (
            await listed(sqlite),
            await listed(memory),
            await listed(late[0]),
            await listed(late[1]),
        )

    sqlite_now, memory_now, sqlite_late, memory_late = asyncio.run(run())

    assert sqlite_now == memory_now == (_PROPOSAL,)
    assert sqlite_late == memory_late == ()


def test_coordinator_empty_client_projection_completes_one_durable_receipt() -> None:
    state = MemoryPrivacyCatalogState()
    policies = MemoryPrivacyPolicyStore(state, _Clock())
    audit = MemoryPrivacyAudit(
        state,
        cast(ObjectStorePort, _UnusedObjects()),
        _Key(),
        _Clock(),  # type: ignore[arg-type]
    )
    coordinator = PrivacyCoordinator(
        policies,
        LocalPrivacyEnforcer(),
        audit,
        _Gateway(),  # type: ignore[arg-type]
        _Clock(),
        _Ids(),
    )
    empty = canonical_encode({})
    candidate = CandidateContext(
        _REQUEST,
        None,
        LocalDisclosureSink.AGENT_CONTEXT,
        "client_result_projection",
        _scope(),
        _DIGEST,
        None,
        (),
        None,
        ProjectionAuditContext(
            _RPC,
            "check",
            _SERVICE,
            1,
            None,
            None,
            empty,
            empty,
        ),
    )

    async def run() -> object:
        await policies.seed_if_absent(_policy())
        return await coordinator.prepare_local_disclosure(candidate)

    result = asyncio.run(run())

    assert type(result) is LocalDisclosureApproved
    assert result.approved_items == ()
    assert result.omissions == ()
    assert result.receipt.outcome is PrivacyOutcome.COMPLETED
    assert len(state.audit) == 1
