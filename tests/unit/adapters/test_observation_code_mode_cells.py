"""Bounded code-mode cell and orphan-notice bookkeeping stays correct at capacity (#917)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from builders.codex_rollout import encode_lines, response_item, session_meta
from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    reconcile_session_stream,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp


def _store(tmp_path: Path) -> tuple[LocalObservationStore, str]:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    return store, workspace


def _tool_hook(session: str, call: str, kind: str = "PreToolUse") -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=session,
        event_kind=kind,
        source_identity=f"hook:{session[-12:]}:{call}:{kind}",
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(1, 0, 1, f"hmac-sha256:{'ab' * 32}", "codex-obs-hook/1.0.0"),
        receipt_time=Timestamp("2026-09-29T17:58:11.363Z"),
        structural_payload=JsonObject({"tool_name": "Bash", "tool_call_id": call}),
        content_object_refs=(),
        gap_codes=(),
    )


def _session(index: int) -> str:
    return f"hmac-sha256:{index:064x}"


def _cell_row(call_id: str, *, output: bool = False) -> dict[str, Any]:
    """One code-mode ``exec`` cell row in the Codex 0.157.1 rollout shape."""

    if output:
        payload: dict[str, Any] = {
            "call_id": call_id,
            "output": [{"text": "Script completed\n", "type": "input_text"}],
            "type": "custom_tool_call_output",
        }
    else:
        payload = {
            "call_id": call_id,
            "input": 'await tools.exec_command({cmd:"true"});\n',
            "name": "exec",
            "status": "completed",
            "type": "custom_tool_call",
        }
    return response_item(payload)


def test_a_new_session_past_the_tracking_bound_keeps_hooked_cells_local(tmp_path: Path) -> None:
    """A full tool-hook map forgets the least recently active session, never the new one."""

    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    session_id = "019f9b27-c1de-7a61-9c5e-5d0b89759418"
    rollout = sessions / f"rollout-{session_id}.jsonl"
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    store, workspace = _store(state)
    with store.batched(workspace):
        for index in range(1, 257):
            store.ingest(_tool_hook(_session(index), "call-1"), workspace_commitment=workspace)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)

    def reconcile() -> None:
        reconcile_session_stream(
            store,
            workspace_commitment=workspace,
            session_commitment=session,
            codex_session_id=session_id,
            locator=CodexSessionStreamLocator(home),
        )

    # The nested tool's hooks fire around the cell, as the hook-triggered reads see them.
    rollout.write_bytes(encode_lines(session_meta(session_id=session_id), _cell_row("call_cell")))
    store.ingest(_tool_hook(session, "call_nested"), workspace_commitment=workspace)
    reconcile()
    store.ingest(_tool_hook(session, "call_nested", "PostToolUse"), workspace_commitment=workspace)
    with rollout.open("ab") as handle:
        handle.write(encode_lines(_cell_row("call_cell", output=True)))
    reconcile()

    wrappers = [
        row.envelope
        for row in store.list_pending_outbox_rows(workspace)
        if row.envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and row.envelope.structural_payload.get("action")
        in {"custom_tool_call", "custom_tool_call_output"}
    ]
    assert wrappers == []
    assert store.codex_tool_hook_count(workspace, session) == 2
    # The least recently active session is the one forgotten; its later cells
    # would simply be delivered.
    assert store.codex_tool_hook_count(workspace, _session(1)) == 0
    assert store.codex_tool_hook_count(workspace, _session(2)) == 1


def test_an_evicted_session_reconciled_before_its_nested_hook_keeps_the_cell_local(
    tmp_path: Path,
) -> None:
    """A forgotten hook session never turns its next cell into a second record (#917).

    Session A was hooked, then 256 other sessions' tool hooks evicted it from the
    bounded map. A manual reconcile reads A's next ``exec`` cell before its nested
    hook is admitted; the nested hook then arrives and the output is reconciled.
    The nested hook rows are the cell's record, so neither wrapper row is delivered.
    """

    home = tmp_path / "codex-home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    session_id = "019f9b27-c1de-7a61-9c5e-5d0b89759418"
    rollout = sessions / f"rollout-{session_id}.jsonl"
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    store, workspace = _store(state)
    session = store.session_commitment(session_id)
    store.bind_session(workspace, session)
    store.ingest(_tool_hook(session, "call_earlier"), workspace_commitment=workspace)
    with store.batched(workspace):
        for index in range(1, 257):
            store.ingest(_tool_hook(_session(index), "call-1"), workspace_commitment=workspace)
    assert store.codex_tool_hook_count(workspace, session) == 0

    def reconcile() -> None:
        reconcile_session_stream(
            store,
            workspace_commitment=workspace,
            session_commitment=session,
            codex_session_id=session_id,
            locator=CodexSessionStreamLocator(home),
        )

    # ``yoetz observe reconcile`` reads the cell's call before its nested hook fires.
    rollout.write_bytes(encode_lines(session_meta(session_id=session_id), _cell_row("call_cell")))
    reconcile()
    store.ingest(_tool_hook(session, "call_nested"), workspace_commitment=workspace)
    store.ingest(_tool_hook(session, "call_nested", "PostToolUse"), workspace_commitment=workspace)
    with rollout.open("ab") as handle:
        handle.write(encode_lines(_cell_row("call_cell", output=True)))
    reconcile()

    wrappers = [
        row.envelope
        for row in store.list_pending_outbox_rows(workspace)
        if row.envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and row.envelope.structural_payload.get("action")
        in {"custom_tool_call", "custom_tool_call_output"}
    ]
    assert wrappers == []


def test_a_cell_read_before_the_sessions_first_tool_hook_follows_its_own_hooks(
    tmp_path: Path,
) -> None:
    """An early read of a session's first cell is decided by that cell's own hooks."""

    store, workspace = _store(tmp_path)
    session = _session(11)
    store.begin_code_mode_cell(workspace, session, "call_first")
    store.ingest(_tool_hook(session, "call-a"), workspace_commitment=workspace)
    assert store.finish_code_mode_cell(workspace, session, "call_first") is True


def test_a_session_forgotten_and_rehooked_while_its_cell_is_open_keeps_the_cell_local(
    tmp_path: Path,
) -> None:
    """Eviction resets a session's hook count; a later nested hook still counts for the cell."""

    store, workspace = _store(tmp_path)
    session = _session(12)
    for call in ("call-a", "call-b", "call-c"):
        store.ingest(_tool_hook(session, call), workspace_commitment=workspace)
    store.begin_code_mode_cell(workspace, session, "call_open")
    with store.batched(workspace):
        for index in range(1_000, 1_256):
            store.ingest(_tool_hook(_session(index), "call-1"), workspace_commitment=workspace)
    assert store.codex_tool_hook_count(workspace, session) == 0
    store.ingest(_tool_hook(session, "call-nested"), workspace_commitment=workspace)
    assert store.finish_code_mode_cell(workspace, session, "call_open") is True


def test_a_cell_is_local_only_when_its_own_nested_tool_hooks_fired(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    session = _session(9)
    # No tool hook has fired in this session yet: a call is still held, and a
    # cell whose tools fire no hook is recorded through its output.
    assert store.begin_code_mode_cell(workspace, session, "call_first") is True
    assert store.finish_code_mode_cell(workspace, session, "call_first") is False

    # A hooked cell: the call is held and the output stays local.
    store.ingest(_tool_hook(session, "call-a"), workspace_commitment=workspace)
    assert store.begin_code_mode_cell(workspace, session, "call_hooked") is True
    store.ingest(_tool_hook(session, "call-a", "PostToolUse"), workspace_commitment=workspace)
    assert store.finish_code_mode_cell(workspace, session, "call_hooked") is True

    # A later cell whose tools fire no hook is still recorded through its output.
    assert store.begin_code_mode_cell(workspace, session, "call_plan") is True
    assert store.finish_code_mode_cell(workspace, session, "call_plan") is False
    # Decisions replay identically, including after a restart.
    reopened = LocalObservationStore(_state=tmp_path)
    assert reopened.finish_code_mode_cell(workspace, session, "call_hooked") is True
    assert reopened.finish_code_mode_cell(workspace, session, "call_plan") is False
    # An output whose start was never seen stays deliverable.
    assert reopened.finish_code_mode_cell(workspace, session, "call_unknown") is False


def test_delivered_notices_do_not_exhaust_new_scope_notices(tmp_path: Path) -> None:
    store, workspace = _store(tmp_path)
    session = _session(3)
    with store.batched(workspace):
        for generation in range(1, 257):
            store.note_unpaired_event(
                workspace,
                source=ObservationSource.CODEX_HOOK,
                session_commitment=session,
                source_generation=generation,
                source_identity=f"hook:orphan-{generation}",
            )
            notice = store.peek_unpaired_notice(workspace, session)
            assert notice is not None and notice.source_generation == generation
            store.commit_unpaired_notice_delivery(workspace, notice.lane)
    store.note_unpaired_event(
        workspace,
        source=ObservationSource.CODEX_HOOK,
        session_commitment=session,
        source_generation=257,
        source_identity="hook:orphan-257",
    )
    fresh = store.peek_unpaired_notice(workspace, session)
    assert fresh is not None and fresh.source_generation == 257
    store.commit_unpaired_notice_delivery(workspace, fresh.lane)
    # A recently announced scope is still never announced again.
    store.note_unpaired_event(
        workspace,
        source=ObservationSource.CODEX_HOOK,
        session_commitment=session,
        source_generation=256,
        source_identity="hook:orphan-256-again",
    )
    assert store.peek_unpaired_notice(workspace, session) is None


def test_a_forgotten_delivered_scope_is_announced_at_most_once_more(tmp_path: Path) -> None:
    """The notice map's bound is the documented one: beyond it a scope repeats once."""

    store, workspace = _store(tmp_path)
    session = _session(4)

    def orphan(generation: int, tag: str) -> None:
        store.note_unpaired_event(
            workspace,
            source=ObservationSource.CODEX_HOOK,
            session_commitment=session,
            source_generation=generation,
            source_identity=f"hook:orphan-{generation}-{tag}",
        )

    with store.batched(workspace):
        for generation in range(1, 258):
            orphan(generation, "first")
            notice = store.peek_unpaired_notice(workspace, session)
            assert notice is not None and notice.source_generation == generation
            store.commit_unpaired_notice_delivery(workspace, notice.lane)
    # Scope 1 was the oldest delivered notice, forgotten to admit scope 257.
    orphan(1, "again")
    repeated = store.peek_unpaired_notice(workspace, session)
    assert repeated is not None and repeated.source_generation == 1
    store.commit_unpaired_notice_delivery(workspace, repeated.lane)
    # Retained again, it is not announced a third time.
    orphan(1, "third")
    assert store.peek_unpaired_notice(workspace, session) is None
    # The aggregate gap stays disclosed throughout.
    gaps = store.status(ObservationStatusQuery(workspace)).gaps
    assert ObservationGapCode.UNPAIRED_EVENT.value in gaps
