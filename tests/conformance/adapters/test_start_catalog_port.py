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
    for version in ("0001", "0002", "0003"):
        db.execute((root / f"migrations/catalog/{version}.sql").read_text(encoding="utf-8"))
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
    workspace_ref: str = "workspace-A",
    external_ref: str = "external-A",
    repository_privacy_commitment: str | None = None,
) -> StartCommand:
    identity = StartIdentityInput(title, workspace_ref, external_ref)
    commitments = await catalog.commit_identity(identity)
    digest = canonical_digest(
        {
            "commitments": {
                "external": commitments.external_ref_commitment,
                "title": commitments.title_commitment,
                "workspace": commitments.workspace_ref_commitment,
            },
            "mode": mode.value,
            "repository_privacy_commitment": repository_privacy_commitment,
            "session_id": session_id,
        }
    )
    return StartCommand(
        operation_id,
        digest,
        mode,
        identity,
        commitments,
        session_id,
        repository_privacy_commitment,
    )


def _result(value: int = 900) -> EncryptedResultRef:
    canonical = canonical_encode({"outcome": "complete", "sequence": value})
    return EncryptedResultRef(
        _id(IdKind.OBJECT, value),
        f"sha256:{hashlib.sha256(b'envelope' + canonical).hexdigest()}",
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
        response_envelope_digest=result.envelope_digest,
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
async def test_historical_session_binding_and_reattach_parity() -> None:
    """Memory and SQLite preserve the same capability-bounded session recovery (#438)."""

    installation_id = _id(IdKind.INSTALLATION, 730)
    now = datetime(2026, 7, 19, 9, 15, tzinfo=UTC)
    memory, _ = _memory_catalog(installation_id, _Clock(now))
    sqlite = _sqlite_catalog(installation_id, _Clock(now))

    memory_created = await memory.reserve_or_resume(
        await _command(memory, operation_id=_id(IdKind.REQUEST, 731))
    )
    sqlite_created = await sqlite.reserve_or_resume(
        await _command(sqlite, operation_id=_id(IdKind.REQUEST, 731))
    )
    assert memory_created == sqlite_created
    await _finish(memory, memory_created)
    await _finish(sqlite, sqlite_created)

    memory_attached = await memory.reserve_or_resume(
        await _command(memory, operation_id=_id(IdKind.REQUEST, 732))
    )
    sqlite_attached = await sqlite.reserve_or_resume(
        await _command(sqlite, operation_id=_id(IdKind.REQUEST, 732))
    )
    assert memory_attached == sqlite_attached
    await _finish(memory, memory_attached)
    await _finish(sqlite, sqlite_attached)

    assert await memory.resolve_route(memory_created.session_id) is None
    assert await sqlite.resolve_route(sqlite_created.session_id) is None
    memory_binding = await memory.session_binding(memory_created.session_id)
    sqlite_binding = await sqlite.session_binding(sqlite_created.session_id)
    assert memory_binding == sqlite_binding
    assert memory_binding is not None
    assert memory_binding.task_id == memory_created.task_id
    assert memory_binding.session_id == memory_attached.session_id
    assert memory_binding.writer_id == memory_attached.writer_id
    unknown = _id(IdKind.SESSION, 733)
    assert await memory.session_binding(unknown) is None
    assert await sqlite.session_binding(unknown) is None

    memory_rebound = await memory.reserve_or_resume(
        await _command(
            memory,
            operation_id=_id(IdKind.REQUEST, 734),
            mode=StartMode.ATTACH,
            session_id=memory_created.session_id,
        )
    )
    sqlite_rebound = await sqlite.reserve_or_resume(
        await _command(
            sqlite,
            operation_id=_id(IdKind.REQUEST, 734),
            mode=StartMode.ATTACH,
            session_id=sqlite_created.session_id,
        )
    )
    assert memory_rebound == sqlite_rebound
    assert memory_rebound.route_action == "attached"
    assert memory_rebound.task_id == memory_created.task_id
    await _finish(memory, memory_rebound)
    await _finish(sqlite, sqlite_rebound)
    assert await memory.session_binding(memory_created.session_id) == await sqlite.session_binding(
        sqlite_created.session_id
    )
    rebound_binding = await sqlite.session_binding(sqlite_created.session_id)
    assert rebound_binding is not None
    assert rebound_binding.session_id == sqlite_rebound.session_id
    assert rebound_binding.writer_id == sqlite_rebound.writer_id


@pytest.mark.anyio
async def test_active_session_can_recover_one_exact_workspace_under_a_new_pair() -> None:
    """#535: held selector + exact workspace admits one serialized host rotation."""

    installation_id = _id(IdKind.INSTALLATION, 740)
    now = datetime(2026, 7, 19, 9, 18, tzinfo=UTC)
    memory, _ = _memory_catalog(installation_id, _Clock(now))
    sqlite = _sqlite_catalog(installation_id, _Clock(now))
    privacy = "hmac-sha256:" + "c" * 64

    for catalog in (memory, sqlite):
        created = await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 741),
                repository_privacy_commitment=privacy,
            )
        )
        await _finish(catalog, created)

        recovered = await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 742),
                mode=StartMode.ATTACH,
                session_id=created.session_id,
                external_ref="external-B",
                repository_privacy_commitment=privacy,
            )
        )
        assert recovered.route_action == "attached"
        assert recovered.task_id == created.task_id

        with pytest.raises(PublicOperationError) as pending:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 743),
                    mode=StartMode.ATTACH,
                    session_id=created.session_id,
                    external_ref="external-C",
                    repository_privacy_commitment=privacy,
                )
            )
        assert pending.value.code is PublicErrorCode.OPERATION_PENDING
        assert pending.value.retryable is True

        await _finish(catalog, recovered)
        with pytest.raises(PublicOperationError) as historical:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 744),
                    mode=StartMode.ATTACH,
                    session_id=created.session_id,
                    external_ref="external-C",
                    repository_privacy_commitment=privacy,
                )
            )
        assert historical.value.code is PublicErrorCode.SESSION_CONFLICT


