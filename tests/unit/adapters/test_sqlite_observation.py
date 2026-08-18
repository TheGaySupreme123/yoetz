"""Unit tests for SQLite ObservationPort over migration 0002 tables."""

from __future__ import annotations

import asyncio

import apsw
import pytest

from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationLifecycle,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

_WORKSPACE = "hmac-sha256:" + "1" * 64
_SESSION = "hmac-sha256:" + "2" * 64
_SOURCE = "hmac-sha256:" + "3" * 64
_TIME = Timestamp("2026-07-22T21:10:00.000Z")


def _store() -> SqliteObservationStore:
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    return SqliteObservationStore(db)


def _cursor(*, generation: int = 1, byte_pos: int = 10, event_pos: int = 1) -> ObservationCursor:
    return ObservationCursor(
        source_generation=generation,
        byte_position=byte_pos,
        event_position=event_pos,
        last_source_commitment=_SOURCE,
        mapping_version="codex-obs-1",
    )


def _envelope(*, cursor: ObservationCursor | None = None) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_SESSION,
        event_kind="PostToolUse",
        source_identity="hook:evt-1",
        source=ObservationSource.CODEX_HOOK,
        cursor=cursor or _cursor(),
        receipt_time=_TIME,
        structural_payload=JsonObject({"tool_name": "shell", "exit_status": 0}),
        content_object_refs=(),
        gap_codes=(),
    )


def test_sqlite_consent_ingest_dedup_and_revoke() -> None:
    async def run() -> None:
        store = _store()
        store.grant_consent(_WORKSPACE, _TIME)
        store.bind_session(_WORKSPACE, _SESSION)
        accepted = await store.ingest(_envelope())
        assert accepted.disposition is ObservationIngestDisposition.ACCEPTED
        duplicate = await store.ingest(_envelope())
        assert duplicate.disposition is ObservationIngestDisposition.DUPLICATE
        status = await store.status(ObservationStatusQuery(_WORKSPACE))
        assert status.source_coverage[ObservationSource.CODEX_HOOK] is True
        assert status.lifecycle is ObservationLifecycle.ACTIVE
        envelopes = store.list_envelopes(_WORKSPACE)
        assert len(envelopes) == 1
        revoked = await store.revoke(ObservationRevokeCommand(_WORKSPACE))
        assert revoked.lifecycle is ObservationLifecycle.STOPPED
        after = await store.ingest(_envelope(cursor=_cursor(byte_pos=20, event_pos=2)))
        assert after.disposition is ObservationIngestDisposition.REJECTED
        assert after.reason == ObservationGapCode.CONSENT_REVOKED.value
        # Evidence retained after revoke.
        assert len(store.list_envelopes(_WORKSPACE)) == 1

    asyncio.run(run())


def test_sqlite_pause_resume() -> None:
    async def run() -> None:
        store = _store()
        store.grant_consent(_WORKSPACE, _TIME)
        store.bind_session(_WORKSPACE, _SESSION)
        await store.ingest(_envelope())
        paused = await store.pause(ObservationControlCommand(_WORKSPACE))
        assert paused.lifecycle is ObservationLifecycle.STOPPED
        rejected = await store.ingest(_envelope(cursor=_cursor(byte_pos=20, event_pos=2)))
        assert rejected.disposition is ObservationIngestDisposition.REJECTED
        resumed = await store.resume(ObservationControlCommand(_WORKSPACE))
        assert resumed.lifecycle is ObservationLifecycle.ACTIVE
        again = await store.ingest(_envelope(cursor=_cursor(byte_pos=20, event_pos=2)))
        assert again.disposition is ObservationIngestDisposition.ACCEPTED

    asyncio.run(run())


def test_sqlite_resume_without_consent_fails() -> None:
    async def run() -> None:
        store = _store()
        with pytest.raises(PublicOperationError):
            await store.resume(ObservationControlCommand(_WORKSPACE))

    asyncio.run(run())


