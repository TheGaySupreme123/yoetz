"""Host-shaped failures recover through real READY inventory and encrypted task stores.

The control transport is in-process, not a native vendor session. Both mapped
parent/worker bundles, the SQLite catalog, writer authorizers, native normalizers, capture handoffs,
object encryption, and frozen-case readback use production implementations.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import apsw
import pytest

from conformance.adapters import test_start_catalog_port as catalog_fixture
from integration.application import test_native_capture_pipeline as native
from unit.service.test_observation_capture_inventory import Router
from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping, store_mapping
from yoetz.adapters.sqlite.start_catalog import SqliteStartCatalog
from yoetz.application.observation_drain import ObservationOutboxSweeper
from yoetz.application.semantic_content import resolve_captured_semantic_content
from yoetz.cli.observe_hooks import handle_claude_observe, handle_cursor_observe, handle_observe
from yoetz.domain.observation import ObservationCaptureBacklog, ObservationSource
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.values import Timestamp
from yoetz.ports.clock import ClockPort
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.runtime import BundleRuntimePort, TaskRuntime
from yoetz.ports.start_catalog import StartMode, TaskRoute
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.service.observation_capture_inventory import build_capture_inventory_bootstrap

_PROFILES = {
    "codex": None,
    "claude": CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    "cursor": CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
}


class _InventoryCatalog(SqliteStartCatalog):
    """Count real bounded SQLite inventory reads without substituting their results."""

    reads: int = 0

    async def capture_inventory_routes(
        self, repository_privacy_commitment: str
    ) -> tuple[TaskRoute, ...]:
        self.reads += 1
        return await super().capture_inventory_routes(repository_privacy_commitment)


class _CatalogIds:
    def __init__(self, runtimes: tuple[TaskRuntime, ...]) -> None:
        self.runtimes = runtimes
        self.offsets = {kind: 0 for kind in (IdKind.TASK, IdKind.SESSION, IdKind.WRITER)}

    def new(self, kind: IdKind) -> str:
        if kind not in self.offsets:
            return new_id(kind)
        runtime = self.runtimes[self.offsets[kind]]
        self.offsets[kind] += 1
        value = {
            IdKind.TASK: runtime.task_id,
            IdKind.SESSION: runtime.session_id,
            IdKind.WRITER: runtime.writer_id,
        }[kind]
        assert isinstance(value, str)
        return value


async def _catalog(
    root: Path, runtimes: tuple[TaskRuntime, ...], clock: ClockPort
) -> tuple[_InventoryCatalog, apsw.Connection]:
    database = apsw.Connection(str(root / "capture-catalog.sqlite3"))
    checkout = Path(__file__).resolve().parents[3]
    for version in ("0001", "0002", "0003"):
        database.execute((checkout / f"migrations/catalog/{version}.sql").read_text())
    installation = new_id(IdKind.INSTALLATION)
    database.executemany(
        "INSERT INTO catalog_meta(key, value) VALUES(?, ?)",
        (("installation_id", installation), ("owner_generation", "1")),
    )
    catalog = _InventoryCatalog(
        database,
        installation_id=installation,
        lookup=catalog_fixture._Lookup(),  # pyright: ignore[reportPrivateUsage]
        clock=clock,
        ids=_CatalogIds(runtimes),
    )
    # Reserve and activate both routes through the catalog's real state machine.
    # Task ledgers/encryption are provisioned by the native pipeline fixture;
    # no route or session-binding query is mocked in this acceptance slice.
    for index, runtime in enumerate(runtimes):
        command = await catalog_fixture._command(  # pyright: ignore[reportPrivateUsage]
            catalog,
            operation_id=new_id(IdKind.REQUEST),
            mode=StartMode.CREATE,
            title=f"capture-recovery-{index}",
            external_ref=f"capture-recovery-{index}",
            repository_privacy_commitment="hmac-sha256:" + "9" * 64,
        )
        allocation = await catalog.reserve_or_resume(command)
        assert allocation.task_id == runtime.task_id
        assert allocation.session_id == runtime.session_id
        await catalog_fixture._finish(catalog, allocation)  # pyright: ignore[reportPrivateUsage]
    return catalog, database


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["codex", "claude", "cursor"])
@pytest.mark.parametrize("enable_recovery", [False, True], ids=["negative-control", "recovery"])
async def test_blocked_native_failure_then_parent_worker_capture_readback(
    tmp_path: Path,
    host: str,
    enable_recovery: bool,
) -> None:
    parent_root, worker_root = tmp_path / "parent", tmp_path / "worker"
    parent_root.mkdir()
    worker_root.mkdir()
    parent_id, worker_id = f"{host}:recovery-parent", f"{host}:recovery-worker"
    (
        project,
        workspace,
        session,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await native._pipeline(  # pyright: ignore[reportPrivateUsage]
        parent_root,
        codex_session_id=parent_id,
        profile=_PROFILES[host],
    )
    (
        _project2,
        _workspace2,
        _session2,
        _local2,
        worker_store,
        worker_ledger,
        worker_runtime,
        worker_coordinator,
        _client2,
        _connect2,
    ) = await native._pipeline(  # pyright: ignore[reportPrivateUsage]
        worker_root,
        codex_session_id=worker_id,
        profile=_PROFILES[host],
        identity_seed=100,
    )
    worker_session = local.bind_codex_session(workspace, worker_id)
    store_mapping(
        LifecycleMapping(
            1,
            worker_id,
            worker_runtime.task_id,
            worker_runtime.session_id,
            cast(str, worker_runtime.writer_id),
            None,
        ),
        _state=parent_root / "state",
    )
    catalog, catalog_database = await _catalog(
        tmp_path, (runtime, worker_runtime), coordinator.clock
    )
    router = Router((runtime, worker_runtime))
    coordinator.runtime = cast(BundleRuntimePort, router)
    coordinator.capture_budget_bootstrap = build_capture_inventory_bootstrap(
        catalog=catalog,
        runtime=coordinator.runtime,
        local_observation=local,
        clock=coordinator.clock,
        generation_is_current=lambda: True,
    )
    local.bootstrap_capture_reservations(
        workspace, {runtime.task_id: ObservationCaptureBacklog(0, 0, None)}
    )
    local.update_capture_backlog(
        workspace,
        0,
        0,
        None,
        Timestamp("2026-09-10T19:00:00.000Z"),
        route_id=worker_runtime.task_id,
    )

    def run_async(factory: Callable[[], Awaitable[object]]) -> object:
        return asyncio.run(factory())

    def hook(host_session: str, identity: str, marker: str) -> int:
        raw_id = host_session if host == "codex" else host_session.split(":", 1)[1]
        payload: dict[str, JsonValue] = {
            "cwd": str(project),
            "hook_event_name": "PostToolUse",
            "session_id": raw_id,
            "tool_name": "Bash",
            "tool_use_id": identity,
            "tool_input": {"command": "python -c 'raise SystemExit(1)'"},
            "tool_response": marker,
            "exit_status": 1,
        }
        common = {
            "workspace": str(project),
            "_state": parent_root / "state",
            "stdout": io.BytesIO(),
            "connect": connect,
            "run_async": run_async,
        }
        if host == "codex":
            payload["tool_response"] = {
                "aggregated_output": marker,
                "stdout": marker,
                "exit_code": 1,
            }
            return handle_observe(
                event_name="PostToolUse",
                stdin_bytes=canonical_encode(payload),
                source=ObservationSource.CODEX_HOOK,
                **common,  # type: ignore[arg-type]
            )
        if host == "claude":
            return handle_claude_observe(
                event_name="PostToolUse",
                stdin_bytes=canonical_encode(payload),
                observation_profile=_PROFILES[host],
                **common,  # type: ignore[arg-type]
            )
        payload.update(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": raw_id,
                "tool_name": "shell",
                "tool_output": '{"exitCode":1,"stdout":"' + marker + '"}',
                "exit_code": 1,
                "workspace_roots": (str(project),),
            }
        )
        return handle_cursor_observe(
            event_name="postToolUse",
            stdin_bytes=canonical_encode(payload),
            observation_profile=_PROFILES[host],
            **common,  # type: ignore[arg-type]
        )

    sweep = ObservationOutboxSweeper(
        local,
        coordinator,
        budget_seconds=10.0,
        capture_recovery=coordinator.recover_workspace_capture_inventory
        if enable_recovery
        else None,
    )
    try:
        # Non-replayable input is refused before the content callback. The
        # negative control retains exactly that original circular dependency.
        assert await asyncio.to_thread(hook, parent_id, "lost-native", "lost-marker") == 0
        assert client.requests == []
        assert local.pending_outbox_count(workspace) == 0
        previous_loss = local.selection_accounting(workspace)
        assert previous_loss["unrecoverable_input_count"] == 1
        await sweep.sweep()  # No fresh hook is needed to build the complete proof.
        assert local.capture_reservation_bootstrap_ready(workspace) is enable_recovery
        assert (
            await asyncio.to_thread(hook, parent_id, "retained-parent", "retained-parent-marker")
            == 0
        )
        assert (
            await asyncio.to_thread(hook, worker_id, "retained-worker", "retained-worker-marker")
            == 0
        )
        if not enable_recovery:
            assert client.requests == []
            assert local.selection_accounting(workspace)["unrecoverable_input_count"] == 3
            assert observation.list_envelopes(workspace) == ()
            assert worker_store.list_envelopes(workspace) == ()
            return

        current_loss = local.selection_accounting(workspace)
        for key in ("unrecoverable_input_count", "loss_identity_commitment", "loss_ranges"):
            assert current_loss[key] == previous_loss[key]
        assert catalog.reads == 2  # Both bundles, once before capture; no per-hook full scans.
        for root, native_session, task_store, task_ledger, task_runtime, marker in (
            (parent_root, session, observation, ledger, runtime, b"retained-parent-marker"),
            (
                worker_root,
                worker_session,
                worker_store,
                worker_ledger,
                worker_runtime,
                b"retained-worker-marker",
            ),
        ):
            envelopes = task_store.list_envelopes_for_session(workspace, native_session)
            assert len(envelopes) == 1
            assert envelopes[0].event_kind == "PostToolUse"
            assert envelopes[0].structural_payload.get("exit_status") == 1
            assert envelopes[0].content_object_refs
            frontier = await task_ledger.load_frontier()
            frozen = await task_ledger.freeze_case(
                task_runtime.session_id,
                cast(str, task_runtime.writer_id),
                frontier.sequence,
                new_id(IdKind.REQUEST),
                "sha256:" + "0" * 64,
            )
            assert isinstance(frozen, FrozenCase)
            resolved = await resolve_captured_semantic_content(
                runtime=task_runtime,
                frozen=frozen,
                workspace_commitment=workspace,
                local_observation=local,
            )
            assert len(resolved.content) == 1
            captured = resolved.content[0]
            assert marker in captured.content
            path = (
                root
                / "bundle"
                / "objects"
                / captured.object_ref.object_id[4:6]
                / captured.object_ref.object_id
            )
            assert path.is_file() and marker not in path.read_bytes()
        assert local.pending_outbox_count(workspace) == 0
        assert router.acquired == router.released
    finally:
        sweep.close()
        coordinator.close()
        worker_coordinator.close()
        catalog_database.close()
