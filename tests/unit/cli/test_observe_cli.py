"""Unit tests for observe CLI consent controls and path commitments."""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe as observe_cli
from yoetz.cli.app import app
from yoetz.cli.hook_diagnostics import record_hook_diagnostic
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.observation import (
    OBSERVATION_BACKPRESSURE_REASON,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationLifecycle,
    ObservationStatusQuery,
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


def test_git_subdirectory_consent_and_hook_share_one_canonical_workspace(
    tmp_path: Path,
    capsys: object,
) -> None:
    state = tmp_path / "state"
    repository = tmp_path / "repo"
    nested = repository / "packages/app"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()

    assert observe_cli.grant_observation(workspace=str(nested), _state=state) == 0
    store = LocalObservationStore(_state=state)
    root_commitment = store.workspace_commitment(str(repository))
    subdirectory_commitment = store.workspace_commitment(str(nested))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    unrelated_commitment = store.workspace_commitment(str(unrelated))
    store.grant_consent(unrelated_commitment)
    assert root_commitment != subdirectory_commitment
    consent = store.consent_for(root_commitment)
    assert consent is not None and consent.active
    assert store.consent_for(subdirectory_commitment) is None

    assert observe_cli.pause_observation(workspace=str(nested), _state=state) == 0
    assert observe_cli.resume_observation(workspace=str(nested), _state=state) == 0
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "git-subdir-session", "hook_event_name": "SessionStart"}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(nested),
        _state=state,
        skip_service=True,
    )
    assert code == 0
    assert store.find_workspace_for_codex_session("git-subdir-session") == root_commitment
    assert store.find_workspace_for_codex_session("git-subdir-session") != unrelated_commitment

    assert observe_cli.revoke_observation(workspace=str(nested), _state=state) == 0
    revoked = store.consent_for(root_commitment)
    assert revoked is not None and not revoked.active
    assert str(repository) not in capsys.readouterr().out  # type: ignore[attr-defined]


def test_non_git_hook_workspace_keeps_exact_locator_with_multiple_consents(tmp_path: Path) -> None:
    state = tmp_path / "state"
    workspace_path = tmp_path / "plain-workspace"
    unrelated = tmp_path / "unrelated"
    workspace_path.mkdir()
    unrelated.mkdir()
    store = LocalObservationStore(_state=state)
    workspace_commitment = store.workspace_commitment(str(workspace_path))
    store.grant_consent(workspace_commitment)
    store.grant_consent(store.workspace_commitment(str(unrelated)))

    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "plain-workspace-session", "hook_event_name": "SessionStart"}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(workspace_path),
        _state=state,
        skip_service=True,
    )

    assert code == 0
    assert store.find_workspace_for_codex_session("plain-workspace-session") == workspace_commitment


def test_explicit_git_root_does_not_fall_back_to_legacy_subdirectory_consent(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    repository = tmp_path / "repo"
    legacy_subdirectory = repository / "packages/app"
    legacy_subdirectory.mkdir(parents=True)
    (repository / ".git").mkdir()
    store = LocalObservationStore(_state=state)
    legacy_commitment = store.workspace_commitment(str(legacy_subdirectory))
    store.grant_consent(legacy_commitment)
    assert store.consent_for(store.workspace_commitment(str(repository))) is None

    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "legacy-subdir-session", "hook_event_name": "SessionStart"}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(repository),
        _state=state,
        skip_service=True,
    )

    assert code == 0
    assert store.codex_sessions_for_workspace(legacy_commitment) == ()
    assert store.list_pending_outbox_rows(legacy_commitment) == ()


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
    assert payload["pending_delivery_causes"] == {"not_attempted": 1}
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
            workspace,
            "quarantine-cli",
            envelope.source_identity,
            ObservationGapCode.LEDGER_REJECTED.value,
        )

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path) == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["quarantine_count"] == 2
    # Per-reason depth (#272): a destroyed-and-replaced event is visible as its
    # cause, not hidden inside one opaque number.
    assert payload["quarantine_causes"] == {ObservationGapCode.LEDGER_REJECTED.value: 2}
    assert payload["delivery_causes"] == {ObservationGapCode.LEDGER_REJECTED.value: 2}
    assert payload["pending_delivery_causes"] == {}
    assert payload["quarantine_evicted_count"] == 0

    assert (
        observe_cli.observe_status(workspace=str(tmp_path), json_output=False, _state=tmp_path) == 0
    )
    text = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "quarantine: 2" in text
    assert "cause: ledger_rejected=2" in text
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


