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
