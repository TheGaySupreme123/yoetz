"""Reuse the ledger's trusted projection at an exact frontier; replay older prefixes off-loop."""

from __future__ import annotations

import asyncio

from yoetz.domain.events import LedgerRecord
from yoetz.domain.values import Frontier
from yoetz.kernel.projections import ProjectionState
from yoetz.kernel.reducers import replay
from yoetz.ports.ledger import LedgerPort, ProjectionView


async def projection_for_records(
    ledger: LedgerPort,
    session_id: str,
    frontier: Frontier,
    records: tuple[LedgerRecord, ...],
) -> ProjectionState:
    """Only reuse an adapter-owned, current, fully rebuilt projection at the requested head.

    No global cache retains task content. The adapter owns recovery, append and redaction
    invalidation. Older snapshots and legacy embedders fall back to genesis-authenticated replay
    of immutable records, without carrying SQLite handles or mutable state into the worker.
    """

    if callable(getattr(ledger, "load_projection", None)):
        stored = await ledger.load_projection(session_id, ProjectionView.CANDIDATE_FINDINGS)
        if (
            stored is not None
            and not stored.rebuild_required
            and stored.lag == 0
            and stored.frontier == frontier
            and type(stored.state) is ProjectionState
            and Frontier(stored.state.frontier, stored.state.head_digest) == frontier
        ):
            return stored.state
    return await asyncio.to_thread(replay, records)