def test_status_reports_modified_present_plugin_as_installed_not_activated(
    tmp_path: Path, capsys: object
) -> None:
    """#347: exercise the production activation classifier through observe status."""

    from yoetz.adapters.integrations.codex_plugin import install_plugin
    from yoetz.ports.integrations import IntegrationScope, IntegrationTarget

    state = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(mode=0o700)
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(project))
    install_plugin(target, allow_untested=True, codex_version=None)
    hooks = project / ".agents/plugins/yoetz/hooks/hooks.json"
    hooks.write_bytes(hooks.read_bytes() + b"\n")

    assert (
        observe_cli.observe_status(
            workspace=str(project),
            codex_path=Path(sys.executable),
            codex_home=codex_home,
            json_output=True,
            _state=state,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["plugin_activation"] == "installed_not_activated"


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


@pytest.mark.anyio
async def test_manual_drain_quarantines_nonretryable_control_error(tmp_path: Path) -> None:
    """#540: the manual drain honors ControlError.retryable like hook drains."""

    from yoetz.ports.control import ControlError

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "manual-control-error", "tool_name": "shell", "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            raise ControlError("frame_invalid", retryable=False)

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
    assert summary["quarantined"] == 1
    assert summary["retry_pending"] == 0
    assert summary["reasons"] == {ObservationGapCode.LEDGER_REJECTED.value: 1}
    assert store.list_pending_outbox_rows(workspace) == ()
    assert store.list_quarantine(workspace)[0][2] == ObservationGapCode.LEDGER_REJECTED.value


@pytest.mark.anyio
async def test_manual_drain_retires_retrying_lane_before_later_row(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session_id = "manual-fifo"
    for ordinal in (1, 2):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": session_id,
                    "tool_name": "shell",
                    "event_ordinal": ordinal,
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    calls = 0

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            nonlocal calls
            del body, deadline_ms
            calls += 1
            return observation_ingest_result_to_json(
                ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.SERVICE_UNAVAILABLE.value,
                    None,
                )
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
    assert calls == 1
    assert summary["attempted"] == 1
    assert summary["retry_pending"] == 1
    rows = store.list_pending_outbox_rows(workspace)
    assert len(rows) == 2
    assert rows[0].attempts == 1
    assert rows[1].attempts == 0
    assert rows[1].last_reason == ObservationGapCode.SERVICE_UNAVAILABLE.value


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["vault_locked", "paused", "observation_disabled"])
async def test_manual_drain_stops_workspace_after_global_retry_reason(
    tmp_path: Path,
    reason: str,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for index in (1, 2):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": f"manual-global-{index}",
                    "tool_name": "shell",
                    "event_ordinal": 1,
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    calls = 0

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            nonlocal calls
            del body, deadline_ms
            calls += 1
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.REJECTED, reason, None)
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
    assert calls == 1
    assert summary["attempted"] == 1
    assert summary["retry_pending"] == 1
    assert len(store.list_pending_outbox_rows(workspace)) == 2


@pytest.mark.anyio
async def test_manual_drain_does_not_project_designed_backpressure_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "manual-barrier", "tool_name": "shell", "event_ordinal": 1}
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
                ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    OBSERVATION_BACKPRESSURE_REASON,
                    None,
                )
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_cli._drain_observation_async(  # pyright: ignore[reportPrivateUsage]
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )

    assert (
        OBSERVATION_BACKPRESSURE_REASON not in store.status(ObservationStatusQuery(workspace)).gaps
    )


