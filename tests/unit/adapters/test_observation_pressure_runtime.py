from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import yoetz.adapters.integrations.observation_local as local_mod
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.observation_budget import ObservationMode, PressureState
from yoetz.domain.observation_settings import (
    ObservationCapacityProfile,
    ObservationDetailProfile,
    ObservationSelection,
)
from yoetz.domain.values import JsonObject, Timestamp

_NOW = Timestamp("2026-09-10T00:01:00.000Z")
_OLD = Timestamp("2026-09-10T00:00:00.000Z")
_WALL_SECONDS = datetime(2026, 9, 10, 0, 1, tzinfo=UTC).timestamp()
_SESSION = "hmac-sha256:" + "1" * 64


def _store(tmp_path: Path, monotonic: list[float]) -> tuple[LocalObservationStore, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    store = LocalObservationStore(
        _state=tmp_path / "state",
        _monotonic=lambda: monotonic[0],
        # Keep wall-minus-monotonic stable so the store recognizes one boot
        # epoch while the test advances its deterministic monotonic clock.
        _wall=lambda: _WALL_SECONDS + monotonic[0],
    )
    return store, store.workspace_commitment(str(workspace_path))


def _select_detailed(store: LocalObservationStore, workspace: str) -> None:
    store.set_session_selection(
        workspace,
        _SESSION,
        ObservationSelection(
            detail=ObservationDetailProfile.DETAILED,
            capacity=ObservationCapacityProfile.STANDARD,
        ),
        set_at=_NOW,
    )


def _pending_envelope(*, session: str, receipt_time: Timestamp) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=session,
        event_kind="PostToolUse",
        source_identity="pending:oldest-age",
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=0,
            event_position=1,
            last_source_commitment="hmac-sha256:" + "a" * 64,
            mapping_version="codex-obs-hook/1.0.0",
        ),
        receipt_time=receipt_time,
        structural_payload=JsonObject({"tool_name": "shell", "exit_status": 1}),
        content_object_refs=(),
        gap_codes=(),
    )


def test_update_pressure_persists_selected_and_effective_state(tmp_path: Path) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    _select_detailed(store, workspace)
    store.update_capture_backlog(workspace, 450, 0, _NOW, _NOW, route_id="capture-a")

    first = store.update_selection_pressure(workspace, _SESSION)
    assert first.selected_mode is ObservationMode.DETAILED
    assert first.effective_mode is ObservationMode.FOCUSED
    assert first.state is PressureState.HIGH
    assert first.transition is not None
    assert first.transition.notice == "downgrade"
    assert workspace in store.pending_workspaces()

    reopened = LocalObservationStore(
        _state=tmp_path / "state",
        _monotonic=lambda: monotonic[0],
        _wall=lambda: _WALL_SECONDS,
    )
    status = reopened.selection_runtime_status(workspace, _SESSION)
    assert status["selected_mode"] == "detailed"
    assert status["effective_mode"] == "focused"
    assert status["pressure_state"] == "high"
    capture_status = cast(Mapping[str, object], status["capture_backlog"])
    assert capture_status["capture_backlog_scope"] == "partial"


def test_workspace_runtime_status_aggregates_active_child_pressure(tmp_path: Path) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    store.set_workspace_selection(
        workspace,
        ObservationSelection(
            detail=ObservationDetailProfile.DETAILED,
            capacity=ObservationCapacityProfile.STANDARD,
        ),
        set_at=_NOW,
    )
    _select_detailed(store, workspace)
    store.update_capture_backlog(workspace, 450, 0, _NOW, _NOW, route_id="capture-a")
    store.update_selection_pressure(workspace, _SESSION)

    status = store.selection_runtime_status(workspace)
    assert status["selected_mode"] == "detailed"
    assert status["effective_mode"] == "focused"
    assert status["pressure_state"] == "high"
    status_wire = store.status(ObservationStatusQuery(workspace)).selection_runtime
    assert status_wire is not None
    assert status_wire.effective_mode is ObservationMode.FOCUSED
    assert status_wire.pressure_state is PressureState.HIGH


def test_runtime_status_does_not_advance_recovery_or_write(tmp_path: Path) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    _select_detailed(store, workspace)
    store.update_capture_backlog(workspace, 450, 0, _NOW, _NOW, route_id="capture-a")
    store.update_selection_pressure(workspace, _SESSION)
    state_path = store._workspace_path(workspace)  # pyright: ignore[reportPrivateUsage]
    before = state_path.stat().st_mtime_ns

    # A low read must not start the dwell timer or persist a snapshot.
    store.update_capture_backlog(workspace, 0, 0, None, _NOW, route_id="capture-a")
    before_low_read = state_path.stat().st_mtime_ns
    status = store.selection_runtime_status(workspace, _SESSION)
    assert status["pressure_state"] == "high"
    assert status["effective_mode"] == "focused"
    assert state_path.stat().st_mtime_ns == before_low_read
    assert before_low_read != before


def test_low_pressure_dwell_recovers_and_emits_one_transition(tmp_path: Path) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    _select_detailed(store, workspace)
    store.update_capture_backlog(workspace, 450, 0, _NOW, _NOW, route_id="capture-a")
    high = store.update_selection_pressure(workspace, _SESSION)
    assert high.state is PressureState.HIGH

    store.update_capture_backlog(workspace, 0, 0, None, _NOW, route_id="capture-a")
    monotonic[0] = 1.0
    before_dwell = store.update_selection_pressure(workspace, _SESSION)
    assert before_dwell.state is PressureState.HIGH
    assert before_dwell.transition is None
    assert before_dwell.effective_mode is ObservationMode.FOCUSED

    monotonic[0] = 11.1
    recovered = store.update_selection_pressure(workspace, _SESSION)
    assert recovered.state is PressureState.HEALTHY
    assert recovered.effective_mode is ObservationMode.DETAILED
    assert recovered.transition is not None
    assert recovered.transition.notice == "recovery"

    monotonic[0] = 12.0
    repeated = store.update_selection_pressure(workspace, _SESSION)
    assert repeated.transition is None
    assert repeated.snapshot.transition_identity == recovered.snapshot.transition_identity


def test_retry_does_not_reset_oldest_pending_age(tmp_path: Path) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    envelope = _pending_envelope(session=_SESSION, receipt_time=_OLD)
    assert store.enqueue_outbox(workspace, "codex-session", envelope) is None

    before = store.selection_runtime_status(workspace, _SESSION)
    assert before["oldest_pending_age_ms"] == 60_000
    (row,) = store.list_pending_outbox_rows(workspace)
    retried = store.bump_outbox_row_attempt(
        workspace, row, reason="transport_unavailable", attempted_at=_NOW
    )
    assert retried is not None

    after = store.selection_runtime_status(workspace, _SESSION)
    assert after["oldest_pending_age_ms"] == before["oldest_pending_age_ms"]


def test_pressure_snapshot_cache_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monotonic = [0.0]
    store, workspace = _store(tmp_path, monotonic)
    monkeypatch.setattr(local_mod, "_MAX_HOOK_SEQUENCES", 2)
    sessions = [_SESSION, "hmac-sha256:" + "2" * 64, "hmac-sha256:" + "3" * 64]
    for session in sessions:
        store.update_selection_pressure(workspace, session)

    with store._lock:  # pyright: ignore[reportPrivateUsage]
        state = store._load(workspace)  # pyright: ignore[reportPrivateUsage]
        assert state.pressure_snapshots is not None
        assert len(state.pressure_snapshots) == 2
