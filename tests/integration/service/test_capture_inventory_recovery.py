"""Issue #695: real catalog inventory recovery is independent of native admission."""

from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast

import apsw
import pytest

import yoetz.service.ready_composition as ready_module
from conformance.adapters import test_start_catalog_port as catalog_fixture
from integration.application import test_native_capture_pipeline as native
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_drain import (
    ObservationCaptureRecoveryOutcome,
    ObservationOutboxSweeper,
)
from yoetz.application.semantic_content import resolve_captured_semantic_content
from yoetz.domain.observation import (
    ObservationCaptureBacklog,
    ObservationCaptureTicket,
    ObservationCursor,
    ObservationIngestRequest,
    ObservationSource,
)
from yoetz.domain.observation_budget import BudgetLimits, CapacityProfile
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.values import timestamp_from_datetime
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.ports.start_catalog import StartAllocation, StartCatalogPort, TaskRoute, TaskRouteState
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.ids import IdKind

_REPOSITORY = "hmac-sha256:" + "7" * 64


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Routes:
    """Route real observation stores, with explicit fault/latency injection."""

    def __init__(self, runtimes: tuple[TaskRuntime, ...]) -> None:
        self.runtimes = {item.session_id: item for item in runtimes}
        self.held: list[TaskRuntime] = []
        self.calls: list[str] = []
        self.block_session: str | None = None
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def route(self, command: RouteCommand) -> TaskRuntime:
        self.calls.append(command.session_id)
        if command.session_id == self.block_session:
            self.entered.set()
            await self.resume.wait()
        current = self.runtimes[command.session_id]
        self.held.append(current)
        return current

    async def release(self, current: TaskRuntime) -> None:
        self.held.remove(current)


@dataclass
class _World:
    root: Path
    project: Path
    workspace: str
    session: str
    local: LocalObservationStore
    catalog: SqliteStartCatalog
    routes: _Routes
    runtime: TaskRuntime
    sibling: TaskRuntime
    observation: SqliteObservationStore
    sibling_observation: SqliteObservationStore
    sibling_database: apsw.Connection
    coordinator: ObservationCoordinator
    requests: list[ObservationIngestRequest]
    connect: Callable[[object], Awaitable[object]]
    host_session: str

    def wire(self, *, generation_is_current: Callable[[], bool] = lambda: True) -> None:
        self.coordinator.runtime = cast(BundleRuntimePort, self.routes)
        self.coordinator.local = self.local
        self.coordinator.capture_budget_bootstrap = partial(
            ready_module._bootstrap_capture_reservations,  # pyright: ignore[reportPrivateUsage]
            catalog=cast(StartCatalogPort, self.catalog),
            runtime=cast(BundleRuntimePort, self.routes),
            local_observation=self.local,
            clock=self.coordinator.clock,
            generation_is_current=generation_is_current,
        )

    def sweep(self, *, capture_recovery_budget_seconds: float = 5.0) -> ObservationOutboxSweeper:
        return ObservationOutboxSweeper(
            self.local,
            self.coordinator,
            capture_recovery=self.coordinator.recover_capture_inventory,
            capture_recovery_budget_seconds=capture_recovery_budget_seconds,
        )