@pytest.mark.anyio
async def test_manual_drain_quarantines_ended_unmapped_lane(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session_id = "manual-ended-unmapped"
    for ordinal in (1, 2):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {"session_id": session_id, "tool_name": "shell", "event_ordinal": ordinal}
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    store.note_session_end(workspace, store.session_commitment(session_id))

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            return observation_ingest_result_to_json(
                ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.MAPPING_MISSING.value,
                    None,
                )
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
    assert summary["quarantined"] == 2
    assert store.list_pending_outbox_rows(workspace) == ()


@pytest.mark.anyio
async def test_manual_drain_retires_lane_after_attempt_cas_loss(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session_id = "manual-cas-loss"
    for ordinal in (1, 2):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {"session_id": session_id, "tool_name": "shell", "event_ordinal": ordinal}
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    selected = store.list_pending_outbox_rows(workspace)[0]
    calls = 0

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            nonlocal calls
            del body, deadline_ms
            calls += 1
            assert store.bump_outbox_row_attempt(
                workspace,
                selected,
                reason=ObservationGapCode.SERVICE_UNAVAILABLE.value,
            )
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_cli._drain_observation_async(  # pyright: ignore[reportPrivateUsage]
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )

    assert calls == 1
    assert len(store.list_pending_outbox_rows(workspace)) == 2


@pytest.mark.anyio
async def test_manual_drain_retry_ceiling_quarantines_head_and_continues_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.application.observation_drain as drain_module

    monkeypatch.setattr(drain_module, "MAX_CONSECUTIVE_OBSERVATION_REJECTIONS", 2)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session_id = "manual-ceiling"
    for ordinal in (1, 2):
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": session_id,
                    "tool_name": "shell",
                    "event_ordinal": ordinal,
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    first = store.list_pending_outbox_rows(workspace)[0]
    store.bump_outbox_row_attempt(
        workspace,
        first,
        reason=ObservationGapCode.SERVICE_UNAVAILABLE.value,
    )
    identities = [row.envelope.source_identity for row in store.list_pending_outbox_rows(workspace)]

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            identity = body["envelope"]["source_identity"]  # type: ignore[index]
            result = (
                ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.SERVICE_UNAVAILABLE.value,
                    None,
                )
                if identity == identities[0]
                else ObservationIngestResult(
                    ObservationIngestDisposition.DUPLICATE,
                    None,
                    None,
                )
            )
            return observation_ingest_result_to_json(result)

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
    assert summary["attempted"] == 2
    assert summary["quarantined"] == 1
    assert summary["acknowledged"] == 1
    assert store.list_pending_outbox_rows(workspace) == ()
    assert store.list_quarantine(workspace)[0][1].source_identity == identities[0]


@pytest.mark.parametrize(
    "workspace", ["", "/private/does-not-exist/CANARY"], ids=["empty", "missing"]
)
def test_status_names_an_unresolvable_locator_instead_of_internal_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], workspace: str
) -> None:
    """`--workspace ""` (an unset CLAUDE_PROJECT_DIR) is a typed refusal, exit 2 (#428)."""

    code = observe_cli.observe_status(workspace=workspace, json_output=False, _state=tmp_path)
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.startswith("observation_status_failed:workspace_unresolvable: ")
    assert "CLAUDE_PROJECT_DIR" in captured.err
    assert "internal_error" not in captured.err
    assert "CANARY" not in captured.err

    code = observe_cli.observe_status(workspace=workspace, json_output=True, _state=tmp_path)
    captured = capsys.readouterr()
    assert code == 2
    error = json.loads(captured.out)["error"]
    assert error["reason"] == "workspace_unresolvable"
    assert error["code"] == "INVALID_REQUEST"
    assert error["operation"] == "status"
    assert error["retryable"] is False
    assert "CANARY" not in captured.out


def test_status_command_reports_typed_workspace_failure_through_the_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.adapters.integrations.observation_local as local

    monkeypatch.setattr(local, "state_dir", lambda: tmp_path)
    result = CliRunner().invoke(app, ["observe", "status", "--workspace", "", "--json"])
    assert result.exit_code == 2
    assert "internal_error" not in result.output
    assert json.loads(result.stdout)["error"]["reason"] == "workspace_unresolvable"


