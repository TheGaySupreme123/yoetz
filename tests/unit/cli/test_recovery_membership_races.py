"""Deterministic membership and recovery interleavings for hook observation."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import (
    acquire_session_lock,
    acquire_workspace_recovery_lock,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import ServiceConnector, handle_observe
from yoetz.domain.observation import (
    ObservationEnvelope,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    observation_ingest_result_to_json,
)
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel, StartRequest

_START_IDS = {
    "task_id": "tsk_1b4e28ba-2fa1-4d3b-8f0a-0c1d2e3f4a5b",
    "session_id": "ses_1b4e28ba-2fa1-4d3b-8f0a-0c1d2e3f4a5c",
    "writer_id": "wri_1b4e28ba-2fa1-4d3b-8f0a-0c1d2e3f4a5d",
}
_SUCCESSOR_IDS = {
    "task_id": _START_IDS["task_id"],
    "session_id": "ses_2b4e28ba-2fa1-4d3b-8f0a-0c1d2e3f4a5c",
    "writer_id": "wri_2b4e28ba-2fa1-4d3b-8f0a-0c1d2e3f4a5d",
}

_HostCase = tuple[
    ObservationSource,
    Literal["claude", "codex", "cursor"],
    str,
]
_HOST_CASES: tuple[_HostCase, ...] = (
    (ObservationSource.CODEX_HOOK, "codex", "codex"),
    (ObservationSource.CLAUDE_HOOK, "claude", "claude:"),
    (ObservationSource.CURSOR_HOOK, "cursor", "cursor:"),
)


class _RecoveryBarrierClient:
    """Let a recovery attach pause after final local validation and before RPC return."""

    def __init__(self) -> None:
        self.requests: list[StartRequest] = []
        self.rpc_started = threading.Event()
        self.release = threading.Event()
        self._start_count = 0

    async def start(self, request: object, *, deadline_ms: int | None = None) -> object:
        del deadline_ms
        assert isinstance(request, StartRequest)
        self.requests.append(request)
        self._start_count += 1
        if self._start_count == 1:
            assert request.mode == "create_or_attach"
            self.rpc_started.set()
            await asyncio.to_thread(self.release.wait, 5)
            return OperationFailureModel.model_validate(
                {
                    "protocol_version": "0.1",
                    "schema_version": "1.0.0",
                    "ok": False,
                    "error": {
                        "code": PublicErrorCode.SESSION_CONFLICT.value,
                        "message": "workspace occupied",
                        "retryable": False,
                        "correlation_id": new_id(IdKind.CORRELATION),
                        "safe_details": {"reason_code": "workspace_task_exists"},
                    },
                }
            )
        assert request.mode == "attach"
        return SimpleNamespace(
            ok=True,
            frontier=SimpleNamespace(sequence="4", head_digest="sha256:" + "b" * 64),
            **_SUCCESSOR_IDS,
        )

    async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
        del body, deadline_ms
        return observation_ingest_result_to_json(
            ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
        )

    async def close(self) -> None:
        return None


class _StartOkClient:
    """Return a real start-shaped response and acknowledge a bounded outbox drain."""

    def __init__(self) -> None:
        self.requests: list[StartRequest] = []
        self.observation_ingest_calls: list[object] = []

    async def start(self, request: object, *, deadline_ms: int | None = None) -> object:
        del deadline_ms
        assert isinstance(request, StartRequest)
        assert request.mode == "create_or_attach"
        self.requests.append(request)
        return SimpleNamespace(
            ok=True,
            frontier=SimpleNamespace(sequence="3", head_digest="sha256:" + "a" * 64),
            **_SUCCESSOR_IDS,
        )

    async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
        del deadline_ms
        self.observation_ingest_calls.append(body)
        return observation_ingest_result_to_json(
            ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
        )

    async def close(self) -> None:
        return None


def _connector(client: object) -> ServiceConnector:
    async def connect(_kind: object):
        return client

    return cast(ServiceConnector, connect)


def _workspace_and_store(tmp_path: Path) -> tuple[Path, Path, LocalObservationStore, str]:
    state = tmp_path / "state"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    store = LocalObservationStore(_state=state)
    workspace = store.workspace_commitment(str(workspace_dir.resolve()))
    store.grant_consent(workspace)
    return state, workspace_dir, store, workspace


def _seed_ended_predecessor(
    store: LocalObservationStore,
    workspace: str,
    predecessor: str,
    *,
    state: Path,
) -> None:
    predecessor_commitment = store.bind_codex_session(workspace, predecessor)
    store.note_session_end(workspace, predecessor_commitment)
    observe_hooks_module.store_mapping(
        observe_hooks_module.mapping_from_start_ids(
            codex_session_id=predecessor,
            yoetz_task_id=_START_IDS["task_id"],
            yoetz_session_id=_START_IDS["session_id"],
            yoetz_writer_id=_START_IDS["writer_id"],
            last_frontier=None,
        ),
        _state=state,
    )


def _payload(session_id: str, event: str, *, tool_name: str = "shell") -> bytes:
    return json.dumps(
        {
            "session_id": session_id,
            "hook_event_name": event,
            "tool_name": tool_name,
            "tool_call_id": f"race-{session_id}",
            "exit_status": 0,
        }
    ).encode()


def _yoetz_mutation_tool(source: ObservationSource) -> str:
    if source is ObservationSource.CLAUDE_HOOK:
        return "mcp__plugin_yoetz_yoetz__publish_work"
    if source is ObservationSource.CURSOR_HOOK:
        return "yoetz:publish_work"
    return "mcp__yoetz__publish_work"


def _assert_retained_for_session(
    store: LocalObservationStore,
    workspace: str,
    session_id: str,
    *,
    event_kind: str,
) -> None:
    commitment = store.session_commitment(session_id)
    retained = [
        envelope
        for envelope in store.list_envelopes(workspace)
        if envelope.session_commitment == commitment
    ]
    assert retained
    assert retained[-1].event_kind == event_kind


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_new_session_during_recovery_is_retained_until_membership_can_bind(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A raw session arriving after recovery validation stays in its target workspace."""

    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    predecessor = f"{prefix}ended-membership-race"
    successor = f"{prefix}recovery-membership-race"
    raw_session = f"{prefix}raw-membership-race"
    _seed_ended_predecessor(store, workspace, predecessor, state=state)
    client = _RecoveryBarrierClient()
    results: list[int] = []
    errors: list[BaseException] = []

    def run_recovery() -> None:
        try:
            results.append(
                handle_observe(
                    event_name="SessionStart",
                    stdin_bytes=_payload(successor, "SessionStart"),
                    stdout=io.BytesIO(),
                    workspace=str(workspace_dir.resolve()),
                    _state=state,
                    connect=_connector(client),
                    source=source,
                    _output_event_name="sessionStart" if source is ObservationSource.CURSOR_HOOK else None,
                )
            )
        except BaseException as error:  # pragma: no cover - surfaced by the assertion below
            errors.append(error)

    worker = threading.Thread(target=run_recovery)
    worker.start()
    assert client.rpc_started.wait(5), "recovery did not reach the post-validation RPC barrier"

    raw_output = io.BytesIO()
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=_payload(raw_session, "PostToolUse"),
            stdout=raw_output,
            workspace=str(workspace_dir.resolve()),
            _state=state,
            skip_service=True,
            source=source,
            _output_event_name="afterMCPExecution"
            if source is ObservationSource.CURSOR_HOOK
            else None,
        )
        == 0
    )

    during = LocalObservationStore(_state=state)
    _assert_retained_for_session(during, workspace, raw_session, event_kind="PostToolUse")
    assert raw_session not in during.codex_sessions_for_workspace(workspace)

    client.release.set()
    worker.join(5)
    assert not worker.is_alive(), "recovery did not leave the RPC barrier"
    assert errors == []
    assert results == [0]

    after = LocalObservationStore(_state=state)
    assert successor in after.codex_sessions_for_workspace(workspace)
    assert raw_session not in after.codex_sessions_for_workspace(workspace)
    successor_mapping = observe_hooks_module.load_mapping(successor, _state=state)
    assert successor_mapping is not None
    assert successor_mapping.yoetz_session_id == _SUCCESSOR_IDS["session_id"]


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_session_lock_owned_still_requires_workspace_reservation(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """The clear-route session lock does not authorize a membership mutation by itself."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    session_id = f"{prefix}session-lock-owned-race"
    assert store.codex_sessions_for_workspace(workspace) == ()
    with acquire_workspace_recovery_lock(workspace, _state=state) as workspace_owned:
        assert workspace_owned
        with acquire_session_lock(session_id, _state=state) as session_owned:
            assert session_owned
            assert (
                handle_observe(
                    event_name="SessionStart",
                    stdin_bytes=json.dumps(
                        {
                            **json.loads(_payload(session_id, "SessionStart")),
                            "source": "clear",
                        }
                    ).encode(),
                    stdout=io.BytesIO(),
                    workspace=str(workspace_dir.resolve()),
                    _state=state,
                    skip_service=True,
                    source=source,
                    _output_event_name="sessionStart"
                    if source is ObservationSource.CURSOR_HOOK
                    else None,
                    _session_lock_owned=True,
                )
                == 0
            )

    after = LocalObservationStore(_state=state)
    assert session_id not in after.codex_sessions_for_workspace(workspace)
    _assert_retained_for_session(after, workspace, session_id, event_kind="SessionStart")


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_busy_membership_keeps_event_in_explicit_workspace_with_two_active_workspaces(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A contended workspace reservation cannot route a raw event to its sibling workspace."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    sibling_dir = tmp_path / "sibling-workspace"
    sibling_dir.mkdir()
    sibling = store.workspace_commitment(str(sibling_dir.resolve()))
    store.grant_consent(sibling)
    session_id = f"{prefix}two-workspace-race"

    with acquire_workspace_recovery_lock(workspace, _state=state) as workspace_owned:
        assert workspace_owned
        with acquire_session_lock(session_id, _state=state) as session_owned:
            assert session_owned
            assert (
                handle_observe(
                    event_name="PostToolUse",
                    stdin_bytes=_payload(session_id, "PostToolUse"),
                    stdout=io.BytesIO(),
                    workspace=str(workspace_dir.resolve()),
                    _state=state,
                    skip_service=True,
                    source=source,
                    _output_event_name="afterMCPExecution"
                    if source is ObservationSource.CURSOR_HOOK
                    else None,
                )
                == 0
            )

    after = LocalObservationStore(_state=state)
    _assert_retained_for_session(after, workspace, session_id, event_kind="PostToolUse")
    assert after.list_envelopes(sibling) == ()
    assert session_id not in after.codex_sessions_for_workspace(workspace)
    assert session_id not in after.codex_sessions_for_workspace(sibling)


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_busy_session_start_is_retained_and_explains_unmapped_membership(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A busy SessionStart keeps its envelope and emits the local-only diagnosis."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    session_id = f"{prefix}session-start-race"
    output = io.BytesIO()
    assert store.codex_sessions_for_workspace(workspace) == ()

    with acquire_workspace_recovery_lock(workspace, _state=state) as workspace_owned:
        assert workspace_owned
        assert (
            handle_observe(
                event_name="SessionStart",
                stdin_bytes=_payload(session_id, "SessionStart"),
                stdout=output,
                workspace=str(workspace_dir.resolve()),
                _state=state,
                skip_service=True,
                source=source,
                _output_event_name="sessionStart"
                if source is ObservationSource.CURSOR_HOOK
                else None,
            )
            == 0
        )

    after = LocalObservationStore(_state=state)
    assert session_id not in after.codex_sessions_for_workspace(workspace)
    _assert_retained_for_session(after, workspace, session_id, event_kind="SessionStart")
    rendered = json.loads(output.getvalue())
    assert "no ledger task is mapped yet" in json.dumps(rendered)


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_session_start_reconciles_deferred_membership_and_drains_without_followup_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A SessionStart that loses reservation ownership repairs itself before returning."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    session_id = f"{prefix}session-start-reconcile-race"
    held = threading.Event()
    release = threading.Event()
    holder_released = threading.Event()
    holder_errors: list[BaseException] = []

    def hold_workspace_reservation() -> None:
        try:
            with acquire_workspace_recovery_lock(workspace, _state=state) as owned:
                assert owned
                held.set()
                assert release.wait(5)
        except BaseException as error:  # pragma: no cover - surfaced by the assertion below
            holder_errors.append(error)
        finally:
            holder_released.set()

    holder = threading.Thread(target=hold_workspace_reservation)
    holder.start()
    assert held.wait(5)

    # A deliverable structural event arrives while the membership reservation is busy.
    assert (
        handle_observe(
            event_name="PostToolUse",
            stdin_bytes=_payload(
                session_id,
                "PostToolUse",
                tool_name=_yoetz_mutation_tool(source),
            ),
            stdout=io.BytesIO(),
            workspace=str(workspace_dir.resolve()),
            _state=state,
            skip_service=True,
            source=source,
            _output_event_name="afterMCPExecution"
            if source is ObservationSource.CURSOR_HOOK
            else None,
        )
        == 0
    )
    assert len(store.list_pending_outbox_rows(workspace)) == 1

    original_ingest = LocalObservationStore.ingest
    ingest_seen = threading.Event()

    def release_after_ingest(
        target: LocalObservationStore,
        envelope: ObservationEnvelope,
        *,
        workspace_commitment: str | None = None,
    ) -> ObservationIngestResult:
        try:
            return original_ingest(
                target,
                envelope,
                workspace_commitment=workspace_commitment,
            )
        finally:
            # The hook has captured its SessionStart while the reservation is held;
            # releasing now lets the same invocation perform its post-capture repair.
            release.set()
            assert holder_released.wait(5)
            ingest_seen.set()

    monkeypatch.setattr(LocalObservationStore, "ingest", release_after_ingest)
    client = _StartOkClient()
    results: list[int] = []
    errors: list[BaseException] = []

    def run_session_start() -> None:
        try:
            results.append(
                handle_observe(
                    event_name="SessionStart",
                    stdin_bytes=_payload(session_id, "SessionStart"),
                    stdout=io.BytesIO(),
                    workspace=str(workspace_dir.resolve()),
                    _state=state,
                    connect=_connector(client),
                    source=source,
                    _output_event_name="sessionStart"
                    if source is ObservationSource.CURSOR_HOOK
                    else None,
                )
            )
        except BaseException as error:  # pragma: no cover - surfaced by the assertion below
            errors.append(error)

    worker = threading.Thread(target=run_session_start)
    worker.start()
    assert ingest_seen.wait(5), "SessionStart did not reach local ingest"
    worker.join(5)
    holder.join(5)
    assert not worker.is_alive(), "SessionStart did not finish after reservation release"
    assert not holder.is_alive(), "reservation holder did not release"
    assert holder_errors == []
    assert errors == []
    assert results == [0]

    after = LocalObservationStore(_state=state)
    assert session_id in after.codex_sessions_for_workspace(workspace)
    assert after.list_pending_outbox_rows(workspace) == ()
    assert client.observation_ingest_calls
    _assert_retained_for_session(after, workspace, session_id, event_kind="SessionStart")


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_busy_resume_clears_ended_lifecycle_without_followup_hook(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A deferred resume must clear the prior ended fence before the host disappears."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    session_id = f"{prefix}resume-ended-race"
    session_commitment = store.bind_codex_session(workspace, session_id)
    store.note_session_end(workspace, session_commitment)
    assert store.codex_session_ended(workspace, session_id)

    with acquire_workspace_recovery_lock(workspace, _state=state) as workspace_owned:
        assert workspace_owned
        assert (
            handle_observe(
                event_name="SessionStart",
                stdin_bytes=json.dumps(
                    {
                        **json.loads(_payload(session_id, "SessionStart")),
                        "source": "resume",
                    }
                ).encode(),
                stdout=io.BytesIO(),
                workspace=str(workspace_dir.resolve()),
                _state=state,
                skip_service=True,
                source=source,
                _output_event_name="sessionStart"
                if source is ObservationSource.CURSOR_HOOK
                else None,
            )
            == 0
        )

    after = LocalObservationStore(_state=state)
    _assert_retained_for_session(after, workspace, session_id, event_kind="SessionStart")
    assert not after.codex_session_ended(workspace, session_id)


@pytest.mark.parametrize(
    ("source", "harness_id", "prefix"),
    _HOST_CASES,
    ids=("codex", "claude", "cursor"),
)
def test_busy_session_end_records_lifecycle_without_a_followup_hook(
    tmp_path: Path,
    source: ObservationSource,
    harness_id: Literal["claude", "codex", "cursor"],
    prefix: str,
) -> None:
    """A retained SessionEnd must still close the bound lifecycle after contention clears."""

    del harness_id
    state, workspace_dir, store, workspace = _workspace_and_store(tmp_path)
    session_id = f"{prefix}session-end-race"
    session_commitment = store.bind_codex_session(workspace, session_id)

    with acquire_workspace_recovery_lock(workspace, _state=state) as workspace_owned:
        assert workspace_owned
        assert (
            handle_observe(
                event_name="SessionEnd",
                stdin_bytes=_payload(session_id, "SessionEnd"),
                stdout=io.BytesIO(),
                workspace=str(workspace_dir.resolve()),
                _state=state,
                skip_service=True,
                source=source,
                _output_event_name="sessionEnd"
                if source is ObservationSource.CURSOR_HOOK
                else None,
            )
            == 0
        )

    after = LocalObservationStore(_state=state)
    _assert_retained_for_session(after, workspace, session_id, event_kind="SessionEnd")
    assert after.codex_session_ended(workspace, session_id)
    assert session_commitment == after.session_commitment(session_id)