def test_record_logical_identity_claim_idempotence_union_and_conflict() -> None:
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    logical_identity = "sha256:" + "a" * 64
    materialization_digest = "sha256:" + "b" * 64
    operation_id = "op_" + "1" * 32
    mapping_version = "obs-ledger/1.2.0"

    def record(*, source_mask: int, digest: str = materialization_digest) -> None:
        store.record_logical_identity_claim(
            workspace=_WORKSPACE,
            logical_identity=logical_identity,
            materialization_digest=digest,
            operation_id=operation_id,
            mapping_version=mapping_version,
            source_mask=source_mask,
            materialized_at=_TIME,
        )

    record(source_mask=1)
    # A replay of the same source is idempotent.
    record(source_mask=1)
    # The other source's copy of the same materialization unions coverage.
    record(source_mask=2)
    row = db.execute(
        "SELECT source_mask FROM observation_logical_identity "
        "WHERE workspace_commitment=? AND logical_identity=?",
        (_WORKSPACE, logical_identity),
    ).fetchone()
    assert row == (3,)

    # A different materialization of the same claim key is corruption.
    with pytest.raises(PublicOperationError) as conflict:
        record(source_mask=1, digest="sha256:" + "c" * 64)
    assert conflict.value.code is PublicErrorCode.STORAGE_CORRUPT
    assert conflict.value.retryable is False

    # Only the two per-source masks are valid inputs; 3 is a stored union.
    with pytest.raises(PublicOperationError) as invalid:
        record(source_mask=3)
    assert invalid.value.code is PublicErrorCode.INVALID_REQUEST


_SESSION_B = "hmac-sha256:" + "4" * 64


def _session_envelope(
    session: str,
    identity: str,
    ordinal: int,
    *,
    gaps: tuple[str, ...] = (),
) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=session,
        event_kind="PostToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=_cursor(byte_pos=10 * ordinal, event_pos=ordinal),
        receipt_time=_TIME,
        structural_payload=JsonObject({"tool_name": "shell", "exit_status": 0}),
        content_object_refs=(),
        gap_codes=gaps,
    )


def test_session_scoped_envelopes_and_status_exclude_other_sessions() -> None:
    """#352: mapped advice inputs must not see another session's envelopes or gaps."""

    async def run() -> None:
        store = _store()
        store.grant_consent(_WORKSPACE, _TIME)
        store.bind_session(_WORKSPACE, _SESSION)
        store.bind_session(_WORKSPACE, _SESSION_B)
        # Session A carries degradation; session B is healthy.
        degraded = await store.ingest(
            _session_envelope(
                _SESSION,
                "hook:degraded-a",
                1,
                gaps=(
                    ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value,
                    ObservationGapCode.SOURCE_LAG.value,
                ),
            )
        )
        assert degraded.disposition is ObservationIngestDisposition.ACCEPTED
        healthy = await store.ingest(_session_envelope(_SESSION_B, "hook:healthy-b", 1))
        assert healthy.disposition is ObservationIngestDisposition.ACCEPTED

        workspace_wide = store.list_envelopes(_WORKSPACE)
        assert len(workspace_wide) == 2
        only_b = store.list_envelopes_for_session(_WORKSPACE, _SESSION_B)
        assert [env.source_identity for env in only_b] == ["hook:healthy-b"]

        status_b = await store.status_for_session(_WORKSPACE, _SESSION_B)
        assert status_b.gaps == ()
        assert status_b.lifecycle is ObservationLifecycle.ACTIVE
        status_a = await store.status_for_session(_WORKSPACE, _SESSION)
        assert ObservationGapCode.SOURCE_LAG.value in status_a.gaps
        assert ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value in status_a.gaps
        # The workspace aggregate still retains A's history for the operator surface.
        aggregate = await store.status(ObservationStatusQuery(_WORKSPACE))
        assert ObservationGapCode.SOURCE_LAG.value in aggregate.gaps

    asyncio.run(run())


