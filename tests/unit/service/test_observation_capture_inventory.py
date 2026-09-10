"""Admission-independent recovery through the production READY catalog callback (#695)."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    clear_mapping,
    store_mapping,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_drain import ObservationOutboxSweeper
from yoetz.domain.observation import (
    ObservationCaptureBacklog,
    ObservationCaptureTicket,
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.ports.start_catalog import SessionBinding, StartCatalogPort, TaskRoute, TaskRouteState
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.ids import IdKind, new_id
from yoetz.service.observation_capture_inventory import build_capture_inventory_bootstrap

_NOW = Timestamp("2026-09-10T19:00:00.000Z")
_REPOSITORY = "hmac-sha256:" + "9" * 64
_ZERO = ObservationCaptureBacklog(0, 0, None)


def route() -> TaskRoute:
    task = new_id(IdKind.TASK)
    path = f"tasks/{task}"
    return TaskRoute(
        task_id=task,
        session_id=new_id(IdKind.SESSION),
        bundle_relpath=path,
        route_generation=1,
        state=TaskRouteState.ACTIVE,
        route_identity_digest=canonical_digest(
            {"task_id": task, "bundle_relpath": path, "route_generation": 1}
        ),
        repository_privacy_commitment=_REPOSITORY,
    )


class Catalog:
    def __init__(self, routes: tuple[TaskRoute, ...]) -> None:
        self.routes = routes
        self.reads = 0
        self.on_read: Callable[[], None] | None = None

    async def resolve_route(self, session: str) -> TaskRoute | None:
        return next((item for item in self.routes if item.session_id == session), None)

    async def capture_inventory_routes(self, repository: str) -> tuple[TaskRoute, ...]:
        self.reads += 1
        if self.on_read is not None:
            self.on_read()
        return tuple(
            item for item in self.routes if item.repository_privacy_commitment in {repository, None}
        )[:257]

    async def session_binding(self, session: str) -> SessionBinding | None:
        item = await self.resolve_route(session)
        if item is None:
            return None
        return SessionBinding(item.task_id, item.session_id, new_id(IdKind.WRITER))


class Store:
    def __init__(self) -> None:
        self.backlog = _ZERO
        self.failure = False
        self.reads = 0

    def capture_backlog(self, workspace: str) -> ObservationCaptureBacklog:
        del workspace
        self.reads += 1
        if self.failure:
            raise OSError("synthetic unreadable task")
        return self.backlog

    def list_pending_capture_tickets(self, task_id: str) -> tuple[ObservationCaptureTicket, ...]:
        del task_id
        return ()


class Router:
    def __init__(self, runtimes: tuple[TaskRuntime, ...]) -> None:
        self.runtimes = {item.session_id: item for item in runtimes}
        self.acquired = 0
        self.released = 0

    async def route(self, command: RouteCommand) -> TaskRuntime:
        result = self.runtimes[command.session_id]
        self.acquired += 1
        return result

    async def release(self, runtime: TaskRuntime) -> None:
        assert runtime in self.runtimes.values()
        self.released += 1


@dataclass
class Harness:
    local: LocalObservationStore
    workspace: str
    session: str
    catalog: Catalog
    stores: tuple[Store, ...]
    router: Router
    coordinator: ObservationCoordinator
    root: Path
    monotonic: float = 1.0

    def sweeper(self) -> ObservationOutboxSweeper:
        return ObservationOutboxSweeper(
            self.local,
            self.coordinator,
            budget_seconds=10.0,
            ingest_gate=asyncio.Lock(),
            _monotonic=lambda: self.monotonic,
            capture_recovery=self.coordinator.recover_workspace_capture_inventory,
        )


def harness(root: Path, *, routes: tuple[TaskRoute, ...] | None = None) -> Harness:
    local = LocalObservationStore(_state=root)
    workspace = local.workspace_commitment(str(root.resolve()))
    local.grant_consent(workspace)
    session = local.bind_codex_session(workspace, "cursor:parent")
    inventory = (route(), route()) if routes is None else routes
    stores = tuple(Store() for _ in inventory)
    runtimes = tuple(
        cast(
            TaskRuntime,
            SimpleNamespace(task_id=item.task_id, session_id=item.session_id, observation=store),
        )
        for item, store in zip(inventory, stores, strict=True)
    )
    catalog = Catalog(inventory)
    router = Router(runtimes)
    clock = cast(
        ClockPort,
        SimpleNamespace(now_utc=lambda: datetime(2026, 9, 10, 19, tzinfo=UTC)),
    )
    store_mapping(
        LifecycleMapping(
            1,
            "cursor:parent",
            inventory[0].task_id,
            inventory[0].session_id,
            new_id(IdKind.WRITER),
            None,
        ),
        _state=root,
    )
    bootstrap = build_capture_inventory_bootstrap(
        catalog=cast(StartCatalogPort, catalog),
        runtime=cast(BundleRuntimePort, router),
        local_observation=local,
        clock=clock,
        generation_is_current=lambda: True,
    )
    coordinator = ObservationCoordinator(
        runtime=cast(BundleRuntimePort, router),
        local=local,
        clock=clock,
        ids=cast(IdPort, object()),
        state_root=root,
        capture_budget_bootstrap=bootstrap,
    )
    return Harness(local, workspace, session, catalog, stores, router, coordinator, root)


def reject_native_failure(case: Harness) -> None:
    envelope = ObservationEnvelope(
        case.session,
        "PostToolUse",
        "failed-command:1",
        ObservationSource.CURSOR_HOOK,
        ObservationCursor(1, 0, 1, "hmac-sha256:" + "2" * 64, "cursor-obs-hook/1.0.0"),
        _NOW,
        JsonObject({"tool_name": "Shell", "exit_status": 1}),
        (),
        (),
    )
    plan = case.local.prepare_selected_admission(
        case.workspace,
        "cursor:parent",
        envelope,
        fence="sha256:" + "3" * 64,
        focused=True,
        routine_candidate=False,
        proven_routine_success=False,
        summary_builder=lambda inputs, _fence: inputs[-1],
    )
    assert not case.local.commit_selected_admission(
        case.workspace,
        plan,
        incoming=envelope,
        newly_observed=True,
        replayable=False,
    )


def assert_closed(case: Harness) -> None:
    assert not case.local.capture_reservation_bootstrap_ready(case.workspace)
    assert case.local.capture_backlog(case.workspace)["capture_backlog_scope"] == "unknown"
    assert not case.local.selection_runtime_status(case.workspace, case.session)[
        "admission_allowed"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("with_rejected_input", [False, True])
async def test_empty_outbox_recovery_uses_complete_ready_inventory(
    tmp_path: Path,
    with_rejected_input: bool,
) -> None:
    case = harness(tmp_path)
    first, second = case.catalog.routes
    case.local.bootstrap_capture_reservations(case.workspace, {first.task_id: _ZERO})
    case.local.update_capture_backlog(case.workspace, 0, 0, None, _NOW, route_id=second.task_id)
    if with_rejected_input:
        reject_native_failure(case)
    assert_closed(case)
    assert case.local.pending_outbox_count(case.workspace) == 0
    before_loss = case.local.selection_accounting(case.workspace)["unrecoverable_input_count"]
    sweep = case.sweeper()
    try:
        result = await sweep.sweep()
        assert result.attempted == result.acknowledged == result.quarantined == 0
        assert case.catalog.reads == 2
        assert [store.reads for store in case.stores] == [1, 1]
        assert case.local.capture_reservation_bootstrap_ready(case.workspace, second.task_id)
        assert case.local.selection_runtime_status(case.workspace, case.session)[
            "admission_allowed"
        ]
        assert (
            case.local.selection_accounting(case.workspace)["unrecoverable_input_count"]
            == before_loss
        )
        # No fresh hook and no repeated full catalog scan after a valid root.
        await sweep.sweep()
        assert case.catalog.reads == 2
        assert case.router.acquired == case.router.released
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["read", "inactive", "missing", "changed", "wrong-runtime"])
async def test_failed_inventory_stays_closed_and_can_retry_without_a_hook(
    tmp_path: Path,
    failure: str,
) -> None:
    case = harness(tmp_path)
    original_routes = case.catalog.routes
    case.local.bootstrap_capture_reservations(
        case.workspace,
        {item.task_id: _ZERO for item in original_routes},
    )
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    original_runtimes = dict(case.router.runtimes)
    if failure == "read":
        case.stores[1].failure = True
    elif failure == "inactive":
        case.catalog.routes = (
            original_routes[0],
            replace(original_routes[1], state=TaskRouteState.QUARANTINED),
        )
    elif failure == "missing":
        case.catalog.routes = original_routes[:1]
    elif failure == "changed":

        def change_catalog() -> None:
            if case.catalog.reads % 2 == 0:
                case.catalog.routes = (*case.catalog.routes, route())

        case.catalog.on_read = change_catalog
    else:
        case.router.runtimes[original_routes[1].session_id] = case.router.runtimes[
            original_routes[0].session_id
        ]
    sweep = case.sweeper()
    try:
        await sweep.sweep()
        assert_closed(case)
        case.stores[1].failure = False
        case.catalog.routes = original_routes
        case.catalog.on_read = None
        case.router.runtimes = original_runtimes
        case.monotonic += 5.0
        await sweep.sweep()
        assert case.local.capture_reservation_bootstrap_ready(case.workspace)
        assert case.local.selection_runtime_status(case.workspace, case.session)[
            "admission_allowed"
        ]
        assert case.local.selection_accounting(case.workspace)["unrecoverable_input_count"] == 0
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
async def test_restart_revalidates_a_persisted_root_even_when_pressure_is_healthy(
    tmp_path: Path,
) -> None:
    case = harness(tmp_path)
    case.local.bootstrap_capture_reservations(
        case.workspace,
        {item.task_id: _ZERO for item in case.catalog.routes},
    )
    assert case.local.pending_workspaces() == ()
    case.local = LocalObservationStore(_state=tmp_path)
    case.coordinator.local = case.local
    case.stores[1].failure = True
    sweep = case.sweeper()
    try:
        await sweep.sweep()
        assert case.catalog.reads == 1
        assert_closed(case)
        case.stores[1].failure = False
        case.monotonic += 5.0
        await sweep.sweep()
        assert case.local.capture_reservation_bootstrap_ready(case.workspace)
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
async def test_proof_commit_cannot_outlive_capture_exclusion_on_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = harness(tmp_path)
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    started, finish = threading.Event(), threading.Event()
    commit = case.local.bootstrap_capture_reservations

    def held_commit(*args: object, **kwargs: object) -> bool:
        started.set()
        assert finish.wait(5)
        return commit(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(case.local, "bootstrap_capture_reservations", held_commit)
    task = asyncio.create_task(case.coordinator.recover_workspace_capture_inventory(case.workspace))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        # A loop checkpoint delivers cancellation; the worker remains owned.
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert case.coordinator._capture_lock.locked()  # pyright: ignore[reportPrivateUsage]
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_closed(case)
        assert not case.coordinator._capture_lock.locked()  # pyright: ignore[reportPrivateUsage]
        assert case.router.acquired == case.router.released
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        case.coordinator.close()


@pytest.mark.anyio
async def test_fresh_unmapped_host_does_not_create_unknown_pressure(tmp_path: Path) -> None:
    """Recovery must not bootstrap a new workspace into a pre-start admission deadlock."""
    case = harness(tmp_path)
    clear_mapping("cursor:parent", _state=tmp_path)
    assert case.local.pending_workspaces(include_capture_recovery=True) == ()
    sweep = case.sweeper()
    try:
        await sweep.sweep()
        assert case.catalog.reads == 0
        assert case.local.selection_runtime_status(case.workspace, case.session)[
            "admission_allowed"
        ]
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
async def test_fast_sibling_drain_passes_do_not_spin_capture_recovery(tmp_path: Path) -> None:
    case = harness(tmp_path)
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    case.stores[1].failure = True
    sweep = case.sweeper()
    try:
        await sweep.sweep()
        assert case.catalog.reads == 1
        for _ in range(20):
            await sweep.sweep()
        assert case.catalog.reads == 1
        case.monotonic += 5.0
        await sweep.sweep()
        assert case.catalog.reads == 2
        assert_closed(case)
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("count, byte_count", [(512, 0), (1, 128 * 1024 * 1024)])
async def test_complete_inventory_does_not_relax_hard_capture_limits(
    tmp_path: Path,
    count: int,
    byte_count: int,
) -> None:
    case = harness(tmp_path)
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    case.stores[1].backlog = ObservationCaptureBacklog(count, byte_count, _NOW)
    sweep = case.sweeper()
    try:
        await sweep.sweep()
        assert case.local.capture_reservation_bootstrap_ready(case.workspace)
        status = case.local.selection_runtime_status(case.workspace, case.session)
        assert status["pressure_state"] == "hard_limit"
        assert not status["admission_allowed"]
        assert case.local.capture_backlog(case.workspace)["count"] == count
        assert case.local.capture_backlog(case.workspace)["byte_count"] == byte_count
        reject_native_failure(case)
    finally:
        sweep.close()
        case.coordinator.close()


@pytest.mark.anyio
async def test_retired_generation_cannot_publish_after_waiting_for_local_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = harness(tmp_path)
    current = True
    case.coordinator.capture_budget_bootstrap = build_capture_inventory_bootstrap(
        catalog=cast(StartCatalogPort, case.catalog),
        runtime=cast(BundleRuntimePort, case.router),
        local_observation=case.local,
        clock=case.coordinator.clock,
        generation_is_current=lambda: current,
    )
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    original = case.local.bootstrap_capture_reservations

    def retire_before_write(*args: object, **kwargs: object) -> bool:
        nonlocal current
        current = False
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(case.local, "bootstrap_capture_reservations", retire_before_write)
    try:
        await case.coordinator.recover_workspace_capture_inventory(case.workspace)
        assert_closed(case)
        assert case.router.acquired == case.router.released
    finally:
        case.coordinator.close()


@pytest.mark.anyio
async def test_new_task_report_racing_the_scan_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = harness(tmp_path)
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    original = case.local.bootstrap_capture_reservations
    unexpected_task = new_id(IdKind.TASK)

    def report_before_write(*args: object, **kwargs: object) -> bool:
        case.local.update_capture_backlog(
            case.workspace,
            1,
            17,
            _NOW,
            _NOW,
            route_id=unexpected_task,
        )
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(case.local, "bootstrap_capture_reservations", report_before_write)
    try:
        await case.coordinator.recover_workspace_capture_inventory(case.workspace)
        assert_closed(case)
        assert case.local.capture_backlog(case.workspace)["byte_count"] == 17
    finally:
        case.coordinator.close()


@pytest.mark.anyio
async def test_overflow_inventory_is_not_a_complete_proof(tmp_path: Path) -> None:
    case = harness(tmp_path, routes=tuple(route() for _ in range(257)))
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    try:
        await case.coordinator.recover_workspace_capture_inventory(case.workspace)
        assert_closed(case)
        assert sum(store.reads for store in case.stores) == 0
    finally:
        case.coordinator.close()


@pytest.mark.anyio
async def test_recovery_keeps_control_reads_live_and_releases_maintenance_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = harness(tmp_path)
    case.local.mark_capture_backlog_scope_unknown(case.workspace)
    started, finish = asyncio.Event(), asyncio.Event()
    inventory = case.catalog.capture_inventory_routes

    async def held_inventory(repository: str) -> tuple[TaskRoute, ...]:
        started.set()
        await finish.wait()
        return await inventory(repository)

    monkeypatch.setattr(case.catalog, "capture_inventory_routes", held_inventory)
    sweep = case.sweeper()
    task = asyncio.create_task(sweep.sweep())
    try:
        await asyncio.wait_for(started.wait(), 2.0)
        status = await asyncio.wait_for(
            asyncio.to_thread(case.local.selection_runtime_status, case.workspace, case.session),
            2.0,
        )
        assert not status["admission_allowed"]
        # Cancellation must not strand installation maintenance behind the scan.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2.0)
        assert sweep.ingest_gate is not None
        async with asyncio.timeout(2.0), sweep.ingest_gate:
            assert_closed(case)
        assert case.router.acquired == case.router.released
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        sweep.close()
        case.coordinator.close()
