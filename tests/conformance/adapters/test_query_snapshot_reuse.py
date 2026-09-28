"""Snapshot parity, bounded reuse and cancellation of off-loop status work."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import apsw
import pytest

from builders.replay import replay_records
from integration.storage.test_append_and_replay import command_from_records, memory_for, sqlite_for
from yoetz.adapters.memory import ledger as module
from yoetz.application import ledger_snapshot
from yoetz.domain.events import LedgerRecord
from yoetz.domain.values import Frontier
from yoetz.kernel.projections import ProjectionState
from yoetz.ports.ledger import ProjectionItem, ProjectionQuery, ProjectionView


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_pages_reuse_frontier_and_rows(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    records = replay_records("all-event-families")
    command, objects = command_from_records(records, expected_frontier=0)
    db = None
    if backend == "memory":
        ledger = memory_for(command, objects)
    else:
        db = apsw.Connection(":memory:")
        ledger = sqlite_for(command, objects, db)
    try:
        await ledger.append_batch(command)
        accepted = tuple([row async for row in ledger.load_events(command.session_id)])
        head = Frontier(accepted[-1].ledger.ingestion_sequence, accepted[-1].entry_digest)
        original_replay, original_items = module.replay, module._projection_items  # pyright: ignore[reportPrivateUsage]
        counts = {"replays": 0, "rows": 0}

        def replay(prefix: tuple[LedgerRecord, ...]) -> ProjectionState:
            counts["replays"] += 1
            return original_replay(prefix)

        def items(
            view: ProjectionView,
            projection: ProjectionState,
            records: tuple[LedgerRecord, ...],
            *,
            task: str,
            session: str,
        ) -> tuple[ProjectionItem, ...]:
            counts["rows"] += 1
            return original_items(view, projection, records, task=task, session=session)

        monkeypatch.setattr(module, "replay", replay)
        monkeypatch.setattr(module, "_projection_items", items)
        query = ProjectionQuery(command.session_id, "history", None, head, 1, None, None)
        first = await ledger.query_projection(query)
        assert first.next_position is not None
        second = await ledger.query_projection(replace(query, position=first.next_position))
        assert first.items != second.items
        assert counts == {"replays": 0, "rows": 1}
        historical = Frontier(accepted[3].ledger.ingestion_sequence, accepted[3].entry_digest)
        historical_query = replace(query, requested_frontier=historical)
        page = await ledger.query_projection(historical_query)
        assert await ledger.query_projection(historical_query) == page
        await ledger.query_projection(replace(historical_query, view="obligations"))
        assert counts["replays"] == 1
        assert page.effective_frontier == historical
    finally:
        if db is not None:
            db.close()


@pytest.mark.anyio
async def test_historical_query_yields_and_joins_cancelled_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = replay_records("all-event-families")
    command, objects = command_from_records(records, expected_frontier=0)
    ledger = memory_for(command, objects)
    await ledger.append_batch(command)
    accepted = tuple([row async for row in ledger.load_events(command.session_id)])
    frontier = Frontier(accepted[3].ledger.ingestion_sequence, accepted[3].entry_digest)
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = module.replay

    def replay(prefix: tuple[LedgerRecord, ...]) -> ProjectionState:
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(10)
        return original(prefix)

    monkeypatch.setattr(module, "replay", replay)
    task = asyncio.create_task(
        ledger.query_projection(
            ProjectionQuery(command.session_id, "history", None, frontier, 1, None, None)
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        # An unrelated ledger read progresses while replay is deliberately held.
        assert await ledger.lookup_operation(command.writer_id, command.operation_id) is not None
        task.cancel()
        scheduled = asyncio.Event()
        asyncio.get_running_loop().call_soon(scheduled.set)
        await scheduled.wait()
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_append_retains_pinned_rows_without_replay(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = replay_records("all-event-families")
    command, objects = command_from_records(records[:4], expected_frontier=0)
    db = None
    if backend == "memory":
        ledger = memory_for(command, objects)
    else:
        db = apsw.Connection(":memory:")
        ledger = sqlite_for(command, objects, db)
    try:
        accepted = await ledger.append_batch(command)
        query = ProjectionQuery(
            command.session_id, "history", None, accepted.result_frontier, 100, None, None
        )
        before = await ledger.query_projection(query)
        next_command, _ = command_from_records(
            records[4:5], expected_frontier=4, request_number=100, objects=objects
        )
        appended = await ledger.append_batch(next_command)
        after = await ledger.query_projection(
            replace(query, requested_frontier=appended.result_frontier)
        )
        assert len(before.items) == 4
        assert len(after.items) == 5

        def forbidden_replay(_records: tuple[LedgerRecord, ...]) -> ProjectionState:
            pytest.fail("unchanged pinned prefix must reuse its authenticated projection")

        monkeypatch.setattr(module, "replay", forbidden_replay)
        historical = await ledger.query_projection(query)
        assert historical.items == before.items
        assert historical.effective_frontier == before.effective_frontier
        assert historical.head_frontier == appended.result_frontier
    finally:
        if db is not None:
            db.close()


@pytest.mark.anyio
async def test_application_snapshot_reuses_current_and_replays_old_prefix_off_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = replay_records("all-event-families")
    command, objects = command_from_records(records, expected_frontier=0)
    ledger = memory_for(command, objects)
    await ledger.append_batch(command)
    accepted = tuple([row async for row in ledger.load_events(command.session_id)])
    head = await ledger.load_frontier()
    original = ledger_snapshot.replay
    replay_threads: list[int] = []
    loop_thread = threading.get_ident()

    def replay(prefix: tuple[LedgerRecord, ...]) -> ProjectionState:
        replay_threads.append(threading.get_ident())
        return original(prefix)

    monkeypatch.setattr(ledger_snapshot, "replay", replay)
    current = await ledger_snapshot.projection_for_records(
        ledger, command.session_id, head, accepted
    )
    assert Frontier(current.frontier, current.head_digest) == head
    assert replay_threads == []
    prefix = accepted[:4]
    past = Frontier(prefix[-1].ledger.ingestion_sequence, prefix[-1].entry_digest)
    historical = await ledger_snapshot.projection_for_records(
        ledger, command.session_id, past, prefix
    )
    assert Frontier(historical.frontier, historical.head_digest) == past
    assert len(replay_threads) == 1 and replay_threads[0] != loop_thread


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_redaction_append_invalidates_pinned_page_cache(
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = replay_records("all-event-families")
    redaction_index = next(
        i for i, row in enumerate(records) if row.schema.name == "redaction_recorded"
    )
    command, objects = command_from_records(records[:redaction_index], expected_frontier=0)
    db = None
    if backend == "memory":
        ledger = memory_for(command, objects)
    else:
        db = apsw.Connection(":memory:")
        ledger = sqlite_for(command, objects, db)
    try:
        accepted = await ledger.append_batch(command)
        query = ProjectionQuery(
            command.session_id, "history", None, accepted.result_frontier, 100, None, None
        )
        await ledger.query_projection(query)
        appended, _ = command_from_records(
            records[redaction_index : redaction_index + 1],
            expected_frontier=redaction_index,
            request_number=100,
            objects=objects,
        )
        await ledger.append_batch(appended)
        calls: list[int] = []
        original = module.replay

        def counted(prefix: tuple[LedgerRecord, ...]) -> ProjectionState:
            calls.append(len(prefix))
            return original(prefix)

        monkeypatch.setattr(module, "replay", counted)
        await ledger.query_projection(query)
        assert calls == [redaction_index]
    finally:
        if db is not None:
            db.close()
