"""Unit tests for unified hook observation ingress and consent plumbing."""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import acquire_session_lock
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.recommendations import RecommendationState, store_recommendation_state
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import (
    SUPPORTED_HOOK_EVENTS,
    handle_observe,
    map_hook_payload_to_envelope,
)
from yoetz.domain.observation import (
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
    observation_ingest_result_to_json,
)
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

_KEY = b"k" * 32


def test_map_supported_hook_payloads_structural_only(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-1")
    for event in sorted(SUPPORTED_HOOK_EVENTS):
        payload = {
            "session_id": "sess-1",
            "hook_event_name": event,
            "tool_name": "shell",
            "correlation_id": "corr-1",
            "exit_status": 0,
            "transcript": "MUST_NOT_APPEAR",
            "prompt": "MUST_NOT_APPEAR",
        }
        envelope = map_hook_payload_to_envelope(
            event,
            payload,
            session_commitment=session,
            event_ordinal=1,
            key_material=store.key_material(),
        )
        assert envelope.source is ObservationSource.CODEX_HOOK
        assert envelope.event_kind == event
        assert "transcript" not in envelope.structural_payload
        assert "prompt" not in envelope.structural_payload
        assert envelope.structural_payload.get("tool_name") == "shell"
        assert envelope.cursor.last_source_commitment.startswith("hmac-sha256:")


def test_unknown_future_hook_becomes_opaque_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-2")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-2"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )
    assert envelope.event_kind == "FutureHostEvent"
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes


def test_visible_unknown_content_is_redacted_and_hidden_fields_are_ignored(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-visible")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-visible"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    chunks, truncated = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        "FutureHostEvent",
        {
            "visibility": "task",
            "visible_content": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
            "reasoning": "HIDDEN_REASONING_CANARY",
            "system": "SYSTEM_PROMPT_CANARY",
        },
        envelope=envelope,
        workspace_locator=None,
    )
    assert truncated is False
    assert len(chunks) == 1
    assert chunks[0].redacted is True
    assert b"sk-abcdefghijklmnopqrstuvwxyz123456" not in chunks[0].content
    assert b"HIDDEN_REASONING_CANARY" not in chunks[0].content
    assert b"SYSTEM_PROMPT_CANARY" not in chunks[0].content


def test_content_cap_sets_truncated_flag(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-trunc")
    envelope = map_hook_payload_to_envelope(
        "AgentMessage",
        {"session_id": "sess-trunc", "message": "x" * 700_000},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    chunks, truncated = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        "AgentMessage",
        {"message": "x" * 700_000},
        envelope=envelope,
        workspace_locator=None,
    )
    assert truncated is True
    assert chunks


def test_identical_tool_calls_remain_distinct(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-dup")
    payload = {
        "session_id": "sess-dup",
        "tool_name": "shell",
        "tool_call_id": "same-call",
        "exit_status": 0,
    }
    first = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    second = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=2,
        key_material=_KEY,
    )
    assert first.source_identity != second.source_identity


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        ("Read", {"path": "ignored"}, True),
        ("exec_command", {"cmd": "rg -n observation src tests"}, True),
        ("exec_command", {"cmd": "git status --short"}, True),
        ("exec_command", {"cmd": "sed -n '1,20p' README.md"}, False),
        ("exec_command", {"cmd": "sed -i s/a/b/ README.md"}, False),
        ("exec_command", {"cmd": "rg --pre ./prepare term src"}, False),
        ("exec_command", {"cmd": "rg term src | tee report.txt"}, False),
        ("exec_command", {"cmd": "pytest -q"}, False),
        ("exec_command", {"cmd": "./ls"}, False),
        ("exec_command", {"cmd": "/tmp/head README.md"}, False),
        ("exec_command", {"cmd": "git diff --output=result.patch"}, False),
        ("exec_command", {"cmd": "git diff --ext-diff HEAD"}, False),
    ],
)
def test_routine_read_classification_is_conservative_and_structural(
    tool_name: str, tool_input: dict[str, str], expected: bool
) -> None:
    envelope = map_hook_payload_to_envelope(
        "PostToolUse",
        {
            "session_id": "routine-read",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "exit_status": 0,
        },
        session_commitment=f"hmac-sha256:{'11' * 32}",
        event_ordinal=1,
        key_material=_KEY,
    )
    assert (envelope.structural_payload.get("action") == "routine_read") is expected
    assert "cmd" not in envelope.structural_payload


@pytest.mark.parametrize(
    ("payload_extra", "expected_routine"),
    [
        ({"exit_status": 0}, True),
        ({"success": True}, True),
        ({"success": False}, False),
        ({"denied": True}, False),
        ({"success": False, "exit_status": 0}, False),
    ],
)
def test_routine_read_label_excludes_explicit_failures(
    payload_extra: dict[str, bool | int], expected_routine: bool
) -> None:
    envelope = map_hook_payload_to_envelope(
        "PostToolUse",
        {
            "session_id": "routine-read-outcome",
            "tool_name": "read",
            "tool_input": {"path": "ignored"},
            **payload_extra,
        },
        session_commitment=f"hmac-sha256:{'22' * 32}",
        event_ordinal=1,
        key_material=_KEY,
    )
    assert (envelope.structural_payload.get("action") == "routine_read") is expected_routine


