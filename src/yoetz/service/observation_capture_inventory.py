"""READY-owned, complete capture-inventory proof for admission and idle recovery.

Only the service catalog and opened task bundles establish completeness. A host
callback, one task's zero count, or an empty outbox never authorizes this proof.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation import (
    ObservationCaptureBacklog,
    ObservationCaptureTicket,
    observation_capture_ticket_id,
)
from yoetz.domain.values import timestamp_from_datetime
from yoetz.ports.clock import ClockPort
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import (
    BundleRuntimePort,
    RouteAccess,
    RouteCommand,
    RuntimeCapability,
    TaskRuntime,
)
from yoetz.ports.start_catalog import StartCatalogPort, TaskRoute, TaskRouteState


async def _settled_local_call[ResultT](call: Callable[[], ResultT]) -> ResultT:
    """Do not release capture exclusion while a cancelled proof write can still commit.

    Local-store flock acquisition is bounded. Cancellation stops subsequent
    inventory work, but a worker already mutating the proof must finish first.
    """

    # Keep the executor future itself, not an intermediary Task that READY
    # teardown could cancel while its thread is still publishing a proof.
    worker = asyncio.get_running_loop().run_in_executor(
        None, partial(contextvars.copy_context().run, call)
    )
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            # More than one owner may cancel (row deadline, then READY close).
            # Neither cancellation transfers ownership of a live mutation.
            if worker.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def build_capture_inventory_bootstrap(
    *,
    catalog: StartCatalogPort,
    runtime: BundleRuntimePort,
    local_observation: LocalObservationStore,
    clock: ClockPort,
    generation_is_current: Callable[[], bool],
) -> Callable[[str, TaskRuntime, TaskObservationPort], Awaitable[bool]]:
    """Build the real catalog callback shared by capture and independent maintenance."""

    async def bootstrap_capture_reservations(
        workspace: str,
        current_runtime: TaskRuntime,
        current_store: TaskObservationPort,
    ) -> bool:
        """Read the complete catalog inventory before central admission.

        The coordinator invokes this callback while its process-wide capture
        lock is held.  The current route's repository commitment scopes the
        inventory; matching routes are all accounted for, while an unreadable
        or inactive route fails closed instead of being silently omitted.
        """

        try:
            if not generation_is_current():
                raise ValueError("capture_generation_changed")
            current_route = await catalog.resolve_route(current_runtime.session_id)
            if (
                current_route is None
                or current_route.state is not TaskRouteState.ACTIVE
                or current_route.task_id != current_runtime.task_id
                or current_route.repository_privacy_commitment is None
            ):
                raise ValueError("capture_current_route_unavailable")
            repository = current_route.repository_privacy_commitment
            recovery_routes = getattr(catalog, "capture_inventory_routes", None)
            if not callable(recovery_routes):
                raise ValueError("capture_catalog_inventory_unavailable")
            raw_routes = await cast(
                Callable[[str], Awaitable[tuple[TaskRoute, ...]]], recovery_routes
            )(repository)
            if type(raw_routes) is not tuple:
                raise ValueError("capture_catalog_inventory_invalid")
            if any(type(route) is not TaskRoute for route in raw_routes):
                raise ValueError("capture_catalog_inventory_invalid")
            all_routes = raw_routes
            if len({route.task_id for route in all_routes}) != len(all_routes):
                raise ValueError("capture_catalog_inventory_duplicate")
            if any(
                route.repository_privacy_commitment not in {repository, None}
                for route in all_routes
            ):
                raise ValueError("capture_catalog_inventory_invalid")
            routes = all_routes
            if not routes or len(routes) > 256:
                raise ValueError("capture_catalog_inventory_incomplete")
            if any(route.state is not TaskRouteState.ACTIVE for route in routes):
                raise ValueError("capture_catalog_inventory_inactive")
            if current_route.task_id not in {route.task_id for route in routes}:
                raise ValueError("capture_catalog_inventory_incomplete")
            inventory: dict[str, ObservationCaptureBacklog] = {}
            ticket_ids_by_task: dict[str, tuple[str, ...]] = {}
            for route in sorted(routes, key=lambda item: item.task_id.encode()):
                task_runtime: TaskRuntime | None = None
                release = False
                try:
                    if route.task_id == current_runtime.task_id:
                        task_runtime = current_runtime
                        task_store = current_store
                    else:
                        binding = await catalog.session_binding(route.session_id)
                        if (
                            binding is None
                            or binding.task_id != route.task_id
                            or binding.session_id != route.session_id
                        ):
                            raise ValueError("capture_route_binding_unavailable")
                        task_runtime = await runtime.route(
                            RouteCommand(
                                session_id=route.session_id,
                                writer_id=binding.writer_id,
                                access=RouteAccess.WRITE,
                                required_capabilities=frozenset({RuntimeCapability.WRITE}),
                            )
                        )
                        release = True
                        task_store = task_runtime.observation
                    if (
                        task_runtime.task_id != route.task_id
                        or task_runtime.session_id != route.session_id
                    ):
                        raise ValueError("capture_task_route_changed")
                    if task_store is None:
                        raise ValueError("capture_task_observation_unavailable")
                    reader = getattr(task_store, "capture_backlog", None)
                    if not callable(reader):
                        raise ValueError("capture_task_backlog_unavailable")
                    backlog = reader(workspace)
                    if type(backlog) is not ObservationCaptureBacklog:
                        raise ValueError("capture_task_backlog_invalid")
                    inventory[route.task_id] = backlog
                    list_pending = getattr(task_store, "list_pending_capture_tickets", None)
                    if callable(list_pending):
                        raw_tickets = list_pending(route.task_id)
                        tickets = cast(tuple[object, ...], raw_tickets)
                        if type(raw_tickets) is not tuple or any(
                            type(ticket) is not ObservationCaptureTicket for ticket in tickets
                        ):
                            raise ValueError("capture_task_ticket_inventory_invalid")
                        scoped_ids: list[str] = []
                        typed_tickets = cast(tuple[ObservationCaptureTicket, ...], tickets)
                        for ticket in typed_tickets:
                            if ticket.task_id != route.task_id:
                                raise ValueError("capture_task_ticket_owner_invalid")
                            if ticket.workspace_commitment == workspace:
                                ticket_id = observation_capture_ticket_id(ticket)
                                if ticket_id in scoped_ids:
                                    raise ValueError("capture_task_ticket_duplicate")
                                scoped_ids.append(ticket_id)
                        if len(scoped_ids) == backlog.count and all(
                            ticket.workspace_commitment == workspace for ticket in typed_tickets
                        ):
                            ticket_ids_by_task[route.task_id] = tuple(
                                sorted(scoped_ids, key=str.encode)
                            )
                finally:
                    if release and task_runtime is not None:
                        with contextlib.suppress(Exception):
                            await runtime.release(task_runtime)
                # Yield between bounded bundle reads. Do not monopolize the
                # service loop for the whole repository inventory.
                await asyncio.sleep(0)
            # Re-read the catalog after all bundle reads.  A route change while
            # the inventory was in flight must not mint a proof for a stale set.
            final_raw_routes = await cast(
                Callable[[str], Awaitable[tuple[TaskRoute, ...]]], recovery_routes
            )(repository)
            if not generation_is_current():
                raise ValueError("capture_generation_changed")
            if final_raw_routes != raw_routes:
                raise ValueError("capture_catalog_inventory_changed")
            bootstrap = getattr(local_observation, "bootstrap_capture_reservations", None)
            if not callable(bootstrap):
                raise ValueError("capture_bootstrap_unavailable")
            observed_at = timestamp_from_datetime(clock.now_utc())
            return bool(
                await _settled_local_call(
                    partial(
                        bootstrap,
                        workspace,
                        inventory,
                        observed_at=observed_at,
                        complete=True,
                        proof_guard=generation_is_current,
                        ticket_ids_by_task=ticket_ids_by_task or None,
                    )
                )
            )
        except asyncio.CancelledError:
            # A timed-out inventory is not an empty inventory. Await the
            # bounded local write before the caller releases the capture lock.
            with contextlib.suppress(Exception):
                await _settled_local_call(
                    partial(local_observation.mark_capture_backlog_scope_unknown, workspace)
                )
            raise
        except Exception:
            mark_unknown = getattr(local_observation, "mark_capture_backlog_scope_unknown", None)
            if callable(mark_unknown):
                with contextlib.suppress(Exception):
                    await _settled_local_call(partial(mark_unknown, workspace))
            return False

    return bootstrap_capture_reservations
