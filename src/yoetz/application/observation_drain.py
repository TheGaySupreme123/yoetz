"""Shared observation-outbox routing and in-process service sweeps."""

from __future__ import annotations

import asyncio
import contextlib
from asyncio import Future
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Final, Protocol

from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    ObservationOutboxRow,
)
from yoetz.domain.observation import (
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
)

__all__ = [
    "DEFAULT_OBSERVATION_SWEEP_LIMIT",
    "ObservationDrainAction",
    "ObservationDrainDecision",
    "ObservationDrainSummary",
    "ObservationOutboxSweeper",
    "RETRYABLE_OBSERVATION_REJECTIONS",
    "route_observation_ingest",
]

DEFAULT_OBSERVATION_SWEEP_LIMIT: Final = 64
# One sweep is sequential, so a single worker would do; the spare capacity only exists so a
# handful of stranded threads (a deadline expiring against a parked flock) cannot wedge the
# next pass outright.
_SWEEP_EXECUTOR_WORKERS: Final = 4
RETRYABLE_OBSERVATION_REJECTIONS: Final = frozenset(
    {
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.VAULT_LOCKED.value,
        ObservationGapCode.MAPPING_MISSING.value,
        "observation_disabled",
        "paused",
    }
)
_WORKSPACE_GLOBAL_STOP_REASONS: Final = frozenset(
    {
        ObservationGapCode.VAULT_LOCKED.value,
        "observation_disabled",
        "paused",
    }
)
SAFE_OBSERVATION_REJECTION_REASONS: Final = frozenset(
    {item.value for item in ObservationGapCode} | RETRYABLE_OBSERVATION_REJECTIONS | {"duplicate"}
)


class ObservationDrainAction(str, Enum):  # noqa: UP042 - stable internal value
    ACKNOWLEDGE = "acknowledge"
    RETRY = "retry"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class ObservationDrainDecision:
    action: ObservationDrainAction
    reason: str | None


def route_observation_ingest(result: ObservationIngestResult) -> ObservationDrainDecision:
    """Classify one typed ingest result without performing storage side effects."""

    if result.disposition in {
        ObservationIngestDisposition.ACCEPTED,
        ObservationIngestDisposition.DUPLICATE,
    }:
        return ObservationDrainDecision(ObservationDrainAction.ACKNOWLEDGE, None)
    supplied_reason = result.reason
    reason = (
        supplied_reason
        if supplied_reason in SAFE_OBSERVATION_REJECTION_REASONS
        else ObservationGapCode.SERVICE_UNAVAILABLE.value
    )
    action = (
        ObservationDrainAction.RETRY
        if reason in RETRYABLE_OBSERVATION_REJECTIONS
        else ObservationDrainAction.QUARANTINE
    )
    return ObservationDrainDecision(action, reason)


def _release_entered_lease(lease: AbstractContextManager[bool], entering: Future[bool]) -> None:
    """Release a drain lease whose enter completed after its sweep was already cancelled.

    Cancelling the await does not stop a worker already inside ``flock``. Releasing is a single
    unlock and close, so doing it from the loop callback costs nothing measurable and is the only
    place left that still knows the descriptor exists.
    """

    if entering.cancelled() or entering.exception() is not None:
        return
    with contextlib.suppress(Exception):
        lease.__exit__(None, None, None)


class ObservationIngestCoordinator(Protocol):
    def ingest_request(
        self, request: ObservationIngestRequest
    ) -> Awaitable[ObservationIngestResult]: ...


@dataclass(frozen=True, slots=True)
class ObservationDrainSummary:
    attempted: int
    acknowledged: int
    retry_pending: int
    quarantined: int
    reasons: tuple[tuple[str, int], ...]


