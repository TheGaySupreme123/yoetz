"""Unit tests for observe CLI consent controls and path commitments."""

from __future__ import annotations

from pathlib import Path

from yoetz.cli import observe as observe_cli
from yoetz.domain.observation import ObservationLifecycle


def test_grant_pause_resume_revoke_roundtrip(tmp_path: Path, capsys: object) -> None:
    workspace = str(tmp_path)
    assert observe_cli.grant_observation(workspace=workspace, _state=tmp_path) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "observation_consent_granted:hmac-sha256:" in out
    assert workspace not in out

    assert observe_cli.pause_observation(workspace=workspace, _state=tmp_path) == 0
    assert observe_cli.resume_observation(workspace=workspace, _state=tmp_path) == 0
    assert observe_cli.revoke_observation(workspace=workspace, _state=tmp_path) == 0

    code = observe_cli.observe_status(workspace=workspace, json_output=False, _state=tmp_path)
    assert code == 0
    status_out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "consent: revoked" in status_out
    assert ObservationLifecycle.STOPPED.value in status_out
    assert workspace not in status_out


def test_workspace_commitment_stable_and_path_free(tmp_path: Path) -> None:
    from yoetz.adapters.integrations.observation_local import LocalObservationStore

    store = LocalObservationStore(_state=tmp_path)
    one = store.workspace_commitment(str(tmp_path.resolve()))
    two = store.workspace_commitment(str(tmp_path.resolve()))
    assert one == two
    assert one.startswith("hmac-sha256:")
    assert str(tmp_path) not in one