def test_observe_without_consent_exits_zero_no_spool(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "s1", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(stdout.getvalue().decode()) == {}


def test_post_tool_hook_delivers_pending_frontier_motion_once(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "frontier-motion")
    store.note_frontier_motion(
        workspace,
        "frontier-motion",
        from_sequence=4,
        to_sequence=6,
        head_digest="sha256:" + "3" * 64,
        observation_record_count=2,
        task_id="tsk-frontier-test",
    )

    payload = json.dumps(
        {"session_id": "frontier-motion", "tool_name": "Read", "exit_status": 0}
    ).encode()
    first = io.BytesIO()
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=payload,
            stdout=first,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    context = json.loads(first.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "task frontier moved from 4 to 6" in context
    assert "observation writer appended 2 ledger record(s)" in context
    assert "run status before an exact-frontier check" in context

    second = io.BytesIO()
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=payload,
            stdout=second,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert "task frontier moved" not in second.getvalue().decode()

    store.note_frontier_motion(
        workspace,
        "frontier-motion",
        from_sequence=4,
        to_sequence=6,
        head_digest="sha256:" + "3" * 64,
        observation_record_count=2,
        task_id="tsk-frontier-test",
    )
    replayed = io.BytesIO()
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=payload,
            stdout=replayed,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert "task frontier moved" not in replayed.getvalue().decode()

    store.note_frontier_motion(
        workspace,
        "frontier-motion",
        from_sequence=6,
        to_sequence=8,
        head_digest="sha256:" + "4" * 64,
        observation_record_count=2,
        task_id="tsk-frontier-test",
    )
    advanced = io.BytesIO()
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=payload,
            stdout=advanced,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    advanced_context = json.loads(advanced.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "task frontier moved from 6 to 8" in advanced_context
    assert "observation writer appended 2 ledger record(s)" in advanced_context


def test_stdout_teardown_failure_exits_zero_and_records_diagnostic(tmp_path: Path) -> None:
    class _ClosedStdout(io.BytesIO):
        def write(self, data: object) -> int:
            del data
            raise ValueError("I/O operation on closed file")

    code = handle_observe(
        event_name="Stop",
        stdin_bytes=json.dumps({"session_id": "stdout-failure"}).encode(),
        stdout=_ClosedStdout(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )

    assert code == 0
    diagnostics = tmp_path / "observation/hook-diagnostics.jsonl"
    assert diagnostics.exists()
    assert "stdout_write_failed" in diagnostics.read_text(encoding="utf-8")


def test_runtime_disabled_skips_capture_but_session_start_surfaces_cached_recommendation(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(False)
    store_recommendation_state(
        RecommendationState(last_evaluated_version="0.1.0", pending=("observation-enabled",)),
        root=tmp_path,
    )
    stdout = io.BytesIO()
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps({"session_id": "disabled-session"}).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
    )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Enable local observation" in context
    assert "workspace consent remains required" in context
    assert "yoetz recommend accept observation-enabled" in context
    assert store.pending_workspaces() == ()
    workspace_state = tmp_path / "observation/workspaces"
    assert not workspace_state.exists() or not list(workspace_state.glob("*.json"))


def test_unsafe_runtime_gate_fails_closed_with_distinct_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(True)
    gate = tmp_path / "observation/runtime-gate.json"
    gate.write_text("not-json", encoding="utf-8")
    stdout = io.BytesIO()
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps({"session_id": "unsafe-gate", "tool_name": "shell"}).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
    )
    assert json.loads(stdout.getvalue()) == {}
    assert "hook_observe_degraded: runtime_gate_unsafe" in capsys.readouterr().err
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"runtime_gate_unsafe"' in diagnostics
    assert store.pending_workspaces() == ()


def test_unsafe_runtime_gate_records_workspace_gap_when_consented(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    gate = tmp_path / "observation/runtime-gate.json"
    gate.write_text("not-json", encoding="utf-8")
    stdout = io.BytesIO()
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps({"session_id": "unsafe-gate-gap", "tool_name": "shell"}).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
    )
    assert json.loads(stdout.getvalue()) == {}
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value in status.gaps


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO gate regression is POSIX-only")
def test_runtime_gate_fifo_fails_closed_without_blocking(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(True)
    gate = tmp_path / "observation/runtime-gate.json"
    gate.unlink()
    os.mkfifo(gate, mode=0o600)

    started = time.monotonic()
    with pytest.raises(PublicOperationError) as caught:
        store.runtime_enabled()

    assert caught.value.code is PublicErrorCode.STORAGE_UNSAFE
    assert time.monotonic() - started < 1.0


def test_unsafe_runtime_gate_falls_back_to_session_mapped_workspace(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    mapped_locator = tmp_path / "mapped-project"
    stale_locator = tmp_path / "stale-project"
    mapped = store.workspace_commitment(str(mapped_locator.resolve()))
    store.grant_consent(mapped)
    store.bind_codex_session(mapped, "unsafe-gate-mapped")
    gate = tmp_path / "observation/runtime-gate.json"
    gate.write_text("not-json", encoding="utf-8")

    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps({"session_id": "unsafe-gate-mapped", "tool_name": "shell"}).encode(),
        stdout=io.BytesIO(),
        workspace=str(stale_locator),
        _state=tmp_path,
    )

    status = store.status(ObservationStatusQuery(mapped))
    assert ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value in status.gaps


def test_runtime_gate_read_is_lock_free_under_store_contention(tmp_path: Path) -> None:
    """Holding the interprocess store lock must not block or fail the gate read (#273)."""

    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(True)
    acquired = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with store._lock:  # pyright: ignore[reportPrivateUsage]
            acquired.set()
            release.wait(timeout=10.0)

    holder = threading.Thread(target=_hold)
    holder.start()
    try:
        assert acquired.wait(timeout=5.0)
        started = time.monotonic()
        assert store.runtime_enabled() is True
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join()


def test_store_lock_contention_at_gate_does_not_discard_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    def _contended(self: LocalObservationStore) -> bool:
        raise TimeoutError("observation_store_lock_timeout")

    monkeypatch.setattr(LocalObservationStore, "runtime_enabled", _contended)
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "contended-gate", "tool_name": "shell", "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert "hook_observe_degraded: runtime_gate_contended" in capsys.readouterr().err
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"runtime_gate_contended"' in diagnostics
    assert '"reason":"runtime_gate_unsafe"' not in diagnostics
    assert store.pending_workspaces() == (workspace,)


def test_service_unavailable_never_spools_visible_plaintext(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    canary = "VISIBLE_TASK_CANARY_6f4b2f"
    code = handle_observe(
        event_name="UserPromptSubmit",
        stdin_bytes=json.dumps(
            {"session_id": "no-spool", "prompt": canary, "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert canary.encode() not in persisted
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value in status.gaps


def test_observe_ingests_when_consented_and_pairs_pre_post(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    pre = handle_observe(
        event_name="PreToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert pre == 0
    post_out = io.BytesIO()
    post = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=post_out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert post == 0
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert status.source_coverage[ObservationSource.CODEX_HOOK] is True
    assert ObservationGapCode.UNPAIRED_EVENT.value not in status.gaps


def test_tool_call_id_pairs_when_legacy_correlation_ids_differ(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    assert (
        handle_observe(
            event_name="PreToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": "pair-alias",
                    "tool_name": "shell",
                    "correlation_id": "pre",
                    "tool_call_id": "shared",
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": "pair-alias",
                    "tool_name": "shell",
                    "tool_call_id": "shared",
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )

    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.UNPAIRED_EVENT.value not in status.gaps


def test_unpaired_post_records_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-2",
                "tool_name": "shell",
                "correlation_id": "missing-pre",
                "exit_status": 1,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert ObservationGapCode.UNPAIRED_EVENT.value in status.gaps


def _codex_0146_payload(event: str, tool_use_id: str, **extra: JsonValue) -> dict[str, JsonValue]:
    """A hook payload using only the field names the Codex 0.146.0 binary emits.

    The key set mirrors the wire-type table embedded in the shipped binary
    (#274): ``tool_use_id``, not ``tool_call_id``/``correlation_id``. Tests
    that feed our own key names back to us cannot catch a contract mismatch.
    """

    payload: dict[str, JsonValue] = {
        "session_id": "01a006a4-1111-4111-8111-000000000001",
        "turn_id": "turn-3",
        "agent_type": "main",
        "transcript_path": "/workspace/.codex/sessions/rollout-2026-08-15.jsonl",
        "cwd": "/workspace/project",
        "hook_event_name": event,
        "model": "gpt-5.3-codex",
        "permission_mode": "on-request",
        "trigger": "model",
        "tool_name": "shell",
        "tool_input": {"command": ["bash", "-lc", "pytest -q"]},
        "tool_use_id": tool_use_id,
    }
    payload.update(extra)
    return payload


def test_codex_tool_use_id_wins_over_legacy_tool_call_id(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    payload = _codex_0146_payload(
        "PreToolUse",
        "call_current",
        tool_call_id="call_legacy_conflict",
    )

    envelope = map_hook_payload_to_envelope(
        "PreToolUse",
        payload,
        session_commitment=store.session_commitment("codex-precedence"),
        event_ordinal=1,
        key_material=store.key_material(),
    )

    assert envelope.structural_payload.get("tool_call_id") == "call_current"


def test_codex_field_names_materialize_linked_action_and_result(tmp_path: Path) -> None:
    import uuid

    from yoetz.application.observation_materialize import materialize_observation_envelope
    from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("codex-conformance")
    task = PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())

    pre = map_hook_payload_to_envelope(
        "PreToolUse",
        _codex_0146_payload("PreToolUse", "call_p8H2mKfQ"),
        session_commitment=session,
        event_ordinal=1,
        key_material=store.key_material(),
    )
    # The host tool-call id survives ingress under the canonical structural key.
    assert pre.structural_payload.get("tool_call_id") == "call_p8H2mKfQ"
    pre_batch = materialize_observation_envelope(pre, task_id=task)
    assert pre_batch.skip_reason is None
    assert [item.draft.schema.name for item in pre_batch.drafts] == ["action_recorded"]

    post = map_hook_payload_to_envelope(
        "PostToolUse",
        _codex_0146_payload(
            "PostToolUse", "call_p8H2mKfQ", tool_response={"output": "ok"}, exit_status=0
        ),
        session_commitment=session,
        event_ordinal=2,
        key_material=store.key_material(),
    )
    post_batch = materialize_observation_envelope(post, task_id=task)
    assert post_batch.skip_reason is None
    assert [item.draft.schema.name for item in post_batch.drafts] == [
        "action_recorded",
        "result_recorded",
    ]
    assert ObservationGapCode.UNPAIRED_EVENT.value not in post_batch.gaps


def test_codex_delivery_pairs_pre_post_end_to_end(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    for event in ("PreToolUse", "PostToolUse"):
        # No event_name argument: the event resolves from ``hook_event_name``,
        # exactly as a real Codex delivery arrives.
        code = handle_observe(
            event_name=None,
            stdin_bytes=json.dumps(_codex_0146_payload(event, "call_e2e_1")).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        assert code == 0
    status = store.status(ObservationStatusQuery(workspace))
    assert status.source_coverage[ObservationSource.CODEX_HOOK] is True
    assert ObservationGapCode.UNPAIRED_EVENT.value not in status.gaps
    assert store.has_open_pre(workspace, "call_e2e_1") is False


def test_unpaired_event_gap_resolves_after_pairing_recovers(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    handle_observe(
        event_name=None,
        stdin_bytes=json.dumps(_codex_0146_payload("PostToolUse", "call_orphan")).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert (
        ObservationGapCode.UNPAIRED_EVENT.value
        in store.status(ObservationStatusQuery(workspace)).gaps
    )

    for event in ("PreToolUse", "PostToolUse"):
        handle_observe(
            event_name=None,
            stdin_bytes=json.dumps(_codex_0146_payload(event, "call_recovered")).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
    # A completed pre→post pair is live evidence pairing works now; the latched
    # gap no longer describes the workspace (#274).
    assert (
        ObservationGapCode.UNPAIRED_EVENT.value
        not in store.status(ObservationStatusQuery(workspace)).gaps
    )


def test_yoetz_tool_still_ingests_but_skips_advice_loop(tmp_path: Path) -> None:
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.domain.values import finding_id
    from yoetz.protocol.coverage import (
        ArtifactObservation,
        AuthorshipAssurance,
        CheckType,
        Coverage,
        EvidenceImmutability,
        LedgerFreshness,
        PublicationChannel,
    )

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.set_advice_snapshot(
        workspace,
        AdviceSnapshot(
            ranked_finding_ids=(finding_id("fnd_00000000-0000-4000-8000-000000000001"),),
            evidence_basis_digest="sha256:" + "a" * 64,
            confidence_coverage=Coverage(
                publication_channels=(PublicationChannel.HOOK_OBSERVED,),
                authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
                artifact_observation=ArtifactObservation.HOOK_OBSERVED,
                evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
                ledger_freshness=LedgerFreshness.CURRENT,
                check_types=(CheckType.DETERMINISTIC,),
                known_gaps=(),
            ),
            recommended_next_action="call_status",
            freshness_frontier="frontier-1",
            suppression_identity="suppress-1",
        ),
    )
    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "yoetz-tool", "tool_name": "mcp__yoetz__status"}
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(out.getvalue().decode()) == {}


def test_skip_service_session_start_never_opens_service_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.adapters.integrations.codex_lifecycle import (
        mapping_from_start_ids,
        store_mapping,
    )
    from yoetz.protocol.ids import IdKind, new_id

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    service_touches: list[str] = []

    async def forbidden_connect(*_args: object, **_kwargs: object) -> object:
        service_touches.append("connect_service")
        raise AssertionError("skip_service must never open a service connection")

    async def forbidden_auto_start(*_args: object, **_kwargs: object) -> object:
        service_touches.append("_try_auto_start")
        raise AssertionError("skip_service must never auto-attach a ledger task")

    monkeypatch.setattr(observe_hooks_module, "connect_service", forbidden_connect)
    monkeypatch.setattr(observe_hooks_module, "_try_auto_start", forbidden_auto_start)

    # Unmapped session: the auto-attach branch must be gated by skip_service.
    unmapped = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "probe-unmapped", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert unmapped == 0

    # Mapped session: the status-read branch must be gated as well.
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="probe-mapped",
            yoetz_task_id=new_id(IdKind.TASK),
            yoetz_session_id=new_id(IdKind.SESSION),
            yoetz_writer_id=new_id(IdKind.WRITER),
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    mapped = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "probe-mapped", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert mapped == 0
    assert service_touches == []
    # Local capture still ran: sessions bound and envelopes queued in the outbox.
    assert store.find_workspace_for_codex_session("probe-unmapped") == workspace
    assert store.find_workspace_for_codex_session("probe-mapped") == workspace
    assert store.list_pending_outbox(workspace)


def test_session_start_stale_mapping_advisory_keeps_advice_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SESSION_CONFLICT status answer means the mapping is stale, not the
    service down: the observe path must emit the shared stale advisory (#308)
    and let pending advice join it instead of being starved."""

    import uuid

    from yoetz.adapters.integrations.codex_lifecycle import (
        load_mapping,
        mapping_from_start_ids,
        store_mapping,
    )
    from yoetz.adapters.integrations.observation_local import AdviceDelivery
    from yoetz.cli import hooks as hooks_module
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.protocol.ids import IdKind, new_id
    from yoetz.protocol.models import OperationFailureModel

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="stale-observe",
            yoetz_task_id=new_id(IdKind.TASK),
            yoetz_session_id=new_id(IdKind.SESSION),
            yoetz_writer_id=new_id(IdKind.WRITER),
            last_frontier="0:genesis",
        ),
        _state=tmp_path,
    )
    failure = OperationFailureModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "ok": False,
            "error": {
                "code": "SESSION_CONFLICT",
                "message": "The requested task attachment conflicts.",
                "retryable": False,
                "correlation_id": f"err_{uuid.uuid4()}",
            },
        }
    )

    class _Result:
        root = failure

    class _Client:
        async def status(self, request: object, *, deadline_ms: int | None = None) -> object:
            del request, deadline_ms
            return _Result()

        async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
            del body, deadline_ms
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object) -> _Client:
        return _Client()

    advice = AdviceDelivery(
        snapshot=cast(AdviceSnapshot, object()),
        item=None,
        delivery_identity="advice-1",
        text="Standing advice: connect a provider.",
    )
    commits: list[str] = []

    def fake_peek(
        self: LocalObservationStore,
        workspace_arg: str,
        *,
        yoetz_session_id: str | None = None,
        allow_standing: bool = True,
        session_commitment: str | None = None,
    ) -> AdviceDelivery:
        del self, workspace_arg, yoetz_session_id, allow_standing, session_commitment
        return advice

    def fake_commit(
        self: LocalObservationStore,
        workspace_arg: str,
        identity: str,
        *,
        yoetz_session_id: str | None = None,
        session_commitment: str | None = None,
    ) -> None:
        del self, workspace_arg, yoetz_session_id, session_commitment
        commits.append(identity)

    monkeypatch.setattr(LocalObservationStore, "peek_advice_for_delivery", fake_peek)
    monkeypatch.setattr(LocalObservationStore, "commit_advice_delivery", fake_commit)

    out = io.BytesIO()
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "stale-observe", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    context = json.loads(out.getvalue().decode())["hookSpecificOutput"]["additionalContext"]
    stale_text = hooks_module._STALE_MAPPING_CONTEXT  # pyright: ignore[reportPrivateUsage]
    assert context.startswith(stale_text)
    assert "unavailable" not in context
    # The static stale advisory must not starve pending advice (#241, #280 pattern).
    assert advice.text in context
    assert commits == ["advice-1"]
    # The stale mapping survives: repair belongs to the agent's own re-start.
    assert load_mapping("stale-observe", _state=tmp_path) is not None
    diagnostics = (tmp_path / "observation" / "hook-diagnostics.jsonl").read_text()
    assert '"reason":"mapping_stale"' in diagnostics


def test_malformed_stdin_exits_zero(tmp_path: Path) -> None:
    code = handle_observe(
        event_name="Stop",
        stdin_bytes=b"{not-json",
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0


def _drain_envelope(store: LocalObservationStore, session: str, identity: str, ordinal: int):
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "shell",
        "correlation_id": f"corr-{ordinal}",
        "exit_status": 1,
    }
    commitment = store.session_commitment(session)
    return map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=commitment,
        event_ordinal=ordinal,
        key_material=store.key_material(),
    )


@pytest.mark.anyio
async def test_drain_quarantines_permanent_and_keeps_retryable(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "sess-drain")
    perm = _drain_envelope(store, "sess-drain", "hook:perm", 1)
    retry = _drain_envelope(store, "sess-drain", "hook:retry", 2)
    store.enqueue_outbox(workspace, "sess-drain", perm)
    store.enqueue_outbox(workspace, "sess-drain", retry)
    assert store.pending_outbox_count(workspace) == 2

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            envelope = body["envelope"]  # type: ignore[index]
            if envelope["source_identity"] == perm.source_identity:
                result = ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_REVOKED.value,
                    None,
                )
            else:
                result = ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.SERVICE_UNAVAILABLE.value,
                    None,
                )
            return observation_ingest_result_to_json(result)

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="sess-drain",
        connect=connect,  # type: ignore[arg-type]
    )

    # Permanent -> quarantined (never dropped); retryable -> still pending.
    assert store.quarantined_count(workspace) == 1
    assert store.list_quarantine(workspace)[0][1].source_identity == perm.source_identity
    pending = store.list_pending_outbox(workspace)
    assert len(pending) == 1
    assert pending[0][1].source_identity == retry.source_identity


@pytest.mark.anyio
async def test_drain_is_round_robin_across_all_workspace_sessions(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session in ("current", "recovered"):
        store.bind_codex_session(workspace, session)
        for ordinal in (1, 2):
            store.enqueue_outbox(
                workspace,
                session,
                _drain_envelope(store, session, f"hook:{session}:{ordinal}", ordinal),
            )
    calls: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            calls.append(body["codex_session_id"])  # type: ignore[index]
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="current",
        connect=connect,  # type: ignore[arg-type]
    )
    assert calls == ["current", "recovered", "current", "recovered"]
    assert store.pending_outbox_count(workspace) == 0


@pytest.mark.anyio
async def test_drain_preflight_failure_skips_rows_and_records_diagnostic(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "preflight")
    store.enqueue_outbox(workspace, "preflight", _drain_envelope(store, "preflight", "x", 1))

    async def unavailable(_kind: object):
        raise RuntimeError("offline")

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="preflight",
        connect=unavailable,
        _state=tmp_path,
    )
    assert store.list_pending_outbox_rows(workspace)[0].attempts == 0
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"drain_preflight_failed"' in diagnostics


@pytest.mark.anyio
async def test_drain_empty_outbox_never_connects_or_records_diagnostics(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "idle")
    connects: list[object] = []

    async def recording_connect(kind: object):
        connects.append(kind)
        raise RuntimeError("offline")

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="idle",
        connect=recording_connect,
        _state=tmp_path,
    )
    assert connects == []
    assert not (tmp_path / "observation/hook-diagnostics.jsonl").exists()


@pytest.mark.anyio
async def test_drain_budget_stops_without_advancing_unfinished_row(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "budget")
    store.enqueue_outbox(workspace, "budget", _drain_envelope(store, "budget", "x", 1))

    class SlowClient:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            await asyncio.sleep(1)

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return SlowClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="budget",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
        budget_seconds=0.01,
    )
    assert store.list_pending_outbox_rows(workspace)[0].attempts == 0
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"drain_budget_exhausted"' in diagnostics


@pytest.mark.anyio
async def test_drain_probes_a_mapping_missing_session_once_per_pass(tmp_path: Path) -> None:
    """#211's recurrence tax: a dead-session backlog must not eat the drain budget.

    mapping_missing is session-scoped and cannot heal mid-pass, so one
    rejection retires the whole session for the rest of the pass while other
    sessions still deliver.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session, count in (("dead", 5), ("healthy", 2)):
        store.bind_codex_session(workspace, session)
        for ordinal in range(1, count + 1):
            store.enqueue_outbox(
                workspace, session, _drain_envelope(store, session, f"hook:{session}", ordinal)
            )

    attempts: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            session = str(body["codex_session_id"])  # type: ignore[index]
            attempts.append(session)
            if session == "dead":
                return observation_ingest_result_to_json(
                    ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.MAPPING_MISSING.value,
                        None,
                    )
                )
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="dead",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert attempts.count("dead") == 1, "a mapping_missing session must be probed once per pass"
    assert attempts.count("healthy") == 2, "healthy sessions must still deliver in the same pass"
    remaining = store.list_pending_outbox_rows(workspace)
    assert {row.codex_session_id for row in remaining} == {"dead"}
    assert len(remaining) == 5
    # Retired siblings carry the shared cause so `observe status` reports
    # mapping_missing=5, never a misleading not_attempted.
    assert all(row.last_reason == ObservationGapCode.MAPPING_MISSING.value for row in remaining)


@pytest.mark.anyio
async def test_drain_quarantines_mapping_missing_rows_of_an_ended_session(tmp_path: Path) -> None:
    """A session that ended unmapped has no future; its rows retire terminally (#275)."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    ended_commitment = store.bind_codex_session(workspace, "ended")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace, "ended", _drain_envelope(store, "ended", "hook:ended", ordinal)
        )
    store.note_session_end(workspace, ended_commitment)

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

    with acquire_session_lock("ended", _state=tmp_path) as owned:
        assert owned is True
        await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
            store,
            workspace_commitment=workspace,
            codex_session_id="ended",
            connect=connect,  # type: ignore[arg-type]
            _state=tmp_path,
        )

    assert len(store.list_pending_outbox_rows(workspace)) == 2
    assert store.quarantined_count(workspace) == 0

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="ended",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert store.list_pending_outbox_rows(workspace) == ()
    assert store.quarantined_count(workspace) == 2
    assert {entry[2] for entry in store.list_quarantine(workspace)} == {
        ObservationGapCode.MAPPING_MISSING.value
    }


def test_unmapped_session_reattempts_auto_attach_on_turn_events_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missed SessionStart attach is retried on turn-boundary events, never per tool call (#275)."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    calls: list[str] = []

    async def fake_auto_start(codex_session_id: str, *, _state: Path | None) -> object | None:
        calls.append(codex_session_id)
        return None

    monkeypatch.setattr(observe_hooks_module, "_try_auto_start", fake_auto_start)

    async def connect(_kind: object):
        return _InstantAckClient()

    code = handle_observe(
        event_name="UserPromptSubmit",
        stdin_bytes=json.dumps(
            {"session_id": "late-attach", "hook_event_name": "UserPromptSubmit"}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    assert calls == ["late-attach"]
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"auto_attach_retry_failed"' in diagnostics

    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "late-attach",
                "tool_name": "shell",
                "correlation_id": "attach-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    assert calls == ["late-attach"], "tool-call storms must never re-attempt auto-attach"


def test_auto_attach_retry_exception_records_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    async def fail_auto_start(codex_session_id: str, *, _state: Path | None) -> object | None:
        del codex_session_id, _state
        raise TimeoutError("bounded auto-attach timeout")

    monkeypatch.setattr(observe_hooks_module, "_try_auto_start", fail_auto_start)

    async def connect(_kind: object):
        return _InstantAckClient()

    code = handle_observe(
        event_name="UserPromptSubmit",
        stdin_bytes=json.dumps(
            {"session_id": "late-attach-timeout", "hook_event_name": "UserPromptSubmit"}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )

    assert code == 0
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert diagnostics.count('"reason":"auto_attach_retry_failed"') == 1


def test_session_reason_stamping_preserves_a_rows_observed_cause(tmp_path: Path) -> None:
    """Skipped siblings inherit the cause without rewriting a prior real attempt."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "mixed-reasons")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace,
            "mixed-reasons",
            _drain_envelope(store, "mixed-reasons", "hook:mixed-reasons", ordinal),
        )
    attempted, skipped = store.list_pending_outbox_rows(workspace)
    assert (
        store.bump_outbox_row_attempt(
            workspace,
            attempted,
            reason=ObservationGapCode.SERVICE_UNAVAILABLE.value,
        )
        is not None
    )

    stamped = store.note_outbox_session_reason(
        workspace,
        "mixed-reasons",
        ObservationGapCode.MAPPING_MISSING.value,
    )

    assert stamped == 1
    attempted_after, skipped_after = store.list_pending_outbox_rows(workspace)
    assert attempted_after.last_reason == ObservationGapCode.SERVICE_UNAVAILABLE.value
    assert skipped_after.row_identity == skipped.row_identity
    assert skipped_after.last_reason == ObservationGapCode.MAPPING_MISSING.value


@pytest.mark.anyio
async def test_drain_service_unavailable_retires_its_session_but_not_the_workspace(
    tmp_path: Path,
) -> None:
    """One poisoned session must not wedge the workspace drain — nor be reordered.

    service_unavailable is the catch-all for row-scoped failures (bundle
    contention, one malformed envelope). Other sessions keep delivering, but
    the failed row stays the head of its own lane: delivering a later row of
    the same session would advance the ingest cursor past it and destroy it
    as terminal cursor_stale (#272).
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "poisoned")
    store.bind_codex_session(workspace, "healthy")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace, "poisoned", _drain_envelope(store, "poisoned", "hook:poisoned", ordinal)
        )
        store.enqueue_outbox(
            workspace, "healthy", _drain_envelope(store, "healthy", "hook:healthy", ordinal)
        )
    poisoned_head = store.list_pending_outbox_rows(workspace, codex_session_id="poisoned")[0]
    poisoned = poisoned_head.envelope.source_identity

    attempts: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            identity = str(body["envelope"]["source_identity"])  # type: ignore[index]
            attempts.append(identity)
            if identity == poisoned:
                return observation_ingest_result_to_json(
                    ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.SERVICE_UNAVAILABLE.value,
                        None,
                    )
                )
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="poisoned",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert attempts.count(poisoned) == 1, "the failed head is probed once, never stepped over"
    assert len(attempts) == 3, "the healthy session still delivers fully"
    remaining = store.list_pending_outbox_rows(workspace)
    assert [row.codex_session_id for row in remaining] == ["poisoned", "poisoned"]
    assert [row.envelope.cursor.event_position for row in remaining] == [1, 2]
    assert [row.last_reason for row in remaining] == [
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
    ]


@pytest.mark.anyio
async def test_slow_successful_connect_is_not_charged_to_the_drain_budget(
    tmp_path: Path,
) -> None:
    """The preflight bounds connect time; the budget bounds drain work.

    Before the clock moved after the connect, a 0.9s connect against the
    0.75s SessionEnd budget entered the row loop with remaining <= 0 and
    drained nothing while recording drain_budget_exhausted — a diagnostic
    blaming the budget for time the connect spent.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "slow-connect")
    store.enqueue_outbox(workspace, "slow-connect", _drain_envelope(store, "slow-connect", "x", 1))

    clock = {"now": 0.0}

    async def slow_connect(_kind: object):
        clock["now"] += 0.9  # slower than the whole 0.75s SessionEnd budget
        return _InstantAckClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="slow-connect",
        connect=slow_connect,  # type: ignore[arg-type]
        _state=tmp_path,
        budget_seconds=0.75,
        monotonic=lambda: clock["now"],
    )
    assert store.pending_outbox_count(workspace) == 0, (
        "a slow but successful connect must leave the whole budget for draining"
    )
    diagnostics_path = tmp_path / "observation/hook-diagnostics.jsonl"
    if diagnostics_path.exists():
        assert '"reason":"drain_budget_exhausted"' not in diagnostics_path.read_text()


@pytest.mark.anyio
async def test_workspace_global_rejection_ends_the_pass(tmp_path: Path) -> None:
    """vault_locked cannot heal mid-pass, so one rejection ends the drain."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session in ("one", "two"):
        store.bind_codex_session(workspace, session)
        for ordinal in (1, 2):
            store.enqueue_outbox(
                workspace, session, _drain_envelope(store, session, f"hook:{session}", ordinal)
            )

    attempts = 0

    class LockedClient:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            nonlocal attempts
            attempts += 1
            return observation_ingest_result_to_json(
                ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.VAULT_LOCKED.value,
                    None,
                )
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return LockedClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="one",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert attempts == 1, "a workspace-global rejection must end the pass after one probe"
    assert store.pending_outbox_count(workspace) == 4


@pytest.mark.anyio
async def test_drain_lease_prevents_concurrent_hooks_from_double_draining(
    tmp_path: Path,
) -> None:
    """#209 made hooks genuinely concurrent; only one may drain at a time."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "leased")
    store.enqueue_outbox(workspace, "leased", _drain_envelope(store, "leased", "x", 1))

    connects: list[object] = []

    async def recording_connect(kind: object):
        connects.append(kind)
        raise RuntimeError("offline")

    with store.drain_lease(workspace) as owned:
        assert owned is True
        # A second store instance (another hook process) must lose the lease
        # and skip the drain entirely — no connect. Losing to a live owner is
        # designed coordination, so no failure-shaped diagnostic is recorded
        # (#351): one row per contending hook buried genuine failures.
        other = LocalObservationStore(_state=tmp_path)
        await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
            other,
            workspace_commitment=workspace,
            codex_session_id="leased",
            connect=recording_connect,
            _state=tmp_path,
        )
    assert connects == []
    diagnostics = tmp_path / "observation/hook-diagnostics.jsonl"
    if diagnostics.exists():
        assert "drain_lease_contended" not in diagnostics.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_budget_expiry_after_delivery_progress_is_a_silent_yield(tmp_path: Path) -> None:
    """A bounded slice that moved backlog and then yielded is working as designed (#351).

    drain_budget_exhausted remains reserved for a pass that timed out before
    any progress; a capacity yield to the service sweeper is not a failure.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "yield")
    for ordinal in (1, 2, 3):
        store.enqueue_outbox(
            workspace, "yield", _drain_envelope(store, "yield", "hook:yield", ordinal)
        )

    clock = {"now": 0.0}

    class OneThenSlowClient:
        def __init__(self) -> None:
            self.calls = 0

        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            self.calls += 1
            # First row delivers instantly; afterwards the budget is exhausted.
            clock["now"] += 0.05 if self.calls == 1 else 1.0
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return OneThenSlowClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="yield",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
        budget_seconds=0.2,
        monotonic=lambda: clock["now"],
    )
    assert store.pending_outbox_count(workspace) == 1, "two rows delivered before the yield"
    diagnostics = tmp_path / "observation/hook-diagnostics.jsonl"
    if diagnostics.exists():
        assert '"reason":"drain_budget_exhausted"' not in diagnostics.read_text()


@pytest.mark.anyio
async def test_operation_pending_deferral_keeps_row_without_gap_or_diagnostic(
    tmp_path: Path,
) -> None:
    """An ADR-022 check-barrier deferral is expected back-pressure, not failure (#351)."""

    from yoetz.domain.observation import OBSERVATION_BACKPRESSURE_REASON

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "deferred")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace, "deferred", _drain_envelope(store, "deferred", "hook:deferred", ordinal)
        )

    attempts = 0

    class BarrierClient:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            nonlocal attempts
            attempts += 1
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
        return BarrierClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="deferred",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    # The deferred head retires its lane for the pass; both rows stay pending
    # with the honest annotation, and neither a coverage gap nor a hook
    # diagnostic reports the designed barrier as a failure.
    assert attempts == 1
    pending = store.list_pending_outbox_rows(workspace)
    assert [row.last_reason for row in pending] == [
        OBSERVATION_BACKPRESSURE_REASON,
        OBSERVATION_BACKPRESSURE_REASON,
    ]
    status = store.status(ObservationStatusQuery(workspace))
    assert OBSERVATION_BACKPRESSURE_REASON not in status.gaps
    assert ObservationGapCode.SERVICE_UNAVAILABLE.value not in status.gaps
    diagnostics = tmp_path / "observation/hook-diagnostics.jsonl"
    if diagnostics.exists():
        assert OBSERVATION_BACKPRESSURE_REASON not in diagnostics.read_text()


def test_drain_lease_refuses_a_symlink_lock_file(tmp_path: Path) -> None:
    """A project-local symlink cannot redirect the advisory-lock target."""

    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("host has no O_NOFOLLOW")

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    target = tmp_path / "redirected-lock-target"
    target.write_text("unchanged", encoding="utf-8")
    digest = workspace.removeprefix("hmac-sha256:")
    lock_path = tmp_path / "observation" / f".drain-{digest}.lock"
    lock_path.symlink_to(target)

    with pytest.raises(OSError):
        with store.drain_lease(workspace):
            raise AssertionError("a symlinked drain lease must not be acquired")
    assert target.read_text(encoding="utf-8") == "unchanged"


def _populate_realistic_store(
    store: LocalObservationStore,
    workspace: str,
    session: str,
    *,
    envelopes: int = 250,
    pending: int = 60,
    quarantined: int = 199,
) -> None:
    """Grow one workspace state to the shape the 2026-08-12 regression ran at.

    Hook cost is store-size-dependent, so latency guards against a small
    fixture pass trivially (#209). The live store that measured 3.06-4.89s
    held 256 envelopes, 73 pending rows, and 199 quarantine entries in a
    ~384KB file; this builds the same order of magnitude in one save.
    """

    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert state.envelopes is not None
    assert state.pending_outbox is not None
    assert state.quarantine is not None
    assert state.dedup is not None
    from datetime import UTC, datetime

    from yoetz.adapters.integrations.observation_local import (
        ObservationOutboxRow,
        _dedup_key,  # pyright: ignore[reportPrivateUsage]
    )
    from yoetz.domain.values import timestamp_from_datetime

    quarantined_at = timestamp_from_datetime(datetime.now(UTC).replace(microsecond=0))
    for ordinal in range(1, envelopes + pending + quarantined + 1):
        envelope = _drain_envelope(store, session, f"hook:bulk:{ordinal}", ordinal)
        if ordinal <= envelopes:
            state.envelopes.append(envelope)
            state.dedup.add(_dedup_key(workspace, envelope))
        elif ordinal <= envelopes + pending:
            state.pending_outbox.append(
                ObservationOutboxRow(codex_session_id=session, envelope=envelope)
            )
        else:
            state.quarantine.append((session, envelope, "service_unavailable", quarantined_at))
    store._save(workspace, state)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


class _InstantAckClient:
    async def observation_ingest(self, body: object, *, deadline_ms: int):
        del body, deadline_ms
        # DUPLICATE routes to ACKNOWLEDGE without needing a service cursor.
        return observation_ingest_result_to_json(
            ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
        )

    async def close(self) -> None:
        return None


def test_hook_invocation_parses_the_state_file_once_not_seventeen_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#209: one hook process loads the store once and reuses the parse.

    Before the stat-validated parse cache, every store method re-read and
    re-parsed the whole workspace state file — 17 times per hook invocation
    against the live 384KB store.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "parse-count")
    _populate_realistic_store(store, workspace, "parse-count", pending=8, quarantined=40)

    parses = 0
    original = LocalObservationStore._state_from_json  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def counting(self: LocalObservationStore, raw: object):
        nonlocal parses
        parses += 1
        return original(self, raw)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "_state_from_json", counting)

    async def connect(_kind: object):
        return _InstantAckClient()

    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "parse-count",
                "tool_name": "shell",
                "correlation_id": "pc-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    assert parses == 1, (
        f"one hook invocation parsed the workspace state {parses} times; "
        "the per-instance parse cache is not being hit (33 parses measured "
        "with the cache neutered at this fixture shape)"
    )


@pytest.mark.slow
def test_hook_wall_clock_meets_the_declared_timeout_on_a_realistic_store(
    tmp_path: Path,
) -> None:
    """#209's guard: hook wall time vs the timeout hooks.json declares.

    The 2026-08-12 regression measured 3.06-4.89s per hook at exactly this
    store shape against a declared 3s, and nothing went red because no test
    asserted wall clock at a realistic store size. The bound is the declared
    timeout with a safety margin: absolute machine-calibrated bounds flake
    (a 3.0s bound measured 0.5s locally and 3.09s on a shared CI runner), so
    the machine-independent cache-regression duty lives in the parse-count
    test above, and this test owns the contract that a hook never comes near
    the budget Codex kills it at.
    """

    import time as time_module

    from yoetz.adapters.integrations.codex_plugin import parse_hooks_json, render_plugin_tree

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "latency")
    _populate_realistic_store(store, workspace, "latency")
    state_file = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    assert state_file.stat().st_size >= 250_000, (
        "latency guard must run against a realistically-sized store; "
        f"got {state_file.stat().st_size} bytes"
    )

    async def connect(_kind: object):
        return _InstantAckClient()

    started = time_module.monotonic()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "latency",
                "tool_name": "shell",
                "correlation_id": "lat-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    elapsed = time_module.monotonic() - started
    assert code == 0
    hooks = parse_hooks_json(render_plugin_tree()["hooks/hooks.json"])
    events = hooks["hooks"]
    declared = None
    for group in events["PostToolUse"]:  # type: ignore[index, call-overload]
        for handler in group["hooks"]:  # type: ignore[index, call-overload]
            if "observe" in str(handler["command"]):  # type: ignore[index]
                assert declared is None, "more than one observe handler declared for PostToolUse"
                declared = handler["timeout"]  # type: ignore[index]
    assert isinstance(declared, int)
    # The machine-independent parse/write-count tests own the tight regression
    # contract. This wall-clock smoke bound still excludes the pre-fix 1.67-2.50s
    # band without failing solely because a shared runner is briefly loaded.
    assert elapsed < declared * 0.4, (
        f"hook invocation took {elapsed:.2f}s against a realistic store — "
        f"over 40% of the declared {declared}s timeout Codex kills it at"
    )


def _write_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the name of every file the store atomically writes."""

    import yoetz.adapters.integrations.observation_local as local_mod

    written: list[str] = []
    original = local_mod._atomic_write  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def counting(path: Path, payload: bytes) -> None:
        written.append(path.name)
        original(path, payload)

    monkeypatch.setattr(local_mod, "_atomic_write", counting)
    return written


def _state_writes(written: list[str]) -> int:
    return sum(1 for name in written if name.endswith(".json"))


def _suffix_counts(written: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in written:
        suffix = Path(name).suffix
        counts[suffix] = counts.get(suffix, 0) + 1
    return counts


def test_hook_invocation_writes_the_state_file_once_not_fourteen_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#242: one hook pass serializes the workspace state a bounded number of times.

    The parse-count guard above pins reads; nothing pinned writes, so 10-18
    serialize+fsync cycles per hook survived #209/#213. At the live 525KB store
    each cycle measured ~91ms of encode alone — 0.9-1.6s per hook.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "write-count")
    _populate_realistic_store(store, workspace, "write-count", pending=8, quarantined=40)

    written = _write_counter(monkeypatch)

    async def connect(_kind: object):
        return _InstantAckClient()

    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "write-count",
                "tool_name": "shell",
                "correlation_id": "wc-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
        _monotonic=lambda: 0.0,
    )
    assert code == 0
    delivered = "additionalContext" in out.getvalue().decode()
    # Exact accounting, so a regression cannot hide inside a loose ceiling:
    #   1 local-pass batch flush
    # + 1 per drained outbox row, bounded by _HOOK_DRAIN_ROW_LIMIT (4 here)
    # + 1 advice-delivery commit, and only when advice actually reached stdout.
    # Seventeen were measured before the write batch. Nothing else writes: the
    # advice sidecar and the async-pair sample are gone.
    assert _suffix_counts(written) == {".json": 5 + int(delivered)}, written


def test_refresh_advice_does_not_rewrite_state_when_the_snapshot_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "noop-advice")
    _populate_realistic_store(
        store, workspace, "noop-advice", envelopes=8, pending=0, quarantined=0
    )

    first = store.refresh_advice(workspace)
    written = _write_counter(monkeypatch)
    second = store.refresh_advice(workspace)

    assert first is not None and second is first
    assert _state_writes(written) == 0, (
        "an unchanged advice snapshot was rewritten; the build returns the prior "
        "object identically when nothing moved"
    )


def test_quarantine_pruning_still_runs_on_a_hook_whose_advice_refresh_no_ops(
    tmp_path: Path,
) -> None:
    """The no-op-save guard must not cost a pruning opportunity (#242 risk 16).

    ``_save`` is what drives quarantine expiry and the size trims, so skipping
    it in ``refresh_advice`` is only safe because the write batch guarantees the
    pass still flushes exactly once.
    """

    from datetime import UTC, datetime, timedelta

    from yoetz.domain.values import timestamp_from_datetime

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "prune")
    _populate_realistic_store(store, workspace, "prune", envelopes=4, pending=0, quarantined=0)

    stale = timestamp_from_datetime((datetime.now(UTC) - timedelta(days=90)).replace(microsecond=0))
    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert state.quarantine is not None
    state.quarantine.append(
        (
            session,
            _drain_envelope(store, session, "hook:stale", 9_000),
            "service_unavailable",
            stale,
        )
    )
    store._save(workspace, state)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert store.quarantined_count(workspace) == 1

    # A second identical refresh no-ops; the hook pass must still prune.
    store.refresh_advice(workspace)
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "prune", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert store.quarantined_count(workspace) == 0


def _seed_standing_only_advice(store: LocalObservationStore, workspace: str) -> None:
    """Persist an advice snapshot whose every action is a standing machine condition."""

    from yoetz.application.observation_advice import (
        ObservationAdviceBuildInput,
        build_observation_advice_snapshot,
    )
    from yoetz.domain.observation import ObservationLifecycle
    from yoetz.kernel.policies.observation_advice import ObservationCompositionFact

    snapshot = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=store.list_envelopes(workspace),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=True,
                semantic_ready=False,
                provider_factory_ids=("fireworks",),
                connected_provider_ids=(),
            ),
            has_real_observation=True,
        )
    )
    assert snapshot is not None
    assert {item.recommended_next_action for item in snapshot.ranked_items} == {"connect_provider"}
    store.set_advice_snapshot(workspace, snapshot)


def _refresh_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    original = LocalObservationStore.refresh_advice

    def counting(self: LocalObservationStore, workspace_commitment: str, **kwargs: object):
        calls[0] += 1
        return original(self, workspace_commitment, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "refresh_advice", counting)
    return calls


def test_advice_stage_runs_on_every_advice_safe_event_including_inert_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No relevance gate: an inert envelope still recomputes and still may deliver.

    The removed fast path could not fire in the sessions it targeted (any
    workspace with a coverage gap carries ``refresh_observation``), and where it
    did fire it reasoned over the workspace snapshot while delivery prefers the
    session snapshot — so it could withhold actionable session advice. The
    advice stage is unconditional on advice-safe events.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "fast", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    _seed_standing_only_advice(store, workspace)
    before = len(store.list_envelopes(workspace))

    refreshes = _refresh_counter(monkeypatch)
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps({"session_id": "fast", "tool_name": "Read"}).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert refreshes[0] == 1, "the advice stage was skipped on an advice-safe event"
    assert len(store.list_envelopes(workspace)) == before + 1


def test_post_tool_use_still_recomputes_advice_for_a_trigger_bearing_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "trigger", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    _seed_standing_only_advice(store, workspace)

    refreshes = _refresh_counter(monkeypatch)
    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "trigger",
                "tool_name": "shell",
                "exit_status": 1,
                "correlation_id": "t1",
            }
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert refreshes[0] == 1
    assert "resolve_failed_command" in out.getvalue().decode()


def test_timing_rows_attribute_the_store_stage(tmp_path: Path) -> None:
    """Session-boundary timing rows carry hydrate/encode/write sub-stages (#290)."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    out = io.BytesIO()
    code = handle_observe(
        event_name="Stop",
        stdin_bytes=json.dumps({"session_id": "attributed", "tool_name": "shell"}).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    rows = [
        cast(dict[str, object], json.loads(line))
        for line in (tmp_path / "observation/hook-diagnostics.jsonl").read_text().splitlines()
    ]
    timing_rows = [row for row in rows if row.get("kind") == "timing"]
    assert timing_rows
    stages = cast(Mapping[str, object], timing_rows[-1]["stages"])
    assert {"store", "store_hydrate", "store_encode", "store_write"} <= set(stages)


def test_timing_rows_partition_the_whole_pass(tmp_path: Path) -> None:
    """#310/#311: stage sums covered as little as 14% of the reported total."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    out = io.BytesIO()
    code = handle_observe(
        event_name="Stop",
        stdin_bytes=json.dumps({"session_id": "partitioned", "tool_name": "shell"}).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    rows = [
        cast(dict[str, object], json.loads(line))
        for line in (tmp_path / "observation/hook-diagnostics.jsonl").read_text().splitlines()
    ]
    stages = cast(
        Mapping[str, int], [row for row in rows if row.get("kind") == "timing"][-1]["stages"]
    )
    # The formerly unwindowed regions, plus lock queueing wherever it happened.
    assert {"resolve", "deliver", "store_lock_wait", "unattributed"} <= set(stages)
    partition = ("import", "resolve", "store", "drain", "deliver")
    total = stages["total"]
    assert sum(stages[name] for name in partition) + stages["unattributed"] >= total - 5
    # Nothing is left unexplained on an uncontended local pass.
    assert stages["unattributed"] <= max(5, total // 4)


def test_hook_total_budget_covers_the_budgets_nested_inside_one_pass() -> None:
    """The end-to-end contract must be satisfiable by a healthy pass (#288).

    The connect preflight and drain budgets are enforced sequentially inside a
    single pass, so a total below their sum fires hook_budget_exceeded on
    passes that are working exactly as designed.
    """

    module = observe_hooks_module
    preflight = module._HOOK_CONNECT_PREFLIGHT_SECONDS  # pyright: ignore[reportPrivateUsage]
    drain = module._HOOK_DRAIN_BUDGET_SECONDS  # pyright: ignore[reportPrivateUsage]
    end_drain = module._SESSION_END_DRAIN_BUDGET_SECONDS  # pyright: ignore[reportPrivateUsage]
    allowance = module._HOOK_LOCAL_STAGE_ALLOWANCE_SECONDS  # pyright: ignore[reportPrivateUsage]
    total = module._HOOK_TOTAL_BUDGET_SECONDS  # pyright: ignore[reportPrivateUsage]
    attach = module._AUTO_ATTACH_RETRY_BUDGET_SECONDS  # pyright: ignore[reportPrivateUsage]
    attach_events = module._AUTO_ATTACH_RETRY_EVENTS  # pyright: ignore[reportPrivateUsage]
    budget_for = module._hook_total_budget_seconds  # pyright: ignore[reportPrivateUsage]

    # Beyond the enforced budgets, a pass pays the local stages (import,
    # store, advice); the measured cost on a full 1 MiB store is ~0.5s.
    assert total >= preflight + max(drain, end_drain) + 0.5
    # Derivation, not coincidence: the total is the sum of its parts.
    assert total == preflight + drain + allowance
    # Events that may legitimately retry auto-attach carry that budget too.
    for event in (*attach_events, "SessionStart"):
        assert budget_for(event) >= total + attach
    for event in ("PreToolUse", "PostToolUse"):
        assert budget_for(event) == total


def test_healthy_pass_shapes_fit_under_the_total_budget(tmp_path: Path) -> None:
    """The two measured healthy drain shapes must not trip the diagnostic (#288)."""

    from yoetz.cli.hook_diagnostics import hook_diagnostic_summary
    from yoetz.cli.observe_hooks import (
        _record_pass_timing,  # pyright: ignore[reportPrivateUsage]
    )

    # Cold drain: preflight + drain + encode (~1.3s) on top of a full-store
    # local pass (~0.55s) — the dominant healthy PreToolUse shape measured in
    # #288. Under the old 1.0s total every such pass was flagged.
    for event, total_seconds in (("PreToolUse", 2.1), ("PostToolUse", 1.3)):
        _record_pass_timing(
            event,
            entry_started=0.0,
            stages={"import": 25, "store": 520},
            monotonic=lambda total=total_seconds: total,
            _state=tmp_path,
        )
    summary = hook_diagnostic_summary(_state=tmp_path)
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    assert "hook_budget_exceeded" not in reasons


def test_end_to_end_hook_budget_is_recorded_and_exceeding_it_is_diagnosed(
    tmp_path: Path,
) -> None:
    """The budget is an observability contract: over it, the hook still succeeds."""

    from yoetz.cli.hook_diagnostics import hook_diagnostic_summary

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    elapsed = [0.0]

    def creeping() -> float:
        elapsed[0] += 5.0
        return elapsed[0]

    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "budget", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
        _monotonic=creeping,
    )
    assert code == 0
    assert out.getvalue().endswith(b"\n")
    summary = hook_diagnostic_summary(_state=tmp_path)
    reasons = dict(cast(Mapping[str, object], summary["reasons"]))
    budget = dict(cast(Mapping[str, object], reasons["hook_budget_exceeded"]))
    assert budget["count"] == 1
    assert budget["recent"] == 1
    timings = dict(cast(Mapping[str, object], summary["timings"]))
    assert timings["count"] == 1
