"""Unit tests for incremental Codex session-stream observation."""

from __future__ import annotations

from pathlib import Path

from yoetz.adapters.integrations.codex_session_stream import (
    SessionStreamReader,
    default_stream_profile,
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
