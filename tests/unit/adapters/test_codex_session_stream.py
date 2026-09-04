"""Unit tests for incremental Codex session-stream observation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from builders.codex_rollout import (
    completed_shell_rollout,
    encode_lines,
    failed_shell_rollout,
    function_call,
    function_call_output,
    item_completed,
    response_item,
    session_meta,
)
from yoetz.adapters.importers.codex_jsonl import CodexParsedRecord
from yoetz.adapters.integrations import codex_session_stream as stream_module
from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    SessionStreamReader,
    default_stream_profile,
    envelope_from_stream_record,
    reconcile_session_stream,
    should_trigger_stream_reconcile,
    stream_profile_from_id,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.application.observation_materialize import materialize_observation_envelope
from yoetz.domain.events import ResultOutcome, ResultRecordedPayload
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject


def test_source_file_identity_bounds_large_filesystem_integers() -> None:
    facts = cast(
        os.stat_result,
        SimpleNamespace(st_dev=1 << 63, st_ino=(1 << 63) + 1),
    )

    identity = stream_module._source_file_identity(  # pyright: ignore[reportPrivateUsage]
        facts,
        b"k" * 32,
    )

    assert identity.startswith("hmac-sha256:")
    assert len(identity) == len("hmac-sha256:") + 64


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
    assert completed.cursor.event_position == 0

    # A rejected header keeps admission durably required: later appends are
    # refused instead of being materialized without an accepted exact profile.
    path.write_bytes(
        first
        + b"\n"
        + encode_lines(
            response_item(
                {
                    "content": [{"text": "later", "type": "output_text"}],
                    "role": "assistant",
                    "type": "message",
                }
            )
        )
    )
    appended = reader.advance(path)
    assert appended.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in appended.gaps
    assert appended.cursor.event_position == 0


def test_completed_oversized_line_advances_and_later_records_remain_reachable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    profile = default_stream_profile()
    chunk = stream_module._MAX_READ_CHUNK  # pyright: ignore[reportPrivateUsage]
    header = encode_lines(session_meta())
    oversized_body = (
        b'{"type":"response_item","payload":"'
        + (b"x" * (profile.max_line_bytes + (2 * chunk)))
        + b'"}'
    )
    later = encode_lines(
        response_item(
            {
                "content": [{"text": "later", "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            }
        )
    )
    path.write_bytes(header + oversized_body + b"\n" + later)
    session = "hmac-sha256:" + ("9" * 64)
    reader = _reader(session)

    admitted = reader.advance(path)
    assert admitted.cursor.event_position == 1
    assert admitted.partial_line

    entered = reader.advance(path)
    assert entered.cursor.event_position == 1
    assert entered.cursor.byte_position == len(header) + profile.max_line_bytes + 1
    assert entered.partial_line.startswith(
        stream_module._OVERSIZED_PARTIAL_PREFIX  # pyright: ignore[reportPrivateUsage]
    )

    cursor = entered.cursor
    partial = entered.partial_line
    skipped = entered
    for _ in range(4):
        prior_position = cursor.byte_position
        skipped = SessionStreamReader(
            session_commitment=session,
            profile=profile,
            cursor=cursor,
            key_material=_KEY,
            partial_line=partial,
        ).advance(path)
        assert skipped.cursor.byte_position - prior_position <= chunk
        cursor = skipped.cursor
        partial = skipped.partial_line
        if skipped.cursor.event_position == 2:
            break

    assert skipped.cursor.event_position == 2
    assert skipped.cursor.byte_position == len(header) + len(oversized_body) + 1
    assert skipped.cursor.last_source_commitment.startswith("hmac-sha256:")
    assert skipped.partial_line == b""
    assert len(skipped.envelopes) == 1
    assert skipped.envelopes[0].event_kind == "unsupported_event"
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in skipped.gaps

    recovered = SessionStreamReader(
        session_commitment=session,
        profile=profile,
        cursor=skipped.cursor,
        key_material=_KEY,
        partial_line=skipped.partial_line,
    ).advance(path)
    assert recovered.cursor.event_position == 3
    assert recovered.cursor.byte_position == path.stat().st_size
    assert len(recovered.envelopes) == 1
    assert recovered.envelopes[0].event_kind == "response_item"


def test_forged_oversized_line_continuation_restarts_from_admission(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    profile = default_stream_profile()
    header = encode_lines(session_meta())
    oversized_body = (
        b'{"type":"response_item","payload":"' + (b"x" * (profile.max_line_bytes + 10)) + b'"}\n'
    )
    path.write_bytes(header + oversized_body)
    session = "hmac-sha256:" + ("8" * 64)
    reader = _reader(session)

    admitted = reader.advance(path)
    entered = reader.advance(path)
    assert admitted.cursor.event_position == 1
    assert entered.partial_line.startswith(
        stream_module._OVERSIZED_PARTIAL_PREFIX  # pyright: ignore[reportPrivateUsage]
    )
    forged = entered.partial_line[:-1] + bytes([entered.partial_line[-1] ^ 1])

    restarted = SessionStreamReader(
        session_commitment=session,
        profile=profile,
        cursor=entered.cursor,
        key_material=_KEY,
        partial_line=forged,
    ).advance(path)

    assert restarted.cursor.source_generation == entered.cursor.source_generation + 1
    assert restarted.cursor.event_position == 1
    assert ObservationGapCode.CURSOR_STALE.value in restarted.gaps
    assert ObservationGapCode.TRUNCATED_PAYLOAD.value in restarted.gaps
    assert not restarted.partial_line.startswith(
        stream_module._OVERSIZED_PARTIAL_PREFIX  # pyright: ignore[reportPrivateUsage]
    )


def test_oversized_initial_header_never_establishes_profile_admission(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    profile = default_stream_profile()
    path.write_bytes(b"{" + (b"x" * (profile.max_line_bytes + 10)) + b"}\n")
    session = "hmac-sha256:" + ("7" * 64)
    advance = _reader(session).advance(path)
    assert advance.cursor.event_position == 0
    assert advance.partial_line.startswith(
        stream_module._OVERSIZED_PARTIAL_PREFIX  # pyright: ignore[reportPrivateUsage]
    )

    completed = SessionStreamReader(
        session_commitment=session,
        profile=profile,
        cursor=advance.cursor,
        key_material=_KEY,
        partial_line=advance.partial_line,
    ).advance(path)

    assert completed.envelopes == ()
    assert completed.cursor.event_position == 0
    assert completed.cursor.byte_position == path.stat().st_size
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in completed.gaps

    # The refusal is durable: appending an exact header and a record afterwards
    # never materializes events for a generation whose first line was rejected.
    with path.open("ab") as handle:
        handle.write(encode_lines(session_meta()))
    refused = SessionStreamReader(
        session_commitment=session,
        profile=profile,
        cursor=completed.cursor,
        key_material=_KEY,
        partial_line=completed.partial_line,
    ).advance(path)
    assert refused.envelopes == ()
    assert refused.cursor.event_position == 0
    assert refused.cursor.byte_position == completed.cursor.byte_position
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in refused.gaps


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


@pytest.mark.parametrize("blocked_by", ["ingest", "outbox"])
def test_oversized_continuation_survives_final_envelope_backpressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked_by: str,
) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = f"stream-oversized-{blocked_by}-retry"
    target = sessions / f"rollout-{session_id}.jsonl"
    profile = default_stream_profile()
    target.write_bytes(
        encode_lines(session_meta())
        + b'{"type":"response_item","payload":"'
        + (b"x" * (profile.max_line_bytes + 10))
        + b'"}\n'
    )
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    locator = CodexSessionStreamLocator(home)

    for _ in range(2):
        reconcile_session_stream(
            store,
            workspace_commitment=workspace,
            session_commitment=session,
            codex_session_id=session_id,
            locator=locator,
        )
    before = store.get_stream_cursor(workspace, session)
    before_partial = store.get_stream_partial(workspace, session)
    assert before is not None and before.event_position == 1
    assert before_partial.startswith(
        stream_module._OVERSIZED_PARTIAL_PREFIX  # pyright: ignore[reportPrivateUsage]
    )

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
            store,
            workspace_commitment=workspace,
            session_commitment=session,
            codex_session_id=session_id,
            locator=locator,
        )

    assert failed["byte_position"] == before.byte_position
    assert failed["event_position"] == before.event_position
    assert store.get_stream_partial(workspace, session) == before_partial

    retried = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=locator,
    )
    assert retried["event_position"] == 2
    assert retried["byte_position"] == target.stat().st_size
    assert store.get_stream_partial(workspace, session) == b""


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


def test_originating_call_name_overrides_mismatched_output_name(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-pair-name-conflict"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            function_call(name="shell", call_id="call-name-conflict"),
            response_item(
                {
                    "call_id": "call-name-conflict",
                    "exit_code": 0,
                    "name": "apply_patch",
                    "output": "ok",
                    "status": "completed",
                    "type": "function_call_output",
                }
            ),
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

    assert result["accepted"] == 3
    output = next(
        row.envelope
        for row in store.list_pending_outbox_rows(workspace)
        if row.envelope.structural_payload.get("action") == "function_call_output"
    )
    assert output.structural_payload.get("tool_name") == "shell"
    assert ObservationGapCode.DEDUP_CONFLICT.value in output.gap_codes
    batch = materialize_observation_envelope(output, task_id="task_stream_pair_conflict")
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


def test_uncompressed_rollout_precedes_compressed_sibling(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "08" / "22"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    sessions.chmod(0o700)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    plain = sessions / f"rollout-{session_id}.jsonl"
    compressed = sessions / f"rollout-{session_id}.jsonl.zst"
    plain.write_bytes(completed_shell_rollout())
    compressed.write_bytes(b"\x28\xb5\x2f\xfd" + b"not-a-real-frame")
    os.chmod(plain, 0o600)
    os.chmod(compressed, 0o600)
    locator = CodexSessionStreamLocator(home)
    assert locator.resolve(session_id=session_id) == plain.resolve()
    assert (
        locator.resolve(session_id=session_id, hook_provided_path=str(compressed))
        == plain.resolve()
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
        locator=locator,
    )
    assert result["resolved"] is True
    accepted = result["accepted"]
    gaps = result["gaps"]
    assert type(accepted) is int and accepted > 0
    assert type(gaps) is tuple
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value not in gaps


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


def test_function_call_output_preserves_negative_one_exit_status() -> None:
    record = CodexParsedRecord(
        1,
        0,
        80,
        "response_item",
        "function_call_output",
        JsonObject(
            {
                "payload": {
                    "call_id": "call-shell-negative",
                    "exit_code": -1,
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
        session_commitment="hmac-sha256:" + ("ac" * 32),
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=80,
            event_position=1,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in envelope.gap_codes
    assert envelope.structural_payload.get("exit_status") == -1
    batch = materialize_observation_envelope(envelope, task_id="task_stream_negative")
    result = batch.drafts[1].draft.payload
    assert isinstance(result, ResultRecordedPayload)
    assert result.outcome is ResultOutcome.FAILURE
    assert result.exit_status == -1


@pytest.mark.parametrize("exit_code", [-2, 256, True, "1"])
def test_function_call_output_rejects_out_of_profile_exit_status(exit_code: object) -> None:
    record = CodexParsedRecord(
        1,
        0,
        80,
        "response_item",
        "function_call_output",
        JsonObject(
            {
                "payload": {
                    "call_id": "call-shell-invalid-exit",
                    "exit_code": exit_code,
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
        session_commitment="hmac-sha256:" + ("ad" * 32),
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=80,
            event_position=1,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes
    assert "exit_status" not in envelope.structural_payload
    batch = materialize_observation_envelope(envelope, task_id="task_stream_invalid_exit")
    assert batch.skip_reason == "unsupported_or_gap"


def test_reconcile_retains_yoetz_self_observation_locally_and_advances_past_it(
    tmp_path: Path,
) -> None:
    """#564: the stream copy of a ``status`` call is the same self-observation the hook saw."""

    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    session_id = "stream-self-observation"
    target = sessions / f"rollout-{session_id}.jsonl"
    target.write_bytes(
        encode_lines(
            session_meta(),
            function_call(name="mcp__yoetz__status", call_id="y1", arguments="{}"),
            function_call_output(call_id="y1", output="private projection", exit_code=None),
            function_call(name="shell", call_id="s1", arguments='{"command":"pytest"}'),
            function_call_output(call_id="s1", output="ok", exit_code=0),
            function_call(name="mcp__yoetz__respond", call_id="y2", arguments="{}"),
            function_call_output(call_id="y2", output="recorded", exit_code=None),
            function_call(name="mcp__yoetz__status", call_id="y3", arguments="{}"),
            function_call_output(call_id="y3", output="error", exit_code=1),
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
    delivered = [
        (
            row.envelope.structural_payload.get("action"),
            row.envelope.structural_payload.get("tool_name"),
        )
        for row in store.list_pending_outbox_rows(workspace)
    ]
    assert delivered == [
        # session_meta is lifecycle, not a tool phase, and stays deliverable.
        (None, None),
        ("function_call", "shell"),
        ("function_call_output", "shell"),
        ("function_call_output", "mcp__yoetz__respond"),
        ("function_call_output", "mcp__yoetz__status"),
    ]
    # Every record was ingested locally and the cursor moved past all of them: a second
    # reconcile finds nothing new rather than re-reading the retained calls.
    accepted = result["accepted"]
    assert isinstance(accepted, int) and accepted >= 8
    again = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert again["accepted"] == 0
    assert again["duplicates"] == 0
    assert store.pending_outbox_count(workspace) == 5


# --- exact per-version profiles (#568) -------------------------------------------------------

_PAGINATED_0150 = "imports/codex/rollout-paginated-0.150.1.case.json"
_UNSUPPORTED_0152 = "imports/codex/rollout-unsupported-0.152.1.case.json"
_CANARY_0150 = "CANARY_0150_"


def _fixture_bytes(path: str, variant: str) -> bytes:
    import base64

    from fixture_loader import build_fixture_loader

    case = cast(dict[str, object], build_fixture_loader().load_json(path))
    variants = cast(dict[str, object], cast(dict[str, object], case["input"])["variants"])
    source = cast(dict[str, object], cast(dict[str, object], variants[variant])["source"])
    return base64.b64decode(cast(str, source["bytes_base64"]).encode("ascii"), validate=True)


def _header_selected_reader(session: str, *, generation: int = 1) -> SessionStreamReader:
    """A reader with no prior profile: the source header must select one."""

    return SessionStreamReader(
        session_commitment=session,
        profile=None,
        cursor=ObservationCursor(
            source_generation=generation,
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
        key_material=_KEY,
    )


def test_stream_profile_from_id_is_exact_lookup() -> None:
    assert stream_profile_from_id(None) is None
    assert stream_profile_from_id("codex-rollout-jsonl/0.152.1/v1") is None
    for version in ("0.148.0", "0.150.1"):
        profile = stream_profile_from_id(f"codex-rollout-jsonl/{version}/v1")
        assert profile is not None
        assert profile.cli_version == version


def test_0_150_1_stream_admits_from_header_and_envelopes_carry_no_content(
    tmp_path: Path,
) -> None:
    raw = _fixture_bytes(_PAGINATED_0150, "paginated")
    path = tmp_path / "session.jsonl"
    path.write_bytes(raw)
    session = "hmac-sha256:" + ("5" * 64)
    reader = _header_selected_reader(session)

    advance = reader.advance(path)

    assert reader.profile is not None
    assert reader.profile.profile_id == "codex-rollout-jsonl/0.150.1/v1"
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value not in advance.gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in advance.gaps
    assert advance.cursor.byte_position == len(raw)
    assert advance.cursor.event_position == raw.count(b"\n")
    assert len(advance.envelopes) == raw.count(b"\n")
    kinds = {envelope.structural_payload["stream_kind"] for envelope in advance.envelopes}
    assert kinds == set(reader.profile.wrapper_types)
    actions = {
        envelope.structural_payload.get("action")
        for envelope in advance.envelopes
        if "action" in envelope.structural_payload
    }
    assert {"function_call", "custom_tool_call", "McpToolCall", "CommandExecution"} <= actions
    dumped = json.dumps(
        [
            {
                "event_kind": envelope.event_kind,
                "structural_payload": dict(envelope.structural_payload),
                "content_object_refs": list(envelope.content_object_refs),
                "gap_codes": list(envelope.gap_codes),
            }
            for envelope in advance.envelopes
        ],
        default=str,
    )
    # Hidden reasoning, base instructions, developer/user/assistant text, tool output,
    # compaction summaries, world state, and the secret canary all stay out of every envelope.
    assert _CANARY_0150 not in dumped
    assert "sk-proj-" not in dumped
    assert "[REDACTED]" not in dumped
    assert all(envelope.content_object_refs == () for envelope in advance.envelopes)
    allowed = {
        "stream_kind",
        "action",
        "tool_name",
        "result_status",
        "exit_status",
        "tool_call_id",
    }
    for envelope in advance.envelopes:
        assert set(envelope.structural_payload) <= allowed, envelope.structural_payload


def test_unsupported_release_is_refused_durably_without_cursor_loss(tmp_path: Path) -> None:
    raw = _fixture_bytes(_UNSUPPORTED_0152, "future")
    path = tmp_path / "session.jsonl"
    path.write_bytes(raw)
    session = "hmac-sha256:" + ("6" * 64)
    reader = _header_selected_reader(session)

    advance = reader.advance(path)

    assert reader.profile is None
    assert advance.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in advance.gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in advance.gaps
    # The bytes are consumed (no re-read storm) and no event is counted as admitted.
    assert advance.cursor.byte_position == len(raw)
    assert advance.cursor.event_position == 0

    path.write_bytes(raw + encode_lines(function_call(name="shell", call_id="later", ordinal=4)))
    appended = reader.advance(path)
    assert appended.envelopes == ()
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in appended.gaps
    assert appended.cursor.event_position == 0
    # The refusal point is held for the whole generation: the appended tail is never read, so
    # nothing of an unproven grammar is interpreted, and the cursor is neither lost nor rewound.
    assert appended.cursor.byte_position == len(raw)
    assert appended.cursor.source_generation == advance.cursor.source_generation


def test_prior_profile_refuses_a_rotated_source_of_another_release(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        encode_lines(
            session_meta(),
            function_call(name="shell", call_id="a", arguments='{"command":"echo one"}'),
            function_call_output(call_id="a"),
            function_call(name="shell", call_id="b", arguments='{"command":"echo two"}'),
            function_call_output(call_id="b"),
        )
    )
    session = "hmac-sha256:" + ("7" * 64)
    reader = _header_selected_reader(session)
    first = reader.advance(path)
    assert reader.profile is not None and reader.profile.cli_version == "0.148.0"
    assert len(first.envelopes) == 5

    # Truncation starts a new generation whose header re-selects the profile from scratch.
    path.write_bytes(
        encode_lines(
            session_meta(cli_version="0.150.1", history_mode="paginated", ordinal=1),
            function_call(name="shell", call_id="b", ordinal=2),
        )
    )
    second = reader.advance(path)
    assert second.truncated is True
    assert second.cursor.source_generation == first.cursor.source_generation + 1
    assert reader.profile is not None and reader.profile.cli_version == "0.150.1"
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value not in second.gaps
    assert len(second.envelopes) == 2


def test_unknown_inner_item_under_0_150_1_is_opaque_unsupported_event(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(
        encode_lines(
            session_meta(cli_version="0.150.1", history_mode="paginated", ordinal=1),
            item_completed({"id": "item_x", "type": "FutureItem", "text": "secret-ish"}, ordinal=2),
            {
                "ordinal": 3,
                "payload": {"tokens": 1},
                "timestamp": "t",
                "type": "token_usage_record",
            },
            function_call(name="shell", call_id="after", ordinal=4),
        )
    )
    session = "hmac-sha256:" + ("8" * 64)
    reader = _header_selected_reader(session)

    advance = reader.advance(path)

    assert ObservationGapCode.UNSUPPORTED_FORMAT.value not in advance.gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in advance.gaps
    assert advance.cursor.event_position == 4
    opaque = [
        envelope
        for envelope in advance.envelopes
        if ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes
    ]
    assert len(opaque) == 2
    assert all("secret-ish" not in json.dumps(dict(e.structural_payload)) for e in opaque)
    assert all("FutureItem" not in json.dumps(dict(e.structural_payload)) for e in opaque)
    assert advance.envelopes[-1].structural_payload["tool_name"] == "shell"


def test_admitted_generation_without_recorded_profile_replays_from_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    body = encode_lines(
        session_meta(cli_version="0.150.1", history_mode="paginated", ordinal=1),
        function_call(name="shell", call_id="a", ordinal=2),
    )
    path.write_bytes(body)
    session = "hmac-sha256:" + ("9" * 64)
    reader = SessionStreamReader(
        session_commitment=session,
        profile=None,
        cursor=ObservationCursor(
            source_generation=3,
            byte_position=len(body),
            event_position=2,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
        key_material=_KEY,
    )

    advance = reader.advance(path)

    assert advance.restarted is True
    assert ObservationGapCode.CURSOR_STALE.value in advance.gaps
    assert advance.cursor.source_generation == 4
    assert advance.cursor.event_position == 2
    assert reader.profile is not None and reader.profile.cli_version == "0.150.1"
    assert len(advance.envelopes) == 2


def test_reconcile_persists_admitted_profile_and_resets_1_2_0_cursors(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(_fixture_bytes(_PAGINATED_0150, "paginated"))
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    # A pre-#568 cursor never recorded which profile admitted its generation; it must replay
    # under the new mapping rather than inherit the 0.148.0 default.
    store.set_stream_cursor(
        workspace,
        session,
        ObservationCursor(
            source_generation=2,
            byte_position=target.stat().st_size,
            event_position=30,
            last_source_commitment=_EMPTY,
            mapping_version="codex-obs-stream/1.2.0",
        ),
    )

    result = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )

    assert result["resolved"] is True
    assert result["profile_id"] == "codex-rollout-jsonl/0.150.1/v1"
    assert store.stream_profile_for_session(workspace, session) == (
        "codex-rollout-jsonl/0.150.1/v1"
    )
    cursor = store.get_stream_cursor(workspace, session)
    assert cursor is not None
    assert cursor.mapping_version == STREAM_MAPPING_VERSION
    assert cursor.source_generation == 3
    accepted = result["accepted"]
    assert isinstance(accepted, int) and accepted >= 1
    gaps = result["gaps"]
    assert isinstance(gaps, tuple)
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value not in gaps
    assert ObservationGapCode.UNSUPPORTED_EVENT.value not in gaps

    # A second pass reuses the persisted profile and makes no progress without new bytes.
    again = reconcile_session_stream(
        store,
        workspace_commitment=workspace,
        session_commitment=session,
        codex_session_id=session_id,
        locator=CodexSessionStreamLocator(home),
    )
    assert again["accepted"] == 0
    assert again["profile_id"] == "codex-rollout-jsonl/0.150.1/v1"


def test_reconcile_of_unsupported_release_records_no_profile(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    session_id = "019f8b27-b98e-7061-bbb5-d0b897594de6"
    target = sessions / f"rollout-2026-07-23T12-00-00-{session_id}.jsonl"
    target.write_bytes(_fixture_bytes(_UNSUPPORTED_0152, "future"))
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
    assert result["profile_id"] is None
    gaps = result["gaps"]
    assert isinstance(gaps, tuple)
    assert ObservationGapCode.UNSUPPORTED_FORMAT.value in gaps
    assert store.stream_profile_for_session(workspace, session) is None
    cursor = store.get_stream_cursor(workspace, session)
    assert cursor is not None
    assert cursor.byte_position == target.stat().st_size
    assert cursor.event_position == 0
