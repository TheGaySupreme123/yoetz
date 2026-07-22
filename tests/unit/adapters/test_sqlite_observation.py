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
from yoetz.protocol.errors import PublicOperationError

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