@pytest.mark.anyio
async def test_workspace_rotation_rejects_wrong_workspace_and_sibling_ambiguity() -> None:
    """A held selector never discovers another workspace or chooses among siblings."""

    installation_id = _id(IdKind.INSTALLATION, 745)
    now = datetime(2026, 7, 19, 9, 19, tzinfo=UTC)
    memory, _ = _memory_catalog(installation_id, _Clock(now))
    sqlite = _sqlite_catalog(installation_id, _Clock(now))

    for catalog in (memory, sqlite):
        first = await catalog.reserve_or_resume(
            await _command(catalog, operation_id=_id(IdKind.REQUEST, 746))
        )
        await _finish(catalog, first)

        with pytest.raises(PublicOperationError) as wrong_workspace:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 747),
                    mode=StartMode.ATTACH,
                    session_id=first.session_id,
                    workspace_ref="workspace-B",
                    external_ref="external-B",
                )
            )
        assert wrong_workspace.value.code is PublicErrorCode.SESSION_CONFLICT

        sibling = await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 748),
                mode=StartMode.CREATE,
                external_ref="external-B",
            )
        )
        await _finish(catalog, sibling)

        with pytest.raises(PublicOperationError) as pair_conflict:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 749),
                    mode=StartMode.ATTACH,
                    session_id=first.session_id,
                    external_ref="external-B",
                )
            )
        assert pair_conflict.value.code is PublicErrorCode.SESSION_CONFLICT

        with pytest.raises(PublicOperationError) as ambiguous:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 750),
                    mode=StartMode.ATTACH,
                    session_id=sibling.session_id,
                    external_ref="external-C",
                )
            )
        assert ambiguous.value.code is PublicErrorCode.SESSION_CONFLICT


@pytest.mark.anyio
async def test_initializing_route_blocks_implicit_drift_but_not_explicit_sibling() -> None:
    """A reclaimable initializing start stays occupied until quarantine or explicit intent."""

    installation_id = _id(IdKind.INSTALLATION, 735)
    now = datetime(2026, 7, 19, 9, 20, tzinfo=UTC)
    memory, _ = _memory_catalog(installation_id, _Clock(now))
    sqlite = _sqlite_catalog(installation_id, _Clock(now))

    for catalog in (memory, sqlite):
        initializing = await catalog.reserve_or_resume(
            await _command(catalog, operation_id=_id(IdKind.REQUEST, 736))
        )
        route = await catalog.resolve_route(initializing.session_id)
        assert route is not None
        assert route.state is TaskRouteState.INITIALIZING

        with pytest.raises(PublicOperationError) as conflict:
            await catalog.reserve_or_resume(
                await _command(
                    catalog,
                    operation_id=_id(IdKind.REQUEST, 737),
                    external_ref="external-B",
                )
            )
        assert conflict.value.code is PublicErrorCode.SESSION_CONFLICT
        assert conflict.value.safe_details == {"reason_code": "workspace_task_exists"}

        sibling = await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 738),
                mode=StartMode.CREATE,
                external_ref="external-B",
            )
        )
        assert sibling.route_action == "created"
        assert sibling.task_id != initializing.task_id


@pytest.mark.anyio
async def test_repository_binding_is_atomic_and_mismatch_precedes_operation_reservation() -> None:
    installation_id = _id(IdKind.INSTALLATION, 705)
    clock = _Clock(datetime(2026, 7, 19, 9, 30, tzinfo=UTC))
    memory, memory_state = _memory_catalog(installation_id, clock)
    sqlite = _sqlite_catalog(installation_id, _Clock(clock.current))
    commitment_a = "hmac-sha256:" + "a" * 64
    commitment_b = "hmac-sha256:" + "b" * 64

    for catalog in (memory, sqlite):
        created = await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 706),
                repository_privacy_commitment=commitment_a,
            )
        )
        route = await catalog.resolve_route(created.session_id)
        assert route is not None
        assert route.repository_privacy_commitment == commitment_a
        if isinstance(catalog, MemoryStartCatalogAdapter):
            before_operations = len(memory_state.operations)
        else:
            row = catalog._db.execute(  # pyright: ignore[reportPrivateUsage]
                "SELECT COUNT(*) FROM start_operations"
            ).fetchone()
            assert row is not None
            before_operations = int(row[0])
        mismatch = await _command(
            catalog,
            operation_id=_id(IdKind.REQUEST, 707),
            mode=StartMode.ATTACH,
            session_id=created.session_id,
            repository_privacy_commitment=commitment_b,
        )
        with pytest.raises(PublicOperationError) as failure:
            await catalog.reserve_or_resume(mismatch)
        assert failure.value.code is PublicErrorCode.SESSION_CONFLICT
        if isinstance(catalog, MemoryStartCatalogAdapter):
            after_operations = len(memory_state.operations)
        else:
            row = catalog._db.execute(  # pyright: ignore[reportPrivateUsage]
                "SELECT COUNT(*) FROM start_operations"
            ).fetchone()
            assert row is not None
            after_operations = int(row[0])
        assert after_operations == before_operations
        assert before_operations == after_operations == 1


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