def test_session_status_clears_current_gap_without_clearing_other_sessions() -> None:
    """Historical gap rows remain append-only while current health recovers per session."""

    async def run() -> None:
        store = _store()
        store.grant_consent(_WORKSPACE, _TIME)
        store.bind_session(_WORKSPACE, _SESSION)
        store.bind_session(_WORKSPACE, _SESSION_B)
        degraded_a = await store.ingest(
            _session_envelope(
                _SESSION,
                "hook:degraded-a-current",
                1,
                gaps=(ObservationGapCode.SOURCE_LAG.value,),
            )
        )
        degraded_b = await store.ingest(
            _session_envelope(
                _SESSION_B,
                "hook:degraded-b-current",
                1,
                gaps=(ObservationGapCode.SOURCE_LAG.value,),
            )
        )
        assert degraded_a.disposition is ObservationIngestDisposition.ACCEPTED
        assert degraded_b.disposition is ObservationIngestDisposition.ACCEPTED

        recovered_b = await store.ingest(
            _session_envelope(_SESSION_B, "hook:recovered-b-current", 2)
        )
        assert recovered_b.disposition is ObservationIngestDisposition.ACCEPTED
        status_a = await store.status_for_session(_WORKSPACE, _SESSION)
        status_b = await store.status_for_session(_WORKSPACE, _SESSION_B)
        assert ObservationGapCode.SOURCE_LAG.value in status_a.gaps
        assert ObservationGapCode.SOURCE_LAG.value not in status_b.gaps
        aggregate = await store.status(ObservationStatusQuery(_WORKSPACE))
        assert ObservationGapCode.SOURCE_LAG.value in aggregate.gaps

        recovered_a = await store.ingest(_session_envelope(_SESSION, "hook:recovered-a-current", 2))
        assert recovered_a.disposition is ObservationIngestDisposition.ACCEPTED
        assert (
            ObservationGapCode.SOURCE_LAG.value
            not in (await store.status_for_session(_WORKSPACE, _SESSION)).gaps
        )
        assert (
            ObservationGapCode.SOURCE_LAG.value
            not in (await store.status(ObservationStatusQuery(_WORKSPACE))).gaps
        )
        # Recovery changes only the current projection; all observations remain.
        assert len(store.list_envelopes(_WORKSPACE)) == 4

    asyncio.run(run())


def test_codex_session_commitment_for_session_recovers_historical_route() -> None:
    store = _store()
    store.grant_consent(_WORKSPACE, _TIME)
    store.record_workspace_session_route(
        workspace=_WORKSPACE,
        yoetz_session_id="ses_10000000-0000-4000-8000-000000000001",
        yoetz_task_id="tsk_10000000-0000-4000-8000-000000000001",
        yoetz_writer_id="wtr_10000000-0000-4000-8000-000000000001",
        codex_session_commitment=_SESSION,
        bound_at=_TIME,
    )
    # Binding a newer session marks the older route inactive, but the durable
    # route still identifies the older task and must remain usable for recovery.
    store.record_workspace_session_route(
        workspace=_WORKSPACE,
        yoetz_session_id="ses_10000000-0000-4000-8000-000000000002",
        yoetz_task_id="tsk_10000000-0000-4000-8000-000000000002",
        yoetz_writer_id="wtr_10000000-0000-4000-8000-000000000002",
        codex_session_commitment=_SESSION_B,
        bound_at=_TIME,
    )
    assert (
        store.codex_session_commitment_for_session(
            workspace=_WORKSPACE,
            yoetz_session_id="ses_10000000-0000-4000-8000-000000000001",
        )
        == _SESSION
    )
    assert (
        store.workspace_for_yoetz_session("ses_10000000-0000-4000-8000-000000000001") == _WORKSPACE
    )
    assert (
        store.codex_session_commitment_for_session(
            workspace=_WORKSPACE,
            yoetz_session_id="ses_10000000-0000-4000-8000-000000000002",
        )
        == _SESSION_B
    )