@dataclass(slots=True)
class ObservationOutboxSweeper:
    """Drain a fair bounded pass directly through the READY coordinator."""

    local: LocalObservationStore
    coordinator: ObservationIngestCoordinator
    limit: int = DEFAULT_OBSERVATION_SWEEP_LIMIT
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.limit) is not int or isinstance(self.limit, bool) or self.limit < 1:
            raise ValueError("observation_sweep_limit_invalid")

    def _off_loop[ResultT](self, call: Callable[[], ResultT]) -> Future[ResultT]:
        """Run one blocking local-store call off the caller's event loop.

        Every ``LocalObservationStore`` method takes a blocking cross-process lock and re-encodes
        the whole workspace document. Running those on the service loop thread let a hook storm
        hold the daemon's control plane for minutes, with no await point for the sweep deadline to
        cancel at (#238). The store's own reentrant thread lock is acquired and released inside a
        single worker thread on every hop, so no lock is ever held across an await.

        The pool is the sweeper's own rather than ``asyncio.to_thread``'s shared default: a sweep
        that hits its deadline while a cross-process flock is contended cannot cancel the worker
        immediately. The store bounds that wait, and this dedicated pool keeps those bounded
        workers from delaying unrelated default-executor work in the meantime.

        The future is returned rather than awaited so the lease enter below can be shielded and
        still observed after a cancellation.
        """

        loop = asyncio.get_running_loop()
        executor = self._executor
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=_SWEEP_EXECUTOR_WORKERS,
                thread_name_prefix="yoetz-obs-sweep",
            )
            self._executor = executor
        return loop.run_in_executor(executor, call)

    def close(self) -> None:
        """Release the sweeper's worker pool; any running lock wait is itself bounded."""

        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    async def sweep(self) -> ObservationDrainSummary:
        rows = await self._off_loop(self._fair_pending_rows)
        attempted = 0
        acknowledged = 0
        retry_pending = 0
        quarantined = 0
        reasons: dict[str, int] = {}
        retired_sessions: set[tuple[str, str]] = set()

        workspaces = tuple(dict.fromkeys(workspace for workspace, _row in rows))
        for workspace in workspaces:
            # The lease is a POSIX file lock, which belongs to the open descriptor rather than to
            # the thread that took it, so entering and leaving it from different worker threads is
            # correct and keeps the whole hold off the event loop.
            lease = self.local.drain_lease(workspace)
            entering = self._off_loop(lease.__enter__)
            entered = False
            try:
                # Enter inside the guarded region. Outside it, a cancellation landing on this
                # await left the flock and its descriptor owned with no ``__exit__`` anywhere on
                # the path -- released only whenever the generator was finalized. The shield
                # keeps the worker's enter observable so the release below is still reached when
                # it lands after the sweep was cancelled.
                owned = await asyncio.shield(entering)
                entered = True
                if not owned:
                    continue
                # The authoritative FIFO queues, re-read under the lease. A selected row is
                # attempted only while it is still the head of its session's queue: anything
                # else means another drain moved the outbox between selection and the lease,
                # and delivering it could jump an earlier pending sibling (#272).
                lane_queues: dict[str, list[ObservationOutboxRow]] = {}
                for pending_row in await self._off_loop(
                    partial(self.local.list_pending_outbox_rows, workspace)
                ):
                    lane_queues.setdefault(pending_row.codex_session_id, []).append(pending_row)
                for selected_workspace, row in rows:
                    if selected_workspace != workspace:
                        continue
                    session_key = (workspace, row.codex_session_id)
                    if session_key in retired_sessions:
                        continue
                    queue = lane_queues.get(row.codex_session_id)
                    if not queue or queue[0] != row:
                        retired_sessions.add(session_key)
                        continue
                    queue.pop(0)
                    attempted += 1
                    request = ObservationIngestRequest(
                        codex_session_id=row.codex_session_id,
                        envelope=row.envelope,
                    )
                    try:
                        result = await self.coordinator.ingest_request(request)
                    except Exception:
                        result = ObservationIngestResult(
                            ObservationIngestDisposition.REJECTED,
                            ObservationGapCode.SERVICE_UNAVAILABLE.value,
                            None,
                        )
                    decision = route_observation_ingest(result)
                    attempted_row = await self._off_loop(
                        partial(
                            self.local.bump_outbox_row_attempt,
                            workspace,
                            row,
                            reason=decision.reason,
                        )
                    )
                    if attempted_row is None:
                        # The row changed under the lease -- the lane's true order is no longer
                        # what this pass selected, so it sits out the rest of the pass.
                        retired_sessions.add(session_key)
                        continue
                    if decision.reason is not None:
                        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
                        await self._off_loop(
                            partial(self.local.note_coverage_gap, workspace, decision.reason)
                        )

                    if decision.action is ObservationDrainAction.RETRY:
                        # The head of this lane stays pending, so no later row of the same
                        # session may be delivered this pass -- stepping over it is exactly the
                        # reorder that strands earlier rows behind the ingest cursor (#272).
                        retired_sessions.add(session_key)
                        if decision.reason == ObservationGapCode.MAPPING_MISSING.value:
                            # The session ended while unmapped: nothing will ever map it,
                            # unless a concurrent attach currently owns its lifecycle
                            # lock. Terminalize atomically with that lock so an attach
                            # already in flight wins this race (#275, #283 review).
                            moved = await self._off_loop(
                                partial(
                                    self.local.quarantine_ended_unmapped_session,
                                    workspace,
                                    row.codex_session_id,
                                    decision.reason,
                                )
                            )
                            if moved:
                                quarantined += moved
                                continue
                        retry_pending += 1
                        if decision.reason is not None:
                            await self._off_loop(
                                partial(
                                    self.local.note_outbox_session_reason,
                                    workspace,
                                    row.codex_session_id,
                                    decision.reason,
                                )
                            )
                        if decision.reason in _WORKSPACE_GLOBAL_STOP_REASONS:
                            # This condition cannot heal for another lane in the
                            # same workspace during this pass. Preserve the
                            # attempted lane's bookkeeping, then stop before
                            # issuing redundant coordinator calls (#283 review).
                            break
                        continue
                    if decision.action is ObservationDrainAction.QUARANTINE:
                        if decision.reason == ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value:
                            retired_sessions.add(session_key)
                            quarantined += await self._off_loop(
                                partial(
                                    self.local.quarantine_outbox_session,
                                    workspace,
                                    row.codex_session_id,
                                    decision.reason,
                                )
                            )
                            continue
                        if await self._off_loop(
                            partial(
                                self.local.quarantine_outbox_row,
                                workspace,
                                attempted_row,
                                decision.reason or ObservationGapCode.SERVICE_UNAVAILABLE.value,
                            )
                        ):
                            quarantined += 1
                        continue
                    if await self._off_loop(
                        partial(self.local.acknowledge_outbox_row, workspace, attempted_row)
                    ):
                        acknowledged += 1
            finally:
                if entered:
                    await self._off_loop(partial(lease.__exit__, None, None, None))
                else:
                    entering.add_done_callback(partial(_release_entered_lease, lease))

        return ObservationDrainSummary(
            attempted=attempted,
            acknowledged=acknowledged,
            retry_pending=retry_pending,
            quarantined=quarantined,
            reasons=tuple(sorted(reasons.items(), key=lambda item: item[0].encode())),
        )

    def _fair_pending_rows(self) -> tuple[tuple[str, ObservationOutboxRow], ...]:
        # Fairness may reorder *lanes*, never rows *within* a lane: each session's rows keep
        # strict outbox (FIFO) order. Sorting a lane by attempts once delivered a session's
        # newer rows ahead of its older, more-attempted ones, which advanced the ingest cursor
        # past the older rows and destroyed them as terminal cursor_stale quarantine (#272).
        lanes: dict[tuple[str, str], list[ObservationOutboxRow]] = {}
        for workspace in self.local.pending_workspaces():
            for row in self.local.list_pending_outbox_rows(workspace):
                lanes.setdefault((workspace, row.codex_session_id), []).append(row)
        selected_per_lane = {lane: 0 for lane in lanes}
        selected: list[tuple[str, ObservationOutboxRow]] = []
        while lanes and len(selected) < self.limit:
            lane = min(
                lanes,
                key=lambda item: (
                    lanes[item][0].attempts,
                    selected_per_lane[item],
                    item[0].encode(),
                    item[1].encode(),
                ),
            )
            workspace, _ = lane
            queue = lanes[lane]
            selected.append((workspace, queue.pop(0)))
            selected_per_lane[lane] += 1
            if not queue:
                lanes.pop(lane)
        return tuple(selected)
