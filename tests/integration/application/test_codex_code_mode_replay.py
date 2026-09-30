"""Replay a Codex 0.157 code-mode session through real hook ingress to the ledger (#917).

The hook payloads and rollout lines use the shapes a Codex 0.157.1 code-mode
session produces (issue #917 examples 1-3 and the issue #910 evidence): one
custom ``exec`` tool per model turn, nested ``tools.exec_command`` /
``tools.apply_patch`` / ``tools.mcp__yoetz__*`` calls that each fire their own
``Bash`` / ``apply_patch`` / MCP hooks, and a session rollout that records the
outer cell plus the nested ``CommandExecution`` items. Nothing is pre-normalised:
every hook payload goes through ``observe_hooks.handle_observe``, every rollout
line through the session-stream reader that the hooks trigger, and every outbox
row through the real sweeper and observation coordinator into a task ledger.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import apsw
import pytest

from builders.ledger_adapters import FixedClock, FixedIds, MemoryObjects, ownership_fence
from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping
from yoetz.adapters.integrations.observation_admission import build_routine_read_summary
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.memory.importer import MemoryImportState
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter, MemoryLedgerState
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_drain import ObservationOutboxSweeper
from yoetz.application.observation_materialize import materialize_observation_envelope
from yoetz.cli import observe_hooks
from yoetz.domain.events import (
    AcceptedEvent,
    ActionKind,
    ActionRecordedPayload,
    EvidenceRecordedPayload,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.observation import (
    ObservationEnvelope,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationSource,
)
from yoetz.ports.runtime import RuntimeCapability, TaskRuntime
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import PublicationChannel
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

HOST = "019f9b27-c1de-7a61-9c5e-5d0b89759417"
_WALL_START = 1_790_704_600.0


def _rollout_row(wrapper: str, payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
    return {"payload": payload, "timestamp": timestamp, "type": wrapper}


def _exec_cell(call_id: str, source: str, timestamp: str) -> dict[str, Any]:
    """The model's code-mode cell exactly as the 0.157.1 rollout records it."""

    return _rollout_row(
        "response_item",
        {
            "call_id": call_id,
            "input": source,
            "name": "exec",
            "status": "completed",
            "type": "custom_tool_call",
        },
        timestamp,
    )


def _exec_cell_output(call_id: str, result: str, timestamp: str) -> dict[str, Any]:
    return _rollout_row(
        "response_item",
        {
            "call_id": call_id,
            "output": [
                {
                    "text": "Script completed\nWall time 25.3 seconds\nOutput:\n",
                    "type": "input_text",
                },
                {"text": result, "type": "input_text"},
            ],
            "type": "custom_tool_call_output",
        },
        timestamp,
    )


def _command_execution(
    item_id: str, command: str, exit_code: int, timestamp: str
) -> dict[str, Any]:
    """The nested ``CommandExecution`` item Codex writes for a code-mode shell call."""

    return _rollout_row(
        "event_msg",
        {
            "completed_at_ms": 1_790_704_690_742,
            "item": {
                "command": ["/bin/bash", "-lc", command],
                "cwd": "file:///app",
                "duration": {"nanos": 226_084_509, "secs": 23},
                "exit_code": exit_code,
                "id": item_id,
                "parsed_cmd": [{"cmd": command, "type": "unknown"}],
                "source": "unified_exec_startup",
                "status": "failed" if exit_code else "completed",
                "stderr": "",
                "stdout": "public synthetic output",
                "type": "CommandExecution",
            },
            "started_at_ms": 1_790_704_667_516,
            "thread_id": HOST,
            "turn_id": "turn_1",
            "type": "item_completed",
        },
        timestamp,
    )


@dataclass
class _Replay:
    """A consented Codex workspace, its rollout, and one task ledger behind a real coordinator."""

    root: Path
    workspace: Path
    rollout: Path
    store: LocalObservationStore
    commitment: str
    session: str
    task_id: str
    mapping: LifecycleMapping
    ledger: MemoryLedgerAdapter
    ids: FixedIds
    objects: MemoryObjects
    task_store: SqliteObservationStore
    wall: list[float] = field(default_factory=lambda: [_WALL_START])

    def append(self, *rows: dict[str, Any]) -> None:
        with self.rollout.open("ab") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True).encode())
                handle.write(b"\n")

    def hook(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "cwd": str(self.workspace),
            "hook_event_name": event,
            "model": "gpt-6-sol",
            "permission_mode": "bypassPermissions",
            "session_id": HOST,
            "transcript_path": str(self.rollout),
            **fields,
        }
        assert (
            observe_hooks.handle_observe(
                event_name=event,
                stdin_bytes=canonical_encode(payload),
                stdout=io.BytesIO(),
                workspace=str(self.workspace),
                _state=self.root,
                skip_service=True,
            )
            == 0
        )

    def advance(self, seconds: float) -> None:
        self.wall[0] += seconds

    def rows(self, name: str) -> list[AcceptedEvent]:
        return [
            row
            for row in self.ledger._state.records
            if type(row) is AcceptedEvent and row.schema.name == name
        ]