def test_status_maps_bounded_storage_refusals_to_their_public_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

    def corrupt(self: LocalObservationStore, workspace: str) -> object:
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT, "Observation state is invalid.", False
        )

    monkeypatch.setattr(LocalObservationStore, "consent_for", corrupt)
    code = observe_cli.observe_status(workspace=str(tmp_path), json_output=False, _state=tmp_path)
    captured = capsys.readouterr()
    assert code == 40
    assert captured.err == (
        "observation_status_failed:storage_corrupt: Observation state is invalid.\n"
    )

    def unsafe(self: LocalObservationStore, workspace: str) -> object:
        raise PublicOperationError(PublicErrorCode.STORAGE_UNSAFE, "Runtime gate is unsafe.", True)

    monkeypatch.setattr(LocalObservationStore, "consent_for", unsafe)
    code = observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path)
    captured = capsys.readouterr()
    assert code == 20
    error = json.loads(captured.out)["error"]
    assert (error["reason"], error["code"], error["retryable"]) == (
        "storage_unsafe",
        "STORAGE_UNSAFE",
        True,
    )


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="owner permission refusal requires a non-root POSIX process",
)
def test_status_maps_real_lock_open_permission_refusal_without_leaking_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue #428 sandbox failure is forced at the real ``os.open`` boundary."""

    import yoetz.adapters.integrations.observation_local as local

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    observation = state / "observation"
    observation.mkdir(mode=0o700)
    lock_path = observation / ".store.lock"
    lock_path.write_bytes(b"")
    lock_path.chmod(0)
    monkeypatch.setattr(local, "state_dir", lambda: state)
    try:
        result = CliRunner().invoke(
            app, ["observe", "status", "--workspace", str(tmp_path), "--json"]
        )
    finally:
        lock_path.chmod(0o600)

    assert result.exit_code == 20
    assert result.stderr == ""
    error = json.loads(result.stdout)["error"]
    assert error == {
        "code": "SERVICE_UNAVAILABLE",
        "message": (
            "the local Yoetz observation store could not be opened or locked from this process; "
            "make the owner-only state directory accessible and writable from the supported host "
            "surface, then retry"
        ),
        "operation": "status",
        "reason": "storage_unavailable",
        "retryable": True,
    }
    assert str(state) not in result.stdout
    assert "internal_error" not in result.stdout


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="read-only directory refusal requires a non-root POSIX process",
)
def test_status_maps_real_read_only_state_directory_to_storage_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"

    def read_only_store(*, _state: Path | None = None, **_kwargs: object) -> LocalObservationStore:
        store = LocalObservationStore(_state=_state)
        store._root.chmod(0o500)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        return store

    monkeypatch.setattr(observe_cli, "LocalObservationStore", read_only_store)
    try:
        code = observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=state)
    finally:
        (state / "observation").chmod(0o700)

    captured = capsys.readouterr()
    assert code == 20
    error = json.loads(captured.out)["error"]
    assert (error["reason"], error["code"], error["retryable"]) == (
        "storage_unavailable",
        "SERVICE_UNAVAILABLE",
        True,
    )
    assert str(state) not in captured.out


def test_status_maps_real_unsafe_lock_path_to_storage_unsafe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    lock_path = state / "observation" / ".store.lock"
    lock_path.parent.mkdir(mode=0o700)
    lock_path.mkdir(mode=0o700)

    code = observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=state)

    captured = capsys.readouterr()
    assert code == 20
    error = json.loads(captured.out)["error"]
    assert (error["reason"], error["code"], error["retryable"]) == (
        "storage_unsafe",
        "STORAGE_UNSAFE",
        False,
    )
    assert str(state) not in captured.out


def test_status_maps_real_missing_store_parent_to_storage_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"

    def missing_parent_store(
        *, _state: Path | None = None, **_kwargs: object
    ) -> LocalObservationStore:
        store = LocalObservationStore(_state=_state)
        store._root.rmdir()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        return store

    monkeypatch.setattr(observe_cli, "LocalObservationStore", missing_parent_store)
    code = observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=state)

    captured = capsys.readouterr()
    assert code == 20
    error = json.loads(captured.out)["error"]
    assert (error["reason"], error["code"], error["retryable"]) == (
        "storage_unavailable",
        "SERVICE_UNAVAILABLE",
        True,
    )
    assert str(state) not in captured.out


