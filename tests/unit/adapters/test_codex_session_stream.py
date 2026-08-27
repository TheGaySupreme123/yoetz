"""Unit tests for incremental Codex session-stream observation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from builders.codex_rollout import (
    completed_shell_rollout,
    encode_lines,
    failed_shell_rollout,
    function_call,
    function_call_output,
    response_item,
    session_meta,
)
from yoetz.adapters.importers.codex_jsonl import CodexParsedRecord
from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    SessionStreamReader,
    default_stream_profile,
    envelope_from_stream_record,
    reconcile_session_stream,
    should_trigger_stream_reconcile,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.application.observation_materialize import materialize_observation_envelope
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject

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


def test_default_stream_profile_is_rollout_0_148() -> None:
    profile = default_stream_profile()
    assert profile.cli_version == "0.148.0"
    assert profile.profile_id == "codex-rollout-jsonl/0.148.0/v1"


def test_incremental_partial_line_then_complete(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    first = encode_lines(session_meta(), terminated=False)
    path.write_bytes(first)
    session = "hmac-sha256:" + ("b" * 64)
    reader = _reader(session)
    advance = reader.advance(path)
    assert advance.envelopes == ()
    assert advance.partial_line.startswith(b'{"payload":')
    path.write_bytes(first + b"\n")
    advance2 = reader.advance(path)
    assert len(advance2.envelopes) == 1
    assert advance2.envelopes[0].source is ObservationSource.CODEX_SESSION_STREAM
    assert advance2.partial_line == b""
    assert advance2.cursor.last_source_commitment.startswith("hmac-sha256:")
    assert advance2.cursor.last_source_commitment != _EMPTY
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in advance2.gaps


def test_incremental_partial_header_still_requires_exact_profile_admission(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    first = encode_lines(session_meta(cli_version="0.149.1"), terminated=False)
    path.write_bytes(first)
    session = "hmac-sha256:" + ("f" * 64)
    reader = _reader(session)

    advance = reader.advance(path)
    assert advance.envelopes == ()
    assert advance.cursor.byte_position == 0
    assert advance.cursor.event_position == 0
    assert advance.partial_line == first

    path.write_bytes(first + b"\n")
    completed = reader.advance(path)
    assert completed.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in completed.gaps
    assert completed.cursor.byte_position == len(first) + 1
    assert completed.cursor.event_position == 1


def test_truncation_bumps_generation(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(failed_shell_rollout())
    session = "hmac-sha256:" + ("c" * 64)
    reader = _reader(session)
    first = reader.advance(path)
    assert first.cursor.byte_position > 0
    path.write_bytes(encode_lines(session_meta(history_mode="paginated", ordinal=1)))
    second = reader.advance(path)
    assert second.truncated is True
    assert second.cursor.source_generation == first.cursor.source_generation + 1
    assert second.cursor.byte_position > 0


def test_restart_from_zero_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(encode_lines(session_meta()))
    session = "hmac-sha256:" + ("d" * 64)
    reader = _reader(session)
    one = reader.advance(path)
    two = reader.advance(path)
    assert two.envelopes == ()
    assert two.cursor.byte_position == one.cursor.byte_position


def test_exec_jsonl_grammar_is_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        b'{"type":"item.completed","item":{"id":"i1","type":"command_execution",'
        b'"command":"echo","aggregated_output":"ok","exit_code":0,"status":"completed"}}\n'
    )
    session = "hmac-sha256:" + ("e" * 64)
    advance = _reader(session).advance(path)
    assert advance.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in advance.gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in advance.gaps


def test_malformed_first_header_never_becomes_an_admitted_opaque_event(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"payload":broken}\n' + encode_lines(session_meta()))
    session = "hmac-sha256:" + ("1" * 64)

    advance = _reader(session).advance(path)

    assert advance.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in advance.gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in advance.gaps


def test_reconcile_enqueues_recovered_envelopes_into_outbox(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(failed_shell_rollout())
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
    accepted = result["accepted"]
    assert isinstance(accepted, int) and accepted >= 1
    gaps = result["gaps"]
    assert isinstance(gaps, tuple)
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in gaps
    assert store.pending_outbox_count(workspace) == accepted


def test_reconcile_persists_unknown_event_before_advancing_cursor(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-unknown-event"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            response_item({"type": "future_tool_result", "content": "must-not-persist"}),
        )
    )
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)

    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )

    assert result["resolved"] is True
    gaps = result["gaps"]
    assert isinstance(gaps, tuple)
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in gaps
    assert result["byte_position"] == target.stat().st_size
    envelopes = store.list_envelopes(workspace)
    unsupported = [
        envelope
        for envelope in envelopes
        if ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes
    ]
    assert len(unsupported) == 1
    assert unsupported[0].event_kind == "unsupported_event"
    assert dict(unsupported[0].structural_payload) == {}
    assert b"must-not-persist" not in repr(unsupported[0]).encode()
    assert store.pending_outbox_count(workspace) == result["accepted"]
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in status.gaps


def test_reconcile_does_not_advance_past_a_rejected_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-rejected-event"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(failed_shell_rollout())
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)

    def reject(
        _store: LocalObservationStore,
        envelope: object,
    ) -> ObservationIngestResult:
        del envelope
        return ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            ObservationGapCode.CURSOR_STALE.value,
            None,
        )

    monkeypatch.setattr(LocalObservationStore, "ingest", reject)
    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )

    assert result["accepted"] == 0
    assert result["byte_position"] == 0
    assert result["event_position"] == 0
    cursor = store.get_stream_cursor(workspace, session)
    assert cursor is not None
    assert cursor.byte_position == 0
    assert cursor.event_position == 0
    assert store.pending_outbox_count(workspace) == 0


def test_reconcile_resets_cursor_when_stream_mapping_changes(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(failed_shell_rollout())
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    store.set_stream_cursor(
        workspace,
        session,
        ObservationCursor(
            source_generation=4,
            byte_position=target.stat().st_size,
            event_position=9,
            last_source_commitment=_EMPTY,
            mapping_version="codex-obs-stream/1.0.0",
        ),
    )

    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    cursor = store.get_stream_cursor(workspace, session)
    assert result["accepted"]
    assert cursor is not None
    assert cursor.mapping_version == STREAM_MAPPING_VERSION
    assert cursor.source_generation == 5
    assert cursor.byte_position > 0


def test_function_call_name_pairs_with_output_across_reconcile_passes(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-pair-across-passes"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            function_call(name="shell", call_id="call-shell-cross-pass"),
        )
    )
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    locator = CodexSessionStreamLocator(home)
    first = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    assert first["accepted"] == 2
    target.write_bytes(
        target.read_bytes()
        + encode_lines(function_call_output(call_id="call-shell-cross-pass", exit_code=0))
    )
    second = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    assert second["accepted"] == 1
    outputs = [
        row.envelope
        for row in store.list_pending_outbox_rows(workspace)
        if row.envelope.structural_payload.get("action") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert outputs[0].structural_payload.get("tool_name") == "shell"
    batch = materialize_observation_envelope(outputs[0], task_id="task_stream_pair")
    assert tuple(item.role for item in batch.drafts) == ("action", "result")


def test_output_without_same_generation_call_is_evidence_only(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-output-without-origin"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            function_call_output(call_id="call-without-origin", exit_code=1),
        )
    )
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)

    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )

    assert result["accepted"] == 2
    output = next(
        row.envelope
        for row in store.list_pending_outbox_rows(workspace)
        if row.envelope.structural_payload.get("action") == "function_call_output"
    )
    assert ObservationGapCode.UNPAIRED_EVENT.value in output.gap_codes
    batch = materialize_observation_envelope(output, task_id="task_stream_unpaired")
    assert tuple(item.role for item in batch.drafts) == ("unpaired_evidence",)


def test_truncation_clears_persisted_call_pairing_across_store_restart(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-pair-truncated-generation"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            function_call(name="shell", call_id="call-reused"),
            response_item(
                {
                    "content": [{"text": "x" * 2048, "type": "output_text"}],
                    "role": "assistant",
                    "type": "message",
                }
            ),
        )
    )
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    locator = CodexSessionStreamLocator(home)
    first = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    assert first["generation"] == 1

    target.write_bytes(
        encode_lines(session_meta(), function_call_output(call_id="call-reused", exit_code=0))
    )
    reopened = LocalObservationStore(_state=tmp_path)
    second = reconcile_session_stream(
        reopened,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )

    assert second["truncated"] is True
    assert second["generation"] == 2
    outputs = [
        row.envelope
        for row in reopened.list_pending_outbox_rows(workspace)
        if row.envelope.cursor.source_generation == 2
        and row.envelope.structural_payload.get("action") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert "tool_name" not in outputs[0].structural_payload


def test_same_or_larger_rotation_clears_pairing_across_store_restart(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-pair-rotated-generation"
    target = sessions / f"rollout-{session_id}.jsonl"
    initial = encode_lines(
        session_meta(),
        function_call(name="shell", call_id="call-reused"),
    )
    target.write_bytes(initial)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    locator = CodexSessionStreamLocator(home)
    reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )

    replacement = target.with_suffix(".replacement")
    rotated = encode_lines(
        session_meta(),
        function_call_output(call_id="call-reused", exit_code=0),
        response_item(
            {
                "content": [{"text": "y" * 2048, "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            }
        ),
    )
    assert len(rotated) >= len(initial)
    replacement.write_bytes(rotated)
    os.replace(replacement, target)
    reopened = LocalObservationStore(_state=tmp_path)
    second = reconcile_session_stream(
        reopened,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )

    assert second["rotated"] is True
    assert second["generation"] == 2
    outputs = [
        row.envelope
        for row in reopened.list_pending_outbox_rows(workspace)
        if row.envelope.cursor.source_generation == 2
        and row.envelope.structural_payload.get("action") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert "tool_name" not in outputs[0].structural_payload


@pytest.mark.parametrize("blocked_by", ["ingest", "outbox"])
def test_rotated_identity_retries_from_header_when_first_envelope_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked_by: str,
) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = f"stream-rotation-{blocked_by}-retry"
    target = sessions / f"rollout-{session_id}.jsonl"
    initial = encode_lines(
        session_meta(),
        function_call(name="shell", call_id="call-old"),
    )
    target.write_bytes(initial)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    locator = CodexSessionStreamLocator(home)
    reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    before = store.get_stream_cursor(workspace, session)
    assert before is not None and before.source_generation == 1

    replacement = target.with_suffix(".replacement")
    rotated = encode_lines(
        session_meta(),
        function_call_output(call_id="call-old", exit_code=0),
        response_item(
            {
                "content": [{"text": "z" * 2048, "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            }
        ),
    )
    assert len(rotated) >= len(initial)
    replacement.write_bytes(rotated)
    os.replace(replacement, target)

    with monkeypatch.context() as blocked:
        if blocked_by == "ingest":

            def reject(
                _store: LocalObservationStore,
                envelope: object,
            ) -> ObservationIngestResult:
                del envelope
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    None,
                )

            blocked.setattr(LocalObservationStore, "ingest", reject)
        else:

            def overflow(
                _store: LocalObservationStore,
                workspace_commitment: str,
                selected_session_id: str,
                envelope: object,
            ) -> str:
                del workspace_commitment, selected_session_id, envelope
                return ObservationGapCode.OUTBOX_OVERFLOW.value

            blocked.setattr(LocalObservationStore, "enqueue_outbox", overflow)
        failed = reconcile_session_stream(
            LocalObservationStore(_state=tmp_path),
            workspace_commitment=workspace,
            session_commitment=session,
            codex_session_id=session_id,
            locator=locator,
        )

    assert failed["generation"] == 1
    assert failed["byte_position"] == before.byte_position
    retry_store = LocalObservationStore(_state=tmp_path)
    retried = reconcile_session_stream(
        retry_store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )

    assert retried["rotated"] is True
    assert retried["generation"] == 2
    assert retried["byte_position"] == len(rotated)
    outputs = [
        row.envelope
        for row in retry_store.list_pending_outbox_rows(workspace)
        if row.envelope.cursor.source_generation == 2
        and row.envelope.structural_payload.get("action") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert "tool_name" not in outputs[0].structural_payload


def test_legacy_unfenced_call_pairing_is_discarded(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("stream-legacy-call-tools")
    store.replace_stream_call_tools(
        workspace,
        session,
        source_generation=1,
        call_tools={"call-old": "shell"},
    )
    state_path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["schema"] = "yoetz.observation-local/8"
    raw["stream_call_tools"][session] = {"call-old": "shell"}
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    reopened = LocalObservationStore(_state=tmp_path)

    assert (
        reopened.stream_call_tools_for_session(
            workspace,
            session,
            source_generation=1,
        )
        == {}
    )


def test_locator_matches_reverted_thread_filename(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "08" / "22"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    rollout_id = "019f8b27-cccc-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-08-22T12-00-00-{session_id}_{rollout_id}.jsonl"
    target.write_bytes(completed_shell_rollout())
    os.chmod(target, 0o600)
    locator = CodexSessionStreamLocator(home)
    assert locator.resolve(session_id=session_id) == target.resolve()


def test_compressed_rollout_is_explicit_unsupported_format(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "08" / "22"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-08-22T12-00-00-{session_id}.jsonl.zst"
    target.write_bytes(b"\x28\xb5\x2f\xfd" + b"not-a-real-frame")
    os.chmod(target, 0o600)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert result["resolved"] is True
    assert result["accepted"] == 0
    assert result["gaps"] == (ObservationGapCode.UNSUPPORTED_FORMAT.value,)
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in status.gaps


def test_hook_stream_dedup_via_local_store(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("stream-dedup")
    store.bind_session(workspace, session)
    path = tmp_path / "session.jsonl"
    path.write_bytes(failed_shell_rollout())
    reader = _reader(session)
    advance = reader.advance(path)
    assert len(advance.envelopes) >= 1
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
    target.write_bytes(failed_shell_rollout())
    os.chmod(target, 0o600)
    locator = CodexSessionStreamLocator(home)
    resolved = locator.resolve(session_id=session_id)
    assert resolved == target.resolve()

    twin = sessions / f"other-{session_id}.jsonl"
    twin.write_bytes(encode_lines(session_meta()))
    os.chmod(twin, 0o600)
    assert locator.resolve(session_id=session_id) is None


def test_locator_rejects_symlink_and_outside_home(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(failed_shell_rollout())
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
    prefix = encode_lines(session_meta(), terminated=True)
    body = encode_lines(
        function_call(name="shell", call_id="i1"),
        terminated=False,
    )
    path.write_bytes(prefix + body)
    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert result["resolved"] is True
    assert result["accepted"] == 1
    partial = store.get_stream_partial(workspace, session)
    assert partial.startswith(b'{"payload":')
    path.write_bytes(
        path.read_bytes() + b"\n" + encode_lines(function_call_output(call_id="i1", exit_code=0))
    )
    result2 = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    accepted = result2["accepted"]
    assert isinstance(accepted, int) and accepted >= 1
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
        should_trigger_stream_reconcile("UserPromptSubmit", last_reconcile_mono=0.0, now_mono=40.0)
        is True
    )


def test_function_call_output_maps_completed_tool_without_unknown_gap() -> None:
    record = CodexParsedRecord(
        1,
        0,
        80,
        "response_item",
        "function_call_output",
        JsonObject(
            {
                "payload": {
                    "call_id": "call-shell-1",
                    "exit_code": 1,
                    "name": "shell",
                    "status": "completed",
                    "type": "function_call_output",
                },
                "type": "response_item",
            }
        ),
    )
    envelope = envelope_from_stream_record(
        record,
        session_commitment="hmac-sha256:" + ("ab" * 32),
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=80,
            event_position=1,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in envelope.gap_codes
    assert envelope.structural_payload.get("tool_name") == "shell"
    assert envelope.structural_payload.get("tool_call_id") == "call-shell-1"
    assert envelope.structural_payload.get("exit_status") == 1
    batch = materialize_observation_envelope(envelope, task_id="task_stream_map")
    assert tuple(item.role for item in batch.drafts) == ("action", "result")
