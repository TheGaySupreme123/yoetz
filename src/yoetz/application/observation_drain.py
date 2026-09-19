"""Shared observation-outbox routing and in-process service sweeps."""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import math
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
    OBSERVATION_BACKPRESSURE_REASON,
    OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
)
from yoetz.ports.control import ControlError

__all__ = [
    "DEFAULT_OBSERVATION_SWEEP_BUDGET_SECONDS",
    "DEFAULT_OBSERVATION_SWEEP_LIMIT",
    "EXPECTED_OBSERVATION_BACKPRESSURE_REASONS",
    "MAX_CONSECUTIVE_OBSERVATION_REJECTIONS",
    "ObservationCaptureRecoveryOutcome",
    "ObservationDrainAction",
    "ObservationDrainDecision",
    "ObservationDrainSummary",
    "ObservationOutboxSweeper",
    "RETRYABLE_OBSERVATION_REJECTIONS",
    "WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS",
    "route_observation_ingest",
]

DEFAULT_OBSERVATION_SWEEP_LIMIT: Final = 64
# A sweep yields with its partial summary once it has run this long. The daemon's
# outer deadline (30s) is a hard stop that discards the summary, so a pass that
# delivered rows and was then cancelled read as "no progress" and the loop slept
# the full interval under exactly the backlog that needed it (#564). Yielding
# under the deadline keeps the progress visible, so the loop re-sweeps at once.
DEFAULT_OBSERVATION_SWEEP_BUDGET_SECONDS: Final = 20.0
MAX_CONSECUTIVE_OBSERVATION_REJECTIONS: Final = 128
# One sweep is sequential, so a single worker would do; the spare capacity only exists so a
# handful of stranded threads (a deadline expiring against a parked flock) cannot wedge the
# next pass outright.
_SWEEP_EXECUTOR_WORKERS: Final = 4
# Designed coordination, not delivery failure (#351): the row stays pending and
# retries, but the reason never becomes a coverage gap or a failure-shaped hook
# diagnostic. ADR-022's check barrier is the canonical producer.
EXPECTED_OBSERVATION_BACKPRESSURE_REASONS: Final = frozenset(
    {OBSERVATION_BACKPRESSURE_REASON, OBSERVATION_CONTENT_CAPTURE_PENDING_REASON}
)
RETRYABLE_OBSERVATION_REJECTIONS: Final = frozenset(
    {
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.VAULT_LOCKED.value,
        ObservationGapCode.MAPPING_MISSING.value,
        OBSERVATION_BACKPRESSURE_REASON,
        OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
        "observation_disabled",
        "paused",
    }
)
WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS: Final = frozenset(
    {
        ObservationGapCode.VAULT_LOCKED.value,
        "observation_disabled",
        "paused",
    }
)
SAFE_OBSERVATION_REJECTION_REASONS: Final = frozenset(
    {item.value for item in ObservationGapCode} | RETRYABLE_OBSERVATION_REJECTIONS | {"duplicate"}
)


class ObservationCaptureRecoveryOutcome(str, Enum):  # noqa: UP042 - stable internal value
    """Payload-free maintenance outcomes, never ledger or input-loss dispositions."""

    RECOVERED = "capture_inventory_recovered"
    MAPPING_MISSING = "capture_inventory_mapping_missing"
    ROUTE_UNAVAILABLE = "capture_inventory_route_unavailable"
    INVENTORY_UNKNOWN = "capture_inventory_unknown"
    DISABLED = "capture_inventory_disabled"
    BUSY = "capture_inventory_busy"
    TIMEOUT = "capture_inventory_timeout"


class ObservationDrainAction(str, Enum):  # noqa: UP042 - stable internal value
    ACKNOWLEDGE = "acknowledge"
    RETRY = "retry"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class ObservationDrainDecision:
    action: ObservationDrainAction
    reason: str | None


@dataclass(frozen=True, slots=True)
class ObservationControlFailure(ObservationIngestResult):
    """Adapter failure, with no assertion that the ledger refused an input."""

    control_reason: str
    control_retryable: bool
    correlation_id: str | None
    failure_stage: str = "control"


def observation_control_failure(error: ControlError) -> ObservationControlFailure:
    return ObservationControlFailure(
        ObservationIngestDisposition.REJECTED,
        "control_" + error.reason,
        None,
        error.reason,
        error.retryable,
        error.correlation_id,
    )


