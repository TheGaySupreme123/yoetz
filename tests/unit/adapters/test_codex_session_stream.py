"""Unit tests for incremental Codex session-stream observation."""

from __future__ import annotations

import os
from pathlib import Path

from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    SessionStreamReader,
    default_stream_profile,
    reconcile_session_stream,
    should_trigger_stream_reconcile,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationSource,
    ObservationStatusQuery,
)

_EMPTY = "hmac-sha256:" + ("0" * 64)
_KEY = b"k" * 32


def _reader(session: str, *, generation: int = 1) -> SessionStreamReader:
    return SessionStreamReader(
        session_commitment=session,
        profile=default_stream_profile(),
        cursor=ObservationCursor(
            source_generation=generation,
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
        key_material=_KEY,
    )


def _line(wrapper: str, item_type: str = "command_execution") -> bytes:
    return (
        b'{"type":"%s","item":{"id":"i1","type":"%s","command":"echo",'
        b'"aggregated_output":"ok","exit_code":0,"status":"completed"}}\n'
        % (wrapper.encode(), item_type.encode())
    )


def test_incremental_partial_line_then_complete(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    first = (
        b'{"type":"item.completed","item":{"id":"i1","type":"command_execution",'
        b'"command":"echo","aggregated_output":"ok","exit_code":0,"status":"completed"'
    )
    path.write_bytes(first)
    session = "hmac-sha256:" + ("b" * 64)
    reader = _reader(session)
    advance = reader.advance(path)
    assert advance.envelopes == ()
    assert advance.partial_line.startswith(b'{"type":')
    path.write_bytes(first + b"}}\n")
    advance2 = reader.advance(path)
    assert len(advance2.envelopes) == 1
    assert advance2.envelopes[0].source is ObservationSource.CODEX_SESSION_STREAM
    assert advance2.partial_line == b""
    assert advance2.cursor.last_source_commitment.startswith("hmac-sha256:")
    assert advance2.cursor.last_source_commitment != _EMPTY


def test_truncation_bumps_generation(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(_line("item.completed") + _line("turn.completed"))
    session = "hmac-sha256:" + ("c" * 64)
    reader = _reader(session)
    first = reader.advance(path)
    assert first.cursor.byte_position > 0
    path.write_bytes(_line("turn.started"))
    second = reader.advance(path)
    assert second.truncated is True
    assert second.cursor.source_generation == first.cursor.source_generation + 1
    assert second.cursor.byte_position > 0


def test_restart_from_zero_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"type":"thread.started","thread_id":"t1"}\n')
    session = "hmac-sha256:" + ("d" * 64)
    reader = _reader(session)
    one = reader.advance(path)
    two = reader.advance(path)
    assert two.envelopes == ()
    assert two.cursor.byte_position == one.cursor.byte_position


def test_reconcile_enqueues_recovered_envelopes_into_outbox(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(_line("item.completed"))
    os.chmod(target, 0o600)

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    assert store.pending_outbox_count(workspace) == 0

    locator = CodexSessionStreamLocator(home)
    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    assert result["resolved"] is True
    assert result["accepted"] >= 1
    # Recovered stream envelopes are queued in the same durable outbox as hooks,
    # so a later mapped drain materializes them into the task ledger.
    assert store.pending_outbox_count(workspace) == result["accepted"]


def test_hook_stream_dedup_via_local_store(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("stream-dedup")
    store.bind_session(workspace, session)
    path = tmp_path / "session.jsonl"
    path.write_bytes(_line("item.completed"))
    reader = _reader(session)
    advance = reader.advance(path)
    assert len(advance.envelopes) == 1
    first = store.ingest(advance.envelopes[0])
    second = store.ingest(advance.envelopes[0])
    assert first.disposition.value == "accepted"
    assert second.disposition.value == "duplicate"
    status = store.status(ObservationStatusQuery(workspace))
    assert status.source_coverage[ObservationSource.CODEX_SESSION_STREAM] is True


def test_locator_exact_session_match_and_rejects_ambiguous(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(_line("item.completed"))
    os.chmod(target, 0o600)
    locator = CodexSessionStreamLocator(home)
    resolved = locator.resolve(session_id=session_id)
    assert resolved == target.resolve()

    twin = sessions / f"other-{session_id}.jsonl"
    twin.write_bytes(_line("turn.started"))
    os.chmod(twin, 0o600)
    assert locator.resolve(session_id=session_id) is None


def test_locator_rejects_symlink_and_outside_home(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_line("item.completed"))
    link = sessions / "linked.jsonl"
    link.symlink_to(outside)
    locator = CodexSessionStreamLocator(home)
    assert locator.resolve(session_id="linked", hook_provided_path=str(link)) is None
    assert locator.resolve(session_id="outside", hook_provided_path=str(outside)) is None


def test_auto_reconcile_helper_persists_partial_and_cursor(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session_id = "auto-recon-1"
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    path = sessions / f"rollout-{session_id}.jsonl"
    path.write_bytes(
        b'{"type":"item.completed","item":{"id":"i1","type":"command_execution",'
        b'"command":"echo","aggregated_output":"ok","exit_code":0,"status":"completed"'
    )
    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert result["resolved"] is True
    assert result["accepted"] == 0
    partial = store.get_stream_partial(workspace, session)
    assert partial.startswith(b'{"type":')
    path.write_bytes(path.read_bytes() + b"}}\n")
    result2 = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert result2["accepted"] == 1
    assert store.get_stream_partial(workspace, session) == b""


def test_should_trigger_stream_reconcile_events() -> None:
    assert should_trigger_stream_reconcile("PostToolUse", last_reconcile_mono=None) is True
    assert should_trigger_stream_reconcile("Stop", last_reconcile_mono=None) is True
    assert (
        should_trigger_stream_reconcile(
            "SessionStart", last_reconcile_mono=None, session_source="resume"
        )
        is True
    )
    assert should_trigger_stream_reconcile("UserPromptSubmit", last_reconcile_mono=None) is False
    assert (
        should_trigger_stream_reconcile(
            "UserPromptSubmit", last_reconcile_mono=0.0, now_mono=40.0
        )
        is True
    )
