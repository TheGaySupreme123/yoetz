"""Durable phase-machine tests for the SQLite start catalog."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import apsw
import pytest

from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.domain.values import Frontier
from yoetz.ports.runtime import StartCompletionEvidence, StartMilestone
from yoetz.ports.start_catalog import (
    EncryptedResultRef,
    SafeReason,
    StartAllocation,
    StartCommand,
    StartIdentityInput,
    StartMode,
    StartPhase,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _id(kind: IdKind, value: int) -> str:
    raw = bytearray(value.to_bytes(16, "big"))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return PREFIX_BY_KIND[kind] + str(uuid.UUID(bytes=bytes(raw)))


class _Ids:
    def __init__(self) -> None:
        self._value = 1

    def new(self, kind: IdKind) -> str:
        result = _id(kind, self._value)
        self._value += 1
        return result


class _Lookup:
    def mac(self, domain: bytes, message: bytes) -> str:
        digest = hmac.new(b"\x82" * 32, domain + message, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"


@dataclass(slots=True)
class _Clock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value

    def monotonic_seconds(self) -> float:
        return 0.0

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass(slots=True)
class _Harness:
    catalog: SqliteStartCatalog
    db: apsw.Connection
    clock: _Clock

    @classmethod
    def create(cls, seed: int) -> _Harness:
        installation_id = _id(IdKind.INSTALLATION, seed)
        db = apsw.Connection(":memory:")
        migration = (Path(__file__).resolve().parents[3] / "migrations/catalog/0001.sql").read_text(
            encoding="utf-8"
        )
        db.execute(migration)
        db.executemany(
            "INSERT INTO catalog_meta(key, value) VALUES(?, ?)",
            (("installation_id", installation_id), ("owner_generation", "1")),
        )
        clock = _Clock(datetime(2026, 7, 19, 12, 0, tzinfo=UTC))
        return cls(
            SqliteStartCatalog(
                db,
                installation_id=installation_id,
                lookup=_Lookup(),
                clock=clock,
                ids=_Ids(),
            ),
            db,
            clock,
        )

    async def command(
        self,
        operation_seed: int,
        *,
        mode: StartMode = StartMode.CREATE_OR_ATTACH,
        refs: str = "A",
        session_id: str | None = None,
    ) -> StartCommand:
        identity = StartIdentityInput("Task title", f"workspace-{refs}", f"external-{refs}")
        commitments = await self.catalog.commit_identity(identity)
        request_digest = canonical_digest(
            {
                "external": commitments.external_ref_commitment,
                "mode": mode.value,
                "session_id": session_id,
                "title": commitments.title_commitment,
                "workspace": commitments.workspace_ref_commitment,
            }
        )
        return StartCommand(
            _id(IdKind.REQUEST, operation_seed),
            request_digest,
            mode,
            identity,
            commitments,
            session_id,
        )


def _result(seed: int) -> EncryptedResultRef:
    content = canonical_encode({"result": "started", "seed": seed})
    return EncryptedResultRef(
        _id(IdKind.OBJECT, seed),
        f"sha256:{hashlib.sha256(b'envelope' + content).hexdigest()}",
        content,
        f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def _evidence(allocation: StartAllocation, result: EncryptedResultRef) -> StartCompletionEvidence:
    frontier = Frontier(1, canonical_digest({"accepted": 1}))
    value: dict[str, JsonValue] = {
        "lifecycle_event_id": allocation.lifecycle_event_id,
        "lifecycle_frontier": dict(frontier.as_wire()),
        "milestone": "result_published",
        "owner_generation": 1,
        "response_envelope_digest": result.envelope_digest,
        "response_object_id": result.response_object_id,
        "result_digest": result.result_digest,
        "route_generation": allocation.route_generation,
        "route_identity_digest": allocation.route_identity_digest,
        "session_id": allocation.session_id,
        "task_id": allocation.task_id,
        "writer_id": allocation.writer_id,
    }
    return StartCompletionEvidence(
        StartMilestone.RESULT_PUBLISHED,
        allocation.task_id,
        allocation.session_id,
        allocation.writer_id,
        allocation.lifecycle_event_id,
        allocation.route_generation,
        allocation.route_identity_digest,
        1,
        frontier,
        result.response_object_id,
        result.envelope_digest,
        result.result_digest,
        canonical_digest(value),
    )


async def _complete(
    catalog: SqliteStartCatalog, allocation: StartAllocation, result_seed: int
) -> EncryptedResultRef:
    allocation = await catalog.advance_phase(allocation, StartPhase.BUNDLE_READY)
    allocation = await catalog.advance_phase(allocation, StartPhase.LIFECYCLE_COMMITTED)
    result = _result(result_seed)
    allocation = await catalog.advance_phase(allocation, StartPhase.RESULT_PUBLISHED, result)
    await catalog.complete(allocation, result, _evidence(allocation, result))
    return result


@pytest.mark.anyio
async def test_reserve_resume_and_complete_paths() -> None:
    harness = _Harness.create(800)
    request = await harness.command(801)
    allocation = await harness.catalog.reserve_or_resume(request)
    with pytest.raises(PublicOperationError) as live:
        await harness.catalog.reserve_or_resume(request)
    assert live.value.code is PublicErrorCode.OPERATION_PENDING

    result = await _complete(harness.catalog, allocation, 802)
    replay = await harness.catalog.reserve_or_resume(request)
    assert replay.outcome == "replayed"
    assert replay.replayed_result == result.result_canonical
    assert replay.lease is None


@pytest.mark.anyio
async def test_crash_and_reclaim_paths() -> None:
    harness = _Harness.create(810)
    request = await harness.command(811)
    reserved = await harness.catalog.reserve_or_resume(request)
    harness.clock.advance(61)
    resumed = await harness.catalog.reserve_or_resume(request)
    assert resumed.outcome == "resumed"
    assert resumed.task_id == reserved.task_id
    assert resumed.session_id == reserved.session_id
    assert resumed.writer_id == reserved.writer_id
    assert resumed.lifecycle_event_id == reserved.lifecycle_event_id
    assert resumed.lease is not None and resumed.lease.lease_generation == 2


@pytest.mark.anyio
async def test_attachment_conflict_and_quarantine_paths() -> None:
    harness = _Harness.create(820)
    first_request = await harness.command(821, mode=StartMode.CREATE, refs="shared")
    first = await harness.catalog.reserve_or_resume(first_request)
    await _complete(harness.catalog, first, 822)

    attach_request = await harness.command(
        825,
        mode=StartMode.ATTACH,
        refs="shared",
        session_id=first.session_id,
    )
    attached = await harness.catalog.reserve_or_resume(attach_request)
    assert attached.route_action == "attached"
    assert attached.task_id == first.task_id
    assert attached.route_identity_digest == first.route_identity_digest
    assert await harness.catalog.resolve_route(attached.session_id) is None
    await _complete(harness.catalog, attached, 826)
    assert await harness.catalog.resolve_route(first.session_id) is None
    active = await harness.catalog.resolve_route(attached.session_id)
    assert active is not None and active.state.value == "active"

    conflict_request = await harness.command(823, mode=StartMode.CREATE, refs="shared")
    with pytest.raises(PublicOperationError) as conflict:
        await harness.catalog.reserve_or_resume(conflict_request)
    assert conflict.value.code is PublicErrorCode.SESSION_CONFLICT

    pending_request = await harness.command(824, mode=StartMode.CREATE, refs="other")
    pending = await harness.catalog.reserve_or_resume(pending_request)
    await harness.catalog.quarantine(pending, SafeReason("start_route_contradiction"))
    replay = await harness.catalog.reserve_or_resume(pending_request)
    assert replay.outcome == "replayed"
    assert replay.replayed_result is not None
    route = await harness.catalog.resolve_route(pending.session_id)
    assert route is not None and route.state.value == "quarantined"


@pytest.mark.anyio
async def test_expiry_and_stale_generation_reclaim() -> None:
    harness = _Harness.create(830)
    expired_request = await harness.command(831, refs="expired")
    expired = await harness.catalog.reserve_or_resume(expired_request)
    harness.clock.advance(61)
    reclaimed_expired = await harness.catalog.reserve_or_resume(expired_request)
    assert reclaimed_expired.task_id == expired.task_id

    stale_request = await harness.command(832, refs="stale")
    stale = await harness.catalog.reserve_or_resume(stale_request)
    harness.db.execute("UPDATE catalog_meta SET value = '2' WHERE key = 'owner_generation'")
    reclaimed_stale = await harness.catalog.reserve_or_resume(stale_request)
    assert reclaimed_stale.outcome == "resumed"
    assert reclaimed_stale.lease is not None
    assert reclaimed_stale.lease.owner_generation == 2
    with pytest.raises(PublicOperationError) as fenced:
        await harness.catalog.advance_phase(stale, StartPhase.BUNDLE_READY)
    assert fenced.value.code is PublicErrorCode.OPERATION_PENDING
