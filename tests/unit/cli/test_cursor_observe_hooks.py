from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from yoetz.cli import observe_hooks
from yoetz.domain.observation import ObservationSource
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse


def test_cursor_hook_ingress_drops_every_content_and_identity_denylist_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "conversation_id": "cursor-session-1",
        "cursor_version": "3.17.9",
        "generation_id": "generation-1",
        "hook_event_name": "afterMCPExecution",
        "model_id": "cursor-grok-4.6-medium",
        "model_params": [{"id": "effort", "value": "medium"}],
        "tool_name": "mcp__yoetz__start",
        "tool_input": "secret prompt and arguments",
        "result_json": "private tool result",
        "transcript_path": "/private/transcript.jsonl",
        "user_email": "private@example.com",
        "workspace_roots": ["/private/project"],
    }
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterMCPExecution",
            stdin_bytes=canonical_encode(payload),
            workspace=".",
        )
        == 0
    )

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert sanitized["session_id"] == "cursor:cursor-session-1"
    assert sanitized["cursor_version"] == "3.17.9"
    assert sanitized["model_id"] == "cursor-grok-4.6-medium"
    assert sanitized["model_effort"] == "medium"
    assert captured["source"] is ObservationSource.CURSOR_HOOK
    forbidden = {
        "tool_input",
        "result_json",
        "transcript_path",
        "user_email",
        "workspace_roots",
        "prompt",
        "response",
        "file_path",
        "edits",
    }
    assert forbidden.isdisjoint(sanitized)
