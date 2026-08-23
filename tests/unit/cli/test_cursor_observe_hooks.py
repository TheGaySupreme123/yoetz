from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
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
        "cursor_version": "3.17.8",
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
    assert sanitized["cursor_version"] == "3.17.8"
    assert sanitized["model_id"] == "cursor-grok-4.6-medium"
    assert sanitized["model_effort"] == "medium"
    assert captured["source"] is ObservationSource.CURSOR_HOOK
    assert "result_status" not in sanitized
    assert "success" not in sanitized
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


def test_cursor_file_edit_uses_keyed_path_commitment_and_drops_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    path = "/private/project/secret.py"
    payload: dict[str, JsonValue] = {
        "conversation_id": "cursor-session-2",
        "hook_event_name": "afterFileEdit",
        "file_path": path,
        "edits": [{"old_string": "secret", "new_string": "private"}],
    }

    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterFileEdit",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
            _state=tmp_path,
        )
        == 0
    )

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    commitment = sanitized["changed_paths_digest"]
    assert isinstance(commitment, str) and commitment.startswith("hmac-sha256:")
    assert path not in canonical_encode(cast(JsonValue, sanitized)).decode("utf-8")
    assert "result_status" not in sanitized
    assert "success" not in sanitized


def test_cursor_session_prefix_reserves_space_inside_token_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        payload = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(payload, Mapping)
        captured.append(payload)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)

    for length in (121, 122):
        payload: dict[str, JsonValue] = {
            "conversation_id": "s" * length,
            "hook_event_name": "sessionStart",
        }
        assert (
            observe_hooks.handle_cursor_observe(
                event_name="sessionStart",
                stdin_bytes=canonical_encode(payload),
                stdout=io.BytesIO(),
                workspace=".",
            )
            == 0
        )

    assert len(captured) == 1
    assert captured[0]["session_id"] == "cursor:" + ("s" * 121)
    assert len(cast(str, captured[0]["session_id"])) == 128