@pytest.fixture
def replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Replay]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "codex-home"
    sessions = home / "sessions" / "2026" / "09" / "29"
    sessions.mkdir(parents=True)
    home.chmod(0o700)
    rollout = sessions / f"rollout-2026-09-29T17-54-00-{HOST}.jsonl"
    rollout.write_bytes(b"")
    rollout.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(home))
    root = tmp_path / "isolated"
    store = LocalObservationStore(_state=root)
    commitment = store.workspace_commitment(str(workspace.resolve()))
    store.grant_consent(commitment)
    session = store.bind_codex_session(commitment, HOST)
    task_id = PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())
    mapping = LifecycleMapping(
        mapping_version=1,
        codex_session_id=HOST,
        yoetz_task_id=task_id,
        yoetz_session_id=PREFIX_BY_KIND[IdKind.SESSION] + str(uuid.uuid4()),
        yoetz_writer_id=PREFIX_BY_KIND[IdKind.WRITER] + str(uuid.uuid4()),
        last_frontier=None,
    )
    ids = FixedIds()
    objects = MemoryObjects(ids)
    ledger = MemoryLedgerAdapter(
        task_id=task_id,
        ownership_fence=ownership_fence(),
        state=MemoryLedgerState(),
        import_state=MemoryImportState(),
        transaction_lock=asyncio.Lock(),
        clock=FixedClock(),
        ids=ids,
        objects=objects,
    )
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": task_id, "owner_generation": "1"})
    cell = _Replay(
        root,
        workspace,
        rollout,
        store,
        commitment,
        session,
        task_id,
        mapping,
        ledger,
        ids,
        objects,
        SqliteObservationStore(db),
    )

    # The admission buffer's five-second pending-attempt deadline runs on the
    # store's wall clock; the replay advances it the way the host session did.
    def _replay_wall(_store: LocalObservationStore) -> float:
        return cell.wall[0]

    monkeypatch.setattr(LocalObservationStore, "_wall_now", _replay_wall)
    yield cell


