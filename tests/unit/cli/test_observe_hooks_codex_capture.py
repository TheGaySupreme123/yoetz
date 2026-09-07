"""Regression tests for the profileless Codex capture lane."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import map_hook_payload_to_envelope
from yoetz.domain.observation import (
    OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
    ObservationContentChunk,
    ObservationContentKind,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    observation_ingest_result_to_json,
)


@pytest.mark.anyio
async def test_codex_content_is_staged_before_a_blocked_fifo_row(tmp_path: Path) -> None:
    """A stalled structural head must not consume profileless Codex bytes."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = "codex:blocked-fifo"
    session_commitment = store.bind_codex_session(workspace, session)

    def envelope(ordinal: int, identity: str):
        return map_hook_payload_to_envelope(
            "PostToolUse",
            {
                "session_id": session,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": identity,
                "exit_status": 0,
            },
            session_commitment=session_commitment,
            event_ordinal=ordinal,
            key_material=store.key_material(),
            source=ObservationSource.CODEX_HOOK,
        )

    blocked = envelope(1, "blocked-head")
    current = envelope(2, "codex-content")
    store.enqueue_outbox(workspace, session, blocked)
    store.enqueue_outbox(workspace, session, current)
    chunks = (
        ObservationContentChunk(
            content_kind=ObservationContentKind.TOOL_OUTPUT,
            correlation_identity=f"{current.source_identity}:tool-output",
            source_commitment=current.cursor.last_source_commitment,
            media_type="text/plain",
            part_index=0,
            part_count=1,
            content=b"profileless codex output",
        ),
    )

    capture_started = asyncio.Event()
    release_head = asyncio.Event()
    calls: list[Mapping[str, object]] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int) -> object:
            del deadline_ms
            request = cast(Mapping[str, object], body)
            calls.append(request)
            if request.get("capture_only") is True:
                capture_started.set()
                return observation_ingest_result_to_json(
                    ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
                        None,
                    )
                )
            envelope_body = cast(Mapping[str, object], request["envelope"])
            if envelope_body.get("source_identity") == blocked.source_identity:
                await release_head.wait()
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object) -> Client:
        return Client()

    drain = asyncio.create_task(
        observe_hooks_module._drain_outbox(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            store,
            workspace_commitment=workspace,
            codex_session_id=session,
            content_by_source_identity={current.source_identity: chunks},
            content_capture_profile=None,
            connect=connect,  # type: ignore[arg-type]
            priority_source_identity=current.source_identity,
            monotonic=lambda: 0.0,
            _state=tmp_path,
        )
    )
    try:
        await asyncio.wait_for(capture_started.wait(), timeout=1.0)
        assert calls[0].get("capture_only") is True
        body = calls[0]
        assert body.get("content_capture_profile") is None
        assert cast(Mapping[str, object], body["envelope"])["source"] == "codex_hook"
    finally:
        release_head.set()
        await asyncio.wait_for(drain, timeout=1.0)

    assert store.list_pending_outbox_rows(workspace) == ()