def route_observation_ingest(
    result: ObservationIngestResult,
    *,
    row: ObservationOutboxRow | None = None,
) -> ObservationDrainDecision:
    """Classify one typed ingest result without performing storage side effects."""

    if result.disposition in {
        ObservationIngestDisposition.ACCEPTED,
        ObservationIngestDisposition.DUPLICATE,
    }:
        return ObservationDrainDecision(ObservationDrainAction.ACKNOWLEDGE, None)
    if isinstance(result, ObservationControlFailure):
        # Invalid/oversized requests cannot heal by reconnecting. Protocol
        # failures can occur before or after admission: retain exact replay
        # identity and stop using that connection, never forge ledger refusal.
        terminal = result.control_reason in {"frame_too_large", "invalid_request"}
        if row is not None and result.control_reason not in {
            "vault_locked",
            "method_forbidden",
            "protocol_mismatch",
            "service_incompatible",
            "peer_untrusted",
            "endpoint_unsafe",
            "privacy_projection_blocked",
        }:
            attempts = (
                row.consecutive_reason_attempts + 1 if row.last_reason == result.reason else 1
            )
            terminal = terminal or attempts >= MAX_CONSECUTIVE_OBSERVATION_REJECTIONS
        return ObservationDrainDecision(
            ObservationDrainAction.QUARANTINE if terminal else ObservationDrainAction.RETRY,
            result.reason,
        )
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
    if (
        action is ObservationDrainAction.RETRY
        and reason
        not in (
            EXPECTED_OBSERVATION_BACKPRESSURE_REASONS | WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS
        )
        and row is not None
    ):
        next_consecutive = row.consecutive_reason_attempts + 1 if row.last_reason == reason else 1
        if next_consecutive >= MAX_CONSECUTIVE_OBSERVATION_REJECTIONS:
            action = ObservationDrainAction.QUARANTINE
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
    # Wall-clock budget for one pass, measured from its start; ``None`` never
    # yields on time. Checked between rows, so one slow ingest can still overrun
    # it: the caller's deadline remains the hard bound.
    budget_seconds: float | None = None
    # Production ready composition supplies the installation maintenance gate here. It is held
    # only across one coordinator ingest, so a long backlog cannot keep ordinary workflow control
    # behind the entire 20-second sweep. Local outbox bookkeeping stays outside the gate and is
    # already fenced by the per-workspace lease.
    ingest_gate: asyncio.Lock | None = None
    # Recovery owns no hook output and no observation row. It runs outside the
    # control/ingest gate, under the coordinator's capture lock instead.
    capture_recovery: (
        Callable[[str], Awaitable[ObservationCaptureRecoveryOutcome | None]] | None
    ) = None
    capture_recovery_budget_seconds: float = 5.0
    _capture_recovery_after: str | None = field(default=None, init=False, repr=False)
    # Tests may provide a monotonic source so budget boundaries can be exercised without wall
    # clock sleeps. Production leaves this unset and uses the running loop's monotonic clock.
    _monotonic: Callable[[], float] | None = field(default=None, repr=False)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _selection_seen_workspaces: set[str] = field(
        default_factory=lambda: set[str](), init=False, repr=False
    )

    def __post_init__(self) -> None:
        if type(self.limit) is not int or isinstance(self.limit, bool) or self.limit < 1:
            raise ValueError("observation_sweep_limit_invalid")
        if self.budget_seconds is not None and (
            type(self.budget_seconds) is not float or not self.budget_seconds > 0.0
        ):
            raise ValueError("observation_sweep_budget_invalid")

        if (
            type(self.capture_recovery_budget_seconds) is not float
            or not math.isfinite(self.capture_recovery_budget_seconds)
            or self.capture_recovery_budget_seconds <= 0.0
        ):
            raise ValueError("observation_capture_recovery_budget_invalid")

    async def _recover_capture_inventory(
        self, workspaces: tuple[str, ...], *, remaining: float | None
    ) -> tuple[ObservationCaptureRecoveryOutcome, ...]:
        """Give at most four workspace lanes a fair, bounded maintenance turn.

        Neither an empty outbox nor native admission pressure can suppress this
        turn. An exhausted deadline rotates the next pass past the slow lane.
        The callback must join side-effecting workers before cancellation returns.
        """

        recover = self.capture_recovery
        if recover is None or not workspaces:
            return ()
        budget = self.capture_recovery_budget_seconds
        if remaining is not None:
            budget = min(budget, remaining)
        if budget <= 0.0:
            return ()
        ordered = sorted(set(workspaces), key=str.encode)
        after = self._capture_recovery_after
        if after is not None:
            ordered = [item for item in ordered if item > after] + [
                item for item in ordered if item <= after
            ]
        deadline = asyncio.get_running_loop().time() + budget
        outcomes: list[ObservationCaptureRecoveryOutcome] = []
        for workspace in ordered[:4]:
            available = deadline - asyncio.get_running_loop().time()
            if available <= 0.0:
                break
            self._capture_recovery_after = workspace
            try:
                async with asyncio.timeout(available):
                    result = await recover(workspace)
            except TimeoutError:
                result = ObservationCaptureRecoveryOutcome.TIMEOUT
            except Exception:
                # Do not copy exception text, paths, or payloads into diagnostics.
                result = ObservationCaptureRecoveryOutcome.INVENTORY_UNKNOWN
            if result is not None:
                if type(result) is not ObservationCaptureRecoveryOutcome:
                    result = ObservationCaptureRecoveryOutcome.INVENTORY_UNKNOWN
                outcomes.append(result)
        return tuple(outcomes)

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

    async def _ingest(self, request: ObservationIngestRequest) -> ObservationIngestResult:
        gate = self.ingest_gate
        if gate is None:
            return await self.coordinator.ingest_request(request)
        async with gate:
            return await self.coordinator.ingest_request(request)

    async def sweep(self) -> ObservationDrainSummary:
        loop = asyncio.get_running_loop()
        monotonic = loop.time if self._monotonic is None else self._monotonic
        deadline = None if self.budget_seconds is None else monotonic() + self.budget_seconds
        preparation_error: Exception | None = None
        try:
            rows, lifecycle_workspaces = await self._off_loop(
                self._prepare_pending_rows_and_lifecycle_workspaces
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Admission maintenance is a fail-closed prerequisite for delivery. Take a fresh
            # workspace inventory for the independent capture-recovery lane, but discard any
            # partially prepared rows and re-raise the maintenance failure after that turn. This
            # keeps an unknown capture inventory recoverable without claiming admission succeeded.
            preparation_error = error
            rows = ()
            lifecycle_workspaces = await self._off_loop(self.local.pending_workspaces)
        attempted = 0
        acknowledged = 0
        retry_pending = 0
        quarantined = 0
        reasons: dict[str, int] = {}
        retired_sessions: set[tuple[str, str]] = set()

        workspaces = tuple(
            dict.fromkeys((*lifecycle_workspaces, *(workspace for workspace, _row in rows)))
        )
        remaining = None if deadline is None else deadline - monotonic()
        for outcome in await self._recover_capture_inventory(workspaces, remaining=remaining):
            reasons[outcome.value] = reasons.get(outcome.value, 0) + 1
        if preparation_error is not None:
            raise preparation_error
        for workspace in workspaces:
            if deadline is not None and monotonic() >= deadline:
                # Budget spent: return what this pass resolved so far. The rows
                # left are still pending and the next pass selects them fairly.
                break
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
                # A busy SessionStart/SessionEnd is durable lifecycle work even
                # when it produced no outbox row. Reconcile it before routing
                # this workspace's delivery lanes; the operation is bounded and
                # uses the same workspace/session lock order as hook ingress.
                bound_sessions, pending_lifecycle_sessions = await self._off_loop(
                    partial(self.local.lifecycle_reconciliation_snapshot, workspace)
                )
                if pending_lifecycle_sessions:
                    reconciled = await self._off_loop(
                        partial(self.local.reconcile_pending_session_lifecycles, workspace)
                    )
                    if reconciled:
                        # Re-read the same bounded snapshot after the pass.
                        # A foreign owner or an unresolved generation can make
                        # reconciliation return successfully while deliberately
                        # retaining its intent; routing from the pre-pass
                        # snapshot would then deliver that row as if membership
                        # had converged.
                        bound_sessions, pending_lifecycle_sessions = await self._off_loop(
                            partial(self.local.lifecycle_reconciliation_snapshot, workspace)
                        )
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
                    if deadline is not None and monotonic() >= deadline:
                        break
                    session_key = (workspace, row.codex_session_id)
                    if session_key in retired_sessions:
                        continue
                    queue = lane_queues.get(row.codex_session_id)
                    if not queue or queue[0] != row:
                        retired_sessions.add(session_key)
                        continue
                    queue.pop(0)
                    # Reconcile a raw host-session membership before delivery.
                    # A hook may have captured this row while recovery held the
                    # workspace reservation; leave it untouched if the same
                    # reservation/session locks are still busy.
                    if (
                        row.codex_session_id not in bound_sessions
                        or row.codex_session_id in pending_lifecycle_sessions
                    ) and not await self._off_loop(
                        partial(self.local.reconcile_outbox_session_lifecycle, workspace, row)
                    ):
                        retired_sessions.add(session_key)
                        continue
                    attempted += 1
                    request = ObservationIngestRequest(
                        codex_session_id=row.codex_session_id,
                        envelope=row.envelope,
                    )
                    try:
                        result = await self._ingest(request)
                    except Exception:
                        result = ObservationIngestResult(
                            ObservationIngestDisposition.REJECTED,
                            ObservationGapCode.SERVICE_UNAVAILABLE.value,
                            None,
                        )
                    decision = route_observation_ingest(result, row=row)
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
                        if decision.reason not in EXPECTED_OBSERVATION_BACKPRESSURE_REASONS:
                            # A deferral behind a check barrier is designed
                            # coordination; recording it as a coverage gap would
                            # project a false current condition (#351).
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
                        if decision.reason in WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS:
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
        return self._fair_pending_rows_and_lifecycle_workspaces()[0]

    def _prepare_pending_rows_and_lifecycle_workspaces(
        self,
    ) -> tuple[
        tuple[tuple[str, ObservationOutboxRow], ...],
        tuple[str, ...],
    ]:
        for workspace in self.local.pending_workspaces():
            # A fresh service flushes accounts from the preceding runtime
            # before extending them. Later sweeps observe due deadlines and
            # recovery dwell even when the host emits no further hook.
            self.local.maintain_selected_admission(
                workspace,
                force=workspace not in self._selection_seen_workspaces,
            )
            self._selection_seen_workspaces.add(workspace)
            if len(self._selection_seen_workspaces) > 256:
                # Forgetting an entry only forces a conservative flush next
                # time; it never acknowledges or discards accepted inputs.
                self._selection_seen_workspaces.remove(min(self._selection_seen_workspaces))
        return self._fair_pending_rows_and_lifecycle_workspaces()

    def _fair_pending_rows_and_lifecycle_workspaces(
        self,
    ) -> tuple[
        tuple[tuple[str, ObservationOutboxRow], ...],
        tuple[str, ...],
    ]:
        # Fairness may reorder *lanes*, never rows *within* a lane: each session's rows keep
        # strict outbox (FIFO) order. Sorting a lane by attempts once delivered a session's
        # newer rows ahead of its older, more-attempted ones, which advanced the ingest cursor
        # past the older rows and destroyed them as terminal cursor_stale quarantine (#272).
        lanes: dict[tuple[str, str], list[ObservationOutboxRow]] = {}
        lifecycle_workspaces = self.local.pending_workspaces()
        for workspace in lifecycle_workspaces:
            for row in self.local.list_pending_outbox_rows(workspace):
                lanes.setdefault((workspace, row.codex_session_id), []).append(row)
        # UTF-8 byte order equals string order for encodable strings. Validate once
        # so malformed keys still fail, without retaining duplicate encoded keys.
        for workspace, session in lanes:
            workspace.encode()
            session.encode()
        # Only the selected lane changes priority. Its selection count is also its
        # FIFO cursor, avoiding list-head shifts and a second per-lane count map.
        pending = [(rows[0].attempts, 0, lane[0], lane[1], rows) for lane, rows in lanes.items()]
        del lanes
        heapq.heapify(pending)
        selected: list[tuple[str, ObservationOutboxRow]] = []
        while pending and len(selected) < self.limit:
            _, count, workspace, session, queue = heapq.heappop(pending)
            selected.append((workspace, queue[count]))
            count += 1
            if count < len(queue):
                heapq.heappush(pending, (queue[count].attempts, count, workspace, session, queue))
        return tuple(selected), lifecycle_workspaces
