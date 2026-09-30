"""Host parity for failure supersession from real host hook payloads (#909).

Each case starts at a host normalizer (Claude Code or Cursor ordinary profile, which already record
failures today), materializes the recorded envelopes exactly as the coordinator does, and appends
the drafts as service-stamped hook observations before a cooperative completion claim. The same
kernel predicate then decides the findings, the claim-revision invariant, and the receipt, so a
red -> green cycle is clean and a red-latest run is one finding on every host.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from builders.observed_runs import ObservedLedger, omissions, omitted_results, receipt_limitations
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_materialize import materialize_observation_envelope
from yoetz.cli import observe_hooks
from yoetz.domain.events import ActionRecordedPayload, EventPayload, ResultRecordedPayload
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.values import JsonValue, ResultId
from yoetz.protocol.canonical import canonical_encode

_TASK = "tsk_00000000-0000-4000-8000-000000000909"
_CANARY = "zq909hostcanary"
type _Emit = Callable[[str, Mapping[str, object]], None]


def _host(tmp_path: Path, host: str) -> tuple[_Emit, Callable[[], ObservedLedger]]:
    state = tmp_path / "state"
    store = LocalObservationStore(_state=state)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    def emit(event: str, payload: Mapping[str, object]) -> None:
        handler = (
            observe_hooks.handle_claude_observe
            if host == "claude"
            else observe_hooks.handle_cursor_observe
        )
        assert (
            handler(
                event_name=event,
                stdin_bytes=canonical_encode(cast(JsonValue, payload)),
                stdout=io.BytesIO(),
                workspace=str(tmp_path),
                _state=state,
                skip_service=True,
                observation_profile=(
                    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
                    if host == "claude"
                    else CURSOR_ORDINARY_OBSERVATION_PROFILE_ID
                ),
            )
            == 0
        )

    def ledger() -> ObservedLedger:
        built = ObservedLedger()
        for envelope in LocalObservationStore(_state=state).list_envelopes(workspace):
            batch = materialize_observation_envelope(envelope, task_id=_TASK)
            for item in batch.drafts:
                if item.draft.schema.name in {"action_recorded", "result_recorded"}:
                    built.append(
                        item.draft.schema, cast(EventPayload, item.draft.payload), observed=True
                    )
        assert _CANARY.encode() not in b"".join(
            path.read_bytes() for path in state.rglob("*") if path.is_file()
        )
        return built

    return emit, ledger


def _claude_bash(
    emit: _Emit, call: str, command: str, *, failed: bool, exit_code: int | None = None
) -> None:
    session = {"session_id": "claude-909", "tool_name": "Bash", "tool_use_id": call}
    tool_input = {"command": command, "description": "Run tests"}
    emit("PreToolUse", {**session, "hook_event_name": "PreToolUse", "tool_input": tool_input})
    if failed:
        emit(
            "PostToolUseFailure",
            {
                **session,
                "hook_event_name": "PostToolUseFailure",
                "tool_input": tool_input,
                "error": f"Exit code {exit_code or 1}\n{_CANARY} FAILED",
            },
        )
    else:
        emit(
            "PostToolUse",
            {
                **session,
                "hook_event_name": "PostToolUse",
                "tool_input": tool_input,
                "tool_response": {
                    "stdout": f"{_CANARY} passed",
                    "stderr": "",
                    "interrupted": False,
                },
            },
        )


def _claude_edit(emit: _Emit, call: str) -> None:
    session = {"session_id": "claude-909", "tool_name": "Edit", "tool_use_id": call}
    tool_input = {"file_path": "src/module.py", "old_string": "a", "new_string": "b"}
    emit("PreToolUse", {**session, "hook_event_name": "PreToolUse", "tool_input": tool_input})
    emit(
        "PostToolUse",
        {
            **session,
            "hook_event_name": "PostToolUse",
            "tool_input": tool_input,
            "tool_response": {"filePath": "src/module.py", "success": True},
        },
    )


def _cursor_shell(emit: _Emit, call: str, command: str, *, exit_code: int) -> None:
    base = {
        "session_id": "cursor-909",
        "conversation_id": "cursor-909-conversation",
        "tool_name": "Shell",
        "tool_use_id": call,
    }
    tool_input = {"command": command}
    emit("preToolUse", {**base, "hook_event_name": "preToolUse", "tool_input": tool_input})
    emit(
        "postToolUse",
        {
            **base,
            "hook_event_name": "postToolUse",
            "tool_input": tool_input,
            "tool_output": f'{{"exitCode":{exit_code},"stdout":"{_CANARY}"}}',
        },
    )


def _results(ledger: ObservedLedger) -> dict[ResultId, ActionRecordedPayload]:
    actions = {
        cast(ActionRecordedPayload, record.payload).action_id: cast(
            ActionRecordedPayload, record.payload
        )
        for record in ledger.records
        if record.schema.name == "action_recorded"
    }
    return {
        cast(ResultRecordedPayload, record.payload).result_id: actions[
            cast(ResultRecordedPayload, record.payload).action_id
        ]
        for record in ledger.records
        if record.schema.name == "result_recorded"
    }


def test_claude_red_green_rerun_is_clean_and_red_latest_is_one_finding(tmp_path: Path) -> None:
    emit, build = _host(tmp_path, "claude")
    _claude_bash(emit, "toolu-red", f"pytest -q tests/{_CANARY}.py", failed=True)
    _claude_bash(emit, "toolu-green", f"pytest  -q tests/{_CANARY}.py", failed=False)
    clean = build()
    actions = list(_results(clean).values())
    assert actions[0].command == actions[1].command
    assert actions[0].command is not None and actions[0].command.startswith("omitted:hmac-sha256:")
    assert actions[0].description == "Observed command via Claude Code hook (tool Bash)"
    clean.claim(versioned=True)
    assert omissions(clean) == ()
    assert "1 was later passed by the same command" in receipt_limitations(clean)

    _claude_bash(emit, "toolu-other", "pytest -q tests/other.py", failed=True)
    red_latest = build()
    red_result = list(_results(red_latest))[-1]
    red_latest.claim()
    assert omitted_results(red_latest) == (red_result,)


def test_claude_failure_then_observed_edit_is_history(tmp_path: Path) -> None:
    emit, build = _host(tmp_path, "claude")
    _claude_bash(emit, "toolu-red", f"npm test -- {_CANARY}", failed=True)
    _claude_edit(emit, "toolu-edit")
    ledger = build()
    ledger.claim(versioned=True)
    assert omissions(ledger) == ()
    assert "1 preceded a later observed workspace edit" in receipt_limitations(ledger)


def test_cursor_red_green_rerun_is_clean_and_different_command_is_not(tmp_path: Path) -> None:
    emit, build = _host(tmp_path, "cursor")
    _cursor_shell(emit, "cursor-red", f"npm test -- {_CANARY}", exit_code=1)
    _cursor_shell(emit, "cursor-green", f"npm test --  {_CANARY}", exit_code=0)
    clean = build()
    clean.claim(versioned=True)
    assert omissions(clean) == ()

    _cursor_shell(emit, "cursor-red-2", "npm run lint", exit_code=2)
    _cursor_shell(emit, "cursor-green-2", "npm run build", exit_code=0)
    ledger = build()
    lint_failure = list(_results(ledger))[2]
    ledger.claim()
    assert omitted_results(ledger) == (lint_failure,)
