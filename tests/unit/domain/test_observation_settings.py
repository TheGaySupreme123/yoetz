"""Owner-scoped observation selection settings and persistence boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation_budget import PressureState
from yoetz.domain.observation_settings import (
    DEFAULT_OBSERVATION_SELECTION,
    ObservationCapacityProfile,
    ObservationDetailProfile,
    ObservationSelection,
    ObservationSelectionRuntimeStatus,
    ObservationSelectionSetting,
    ObservationSelectionSettings,
    observation_selection_runtime_status_from_json,
    observation_selection_runtime_status_to_json,
    observation_selection_settings_from_json,
    observation_selection_settings_to_json,
    resolve_observation_selection,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.errors import ProtocolValueError

_SET = Timestamp("2026-01-01T00:00:00.000Z")
_EXPIRY = Timestamp("2026-01-02T00:00:00.000Z")
_LATER = Timestamp("2026-01-03T00:00:00.000Z")


def _selection(
    detail: ObservationDetailProfile, capacity: ObservationCapacityProfile
) -> ObservationSelection:
    return ObservationSelection(detail, capacity)


def test_resolution_prefers_session_then_workspace_then_default() -> None:
    workspace = ObservationSelectionSetting(
        _selection(ObservationDetailProfile.FOCUSED, ObservationCapacityProfile.LARGER),
        _SET,
    )
    session = ObservationSelectionSetting(
        _selection(ObservationDetailProfile.DETAILED, ObservationCapacityProfile.STANDARD),
        _SET,
    )
    commitment = "hmac-sha256:" + "a" * 64
    settings = ObservationSelectionSettings(workspace=workspace).with_session(commitment, session)

    resolved = resolve_observation_selection(settings, session_commitment=commitment)
    assert resolved.selection == session.selection
    assert resolved.origin == "session"
    assert resolve_observation_selection(settings).selection == workspace.selection
    assert (
        resolve_observation_selection(ObservationSelectionSettings()).selection
        == DEFAULT_OBSERVATION_SELECTION
    )


def test_settings_json_round_trip_is_closed_and_path_free() -> None:
    commitment = "hmac-sha256:" + "b" * 64
    setting = ObservationSelectionSetting(
        _selection(ObservationDetailProfile.DETAILED, ObservationCapacityProfile.LARGEST),
        _SET,
        _EXPIRY,
    )
    settings = ObservationSelectionSettings(workspace=setting).with_session(commitment, setting)

    restored = observation_selection_settings_from_json(
        observation_selection_settings_to_json(settings)
    )
    assert restored == settings
    assert all("/" not in key for key, _ in restored.sessions)


def test_malformed_settings_drop_to_safe_default() -> None:
    parsed = observation_selection_settings_from_json(
        {
            "workspace": {"detail": "detailed", "capacity": 123, "set_at": "bad"},
            "sessions": {"/private/path": {"detail": "detailed", "capacity": 8192}},
        }
    )
    assert parsed == ObservationSelectionSettings()


def test_expired_store_setting_is_removed_and_falls_back(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.set_workspace_selection(
        workspace,
        _selection(ObservationDetailProfile.DETAILED, ObservationCapacityProfile.LARGER),
        set_at=_SET,
        expires_at=_EXPIRY,
    )
    assert store.selection_settings_for(workspace, now=_LATER).workspace is None
    reopened = LocalObservationStore(_state=tmp_path)
    assert reopened.selection_settings_for(workspace, now=_LATER).workspace is None


def test_session_selection_is_cleared_on_session_end(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace, granted_at=_SET)
    session = store.bind_codex_session(workspace, "session-settings")
    store.set_session_selection(
        workspace,
        session,
        _selection(ObservationDetailProfile.DETAILED, ObservationCapacityProfile.STANDARD),
        set_at=_SET,
        expires_at=_EXPIRY,
    )
    store.note_session_end(workspace, session, generation=1)
    assert store.session_selection_setting(workspace, session, now=_SET) is None


def test_expiry_must_be_after_setting_time() -> None:
    with pytest.raises(ProtocolValueError):
        ObservationSelectionSetting(
            DEFAULT_OBSERVATION_SELECTION,
            _SET,
            _SET,
        )


def test_runtime_status_round_trip_is_typed_and_path_free() -> None:
    commitment = "hmac-sha256:" + "c" * 64
    runtime = ObservationSelectionRuntimeStatus(
        selected_mode=ObservationDetailProfile.DETAILED,
        effective_mode=ObservationDetailProfile.FOCUSED,
        selected_capacity=ObservationCapacityProfile.LARGER,
        effective_capacity=ObservationCapacityProfile.STANDARD,
        selection_origin="session",
        selection_expires_at=_EXPIRY,
        pressure_state=PressureState.HIGH,
        pressure_transition_identity="observation-budget-v1:healthy>high:bytes:7",
        content_allowed=False,
        admission_allowed=True,
        queue_count=7,
        queue_bytes=128,
        state_bytes=256,
        oldest_pending_age_ms=10,
        pending_attempts=1,
        pending_lifecycle_count=0,
        capture_backlog=JsonObject({"count": 0}),
        protected_count_reserve=128,
        protected_bytes_reserve=131072,
        session_fair_share=128,
        session_fair_share_bytes=131072,
        accounting=JsonObject({"observed_count": 7}),
        session_commitment=commitment,
    )
    encoded = observation_selection_runtime_status_to_json(runtime)
    assert observation_selection_runtime_status_from_json(encoded) == runtime
    assert "/" not in str(encoded)