async def _world(tmp_path: Path, *, host: str = "claude") -> _World:
    """Create two completed real SQLite catalog routes and two task SQLite stores."""

    clock = catalog_fixture._Clock(datetime(2026, 9, 10, tzinfo=UTC))  # pyright: ignore[reportPrivateUsage]
    installation = native._ids(IdKind.INSTALLATION, 695)  # pyright: ignore[reportPrivateUsage]
    catalog = catalog_fixture._sqlite_catalog(installation, clock)  # pyright: ignore[reportPrivateUsage]
    allocations: list[StartAllocation] = []
    for seed in (1, 2):
        request = await catalog_fixture._command(  # pyright: ignore[reportPrivateUsage]
            catalog,
            operation_id=native._ids(IdKind.REQUEST, 700 + seed),  # pyright: ignore[reportPrivateUsage]
            workspace_ref=f"synthetic-workspace-{seed}",
            external_ref=f"synthetic-task-{seed}",
            repository_privacy_commitment=_REPOSITORY,
        )
        allocation = await catalog.reserve_or_resume(request)
        await catalog_fixture._finish(catalog, allocation)  # pyright: ignore[reportPrivateUsage]
        allocations.append(allocation)
    first, second = allocations
    profile = {
        "claude": CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        "cursor": CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
        "codex": None,
    }[host]
    host_session = "inventory-recovery" if host == "codex" else f"{host}:inventory-recovery"
    (
        project,
        workspace,
        session,
        local,
        observation,
        _ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await native._pipeline(  # pyright: ignore[reportPrivateUsage]
        tmp_path,
        codex_session_id=host_session,
        profile=profile,
        identity=(first.task_id, first.session_id, first.writer_id),
    )
    sibling_db = apsw.Connection(str(tmp_path / "sibling.sqlite3"))
    initialize_bundle(
        sibling_db,
        {"task_id": second.task_id, "owner_generation": "1", "owner_nonce": "test-nonce"},
    )
    sibling_store = SqliteObservationStore(sibling_db)
    sibling = replace(
        runtime,
        task_id=second.task_id,
        session_id=second.session_id,
        writer_id=second.writer_id,
        observation=sibling_store,
    )
    routes = _Routes((runtime, sibling))
    world = _World(
        tmp_path,
        project,
        workspace,
        session,
        local,
        catalog,
        routes,
        runtime,
        sibling,
        observation,
        sibling_store,
        sibling_db,
        coordinator,
        client.requests,
        cast(Callable[[object], Awaitable[object]], connect),
        host_session,
    )
    world.wire()
    local.set_capture_reservation_bootstrap_required(True)
    local.bootstrap_capture_reservations(
        workspace, {runtime.task_id: ObservationCaptureBacklog(0, 0, None)}
    )
    local.update_capture_backlog(
        workspace, 0, 0, None, timestamp_from_datetime(clock.now_utc()), route_id=sibling.task_id
    )
    assert not local.capture_reservation_bootstrap_ready(workspace)
    assert local.pending_outbox_count(workspace) == 0
    return world


@pytest.mark.anyio
@pytest.mark.parametrize("restart", (False, True), ids=("live", "restart"))
async def test_real_catalog_recovers_both_routes_with_no_new_host_event(
    tmp_path: Path,
    restart: bool,
) -> None:
    world = await _world(tmp_path)
    if restart:
        world.coordinator.close()
        world.local = LocalObservationStore(_state=tmp_path / "state")
        world.local.set_capture_reservation_bootstrap_required(True)
        world.coordinator = ObservationCoordinator(
            runtime=cast(BundleRuntimePort, world.routes),
            local=world.local,
            clock=world.coordinator.clock,
            ids=world.coordinator.ids,
            state_root=tmp_path / "state",
        )
        world.wire()
    sweeper = world.sweep()
    try:
        summary = await sweeper.sweep()
        assert summary.attempted == summary.acknowledged == 0
        assert summary.reasons == (("capture_inventory_recovered", 1),)
        for current in (world.runtime, world.sibling):
            assert world.local.capture_reservation_bootstrap_ready(world.workspace, current.task_id)
        assert (
            world.local.selection_runtime_status(world.workspace, world.session)[
                "admission_allowed"
            ]
            is True
        )
        assert not world.routes.held
        assert world.requests == []
        calls = len(world.routes.calls)
        await sweeper.sweep()
        assert len(world.routes.calls) == calls  # no repeated healthy catalog scan
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_transient_bundle_read_failure_retries_without_native_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _world(tmp_path)
    original = world.sibling_observation.capture_backlog

    def unreadable(_workspace: str) -> ObservationCaptureBacklog:
        raise OSError("synthetic-private-path-must-not-enter-diagnostics")

    monkeypatch.setattr(world.sibling_observation, "capture_backlog", unreadable)
    sweeper = world.sweep()
    try:
        failed = await sweeper.sweep()
        assert failed.reasons == (("capture_inventory_unknown", 1),)
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
        assert world.local.pending_workspaces() == (world.workspace,)
        assert not world.routes.held
        monkeypatch.setattr(world.sibling_observation, "capture_backlog", original)
        recovered = await sweeper.sweep()
        assert recovered.reasons == (("capture_inventory_recovered", 1),)
        assert world.local.capture_reservation_bootstrap_ready(
            world.workspace, world.sibling.task_id
        )
        assert world.requests == []
        assert not world.routes.held
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fault",
    (
        "inactive",
        "missing-current",
        "duplicate",
        "changed",
        "missing-binding",
        "corrupt-bundle",
        "missing-observation",
        "unknown-repository-unreadable",
        "bad-backlog",
    ),
)
async def test_incomplete_inventory_never_clears_unknown_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    world = await _world(tmp_path)
    routes = await world.catalog.recovery_routes()
    calls = 0

    async def inventory() -> tuple[TaskRoute, ...]:
        nonlocal calls
        calls += 1
        if fault == "missing-current":
            return tuple(route for route in routes if route.task_id != world.runtime.task_id)
        if fault == "duplicate":
            return (*routes, routes[0])
        if fault == "changed" and calls > 1:
            return tuple(reversed(routes))
        if fault in {"inactive", "unknown-repository-unreadable"}:
            return tuple(
                replace(route, state=TaskRouteState.INITIALIZING)
                if fault == "inactive" and route.task_id == world.sibling.task_id
                else replace(route, repository_privacy_commitment=None)
                if fault == "unknown-repository-unreadable"
                and route.task_id == world.sibling.task_id
                else route
                for route in routes
            )
        return routes

    monkeypatch.setattr(world.catalog, "recovery_routes", inventory)
    if fault == "missing-binding":

        async def no_binding(_session: str) -> None:
            return None

        monkeypatch.setattr(world.catalog, "session_binding", no_binding)
    if fault in {"corrupt-bundle", "unknown-repository-unreadable"}:
        world.sibling_database.execute("DROP TABLE observation_capture_tickets")
    if fault == "missing-observation":
        world.routes.runtimes[world.sibling.session_id] = replace(world.sibling, observation=None)
    if fault == "bad-backlog":

        def no_backlog(_workspace: str) -> None:
            return None

        monkeypatch.setattr(world.sibling_observation, "capture_backlog", no_backlog)
    sweeper = world.sweep()
    try:
        summary = await sweeper.sweep()
        assert summary.reasons == (("capture_inventory_unknown", 1),)
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
        assert (
            world.local.selection_runtime_status(world.workspace, world.session)[
                "admission_allowed"
            ]
            is False
        )
        assert world.requests == []
        assert not world.routes.held
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_unrelated_quarantined_repository_does_not_poison_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _world(tmp_path)
    original = await world.catalog.recovery_routes()

    async def inventory() -> tuple[TaskRoute, ...]:
        return tuple(
            replace(
                route,
                repository_privacy_commitment="hmac-sha256:" + "8" * 64,
                state=TaskRouteState.QUARANTINED,
            )
            if route.task_id == world.sibling.task_id
            else route
            for route in original
        )

    monkeypatch.setattr(world.catalog, "recovery_routes", inventory)
    try:
        result = await world.coordinator.recover_capture_inventory(world.workspace)
        assert result is ObservationCaptureRecoveryOutcome.RECOVERED
        assert world.sibling.session_id not in world.routes.calls
        assert world.local.capture_reservation_bootstrap_ready(
            world.workspace, world.runtime.task_id
        )
        assert not world.routes.held
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_slow_inventory_does_not_hold_the_control_gate(tmp_path: Path) -> None:
    world = await _world(tmp_path)
    gate = asyncio.Lock()
    sweeper = ObservationOutboxSweeper(
        world.local,
        world.coordinator,
        ingest_gate=gate,
        capture_recovery=world.coordinator.recover_capture_inventory,
    )
    world.routes.block_session = world.sibling.session_id
    operation = asyncio.create_task(sweeper.sweep())
    try:
        await asyncio.wait_for(world.routes.entered.wait(), 2.0)
        async with asyncio.timeout(0.5):
            async with gate:
                assert not operation.done()
        world.routes.resume.set()
        result = await operation
        assert result.reasons == (("capture_inventory_recovered", 1),)
        assert not world.routes.held
    finally:
        world.routes.resume.set()
        if not operation.done():
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_cancellation_joins_publication_before_releasing_capture_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _world(tmp_path)
    entered = threading.Event()
    resume = threading.Event()
    original = world.local.bootstrap_capture_reservations
    order: list[str] = []

    def blocking_publish(*args: object, **kwargs: object) -> bool:
        entered.set()
        assert resume.wait(5), "test must release the bounded publication worker"
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        order.append("published")
        return result

    monkeypatch.setattr(world.local, "bootstrap_capture_reservations", blocking_publish)
    operation = asyncio.create_task(world.coordinator.recover_capture_inventory(world.workspace))
    lock = world.coordinator._capture_lock  # pyright: ignore[reportPrivateUsage]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # READY shutdown can cancel all asyncio Tasks. A separately cancellable
        # to_thread Task would lose the future while its thread still commits.
        assert not any(
            getattr(task.get_coro(), "__qualname__", None) == "to_thread"
            for task in asyncio.all_tasks()
        )
        operation.cancel()
        for _ in range(3):
            await asyncio.sleep(0)
        operation.cancel()  # a second deadline must not orphan the committing worker
        await asyncio.sleep(0)
        assert lock.locked()
        assert not operation.done()

        async def later_capture() -> None:
            async with lock:
                order.append("later-capture")

        later = asyncio.create_task(later_capture())
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await later
        assert order == ["published", "later-capture"]
        assert not world.routes.held
        assert not lock.locked()
    finally:
        resume.set()
        if not operation.done():
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        world.coordinator.close()


