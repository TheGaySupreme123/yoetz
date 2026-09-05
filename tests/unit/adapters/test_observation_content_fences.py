"""Durable owner-local fences for native captured-content authority."""

from __future__ import annotations

import json
from pathlib import Path

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation import ObservationControlCommand, ObservationRevokeCommand
from yoetz.domain.observation_profiles import CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
from yoetz.domain.values import Timestamp

_PROFILE = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
_STAMP = Timestamp("2026-01-01T00:00:00.000Z")


def _consented(tmp_path: Path) -> tuple[LocalObservationStore, str]:
    state = tmp_path / "isolated-state"
    state.mkdir(mode=0o700)
    store = LocalObservationStore(_state=state)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.set_runtime_enabled(True)
    store.grant_consent(workspace, _STAMP, content_capture_profiles=(_PROFILE,))
    return store, workspace


def _generation(store: LocalObservationStore, workspace: str) -> str:
    authority = store.content_capture_authority(workspace)
    assert authority is not None
    assert authority.active
    assert authority.runtime_enabled
    return authority.generation


def test_real_authority_transitions_fence_aba_and_noop_repeats(tmp_path: Path) -> None:
    store, workspace = _consented(tmp_path)
    command = ObservationControlCommand(workspace)
    initial = _generation(store, workspace)

    # Repeating an already-effective operation leaves an in-flight fence valid.
    store.grant_consent(workspace, _STAMP, content_capture_profiles=(_PROFILE,))
    store.enable_content_capture(workspace, _PROFILE)
    store.set_runtime_enabled(True)
    assert _generation(store, workspace) == initial
    assert store.content_capture_authority_is_current(workspace, initial, (_PROFILE,))

    # Each real transition gets a different durable epoch, even when the
    # visible fields later return to their original values.
    store.pause(command)
    paused = store.content_capture_authority(workspace)
    assert paused is not None and paused.generation != initial
    assert not store.content_capture_authority_is_current(workspace, initial, (_PROFILE,))

    store.resume(command)
    resumed = _generation(store, workspace)
    assert resumed != initial
    assert store.content_capture_authority_is_current(workspace, resumed, (_PROFILE,))

    store.disable_content_capture(workspace, _PROFILE)
    disabled = store.content_capture_authority(workspace)
    assert disabled is not None and disabled.generation != resumed
    store.enable_content_capture(workspace, _PROFILE)
    reenabled = _generation(store, workspace)
    assert reenabled not in {initial, resumed}
    assert not store.content_capture_authority_is_current(workspace, resumed, (_PROFILE,))

    store.set_runtime_enabled(False)
    off = store.content_capture_authority(workspace)
    assert off is not None and not off.runtime_enabled and off.generation != reenabled
    store.set_runtime_enabled(True)
    online = _generation(store, workspace)
    assert online not in {initial, resumed, reenabled}
    assert not store.content_capture_authority_is_current(workspace, reenabled, (_PROFILE,))

    # A repeated READY-style write does not create another epoch.
    store.set_runtime_enabled(True)
    assert _generation(store, workspace) == online

    store.revoke(ObservationRevokeCommand(workspace))
    revoked = store.content_capture_authority(workspace)
    assert revoked is not None and revoked.generation != online


def test_content_fence_epoch_migrates_legacy_state_and_survives_reopen(tmp_path: Path) -> None:
    store, workspace = _consented(tmp_path)
    store.set_runtime_enabled(False)
    store.set_runtime_enabled(True)
    before = _generation(store, workspace)
    state_path = next((tmp_path / "isolated-state" / "observation" / "workspaces").glob("*.json"))

    # A /12 state has no content epoch.  Loading it must mint and persist a
    # fresh nonce instead of deriving a reusable token from consent fields.
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["schema"] = "yoetz.observation-local/12"
    raw.pop("content_capture_epoch", None)
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = LocalObservationStore(_state=tmp_path / "isolated-state")
    after = _generation(migrated, workspace)
    assert after != before
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["schema"] == "yoetz.observation-local/13"
    assert isinstance(persisted["content_capture_epoch"], str)
    assert not migrated.content_capture_authority_is_current(workspace, before, (_PROFILE,))

    reopened = LocalObservationStore(_state=tmp_path / "isolated-state")
    assert _generation(reopened, workspace) == after
    assert reopened.content_capture_authority_is_current(workspace, after, (_PROFILE,))
