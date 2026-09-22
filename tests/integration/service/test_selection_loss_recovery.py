"""Admission-independent loss reporting uses only the original task attribution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from integration.application.test_check import (  # pyright: ignore[reportPrivateUsage]
    _App,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    execute_check_commit,
)
from integration.application.test_native_capture_pipeline import (
    _ids,  # pyright: ignore[reportPrivateUsage]
)
from integration.service.test_capture_inventory_recovery import (  # pyright: ignore[reportPrivateUsage]
    _native_failed_command,  # pyright: ignore[reportPrivateUsage]
    _world,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.ports.ledger import FrozenCase
from yoetz.protocol.ids import IdKind


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("host", ("claude", "cursor", "codex"))
async def test_loss_reaches_task_and_frozen_check_without_new_event(
    tmp_path: Path, host: str
) -> None:
    world = await _world(tmp_path, host=host)
    sweeper = world.sweep()
    try:
        await asyncio.to_thread(_native_failed_command, world, host, "lost", "not-retained")
        before = world.local.selection_accounting(world.workspace)
        assert before["unrecoverable_input_count"] == 1
        assert world.local.pending_selection_losses(world.workspace)
        await sweeper.sweep()
        assert world.requests == []
        assert not world.local.pending_selection_losses(world.workspace)
        after = world.local.selection_accounting(world.workspace)
        assert after["unrecoverable_input_count"] == 1
        assert after["loss_identity_commitment"] == before["loss_identity_commitment"]
        history = world.observation.list_envelopes_for_session(world.workspace, world.session)
        assert len(history) == 1
        assert history[0].event_kind == "observation_gap"
        assert history[0].gap_codes == ("observation_input_loss",)
        assert world.sibling_observation.list_envelopes(world.workspace) == ()
        frontier = await world.runtime.ledger.load_frontier()
        assert frontier.sequence > 0
        frozen = await world.runtime.ledger.freeze_case(
            world.runtime.session_id,
            cast(str, world.runtime.writer_id),
            frontier.sequence,
            _ids(IdKind.REQUEST, 9201),
            "sha256:" + "0" * 64,
        )
        assert isinstance(frozen, FrozenCase)
        assert any(
            "observation_input_loss" in coverage.known_gaps
            for coverage in frozen.case.coverage_by_ref.values()
        )
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_retry_after_ledger_commit_and_restart_does_not_duplicate_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        original = world.local.acknowledge_selection_loss

        def failed_ack(_workspace: str, _lane: str) -> None:
            raise OSError("synthetic acknowledgement failure")

        monkeypatch.setattr(world.local, "acknowledge_selection_loss", failed_ack)
        with pytest.raises(OSError):
            await world.coordinator.reconcile_task_selection_losses(world.runtime)
        before = await world.runtime.ledger.load_frontier()
        assert world.local.pending_selection_losses(world.workspace)
        reopened = LocalObservationStore(_state=tmp_path / "state")
        world.local = reopened
        world.wire()
        await world.coordinator.reconcile_task_selection_losses(world.runtime)
        assert (await world.runtime.ledger.load_frontier()).sequence == before.sequence
        assert not reopened.pending_selection_losses(world.workspace)
        assert len(world.observation.list_envelopes(world.workspace)) == 1
        # Durable acknowledgement avoids a new report after another restart.
        assert not LocalObservationStore(_state=tmp_path / "state").pending_selection_losses(
            world.workspace
        )
        del original
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_new_check_reconciles_losses_and_completed_replay_skips_new_history() -> None:
    app = _App()
    calls: list[object] = []

    async def reconcile(runtime: object) -> None:
        assert app.ledger.operation is None
        calls.append(runtime)

    setattr(app, "reconcile_observation_losses", reconcile)
    first = await execute_check_commit(app, _request())
    assert len(calls) == 1

    async def fail_if_called(_runtime: object) -> None:
        raise AssertionError("a completed check must preserve its frozen inputs")

    setattr(app, "reconcile_observation_losses", fail_if_called)
    app.ledger.replay = first
    assert await execute_check_commit(app, _request()) is first


@pytest.mark.anyio
async def test_real_check_imports_loss_before_freezing_and_preserves_replay(tmp_path: Path) -> None:
    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        app = _App()
        app.runtime = cast(object, world.routes)  # pyright: ignore[reportAttributeAccessIssue]
        setattr(
            app, "reconcile_observation_losses", world.coordinator.reconcile_task_selection_losses
        )
        frontier = await world.runtime.ledger.load_frontier()
        request = _request().model_copy(
            update={
                "session_id": world.runtime.session_id,
                "writer_id": world.runtime.writer_id,
                "request_id": _ids(IdKind.REQUEST, 9300),
                "expected_frontier": _request().expected_frontier.model_copy(
                    update={
                        "sequence": str(frontier.sequence),
                        "head_digest": frontier.head_digest,
                    }
                ),
            }
        )
        first = await execute_check_commit(app, request)
        assert "observation_input_loss" in first.coverage.known_gaps
        assert not world.local.pending_selection_losses(world.workspace)

        # A later lost lane cannot change this check's already frozen result.
        async def fail_if_called(_runtime: object) -> None:
            raise AssertionError("replay inspected mutable loss history")

        setattr(app, "reconcile_observation_losses", fail_if_called)
        replay = await execute_check_commit(app, request)
        assert replay.coverage == first.coverage

        from dataclasses import replace

        from integration.application.test_respond_status_receipt import (  # pyright: ignore[reportPrivateUsage]
            _build_app,  # pyright: ignore[reportPrivateUsage]
            _IdleImporter,  # pyright: ignore[reportPrivateUsage]
            _receipt_wire,  # pyright: ignore[reportPrivateUsage]
        )
        from yoetz.application.receipt import execute_receipt
        from yoetz.ports.importer import ImporterPort
        from yoetz.ports.runtime import BundleRuntimePort
        from yoetz.protocol.models import ReceiptRequest

        current = replace(world.runtime, importer=cast(ImporterPort, _IdleImporter()))
        world.routes.runtimes[current.session_id] = current
        receipt_app, _, _ = _build_app()
        receipt_app = replace(receipt_app, runtime=cast(BundleRuntimePort, world.routes))
        receipt = await execute_receipt(
            receipt_app,  # pyright: ignore[reportArgumentType]
            ReceiptRequest.model_validate(
                _receipt_wire(
                    9399,
                    task_id=current.task_id,
                    session=current.session_id,
                    writer=cast(str, current.writer_id),
                    frontier=replay.result_frontier,
                )
            ),
        )
        assert "observation_input_loss" in receipt.coverage.known_gaps
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_unrouted_loss_stays_local_and_new_task_binding_cannot_claim_it(
    tmp_path: Path,
) -> None:
    from yoetz.domain.observation import ObservationCursor, ObservationEnvelope, ObservationSource
    from yoetz.domain.values import JsonObject, timestamp_from_datetime

    world = await _world(tmp_path)
    try:
        envelope = ObservationEnvelope(
            world.session,
            "PostToolUse",
            "unrouted-failure",
            ObservationSource.CLAUDE_HOOK,
            ObservationCursor(1, 0, 1, "hmac-sha256:" + "2" * 64, "test/1"),
            timestamp_from_datetime(world.coordinator.clock.now_utc()),
            JsonObject({"tool_name": "Bash", "exit_status": 1}),
            (),
            (),
        )
        world.local.record_admission_loss(world.workspace, envelope)
        assert world.local.selection_accounting(world.workspace)["unrecoverable_input_count"] == 1
        assert not world.local.pending_selection_losses(world.workspace)
        assert not world.local.selection_loss_workspaces()
        await world.coordinator.reconcile_task_selection_losses(world.runtime)
        assert (await world.runtime.ledger.load_frontier()).sequence == 0
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_healthy_capture_inventory_still_reports_loss_and_preserves_native_cursor(
    tmp_path: Path,
) -> None:
    world = await _world(tmp_path)
    sweeper = world.sweep()
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        # A separate successful inventory proof must not remove loss demand.
        from yoetz.domain.observation import ObservationCaptureBacklog

        world.local.bootstrap_capture_reservations(
            world.workspace,
            {
                world.runtime.task_id: ObservationCaptureBacklog(0, 0, None),
                world.sibling.task_id: ObservationCaptureBacklog(0, 0, None),
            },
            complete=True,
        )
        assert world.local.capture_reservation_bootstrap_ready(world.workspace)
        assert world.workspace in world.local.pending_workspaces()
        await sweeper.sweep()
        assert not world.local.pending_selection_losses(world.workspace)
        assert (await world.runtime.ledger.load_frontier()).sequence > 0
        # The first actual host event is still admissible: the loss marker must
        # not move the task's native cursor or consume its source identity.
        await asyncio.to_thread(_native_failed_command, world, "claude", "next", "retained")
        assert world.requests
        assert world.local.selection_accounting(world.workspace)["unrecoverable_input_count"] == 1
    finally:
        sweeper.close()
        world.coordinator.close()


@pytest.mark.anyio
async def test_failed_loss_reconciliation_prevents_new_check_freeze() -> None:
    app = _App()

    async def unavailable(_runtime: object) -> None:
        raise OSError("synthetic loss read failure")

    setattr(app, "reconcile_observation_losses", unavailable)
    from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

    with pytest.raises(PublicOperationError) as caught:
        await execute_check_commit(app, _request())
    assert caught.value.code is PublicErrorCode.INTERNAL_ERROR
    assert app.ledger.operation is None


@pytest.mark.anyio
async def test_wrong_runtime_cannot_retarget_a_loss(tmp_path: Path) -> None:
    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        world.routes.runtimes[world.runtime.session_id] = world.sibling
        await world.coordinator.reconcile_task_selection_losses(world.runtime)
        assert not world.local.pending_selection_losses(world.workspace)
        assert (await world.runtime.ledger.load_frontier()).sequence > 0
        assert len(world.observation.list_envelopes(world.workspace)) == 1
        assert world.sibling_observation.list_envelopes(world.workspace) == ()
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_quarantined_loss_marker_uses_one_recovery_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hashlib import sha256

    from yoetz.ports.ledger import (
        CheckPhase,
        OperationKind,
        OperationQuarantineCode,
        OperationRecord,
        OperationState,
    )

    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        report = world.local.pending_selection_losses(world.workspace)[0]
        from yoetz.domain.observation_loss import ObservationSelectionLoss
        from yoetz.protocol.canonical import canonical_digest

        loss = ObservationSelectionLoss.from_local_range(report)
        digest = canonical_digest(
            {
                "format": "yoetz.selection-loss-report/1",
                "task_id": loss.task_id,
                "lane": loss.lane,
            }
        )
        operation_id = world.coordinator._stable_operation_id(digest)  # pyright: ignore[reportPrivateUsage]
        terminal = world.coordinator.clock.now_utc()
        result = b"{}"
        quarantined = OperationRecord(
            cast(str, world.runtime.writer_id),
            operation_id,
            OperationKind.PUBLISH_WORK,
            digest,
            OperationState.QUARANTINED,
            CheckPhase.TERMINAL,
            None,
            None,
            None,
            None,
            None,
            result,
            "sha256:" + sha256(result).hexdigest(),
            None,
            OperationQuarantineCode.OPERATION_KIND_STATE_CONTRADICTION,
            terminal,
        )
        original_lookup = world.runtime.ledger.lookup_task_operation

        async def lookup(writer_id: str, candidate_id: str) -> OperationRecord | None:
            if candidate_id == operation_id:
                return quarantined
            return await original_lookup(writer_id, candidate_id)

        monkeypatch.setattr(world.runtime.ledger, "lookup_task_operation", lookup)
        await world.coordinator.reconcile_task_selection_losses(world.runtime)

        assert not world.local.pending_selection_losses(world.workspace)
        reopened = LocalObservationStore(_state=tmp_path / "state")
        assert not reopened.pending_selection_losses(world.workspace)
        assert len(world.observation.list_envelopes(world.workspace)) == 1
        assert (await world.runtime.ledger.load_frontier()).sequence > 0
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_route_valid_malformed_lane_keeps_explicit_loss_coverage(tmp_path: Path) -> None:
    from yoetz.domain.observation_loss import ObservationSelectionLoss
    from yoetz.domain.values import JsonObject

    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")
        state = world.local._load(world.workspace)  # pyright: ignore[reportPrivateUsage]
        entry = dict(state.selection_loss_ranges[0])
        entry["lane"] = "sha256:" + "0" * 64
        state.selection_loss_ranges = (JsonObject(entry),)
        world.local._save(world.workspace, state)  # pyright: ignore[reportPrivateUsage]

        await world.coordinator.reconcile_task_selection_losses(world.runtime)

        assert not world.local.pending_selection_losses(world.workspace)
        normalized = ObservationSelectionLoss.from_local_range_for_recovery(JsonObject(entry))
        state = world.local._load(world.workspace)  # pyright: ignore[reportPrivateUsage]
        state.selection_reported_loss_lanes = tuple(
            [f"sha256:{index:064x}" for index in range(64)] + [normalized.lane]
        )
        world.local._save(world.workspace, state)  # pyright: ignore[reportPrivateUsage]
        reopened = LocalObservationStore(_state=tmp_path / "state")
        assert not reopened.pending_selection_losses(world.workspace)
        assert len(world.observation.list_envelopes(world.workspace)) == 1
        frontier = await world.runtime.ledger.load_frontier()
        frozen = await world.runtime.ledger.freeze_case(
            world.runtime.session_id,
            cast(str, world.runtime.writer_id),
            frontier.sequence,
            _ids(IdKind.REQUEST, 9251),
            "sha256:" + "0" * 64,
        )
        assert isinstance(frozen, FrozenCase)
        assert any(
            "observation_input_loss" in coverage.known_gaps
            for coverage in frozen.case.coverage_by_ref.values()
        )
    finally:
        world.coordinator.close()


@pytest.mark.anyio
async def test_loss_reporting_does_not_require_retained_host_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await _world(tmp_path)
    try:
        await asyncio.to_thread(_native_failed_command, world, "claude", "lost", "not-retained")

        # Lifecycle pruning and a unavailable future hook must not hide an
        # already authenticated loss route. No new mapping is created.
        def no_sessions(_workspace: str) -> tuple[str, ...]:
            return ()

        def no_mapping(_host: str, *, _state: Path | None = None) -> None:
            return None

        monkeypatch.setattr(world.local, "unambiguous_codex_sessions_for_workspace", no_sessions)
        monkeypatch.setattr(world.coordinator, "mapping_loader", no_mapping)
        await world.coordinator.reconcile_task_selection_losses(world.runtime)
        assert not world.local.pending_selection_losses(world.workspace)
        assert (await world.runtime.ledger.load_frontier()).sequence > 0
    finally:
        world.coordinator.close()
