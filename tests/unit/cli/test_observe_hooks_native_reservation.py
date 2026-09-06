"""Regression tests for native hook content's foreground drain reservation."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import handle_claude_observe
from yoetz.domain.observation import (
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationStatusQuery,
    observation_ingest_result_to_json,
)
from yoetz.domain.observation_profiles import CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID


def test_native_content_reservation_beats_a_sweeper_started_during_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(
        workspace,
        content_capture_profiles=(CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,),
    )
    foreground_bodies: list[Mapping[str, object]] = []
    background_connects: list[object] = []
    contender_finished = threading.Event()
    drain_calls: list[Mapping[str, object]] = []
    runner_calls: list[object] = []

    class ForegroundClient:
        async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
            del deadline_ms
            foreground_bodies.append(cast(Mapping[str, object], body))
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def foreground_connect(_kind: object) -> ForegroundClient:
        return ForegroundClient()

    async def background_connect(_kind: object) -> ForegroundClient:
        background_connects.append(_kind)
        return ForegroundClient()

    def refresh_during_reserved_pass(
        self: LocalObservationStore, commitment: str, **_kwargs: object
    ) -> None:
        def contend() -> None:
            async def drain() -> None:
                await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
                    self,
                    workspace_commitment=commitment,
                    codex_session_id="claude:foreground-reservation",
                    connect=background_connect,  # type: ignore[arg-type]
                    _state=tmp_path,
                )

            asyncio.run(drain())
            contender_finished.set()

        thread = threading.Thread(target=contend)
        thread.start()
        assert contender_finished.wait(timeout=2.0)
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    monkeypatch.setattr(LocalObservationStore, "refresh_advice", refresh_during_reserved_pass)
    original_drain = observe_hooks_module._drain_outbox  # pyright: ignore[reportPrivateUsage]

    async def recording_drain(*args: object, **kwargs: object) -> None:
        drain_calls.append(kwargs)
        await original_drain(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(observe_hooks_module, "_drain_outbox", recording_drain)

    def run_async(coro: object) -> object:
        runner_calls.append(coro)
        return asyncio.run(coro())  # type: ignore[operator]

    assert (
        handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": "foreground-reservation",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_use_id": "call-foreground-reservation",
                    "tool_response": "foreground transient output",
                    "exit_status": 0,
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            connect=foreground_connect,  # type: ignore[arg-type]
            run_async=run_async,  # type: ignore[arg-type]
            observation_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        )
        == 0
    )

    # The sweeper was triggered while advice refreshed, but the foreground
    # reservation kept it from claiming the row before transient content was
    # attached to the foreground ingest request.
    assert background_connects == []
    assert runner_calls
    assert len(drain_calls) == 2
    assert drain_calls[-1]["drain_lease_owned"] is True
    assert len(foreground_bodies) == 1
    assert foreground_bodies[0].get("content_chunks")
    assert store.list_pending_outbox_rows(workspace) == ()
    assert "content_capture_unavailable" not in store.status(ObservationStatusQuery(workspace)).gaps


def test_native_content_reservation_releases_after_cancelled_drain(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(
        workspace,
        content_capture_profiles=(CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,),
    )

    def cancelled_runner(_runner: object) -> object:
        raise asyncio.CancelledError()

    assert (
        handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=json.dumps(
                {
                    "session_id": "cancelled-reservation",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_use_id": "call-cancelled-reservation",
                    "tool_response": "cancelled transient output",
                    "exit_status": 0,
                }
            ).encode(),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            run_async=cancelled_runner,  # type: ignore[arg-type]
            observation_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        )
        == 0
    )

    # The cancelled runner interrupted the foreground drain before it could
    # release normally; the outer finally must leave the workspace usable.
    with store.drain_lease(workspace) as owned:
        assert owned is True