def _native_failed_command(
    world: _World,
    host: str,
    identity: str,
    marker: str,
    raw_session: str = "inventory-recovery",
) -> int:
    """Invoke actual host normalizers/handlers, not the installed vendor application."""

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    common = {
        "cwd": str(world.project),
        "session_id": raw_session,
        "tool_name": "Bash",
        "tool_use_id": identity,
        "tool_input": {"command": "exit 1"},
        "tool_response": {"stdout": marker, "aggregated_output": marker, "exit_code": 1},
    }
    stdout = io.BytesIO()
    if host == "cursor":
        payload = {
            "conversation_id": raw_session,
            "tool_use_id": identity,
            "tool_name": "shell",
            "tool_input": {"command": "exit 1"},
            "tool_output": canonical_encode({"stdout": marker, "exitCode": 1}).decode(),
            "exit_code": 1,
        }
        return native.handle_cursor_observe(
            event_name="postToolUse",
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, payload)),
            workspace=str(world.project),
            _state=world.root / "state",
            stdout=stdout,
            connect=cast(object, world.connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
        )
    if host == "claude":
        return native.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(cast(CanonicalJsonValue, common)),
            workspace=str(world.project),
            _state=world.root / "state",
            stdout=stdout,
            connect=cast(object, world.connect),  # type: ignore[arg-type]
            run_async=run_async,
            observation_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        )
    return native.handle_observe(
        event_name="PostToolUse",
        stdin_bytes=canonical_encode(cast(CanonicalJsonValue, common)),
        workspace=str(world.project),
        _state=world.root / "state",
        stdout=stdout,
        connect=cast(object, world.connect),  # type: ignore[arg-type]
        run_async=run_async,
        source=ObservationSource.CODEX_HOOK,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("host", ("claude", "cursor", "codex"))
async def test_failed_native_command_after_real_recovery_has_encrypted_readback(
    tmp_path: Path,
    host: str,
) -> None:
    world = await _world(tmp_path, host=host)
    sweeper = world.sweep()
    try:
        assert (
            await asyncio.to_thread(
                _native_failed_command, world, host, "before-recovery", "lost-native-marker"
            )
            == 0
        )
        assert world.requests == []
        assert world.local.pending_outbox_count(world.workspace) == 0
        before = world.local.selection_accounting(world.workspace)
        assert before["unrecoverable_input_count"] == 1
        summary = await sweeper.sweep()
        assert summary.reasons == (("capture_inventory_recovered", 1),)
        assert summary.attempted == 0
        marker = f"{host}-encrypted-recovery-readback-695"
        assert (
            await asyncio.to_thread(_native_failed_command, world, host, "after-recovery", marker)
            == 0
        )
        assert any(request.capture_only for request in world.requests)
        assert world.local.pending_outbox_count(world.workspace) == 0
        after = world.local.selection_accounting(world.workspace)
        assert after["unrecoverable_input_count"] == before["unrecoverable_input_count"]
        assert after["loss_identity_commitment"] == before["loss_identity_commitment"]
        frontier = await world.runtime.ledger.load_frontier()
        frozen = await world.runtime.ledger.freeze_case(
            world.runtime.session_id,
            cast(str, world.runtime.writer_id),
            frontier.sequence,
            native._ids(IdKind.REQUEST, 911),  # pyright: ignore[reportPrivateUsage]
            "sha256:" + "0" * 64,
        )
        assert isinstance(frozen, FrozenCase)
        resolved = await resolve_captured_semantic_content(
            runtime=world.runtime,
            frozen=frozen,
            workspace_commitment=world.workspace,
            local_observation=world.local,
        )
        assert len(resolved.content) == 1
        assert marker.encode() in resolved.content[0].content
        for path in (tmp_path / "bundle").rglob("*"):
            if path.is_file():
                assert marker.encode() not in path.read_bytes(), (
                    "content must not persist in plaintext"
                )
        assert not world.routes.held
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_unmapped_parent_worker_candidates_do_not_starve_a_usable_mapping(
    tmp_path: Path,
) -> None:
    world = await _world(tmp_path)
    for number in range(9):
        world.local.bind_codex_session(world.workspace, f"aaa-unmapped-worker-{number}")
    try:
        first = await world.coordinator.recover_capture_inventory(world.workspace)
        assert first is ObservationCaptureRecoveryOutcome.MAPPING_MISSING
        assert world.routes.calls == []
        second = await world.coordinator.recover_capture_inventory(world.workspace)
        assert second is ObservationCaptureRecoveryOutcome.RECOVERED
        assert world.local.capture_reservation_bootstrap_ready(
            world.workspace, world.sibling.task_id
        )
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_ambiguous_host_session_cannot_publish_an_inventory(tmp_path: Path) -> None:
    world = await _world(tmp_path)
    unrelated = tmp_path / "unrelated-project"
    unrelated.mkdir()
    other_workspace = world.local.workspace_commitment(str(unrelated))
    world.local.grant_consent(other_workspace)
    world.local.bind_codex_session(other_workspace, world.host_session)
    try:
        result = await world.coordinator.recover_capture_inventory(world.workspace)
        assert result is ObservationCaptureRecoveryOutcome.MAPPING_MISSING
        assert world.routes.calls == []
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
    finally:
        world.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("disabled_by", ("config", "local-gate", "busy-capture"))
async def test_recovery_respects_runtime_fences_without_waiting_for_capture(
    tmp_path: Path,
    disabled_by: str,
) -> None:
    world = await _world(tmp_path)
    lock = world.coordinator._capture_lock  # pyright: ignore[reportPrivateUsage]
    if disabled_by == "config":
        world.coordinator.observation_enabled = False
    elif disabled_by == "local-gate":
        world.local.set_runtime_enabled(False)
    else:
        await lock.acquire()
    try:
        async with asyncio.timeout(0.5):
            result = await world.coordinator.recover_capture_inventory(world.workspace)
        assert result is (
            ObservationCaptureRecoveryOutcome.BUSY
            if disabled_by == "busy-capture"
            else ObservationCaptureRecoveryOutcome.DISABLED
        )
        assert world.routes.calls == []
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
    finally:
        if lock.locked():
            lock.release()
        world.coordinator.close()


@pytest.mark.anyio
async def test_recovered_inventory_preserves_real_pending_ticket_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _world(tmp_path)
    limits = BudgetLimits.for_profile(CapacityProfile.STANDARD)

    def small_capture_budget(
        cls: type[BudgetLimits], profile: CapacityProfile | int | str
    ) -> BudgetLimits:
        del cls, profile
        return replace(limits, capture_tickets=2)

    monkeypatch.setattr(BudgetLimits, "for_profile", classmethod(small_capture_budget))
    stamp = timestamp_from_datetime(world.coordinator.clock.now_utc())
    for number, (current, store) in enumerate(
        (
            (world.runtime, world.observation),
            (world.sibling, world.sibling_observation),
        ),
        start=1,
    ):
        store.record_capture_ticket(
            ObservationCaptureTicket(
                workspace_commitment=world.workspace,
                task_id=current.task_id,
                yoetz_session_id=current.session_id,
                session_commitment=world.session,
                source=ObservationSource.CLAUDE_HOOK,
                source_identity=f"retained-{number}",
                cursor=ObservationCursor(1, 0, number, "hmac-sha256:" + "a" * 64, "fixture-v1"),
                logical_identity=f"retained-{number}",
                content_capture_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
                authority_generation="sha256:" + "0" * 64,
                object_ids=(),
                captured_at=stamp,
            )
        )
    try:
        result = await world.coordinator.recover_capture_inventory(world.workspace)
        assert result is ObservationCaptureRecoveryOutcome.RECOVERED
        assert world.local.capture_reservation_bootstrap_ready(
            world.workspace, world.runtime.task_id
        )
        status = world.local.selection_runtime_status(world.workspace, world.session)
        assert status["admission_allowed"] is False
        assert status["pressure_state"] == "hard_limit"
        assert world.local.capture_backlog(world.workspace)["count"] == 2
        assert (
            await asyncio.to_thread(
                _native_failed_command, world, "claude", "real-capacity-rejection", "not-retainable"
            )
            == 0
        )
        assert world.requests == []
        assert world.local.selection_accounting(world.workspace)["unrecoverable_input_count"] == 1
        assert world.observation.capture_backlog(world.workspace).count == 1
        assert world.sibling_observation.capture_backlog(world.workspace).count == 1
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_blocking_task_metadata_read_leaves_control_loop_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(tmp_path)
    entered = threading.Event()
    resume = threading.Event()
    original = world.sibling_observation.capture_backlog

    def blocked_read(workspace: str) -> ObservationCaptureBacklog:
        entered.set()
        if not resume.wait(5):
            raise TimeoutError("bounded-test-rendezvous")
        return original(workspace)

    monkeypatch.setattr(world.sibling_observation, "capture_backlog", blocked_read)
    operation = asyncio.create_task(world.coordinator.recover_capture_inventory(world.workspace))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # A synchronous adapter wait must not run on the service event loop.
        # The event is released only after ordinary loop work has completed.
        async with asyncio.timeout(0.5):
            gate = asyncio.Lock()
            async with gate:
                assert not operation.done()
        resume.set()
        assert await operation is ObservationCaptureRecoveryOutcome.RECOVERED
        assert world.routes.held == []
    finally:
        resume.set()
        if not operation.done():
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        world.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("host", ("claude", "cursor", "codex"))
async def test_parent_worker_routes_recover_and_keep_encrypted_content_task_scoped(
    tmp_path: Path, host: str
) -> None:
    """Interleaved host-shaped lanes, not a vendor-native delegated session."""

    world = await _world(tmp_path, host=host)
    worker_root = tmp_path / "worker"
    worker_root.mkdir(mode=0o700)
    profile = {
        "claude": CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        "cursor": CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
        "codex": None,
    }[host]
    raw_worker = "inventory-worker"
    worker_session = raw_worker if host == "codex" else f"{host}:{raw_worker}"
    worker = await native._pipeline(  # pyright: ignore[reportPrivateUsage]
        worker_root,
        codex_session_id=worker_session,
        profile=profile,
        identity=(
            world.sibling.task_id,
            world.sibling.session_id,
            cast(str, world.sibling.writer_id),
        ),
    )
    worker[7].close()  # Both native lanes must use the one service coordinator below.
    world.sibling = worker[6]
    world.sibling_observation = worker[4]
    world.routes.runtimes[world.sibling.session_id] = world.sibling
    world.local.bind_codex_session(world.workspace, worker_session)
    native.store_mapping(
        native.LifecycleMapping(
            mapping_version=1,
            codex_session_id=worker_session,
            yoetz_task_id=world.sibling.task_id,
            yoetz_session_id=world.sibling.session_id,
            yoetz_writer_id=cast(str, world.sibling.writer_id),
            last_frontier=None,
        ),
        _state=tmp_path / "state",
    )
    sweeper = world.sweep()
    lanes = ("inventory-recovery", raw_worker)
    try:
        for lane in lanes:
            assert (
                await asyncio.to_thread(
                    _native_failed_command, world, host, "lost-before-recovery", "lost", lane
                )
                == 0
            )
        before = world.local.selection_accounting(world.workspace)
        assert before["unrecoverable_input_count"] == 2
        assert world.local.pending_outbox_count(world.workspace) == 0
        assert world.requests == []
        assert (await sweeper.sweep()).reasons == (("capture_inventory_recovered", 1),)
        # Interleave lanes through the same shared local store and service; no
        # second independently bootstrapped coordinator may mint a partial proof.
        for index in range(3):
            for lane in lanes:
                marker = f"{host}-{lane}-encrypted-{index}-695"
                assert (
                    await asyncio.to_thread(
                        _native_failed_command, world, host, f"tool-{index}", marker, lane
                    )
                    == 0
                )
        after = world.local.selection_accounting(world.workspace)
        assert after["unrecoverable_input_count"] == 2
        assert after["loss_identity_commitment"] == before["loss_identity_commitment"]
        for index, (runtime, lane) in enumerate(zip((world.runtime, world.sibling), lanes)):
            frontier = await runtime.ledger.load_frontier()
            frozen = await runtime.ledger.freeze_case(
                runtime.session_id,
                cast(str, runtime.writer_id),
                frontier.sequence,
                native._ids(IdKind.REQUEST, 950 + index),  # pyright: ignore[reportPrivateUsage]
                "sha256:" + "0" * 64,
            )
            assert isinstance(frozen, FrozenCase)
            resolved = await resolve_captured_semantic_content(
                runtime=runtime,
                frozen=frozen,
                workspace_commitment=world.workspace,
                local_observation=world.local,
            )
            assert len(resolved.content) == 3
            plaintext = b"\n".join(item.content for item in resolved.content)
            for number in range(3):
                assert f"{host}-{lane}-encrypted-{number}-695".encode() in plaintext
            other = lanes[1 - index]
            assert f"{host}-{other}-encrypted".encode() not in plaintext
        assert not world.routes.held
        assert world.local.pending_outbox_count(world.workspace) == 0
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ("before-read", "during-read", "waiting-to-publish"))
async def test_changed_ready_generation_cannot_publish_recovered_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    world = await _world(tmp_path)
    current = stage != "before-read"
    world.wire(generation_is_current=lambda: current)
    if stage == "during-read":
        original = world.sibling_observation.capture_backlog

        def change_while_reading(workspace: str) -> ObservationCaptureBacklog:
            nonlocal current
            current = False
            return original(workspace)

        monkeypatch.setattr(world.sibling_observation, "capture_backlog", change_while_reading)
    if stage == "waiting-to-publish":
        original_join = ready_module._run_capture_inventory_joined  # pyright: ignore[reportPrivateUsage]

        async def change_before_local_lock[ResultT](
            call: Callable[[], ResultT], *, operation: str
        ) -> ResultT:
            nonlocal current
            if operation == "observation_capture_inventory_publish":
                current = False
            return await original_join(call, operation=operation)

        monkeypatch.setattr(ready_module, "_run_capture_inventory_joined", change_before_local_lock)
    sweeper = world.sweep()
    try:
        assert (await sweeper.sweep()).reasons == (("capture_inventory_unknown", 1),)
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
        assert not world.local.selection_runtime_status(world.workspace, world.session)[
            "admission_allowed"
        ]
        assert world.workspace in world.local.pending_workspaces()
        assert not world.routes.held
        assert world.requests == []
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_runtime_with_wrong_task_identity_cannot_supply_a_route_inventory(
    tmp_path: Path,
) -> None:
    world = await _world(tmp_path)
    world.routes.runtimes[world.sibling.session_id] = world.runtime
    sweeper = world.sweep()
    try:
        assert (await sweeper.sweep()).reasons == (("capture_inventory_unknown", 1),)
        assert not world.local.capture_reservation_bootstrap_ready(world.workspace)
        assert not world.local.selection_runtime_status(world.workspace, world.session)[
            "admission_allowed"
        ]
        assert not world.routes.held
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_partial_ticket_ids_do_not_release_an_unlisted_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(tmp_path)
    zero = ObservationCaptureBacklog(0, 0, None)
    world.local.bootstrap_capture_reservations(
        world.workspace,
        {world.runtime.task_id: zero, world.sibling.task_id: zero},
    )
    world.local.reserve_capture_ticket(
        world.workspace, "sha256:" + "c" * 64, world.runtime.task_id, 17
    )
    world.local.mark_capture_backlog_scope_unknown(world.workspace)
    stamp = timestamp_from_datetime(world.coordinator.clock.now_utc())

    def retained_without_complete_ids(workspace: str) -> ObservationCaptureBacklog:
        assert workspace == world.workspace
        return ObservationCaptureBacklog(1, 0, stamp)

    monkeypatch.setattr(world.observation, "capture_backlog", retained_without_complete_ids)
    # The real empty store returns no IDs, while the injected legacy aggregate
    # reports one retained ticket. An empty enumeration is not an absence proof.
    try:
        assert await world.coordinator.recover_capture_inventory(world.workspace) is (
            ObservationCaptureRecoveryOutcome.RECOVERED
        )
        status = world.local.capture_backlog(world.workspace)
        assert status["count"] == 2
        assert status["byte_count"] == 17
        assert not world.routes.held
    finally:
        world.coordinator.close()
