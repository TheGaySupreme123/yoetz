"""Structural setup consent keeps other hosts' native content arms (issue #835)."""

from __future__ import annotations

from pathlib import Path

import pytest

import yoetz.adapters.integrations.observation_local as observation_local
import yoetz.cli.observe as observe_cli
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import setup
from yoetz.domain.observation import ObservationRevokeCommand
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.tui.models import LayerState
from yoetz.tui.runtime import YoetzRuntime

_CLAUDE = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
_CURSOR = CURSOR_ORDINARY_OBSERVATION_PROFILE_ID


def _isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[LocalObservationStore, Path, str]:
    """Point the default store root (the one setup opens) at a disposable directory."""

    state = tmp_path / "state"
    monkeypatch.setattr(observation_local, "state_dir", lambda: state)
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = LocalObservationStore()
    commitment = store.workspace_commitment(str(workspace.resolve()))
    store.set_runtime_enabled(True)
    return store, workspace, commitment


def test_codex_setup_consent_keeps_an_approved_claude_arm_and_its_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact reproduction from the issue, asserted the other way round."""

    store, workspace, commitment = _isolated(tmp_path, monkeypatch)
    store.grant_consent(commitment, content_capture_profiles=(_CLAUDE,))
    before = store.content_capture_authority(commitment)
    assert before is not None and before.active and before.profiles == (_CLAUDE,)

    report = setup._grant_observation_consent(workspace)  # pyright: ignore[reportPrivateUsage]

    assert report == {
        "outcome": "granted",
        "transition": "unchanged",
        "workspace_commitment": commitment,
        "content_capture_profiles": [_CLAUDE],
    }
    reopened = LocalObservationStore()
    after = reopened.content_capture_authority(commitment)
    assert after == before
    assert reopened.content_capture_authority_is_current(
        commitment, before.generation, before.profiles
    )


def test_codex_setup_consent_keeps_a_pending_selection_preview_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selection preview binds the consent fence; routine setup must not stale it."""

    store, workspace, commitment = _isolated(tmp_path, monkeypatch)
    store.grant_consent(commitment, content_capture_profiles=(_CLAUDE,))
    preview_authority = observe_cli._selection_authority_digest(  # pyright: ignore[reportPrivateUsage]
        store, commitment
    )

    setup._grant_observation_consent(workspace)  # pyright: ignore[reportPrivateUsage]

    assert (
        observe_cli._selection_authority_digest(  # pyright: ignore[reportPrivateUsage]
            LocalObservationStore(), commitment
        )
        == preview_authority
    )


def test_codex_setup_consent_on_a_fresh_workspace_grants_no_content_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, commitment = _isolated(tmp_path, monkeypatch)

    report = setup._grant_observation_consent(workspace)  # pyright: ignore[reportPrivateUsage]

    assert report == {
        "outcome": "granted",
        "transition": "granted",
        "workspace_commitment": commitment,
        "content_capture_profiles": [],
    }
    assert store.content_capture_profiles(commitment) == ()


def test_codex_setup_consent_after_revocation_does_not_restore_the_revoked_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, workspace, commitment = _isolated(tmp_path, monkeypatch)
    store.grant_consent(commitment, content_capture_profiles=(_CLAUDE,))
    revoked = store.content_capture_authority(commitment)
    assert revoked is not None
    store.revoke(ObservationRevokeCommand(commitment))
    pending = store.pending_consent_revocation(commitment)
    assert pending is not None

    blocked = setup._grant_observation_consent(workspace)  # pyright: ignore[reportPrivateUsage]
    assert blocked["outcome"] == "failed"
    assert store.content_capture_profiles(commitment) == ()

    store.mark_consent_revocation_fenced(commitment, pending[0])
    report = setup._grant_observation_consent(workspace)  # pyright: ignore[reportPrivateUsage]

    assert report["transition"] == "granted"
    assert report["content_capture_profiles"] == []
    assert not store.content_capture_authority_is_current(
        commitment, revoked.generation, revoked.profiles
    )


def test_observe_grant_reports_the_arms_it_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = str(tmp_path)
    assert observe_cli.grant_observation(workspace=workspace, _state=tmp_path) == 0
    first = capsys.readouterr().out
    assert "observation_content_capture_kept" not in first

    for profile in (_CURSOR, _CLAUDE):
        assert (
            observe_cli.enable_observation_content(
                profile=profile, workspace=workspace, _state=tmp_path
            )
            == 0
        )
    capsys.readouterr()
    assert observe_cli.grant_observation(workspace=workspace, _state=tmp_path) == 0

    out = capsys.readouterr().out
    assert "observation_consent_granted:hmac-sha256:" in out
    assert f"observation_content_capture_kept:{_CLAUDE},{_CURSOR}" in out
    assert workspace not in out
    store = LocalObservationStore(_state=tmp_path)
    commitment = store.workspace_commitment(str(tmp_path.resolve()))
    assert store.content_capture_profiles(commitment) == (_CLAUDE, _CURSOR)


def test_setup_summary_names_the_content_arms_consent_kept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup._emit_human_report(  # pyright: ignore[reportPrivateUsage]
        {
            "registration": {
                "outcome": "already_registered",
                "observation_consent": {
                    "outcome": "granted",
                    "transition": "unchanged",
                    "workspace_commitment": "hmac-sha256:" + "a" * 64,
                    "content_capture_profiles": [_CLAUDE],
                },
            },
            "service": {"reachable": True, "state": "ready"},
            "provider": {},
            "integration": {},
            "next_steps": [],
        }
    )

    out = capsys.readouterr().out
    assert "Observation consent: granted" in out
    assert f"Native content profiles kept: {_CLAUDE}" in out


def test_terminal_interface_consent_layer_names_the_kept_arms(tmp_path: Path) -> None:
    runtime = YoetzRuntime(cwd=tmp_path)

    def consent_layer(profiles: list[str]) -> tuple[LayerState, str]:
        outcome = runtime._integration_outcome(  # pyright: ignore[reportPrivateUsage]
            {
                "outcome": "already_registered",
                "state": "yoetz_owned",
                "observation_consent": {
                    "outcome": "granted",
                    "transition": "unchanged",
                    "content_capture_profiles": profiles,
                },
            }
        )
        layer = next(item for item in outcome.layers if item.key == "project_consent")
        return layer.state, layer.detail

    assert consent_layer([_CLAUDE]) == (
        LayerState.VERIFIED,
        f"native content profiles kept: {_CLAUDE}",
    )
    assert consent_layer([]) == (LayerState.VERIFIED, "")