def test_status_maps_real_lock_contention_to_storage_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.adapters.integrations.observation_local as local

    if local.fcntl is None:
        pytest.skip("POSIX flock is unavailable")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    observation = state / "observation"
    observation.mkdir(mode=0o700)
    lock_path = observation / ".store.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    local.fcntl.flock(descriptor, local.fcntl.LOCK_EX | local.fcntl.LOCK_NB)
    monkeypatch.setattr(local, "_STORE_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(local, "_STORE_LOCK_POLL_SECONDS", 0.005)
    try:
        code = observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=state)
    finally:
        local.fcntl.flock(descriptor, local.fcntl.LOCK_UN)
        os.close(descriptor)

    captured = capsys.readouterr()
    assert code == 20
    error = json.loads(captured.out)["error"]
    assert (error["reason"], error["code"], error["retryable"]) == (
        "storage_unavailable",
        "SERVICE_UNAVAILABLE",
        True,
    )
    assert str(state) not in captured.out


def test_status_leaves_genuinely_unexpected_defects_for_the_internal_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def defect(self: LocalObservationStore, path: str) -> str:
        del self, path
        raise RuntimeError("unexpected_observation_defect")

    monkeypatch.setattr(LocalObservationStore, "workspace_commitment", defect)
    with pytest.raises(RuntimeError, match="unexpected_observation_defect"):
        observe_cli.observe_status(workspace=str(tmp_path), json_output=True, _state=tmp_path)


@pytest.mark.parametrize(
    ("verb", "method"),
    [
        ("pause", "pause"),
        ("resume", "resume"),
        ("revoke", "revoke"),
    ],
)
def test_control_verbs_preserve_bounded_public_error_exits(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    method: str,
) -> None:
    from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

    def corrupt(self: LocalObservationStore, command: object) -> object:
        del self, command
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT, "Observation state is invalid.", False
        )

    monkeypatch.setattr(LocalObservationStore, method, corrupt)
    if verb == "pause":
        code = observe_cli.pause_observation(workspace=str(tmp_path), _state=tmp_path)
    elif verb == "resume":
        code = observe_cli.resume_observation(workspace=str(tmp_path), _state=tmp_path)
    else:
        code = observe_cli.revoke_observation(workspace=str(tmp_path), _state=tmp_path)
    assert code == 40
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"observation_{verb}_failed:storage_corrupt: Observation state is invalid.\n"
    )


def test_status_maps_an_unsafe_state_path_to_storage_unsafe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.config.paths import PathSafetyError

    def unsafe_store(*args: object, **kwargs: object) -> object:
        raise PathSafetyError("path_contains_symlink")

    monkeypatch.setattr(observe_cli, "LocalObservationStore", unsafe_store)
    code = observe_cli.observe_status(workspace=str(tmp_path), json_output=False, _state=tmp_path)
    captured = capsys.readouterr()
    assert code == 20
    assert captured.err.startswith(
        "observation_status_failed:storage_unsafe: the local Yoetz observation store has an "
        "unsafe file or path shape"
    )


def test_other_observe_verbs_share_the_typed_workspace_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for verb, call in (
        ("grant", lambda: observe_cli.grant_observation(workspace="", _state=tmp_path)),
        ("pause", lambda: observe_cli.pause_observation(workspace="", _state=tmp_path)),
        ("resume", lambda: observe_cli.resume_observation(workspace="", _state=tmp_path)),
        ("revoke", lambda: observe_cli.revoke_observation(workspace="", _state=tmp_path)),
        (
            "reclaim",
            lambda: observe_cli.reclaim_observation(
                workspace="", json_output=False, _state=tmp_path
            ),
        ),
        (
            "drain",
            lambda: observe_cli.drain_observation(workspace="", json_output=False, _state=tmp_path),
        ),
    ):
        assert call() == 2
        captured = capsys.readouterr()
        assert captured.err.startswith(f"observation_{verb}_failed:workspace_unresolvable: ")
