"""Unit tests for observe CLI consent controls and path commitments."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe as observe_cli
from yoetz.cli.app import app
from yoetz.cli.hook_diagnostics import record_hook_diagnostic
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.observation import (
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationLifecycle,
    observation_ingest_result_to_json,
)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _terminal_compact(text: str) -> str:
    """Normalize Rich styling and soft wraps before asserting CLI contract text."""

    return "".join(_ANSI_ESCAPE.sub("", text).split())


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


def test_status_separates_undelivered_from_lag_and_reports_ambiguous_activation(
    tmp_path: Path, capsys: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_inspection(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("status must not inspect activation without an exact executable")

    monkeypatch.setattr(observe_cli, "inspect_activation", unexpected_inspection)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "status-session", "tool_name": "shell", "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    record_hook_diagnostic("service_unavailable", "PostToolUse", _state=tmp_path)

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path) == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["status"]["lag_events"] == 0
    assert payload["undelivered_count"] == 1
    assert payload["delivery_causes"] == {"not_attempted": 1}
    assert payload["last_successful_drain"] == "never"
    assert payload["mapping_present"] is False
    assert payload["plugin_activation"] == "unknown"
    unavailable = payload["hook_diagnostics"]["reasons"]["service_unavailable"]
    assert unavailable["count"] == 1
    # A live failure reads as live; a stale one is dated instead of tallied (#310).
    assert unavailable["recent"] == 1
    assert unavailable["first_seen"] == unavailable["last_seen"]
    assert payload["hook_diagnostics"]["recent_count"] == 1


def test_status_surfaces_quarantine_depth_and_reclaim_empties_it(
    tmp_path: Path, capsys: object
) -> None:
    """#211: quarantine is a first-class status line, and reclaim drops it loudly."""

    from yoetz.cli.observe_hooks import map_hook_payload_to_envelope

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "quarantine-cli")
    session = store.session_commitment("quarantine-cli")
    for ordinal in (1, 2):
        envelope = map_hook_payload_to_envelope(
            "PostToolUse",
            {"session_id": "quarantine-cli", "tool_name": "shell", "correlation_id": f"q{ordinal}"},
            session_commitment=session,
            event_ordinal=ordinal,
            key_material=store.key_material(),
        )
        store.ingest(envelope)
        store.enqueue_outbox(workspace, "quarantine-cli", envelope)
        assert store.quarantine_outbox(
            workspace, "quarantine-cli", envelope.source_identity, "consent_revoked"
        )

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path) == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["quarantine_count"] == 2
    # Per-reason depth (#272): a destroyed-and-replaced event is visible as its
    # cause, not hidden inside one opaque number.
    assert payload["quarantine_causes"] == {"consent_revoked": 2}
    assert payload["quarantine_evicted_count"] == 0

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=False, _state=tmp_path) == 0
    )
    text = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "quarantine: 2" in text
    assert "cause: consent_revoked=2" in text
    assert (
        "reclaim by changing to the selected workspace and running "
        "'yoetz observe reclaim --workspace .'"
    ) in text
    assert str(tmp_path) not in text

    assert (
        observe_cli.reclaim_observation(workspace=str(tmp_path), json_output=True, _state=tmp_path)
        == 0
    )
    reclaim_payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert reclaim_payload["reclaimed"] == 2
    assert reclaim_payload["quarantine_count"] == 0
    # A voluntary reclaim is never reported as involuntary eviction.
    assert reclaim_payload["quarantine_evicted_count"] == 0
    assert reclaim_payload["quarantine_reclaimed_count"] == 2
    assert str(tmp_path) not in json.dumps(reclaim_payload)

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path) == 0
    )
    after = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert after["quarantine_count"] == 0
    assert after["quarantine_evicted_count"] == 0
    assert after["quarantine_reclaimed_count"] == 2


def test_status_inspects_only_the_explicit_codex_target(
    tmp_path: Path, capsys: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = tmp_path / "bin" / "codex-testing"
    codex_home = tmp_path / ".codex-testing"
    observed: dict[str, object] = {}

    class State:
        value = "active"

    class Inspection:
        state = State()

    def inspect(target: object, **kwargs: object) -> object:
        observed["target"] = target
        observed.update(kwargs)
        return Inspection()

    monkeypatch.setattr(observe_cli, "inspect_activation", inspect)

    assert (
        observe_cli.observe_status(
            workspace=str(tmp_path),
            codex_path=codex_path,
            codex_home=codex_home,
            json_output=True,
            _state=tmp_path,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["plugin_activation"] == "active"
    assert observed["executable_path"] == str(codex_path)
    assert observed["codex_home"] == codex_home


def test_status_help_exposes_exact_activation_target_options() -> None:
    result = CliRunner().invoke(app, ["observe", "status", "--help"])

    assert result.exit_code == 0
    output = _terminal_compact(result.stdout)
    assert "--codex-path" in output
    assert "ExactCodexexecutable" in output
    assert "--codex-home" in output
    assert "ExpectedCodexhome/cache" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ("--codex-path", "/exact/bin/codex"),
        ("--codex-home", "/exact/codex-home"),
    ],
)
def test_status_command_rejects_partial_activation_target(arguments: tuple[str, str]) -> None:
    result = CliRunner().invoke(app, ["observe", "status", *arguments])

    assert result.exit_code == 2
    assert "--codex-pathand--codex-homemustbeprovidedtogether" in _terminal_compact(result.stderr)


def test_status_command_forwards_the_exact_activation_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_path = tmp_path / "bin" / "codex-testing"
    codex_home = tmp_path / ".codex-testing"
    observed: dict[str, object] = {}

    def status(**kwargs: object) -> int:
        observed.update(kwargs)
        return 0

    monkeypatch.setattr(observe_cli, "observe_status", status)
    result = CliRunner().invoke(
        app,
        [
            "observe",
            "status",
            "--workspace",
            str(tmp_path),
            "--codex-path",
            str(codex_path),
            "--codex-home",
            str(codex_home),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "workspace": str(tmp_path),
        "codex_path": codex_path,
        "codex_home": codex_home,
        "json_output": True,
    }


@pytest.mark.anyio
async def test_drain_quarantines_setup_probe_and_routes_other_rows(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session in ("yoetz-setup-probe-session", "ordinary-session"):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {"session_id": session, "tool_name": "shell", "event_ordinal": 1}
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    code, summary = await observe_cli._drain_observation_async(  # pyright: ignore[reportPrivateUsage]
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    assert summary["attempted"] == 1
    assert summary["acknowledged"] == 1
    assert summary["quarantined"] == 1
    assert summary["reasons"] == {"setup_probe": 1}
    assert store.pending_outbox_count(workspace) == 0
    assert store.quarantined_count(workspace) == 1
