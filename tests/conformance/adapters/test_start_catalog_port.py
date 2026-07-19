"""Conformance traces shared by the memory and SQLite start catalogs."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import apsw
import pytest

from yoetz.adapters.memory.start_catalog import (
    MemoryStartCatalogAdapter,
    MemoryStartCatalogState,
)
from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.domain.values import Frontier
from yoetz.ports.runtime import StartCompletionEvidence, StartMilestone
from yoetz.ports.start_catalog import (
    EncryptedResultRef,
    SafeReason,
    StartCommand,
    StartIdentityInput,
    StartMode,
    StartPhase,
    TaskRouteState,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(slots=True)
class _Clock:
    current: datetime

    def now_utc(self) -> datetime:
        return self.current

    def monotonic_seconds(self) -> float:
        return 0.0

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _Ids:
    def __init__(self) -> None:
        self._next = 1

    def new(self, kind: IdKind) -> str:
        value = self._next
        self._next += 1
        raw = bytearray(value.to_bytes(16, "big"))
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        return PREFIX_BY_KIND[kind] + str(uuid.UUID(bytes=bytes(raw)))


class _Lookup:
    def __init__(self) -> None:
        self._key = b"\x91" * 32

    def mac(self, domain: bytes, message: bytes) -> str:
        return f"hmac-sha256:{hmac.new(self._key, domain + message, hashlib.sha256).hexdigest()}"


def _id(kind: IdKind, value: int) -> str:
    raw = bytearray(value.to_bytes(16, "big"))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return PREFIX_BY_KIND[kind] + str(uuid.UUID(bytes=bytes(raw)))


def _sqlite_catalog(installation_id: str, clock: _Clock) -> SqliteStartCatalog:
    db = apsw.Connection(":memory:")
    root = Path(__file__).resolve().parents[3]
    db.execute((root / "migrations/catalog/0001.sql").read_text(encoding="utf-8"))
    db.executemany(
        "INSERT INTO catalog_meta(key, value) VALUES(?, ?)",
        (("installation_id", installation_id), ("owner_generation", "1")),
    )
    return SqliteStartCatalog(
        db,
        installation_id=installation_id,
        lookup=_Lookup(),
        clock=clock,
        ids=_Ids(),
    )


def _memory_catalog(
    installation_id: str, clock: _Clock
) -> tuple[MemoryStartCatalogAdapter, MemoryStartCatalogState]:
    state = MemoryStartCatalogState()
    return (
        MemoryStartCatalogAdapter(
            installation_id=installation_id,
            lookup=_Lookup(),
            state=state,
            transaction_lock=anyio.Lock(),
            clock=clock,
            ids=_Ids(),
        ),
        state,
    )


async def _command(
    catalog: MemoryStartCatalogAdapter | SqliteStartCatalog,
    *,
    operation_id: str,
    mode: StartMode = StartMode.CREATE_OR_ATTACH,
    session_id: str | None = None,
    title: str = "Exact task",
) -> StartCommand:
    identity = StartIdentityInput(title, "workspace-A", "external-A")
    commitments = await catalog.commit_identity(identity)
    digest = canonical_digest(
        {
            "commitments": {
                "external": commitments.external_ref_commitment,
                "title": commitments.title_commitment,
                "workspace": commitments.workspace_ref_commitment,
            },
            "mode": mode.value,
            "session_id": session_id,
        }
    )
    return StartCommand(operation_id, digest, mode, identity, commitments, session_id)


def _result(value: int = 900) -> EncryptedResultRef:
    canonical = canonical_encode({"outcome": "complete", "sequence": value})
    return EncryptedResultRef(
        _id(IdKind.OBJECT, value),
        canonical,
        f"sha256:{hashlib.sha256(canonical).hexdigest()}",
    )


def _evidence(allocation: object, result: EncryptedResultRef) -> StartCompletionEvidence:
    from yoetz.ports.start_catalog import StartAllocation

    assert type(allocation) is StartAllocation
    frontier = Frontier(1, canonical_digest({"frontier": 1}))
    value: dict[str, JsonValue] = {
        "lifecycle_event_id": allocation.lifecycle_event_id,
        "lifecycle_frontier": dict(frontier.as_wire()),
        "milestone": StartMilestone.RESULT_PUBLISHED.value,
        "owner_generation": 1,
        "response_object_id": result.response_object_id,
        "result_digest": result.result_digest,
        "route_generation": allocation.route_generation,
        "route_identity_digest": allocation.route_identity_digest,
        "session_id": allocation.session_id,
        "task_id": allocation.task_id,
        "writer_id": allocation.writer_id,
    }
    return StartCompletionEvidence(
        milestone=StartMilestone.RESULT_PUBLISHED,
        task_id=allocation.task_id,
        session_id=allocation.session_id,
        writer_id=allocation.writer_id,
        lifecycle_event_id=allocation.lifecycle_event_id,
        route_generation=allocation.route_generation,
        route_identity_digest=allocation.route_identity_digest,
        owner_generation=1,
        lifecycle_frontier=frontier,
        response_object_id=result.response_object_id,
        result_digest=result.result_digest,
        evidence_digest=canonical_digest(value),
    )


async def _finish(
    catalog: MemoryStartCatalogAdapter | SqliteStartCatalog,
    allocation: object,
) -> tuple[object, EncryptedResultRef]:
    from yoetz.ports.start_catalog import StartAllocation

    assert type(allocation) is StartAllocation
    allocation = await catalog.advance_phase(allocation, StartPhase.BUNDLE_READY)
    allocation = await catalog.advance_phase(allocation, StartPhase.LIFECYCLE_COMMITTED)
    result = _result()
    allocation = await catalog.advance_phase(allocation, StartPhase.RESULT_PUBLISHED, result)
    await catalog.complete(allocation, result, _evidence(allocation, result))
    return allocation, result


@pytest.mark.anyio
async def test_reserve_resume_complete_parity() -> None:
    installation_id = _id(IdKind.INSTALLATION, 700)
    memory_clock = _Clock(datetime(2026, 7, 19, 9, 0, tzinfo=UTC))
    sqlite_clock = _Clock(memory_clock.current)
    memory, _ = _memory_catalog(installation_id, memory_clock)
    sqlite = _sqlite_catalog(installation_id, sqlite_clock)
    memory_request = await _command(memory, operation_id=_id(IdKind.REQUEST, 701))
    sqlite_request = await _command(sqlite, operation_id=_id(IdKind.REQUEST, 701))

    memory_allocation = await memory.reserve_or_resume(memory_request)
    sqlite_allocation = await sqlite.reserve_or_resume(sqlite_request)
    assert memory_allocation == sqlite_allocation
    assert await memory.resolve_route(memory_allocation.session_id) == await sqlite.resolve_route(
        sqlite_allocation.session_id
    )

    memory_terminal, _ = await _finish(memory, memory_allocation)
    sqlite_terminal, _ = await _finish(sqlite, sqlite_allocation)
    assert memory_terminal == sqlite_terminal
    memory_replay = await memory.reserve_or_resume(memory_request)
    sqlite_replay = await sqlite.reserve_or_resume(sqlite_request)
    assert memory_replay == sqlite_replay
    assert memory_replay.outcome == "replayed"
    assert memory_replay.replayed_result is not None


@pytest.mark.anyio
async def test_quarantine_and_reclaim_parity() -> None:
    installation_id = _id(IdKind.INSTALLATION, 710)
    memory_clock = _Clock(datetime(2026, 7, 19, 10, 0, tzinfo=UTC))
    sqlite_clock = _Clock(memory_clock.current)
    memory, _ = _memory_catalog(installation_id, memory_clock)
    sqlite = _sqlite_catalog(installation_id, sqlite_clock)
    memory_request = await _command(memory, operation_id=_id(IdKind.REQUEST, 711))
    sqlite_request = await _command(sqlite, operation_id=_id(IdKind.REQUEST, 711))
    original_memory = await memory.reserve_or_resume(memory_request)
    original_sqlite = await sqlite.reserve_or_resume(sqlite_request)

    memory_clock.advance(61)
    sqlite_clock.advance(61)
    resumed_memory = await memory.reserve_or_resume(memory_request)
    resumed_sqlite = await sqlite.reserve_or_resume(sqlite_request)
    assert resumed_memory == resumed_sqlite
    assert resumed_memory.outcome == "resumed"
    assert resumed_memory.task_id == original_memory.task_id
    assert resumed_sqlite.writer_id == original_sqlite.writer_id

    reason = SafeReason("start_bundle_invalid")
    await memory.quarantine(resumed_memory, reason)
    await sqlite.quarantine(resumed_sqlite, reason)
    memory_replay = await memory.reserve_or_resume(memory_request)
    sqlite_replay = await sqlite.reserve_or_resume(sqlite_request)
    assert memory_replay == sqlite_replay
    assert memory_replay.replayed_result is not None


@pytest.mark.anyio
async def test_generation_and_route_identity_parity() -> None:
    installation_id = _id(IdKind.INSTALLATION, 720)
    memory_clock = _Clock(datetime(2026, 7, 19, 11, 0, tzinfo=UTC))
    sqlite_clock = _Clock(memory_clock.current)
    memory, state = _memory_catalog(installation_id, memory_clock)
    sqlite = _sqlite_catalog(installation_id, sqlite_clock)
    memory_request = await _command(memory, operation_id=_id(IdKind.REQUEST, 721))
    sqlite_request = await _command(sqlite, operation_id=_id(IdKind.REQUEST, 721))
    stale_memory = await memory.reserve_or_resume(memory_request)
    stale_sqlite = await sqlite.reserve_or_resume(sqlite_request)

    state.owner_generation = 2
    sqlite._db.execute(  # pyright: ignore[reportPrivateUsage]
        "UPDATE catalog_meta SET value = '2' WHERE key = 'owner_generation'"
    )
    resumed_memory = await memory.reserve_or_resume(memory_request)
    resumed_sqlite = await sqlite.reserve_or_resume(sqlite_request)
    assert resumed_memory == resumed_sqlite
    assert resumed_memory.route_identity_digest == stale_memory.route_identity_digest

    for catalog, allocation in ((memory, stale_memory), (sqlite, stale_sqlite)):
        with pytest.raises(PublicOperationError) as failure:
            await catalog.advance_phase(allocation, StartPhase.BUNDLE_READY)
        assert failure.value.code is PublicErrorCode.OPERATION_PENDING

    route_memory = await memory.resolve_route(resumed_memory.session_id)
    route_sqlite = await sqlite.resolve_route(resumed_sqlite.session_id)
    assert route_memory == route_sqlite
    assert route_memory is not None
    assert route_memory.state is TaskRouteState.INITIALIZING
