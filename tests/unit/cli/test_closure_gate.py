"""The Stop-time closure gate (issue #977): decision, memory and per-host delivery."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import mapping_from_start_ids, store_mapping
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import closure_gate as gate_module
from yoetz.cli import observe_hooks
from yoetz.cli.closure_gate import (
    closure_gate_already_delivered,
    closure_gate_from_readiness,
    record_closure_gate_delivered,
)
from yoetz.domain.observation import (
    ObservationIngestDisposition,
    ObservationIngestResult,
    observation_ingest_result_to_json,
)
from yoetz.kernel.task_facts import STOP_GATE_TOKENS
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.ids import IdKind, new_id

_DIGEST = "sha256:" + "d" * 64


def _readiness(
    state: str = "action_required", actionable: tuple[str, ...] = ("obligations_open",)
) -> Any:
    return SimpleNamespace(
        state=state,
        agent_actionable=actionable,
        open_obligation_count="2",
        receipt_blocking_finding_count="1",
    )


def _gate(readiness: object, *, pending: bool = False) -> Any:
    return closure_gate_from_readiness(
        readiness, frontier_sequence="7", frontier_digest=_DIGEST, observation_pending=pending
    )


# --- pure decision -------------------------------------------------------------------------


@pytest.mark.parametrize("token", STOP_GATE_TOKENS)
def test_every_stop_gate_token_fires(token: str) -> None:
    gate = _gate(_readiness(actionable=(token,)))
    assert gate is not None
    assert gate.items == (token,)
    assert gate.identity == f"7:{_DIGEST}"
    assert "yoetz-blocker:" in gate.text
    assert len(gate.text) <= 2000
    assert "/" not in gate.text.replace("<authority|consent|credential|dependency_unavailable>", "")


@pytest.mark.parametrize("state", ["ready", "ready_with_limitations"])
def test_ready_states_do_not_gate(state: str) -> None:
    assert _gate(_readiness(state=state)) is None


@pytest.mark.parametrize(
    "token", ["findings_unanswered", "check_not_applicable", "no_plan_published"]
)
def test_non_gate_actionable_items_do_not_gate(token: str) -> None:
    assert token not in STOP_GATE_TOKENS
    assert _gate(_readiness(actionable=(token,))) is None


def test_observation_pending_drops_only_observation_dependent_items() -> None:
    dependent = tuple(token for token in STOP_GATE_TOKENS if token.startswith("planned_verif"))
    assert dependent
    assert _gate(_readiness(actionable=dependent), pending=True) is None
    mixed = _gate(_readiness(actionable=(*dependent, "obligations_open")), pending=True)
    assert mixed is not None
    assert mixed.items == ("obligations_open",)
    assert "draining" in mixed.text


def test_malformed_readiness_does_not_gate() -> None:
    assert _gate(None) is None
    assert (
        _gate(SimpleNamespace(state="action_required", agent_actionable="obligations_open")) is None
    )


# --- once-per-frontier memory ----------------------------------------------------------------


def test_memory_round_trip_and_session_isolation(tmp_path: Path) -> None:
    assert not closure_gate_already_delivered("ses_a", "1:x", _state=tmp_path)
    assert record_closure_gate_delivered("ses_a", "1:x", _state=tmp_path)
    assert closure_gate_already_delivered("ses_a", "1:x", _state=tmp_path)
    assert not closure_gate_already_delivered("ses_a", "2:x", _state=tmp_path)
    assert not closure_gate_already_delivered("ses_b", "1:x", _state=tmp_path)
    assert record_closure_gate_delivered("ses_b", "2:y", _state=tmp_path)
    assert closure_gate_already_delivered("ses_a", "1:x", _state=tmp_path)
    assert closure_gate_already_delivered("ses_b", "2:y", _state=tmp_path)


def test_memory_is_bounded(tmp_path: Path) -> None:
    limit = gate_module._MAX_SESSIONS  # pyright: ignore[reportPrivateUsage]
    for index in range(limit + 10):
        assert record_closure_gate_delivered(f"s{index}", f"{index}:d", _state=tmp_path)
    stored = json.loads((tmp_path / "observation" / "closure-gate.json").read_text())
    assert len(stored) == limit
    assert not closure_gate_already_delivered("s0", "0:d", _state=tmp_path)
    assert closure_gate_already_delivered(f"s{limit + 9}", f"{limit + 9}:d", _state=tmp_path)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"a": 1}', ""])
def test_corrupt_memory_is_tolerated(tmp_path: Path, content: str) -> None:
    directory = tmp_path / "observation"
    directory.mkdir(mode=0o700)
    (directory / "closure-gate.json").write_text(content)
    assert not closure_gate_already_delivered("ses_a", "1:x", _state=tmp_path)
    assert record_closure_gate_delivered("ses_a", "1:x", _state=tmp_path)
    assert closure_gate_already_delivered("ses_a", "1:x", _state=tmp_path)


# --- hook level, per host --------------------------------------------------------------------


def _status_branch(readiness: object, sequence: str) -> object:
    return SimpleNamespace(
        head_frontier=SimpleNamespace(sequence=sequence, head_digest=_DIGEST),
        closure_readiness=readiness,
        task_id=new_id(IdKind.TASK),
    )


class _Harness:
    def __init__(self, tmp_path: Path, host_key: str) -> None:
        self.state = tmp_path
        self.store = LocalObservationStore(_state=tmp_path)
        commitment = self.store.workspace_commitment(str(tmp_path.resolve()))
        self.store.grant_consent(commitment)
        self.session_id = new_id(IdKind.SESSION)
        store_mapping(
            mapping_from_start_ids(
                codex_session_id=host_key,
                yoetz_task_id=new_id(IdKind.TASK),
                yoetz_session_id=self.session_id,
                yoetz_writer_id=new_id(IdKind.WRITER),
                last_frontier="0:genesis",
            ),
            _state=tmp_path,
        )
        self.readiness: object = _readiness()
        self.sequence = "5"
        self.status_calls = 0
        harness = self

        class _Client:
            async def status(self, request: object, *, deadline_ms: int | None = None) -> object:
                del request, deadline_ms
                harness.status_calls += 1
                return SimpleNamespace(root=_status_branch(harness.readiness, harness.sequence))

            async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
                del body, deadline_ms
                return observation_ingest_result_to_json(
                    ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
                )

            async def close(self) -> None:
                return None

        async def connect(_kind: object) -> _Client:
            return _Client()

        self.connect: Any = connect

    def reasons(self) -> list[str]:
        path = self.state / "observation" / "hook-diagnostics.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        return [row["reason"] for row in rows if str(row.get("reason", "")).startswith("closure_")]

    def run(self, handler: Any, event: str, body: dict[str, JsonValue]) -> Mapping[str, Any]:
        stdout = io.BytesIO()
        code = handler(
            event_name=event,
            stdin_bytes=canonical_encode({"hook_event_name": event, **body}),
            stdout=stdout,
            workspace=str(self.state),
            _state=self.state,
            connect=self.connect,
        )
        assert code == 0
        return cast(Mapping[str, Any], json.loads(stdout.getvalue().decode() or "{}"))


class _Host:
    def __init__(self, name: str) -> None:
        self.name = name

    def key(self, session: str) -> str:
        return session if self.name == "codex" else f"{self.name}:{session}"

    def handler(self) -> Any:
        return {
            "codex": observe_hooks.handle_observe,
            "claude": observe_hooks.handle_claude_observe,
            "cursor": observe_hooks.handle_cursor_observe,
        }[self.name]

    def event(self) -> str:
        return "stop" if self.name == "cursor" else "Stop"

    def body(self, session: str, *, guarded: bool = False) -> dict[str, JsonValue]:
        if self.name == "cursor":
            return {
                "conversation_id": session,
                "cursor_version": "3.17.8",
                "status": "completed",
                "loop_count": 1 if guarded else 0,
            }
        return {"session_id": session, "stop_hook_active": guarded}

    def assert_gate(self, output: Mapping[str, Any]) -> str:
        if self.name == "codex":
            assert output["decision"] == "block"
            text = output["reason"]
        elif self.name == "claude":
            assert set(output) == {"hookSpecificOutput"}
            specific = output["hookSpecificOutput"]
            assert specific["hookEventName"] == "Stop"
            text = specific["additionalContext"]
        else:
            assert set(output) == {"followup_message"}
            text = output["followup_message"]
        assert "Yoetz closure gate" in text
        assert "open obligation" in text
        return cast(str, text)


@pytest.fixture(params=["codex", "claude", "cursor"])
def host(request: pytest.FixtureRequest) -> _Host:
    return _Host(cast(str, request.param))


def _stop(h: _Harness, host: _Host, session: str, *, guarded: bool = False) -> Mapping[str, Any]:
    return h.run(host.handler(), host.event(), host.body(session, guarded=guarded))


def test_stop_continues_once_per_frontier(tmp_path: Path, host: _Host) -> None:
    session = "gate-host"
    h = _Harness(tmp_path, host.key(session))
    first = _stop(h, host, session)
    host.assert_gate(first)
    assert h.reasons() == ["closure_gate_continued"]
    assert closure_gate_already_delivered(h.session_id, f"5:{_DIGEST}", _state=tmp_path)

    second = _stop(h, host, session)
    assert "closure gate" not in json.dumps(second)
    assert h.reasons() == ["closure_gate_continued", "closure_gate_repeat_suppressed"]

    h.sequence = "6"  # the ledger moved: a new frontier may be gated once more
    third = _stop(h, host, session)
    host.assert_gate(third)


def test_loop_guard_skips_the_gate_and_the_status_read(tmp_path: Path, host: _Host) -> None:
    session = "gate-guard"
    h = _Harness(tmp_path, host.key(session))
    output = _stop(h, host, session, guarded=True)
    assert "closure gate" not in json.dumps(output)
    assert h.status_calls == 0
    assert "closure_gate_loop_guard" in h.reasons()
    assert not closure_gate_already_delivered(h.session_id, f"5:{_DIGEST}", _state=tmp_path)


def test_ready_status_does_not_gate(tmp_path: Path, host: _Host) -> None:
    session = "gate-ready"
    h = _Harness(tmp_path, host.key(session))
    h.readiness = _readiness(state="ready", actionable=())
    output = _stop(h, host, session)
    assert "closure gate" not in json.dumps(output)
    assert h.reasons() == ["closure_gate_not_required"]


def test_unreachable_status_lets_the_agent_stop(tmp_path: Path, host: _Host) -> None:
    session = "gate-down"
    h = _Harness(tmp_path, host.key(session))

    async def broken(_kind: object) -> object:
        raise OSError("service down")

    h.connect = broken
    output = _stop(h, host, session)
    assert "closure gate" not in json.dumps(output)
    # A status read that failed is recorded as unavailable, never as "nothing left to do".
    assert h.reasons() == ["closure_gate_unavailable"]


def test_closing_review_requirement_continues_with_the_final_review_repair() -> None:
    from types import SimpleNamespace

    from yoetz.cli.closure_gate import closure_gate_from_readiness

    gate = closure_gate_from_readiness(
        SimpleNamespace(
            state="action_required",
            agent_actionable=("closing_review_required",),
            open_obligation_count="0",
            receipt_blocking_finding_count="0",
        ),
        frontier_sequence="9",
        frontier_digest="sha256:" + "a" * 64,
        observation_pending=False,
    )
    assert gate is not None
    assert gate.items == ("closing_review_required",)
    assert "`final_review: true`" in gate.text