def _coordinator(cell: _Replay) -> ObservationCoordinator:
    class _RuntimePort:
        async def route(self, command: object) -> TaskRuntime:
            return TaskRuntime(
                cell.task_id,
                cast(str, getattr(command, "session_id")),
                cast(str, getattr(command, "writer_id")),
                frozenset(
                    {
                        RuntimeCapability.STRUCTURAL_READ,
                        RuntimeCapability.PAYLOAD_READ,
                        RuntimeCapability.WRITE,
                    }
                ),
                cell.ledger,
                cell.objects,  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                "0.1.0",
                "0.1.0",
                "0.1",
                "1.0.0",
                ownership_fence(),
                observation=cell.task_store,
            )

        async def release(self, released: object) -> None:
            del released

    class _Coordinator(ObservationCoordinator):
        async def _enqueue_verification(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
            del args, kwargs

        async def _run_advice(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
            del args, kwargs

    return _Coordinator(
        runtime=_RuntimePort(),  # type: ignore[arg-type]
        local=cell.store,
        clock=FixedClock(),  # type: ignore[arg-type]
        ids=cell.ids,  # type: ignore[arg-type]
        state_root=cell.root,
        mapping_loader=lambda *_args, **_kwargs: cell.mapping,  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )


class _Recorder:
    """Deliver every outbox row to the real coordinator and keep the delivered envelopes."""

    def __init__(self, coordinator: ObservationCoordinator) -> None:
        self.coordinator = coordinator
        self.delivered: list[ObservationEnvelope] = []

    async def ingest_request(self, request: ObservationIngestRequest) -> ObservationIngestResult:
        result = await self.coordinator.ingest_request(request)
        if result.disposition.value in {"accepted", "duplicate"}:
            self.delivered.append(request.envelope)
        return result


def _code_mode_session(cell: _Replay) -> dict[str, int]:
    """Drive one realistic code-mode session; return the host calls it made."""

    cell.append(
        _rollout_row(
            "session_meta",
            {
                "cli_version": "0.157.1",
                "cwd": str(cell.workspace),
                "history_mode": "legacy",
                "id": HOST,
                "originator": "codex_exec",
            },
            "2026-09-29T17:54:00.000Z",
        )
    )
    cell.hook("SessionStart", source="startup")

    # Example 1 (#910): one cell, one failing typecheck through tools.exec_command.
    cell.append(
        _exec_cell(
            "call_cellTypecheck",
            'const r=await tools.exec_command({cmd:"npm run test-type",workdir:"/app"});text(r)\n',
            "2026-09-29T17:57:47.000Z",
        )
    )
    command = {"command": "npm run test-type"}
    cell.hook("PreToolUse", tool_name="Bash", tool_use_id="call_nTypecheck", tool_input=command)
    cell.advance(23.0)
    cell.append(
        _command_execution("exec-42439c0d-a", "npm run test-type", 2, "2026-09-29T17:58:10.743Z")
    )
    cell.hook(
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="call_nTypecheck",
        tool_input=command,
        tool_response=json.dumps(
            {
                "chunk_id": "d74c6f",
                "exit_code": 2,
                "original_token_count": 1919,
                "output": "> tsc --noEmit\nerror TS2322: public synthetic",
                "wall_time_seconds": 23.226037979,
            }
        ),
    )
    cell.append(
        _exec_cell_output(
            "call_cellTypecheck",
            '{"chunk_id":"d74c6f","exit_code":2,"output":"error TS2322"}',
            "2026-09-29T17:58:12.000Z",
        )
    )

    # Example 2 (#917): one cell, two nested Yoetz publish_work calls. Their pres
    # stay local by contract (#564); each post carries the call.
    cell.advance(2.0)
    cell.append(
        _exec_cell(
            "call_cellPublish",
            "await tools.mcp__yoetz__publish_work({dry_run:true});"
            "await tools.mcp__yoetz__publish_work({dry_run:false});\n",
            "2026-09-29T17:55:00.355Z",
        )
    )
    for index, call in enumerate(("call_nPublishDry", "call_nPublishCommit")):
        publish_input = {"dry_run": index == 0, "request_id": f"req_public_{index}"}
        cell.hook(
            "PreToolUse",
            tool_name="mcp__yoetz__publish_work",
            tool_use_id=call,
            tool_input=publish_input,
        )
        cell.advance(0.3)
        cell.hook(
            "PostToolUse",
            tool_name="mcp__yoetz__publish_work",
            tool_use_id=call,
            tool_input=publish_input,
            tool_response={"content": [{"text": "published", "type": "text"}], "isError": False},
        )
    cell.append(
        _exec_cell_output("call_cellPublish", '{"published":2}', "2026-09-29T17:55:03.582Z")
    )

    # A nested apply_patch and a passing rerun in one cell.
    cell.advance(2.0)
    cell.append(
        _exec_cell(
            "call_cellPatch",
            'tools.apply_patch("*** Begin Patch\\n*** End Patch");'
            'await tools.exec_command({cmd:"npm run test-type"});\n',
            "2026-09-29T17:59:00.000Z",
        )
    )
    patch = {
        "command": "*** Begin Patch\n*** Update File: src/public.ts\n@@\n-a\n+b\n*** End Patch"
    }
    cell.hook("PreToolUse", tool_name="apply_patch", tool_use_id="call_nPatch", tool_input=patch)
    cell.hook(
        "PostToolUse",
        tool_name="apply_patch",
        tool_use_id="call_nPatch",
        tool_input=patch,
        tool_response="Exit code: 0\nWall time: 0.1 seconds\nOutput:\nSuccess. Updated the following files:\nM src/public.ts\n",
    )
    cell.hook("PreToolUse", tool_name="Bash", tool_use_id="call_nRerun", tool_input=command)
    cell.advance(20.0)
    cell.append(
        _command_execution("exec-42439c0d-b", "npm run test-type", 0, "2026-09-29T17:59:21.000Z")
    )
    cell.hook(
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="call_nRerun",
        tool_input=command,
        tool_response=json.dumps({"chunk_id": "e1", "exit_code": 0, "output": "ok"}),
    )
    cell.append(_exec_cell_output("call_cellPatch", '{"exit_code":0}', "2026-09-29T17:59:22.000Z"))

    # A routine read whose pre is flushed individually at its five-second
    # deadline (ADR-029) before the read fails.
    cell.advance(2.0)
    cell.append(
        _exec_cell(
            "call_cellRead",
            'await tools.exec_command({cmd:"cat missing-public.txt"});\n',
            "2026-09-29T17:59:30.000Z",
        )
    )
    read = {"command": "cat missing-public.txt"}
    cell.hook("PreToolUse", tool_name="Bash", tool_use_id="call_nRead", tool_input=read)
    cell.advance(6.0)
    assert cell.store.flush_selected_admission(
        cell.commitment, summary_builder=build_routine_read_summary
    )
    cell.append(
        _command_execution(
            "exec-42439c0d-c", "cat missing-public.txt", 1, "2026-09-29T17:59:36.000Z"
        )
    )
    cell.hook(
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="call_nRead",
        tool_input=read,
        tool_response=json.dumps({"chunk_id": "f1", "exit_code": 1, "output": "No such file"}),
    )
    cell.append(_exec_cell_output("call_cellRead", '{"exit_code":1}', "2026-09-29T17:59:37.000Z"))
    cell.hook("Stop", last_assistant_message="Typecheck rerun passed.", stop_hook_active=False)
    return {"shell": 3, "patch": 1, "mcp": 2, "cells": 4}


def _facts(
    actions: list[ActionRecordedPayload],
    results: list[ResultRecordedPayload],
    evidence: list[EvidenceRecordedPayload],
) -> set[tuple[str, ...]]:
    """Every observed fact the ledger holds, without record identities.

    An ``unknown`` outcome and a digest-free ``omitted:structural`` command state
    no fact; they are identity placeholders, not observations.
    """

    facts: set[tuple[str, ...]] = set()
    for result in results:
        if result.outcome is not ResultOutcome.UNKNOWN:
            facts.add(("outcome", result.outcome.value, str(result.exit_status)))
        elif result.exit_status is not None:
            facts.add(("exit_status", str(result.exit_status)))
    for action in actions:
        if action.command is not None and action.command != "omitted:structural":
            facts.add(("command", action.command))
    for item in evidence:
        if item.content_digest is not None:
            facts.add(("content", item.content_digest))
    return facts


@pytest.mark.anyio
async def test_code_mode_replay_records_one_action_per_host_call(replay: _Replay) -> None:
    calls = _code_mode_session(replay)
    host_calls = calls["shell"] + calls["patch"] + calls["mcp"]
    recorder = _Recorder(_coordinator(replay))
    sweeper = ObservationOutboxSweeper(replay.store, recorder)
    try:
        for _ in range(8):
            summary = await sweeper.sweep()
            if summary.attempted == 0:
                break
    finally:
        sweeper.close()
    assert replay.store.list_pending_outbox_rows(replay.commitment) == ()
    assert not replay.store.list_quarantine(replay.commitment)

    actions = [cast(ActionRecordedPayload, row.payload) for row in replay.rows("action_recorded")]
    results = [cast(ResultRecordedPayload, row.payload) for row in replay.rows("result_recorded")]
    evidence = [
        cast(EvidenceRecordedPayload, row.payload) for row in replay.rows("evidence_recorded")
    ]

    # One action and one result per host call: never 2N, and no independent
    # action for any of the four code-mode cells.
    assert len(actions) == host_calls, [item.description for item in actions]
    assert len(results) == host_calls
    assert len({item.action_id for item in actions}) == host_calls
    assert {item.action_id for item in results} == {item.action_id for item in actions}
    # The deadline-flushed read is one pending action, later linked to its failure.
    pending = [item for item in actions if "pending" in item.description]
    assert pending
    linked = {item.action_id: item for item in results}
    assert all(item.action_id in linked for item in pending)

    # Hook-observed ledger events per shell command stay within the issue's bound
    # (about 7.9 before #917): the call's action, its result, evidence recorded
    # under that action, and the stream's own record of the nested command.
    command_rows = [
        row
        for row in replay.rows("action_recorded")
        if cast(ActionRecordedPayload, row.payload).action_kind is ActionKind.COMMAND
    ]
    command_actions = {cast(ActionRecordedPayload, row.payload).action_id for row in command_rows}
    command_events = {row.event_id for row in command_rows}
    command_rows += [
        row
        for row in replay.rows("result_recorded")
        if cast(ResultRecordedPayload, row.payload).action_id in command_actions
    ]
    command_rows += [
        row
        for row in replay.rows("evidence_recorded")
        if command_events.intersection(row.causal_parents)
        or cast(EvidenceRecordedPayload, row.payload).description
        == "Opaque observation kind=event_msg"
    ]
    assert len(command_actions) == calls["shell"]
    assert all(row.publication_channel is PublicationChannel.HOOK_OBSERVED for row in command_rows)
    assert len(command_rows) / calls["shell"] <= 4, len(command_rows)

    # Nested tool hooks fired for this session, so the code-mode cell wrappers
    # stay in the local store: retained, not dropped.
    assert replay.store.codex_hook_observes_session(replay.commitment, replay.session)
    wrappers = [
        envelope
        for envelope in replay.store.list_envelopes(replay.commitment)
        if envelope.source is ObservationSource.CODEX_SESSION_STREAM
        and envelope.structural_payload.get("tool_name") == "exec"
    ]
    assert len(wrappers) == 2 * calls["cells"]
    assert not {item.source_identity for item in wrappers} & {
        item.source_identity for item in recorder.delivered
    }

    # Delay, not drop: every fact the pre-#917 pipeline would have materialized
    # from these inputs (every delivered envelope plus every cell wrapper, each
    # as a full independent batch) is still in the ledger.
    before_actions: list[ActionRecordedPayload] = []
    before_results: list[ResultRecordedPayload] = []
    before_evidence: list[EvidenceRecordedPayload] = []
    for envelope in (*recorder.delivered, *wrappers):
        batch = materialize_observation_envelope(envelope, task_id=replay.task_id)
        for item in batch.drafts:
            payload = item.draft.payload
            if type(payload) is ActionRecordedPayload:
                before_actions.append(payload)
            elif type(payload) is ResultRecordedPayload:
                before_results.append(payload)
            elif type(payload) is EvidenceRecordedPayload:
                before_evidence.append(payload)
    before = _facts(before_actions, before_results, before_evidence)
    after = _facts(actions, results, evidence)
    assert before <= after, before - after
    # The patch's stated exit code is one such fact. Shell outcomes stay
    # ``unknown`` here until #910 parses them; this replay only proves that
    # consolidating records drops none of the facts that are recorded.
    assert ("outcome", "success", "0") in before


@pytest.mark.anyio
async def test_cell_without_tool_hooks_is_still_recorded(replay: _Replay) -> None:
    """Delay, not drop: lifecycle hooks alone do not prove a cell's nested calls were hooked.

    A cell whose nested tool fired no tool hook (an unhooked tool, an older Codex,
    a hook timeout) is the only record of that work, so its wrapper is delivered.
    """

    replay.append(
        _rollout_row(
            "session_meta",
            {
                "cli_version": "0.157.1",
                "cwd": str(replay.workspace),
                "history_mode": "legacy",
                "id": HOST,
                "originator": "codex_exec",
            },
            "2026-09-29T17:54:00.000Z",
        )
    )
    replay.hook("SessionStart", source="startup")
    replay.append(
        _exec_cell(
            "call_cellPlan",
            'await tools.update_plan({plan:[{step:"public",status:"completed"}]});\n',
            "2026-09-29T17:54:10.000Z",
        ),
        _exec_cell_output("call_cellPlan", '{"ok":true}', "2026-09-29T17:54:11.000Z"),
    )
    replay.hook("Stop", last_assistant_message="Plan updated.", stop_hook_active=False)
    assert not replay.store.codex_hook_observes_session(replay.commitment, replay.session)

    recorder = _Recorder(_coordinator(replay))
    sweeper = ObservationOutboxSweeper(replay.store, recorder)
    try:
        for _ in range(8):
            if (await sweeper.sweep()).attempted == 0:
                break
    finally:
        sweeper.close()
    delivered = [
        envelope
        for envelope in recorder.delivered
        if envelope.structural_payload.get("tool_name") == "exec"
    ]
    assert len(delivered) == 2
    actions = [cast(ActionRecordedPayload, row.payload) for row in replay.rows("action_recorded")]
    results = replay.rows("result_recorded")
    assert len(actions) == 1 and len(results) == 1
    accounting = replay.store.selection_accounting(replay.commitment)
    assert accounting["intentionally_omitted_input_count"] == 0
    assert accounting["observed_count"] == accounting["admitted_input_count"]
