from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import load_mapping
from yoetz.cli import observe_hooks
from yoetz.domain.observation import ObservationSource
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse


def test_claude_hook_ingress_retains_only_closed_structural_mcp_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "cwd": "/private/project",
        "hook_event_name": "PostToolUse",
        "permission_mode": "bypassPermissions",
        "session_id": "session-1",
        "tool_input": {"secret": "private prompt"},
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": {"content": "private output"},
        "tool_use_id": "tool-1",
        "transcript_path": "/private/transcript.jsonl",
    }

    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
        )
        == 0
    )
    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert sanitized == {
        "action": "claude_mcp_success",
        "capability_profile_id": "untested",
        "hook_event_name": "PostToolUse",
        "session_id": "claude:session-1",
        "success": True,
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_use_id": "tool-1",
    }
    assert captured["source"] is ObservationSource.CLAUDE_HOOK


@pytest.mark.parametrize("response_shape", ["structured", "content_blocks"])
def test_claude_successful_start_binds_only_structural_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response_shape: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    start_result: dict[str, JsonValue] = {
        "ok": True,
        "task_id": "tsk_11111111-1111-4111-8111-111111111111",
        "session_id": "ses_22222222-2222-4222-8222-222222222222",
        "writer_id": "wri_33333333-3333-4333-8333-333333333333",
        "frontier": {"sequence": "4", "head_digest": "sha256:" + "a" * 64},
    }
    tool_response: JsonValue = (
        {"structuredContent": start_result}
        if response_shape == "structured"
        else [{"type": "text", "text": canonical_encode(start_result).decode("utf-8")}]
    )
    payload: dict[str, JsonValue] = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-bind",
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": tool_response,
        "tool_use_id": "tool-bind",
    }

    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
            _state=tmp_path,
        )
        == 0
    )
    mapping = load_mapping("claude:session-bind", _state=tmp_path)
    assert mapping is not None
    assert mapping.yoetz_task_id == start_result["task_id"]
    assert mapping.yoetz_session_id == start_result["session_id"]
    assert mapping.yoetz_writer_id == start_result["writer_id"]
    assert mapping.last_frontier == "4:sha256:" + "a" * 64

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert "tool_response" not in sanitized
    assert "task_id" not in sanitized
    assert "writer_id" not in sanitized


def test_claude_failed_or_non_start_result_creates_no_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_handle_observe(**_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for index, (tool_name, response) in enumerate(
        (
            ("mcp__plugin_yoetz_yoetz__start", {"structuredContent": {"ok": False}}),
            (
                "mcp__plugin_yoetz_yoetz__status",
                {
                    "structuredContent": {
                        "ok": True,
                        "task_id": "tsk_11111111-1111-4111-8111-111111111111",
                        "session_id": "ses_22222222-2222-4222-8222-222222222222",
                        "writer_id": "wri_33333333-3333-4333-8333-333333333333",
                    }
                },
            ),
        )
    ):
        session = f"session-negative-{index}"
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session,
                    "tool_name": tool_name,
                    "tool_response": response,
                }
            ),
            stdout=io.BytesIO(),
            _state=tmp_path,
        )
        assert load_mapping(f"claude:{session}", _state=tmp_path) is None


def test_claude_capability_profile_requires_exact_evidenced_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for version, expected in (
        ("2.1.241", "claude-code-cli-local-project-2.1.241"),
        # A neighboring version whose contract was never proven, and a payload
        # naming no version at all, must both stay explicitly untested rather
        # than emit evidence for the 2.1.241 profile.
        ("2.1.240", "untested"),
        (None, "untested"),
    ):
        payload: dict[str, JsonValue] = {
            "hook_event_name": "Stop",
            "session_id": "session-version",
        }
        if version is not None:
            payload["claude_code_version"] = version
        assert (
            observe_hooks.handle_claude_observe(
                event_name="Stop",
                stdin_bytes=canonical_encode(payload),
                stdout=io.BytesIO(),
            )
            == 0
        )
        assert captured[-1]["capability_profile_id"] == expected


def test_claude_read_guidance_calls_survive_the_scoped_ingress_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-guidance",
        "tool_name": "mcp__plugin_yoetz_yoetz__read_guidance",
        "tool_use_id": "tool-guidance",
    }
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert captured[0]["tool_name"] == "mcp__plugin_yoetz_yoetz__read_guidance"


def test_claude_failure_discards_raw_error_and_bare_mcp_names_are_negative_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    failure: dict[str, JsonValue] = {
        "error": "secret exception",
        "hook_event_name": "PostToolUseFailure",
        "session_id": "session-2",
        "tool_input": {"prompt": "private"},
        "tool_name": "mcp__plugin_yoetz_yoetz__publish_work",
        "tool_use_id": "tool-2",
    }
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUseFailure",
            stdin_bytes=canonical_encode(failure),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert captured[0]["hook_event_name"] == "PostToolUse"
    assert captured[0]["success"] is False
    assert "error" not in captured[0]

    bare = {**failure, "tool_name": "mcp__yoetz__publish_work"}
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUseFailure",
            stdin_bytes=canonical_encode(cast(JsonValue, bare)),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert len(captured) == 1


def test_claude_session_source_is_closed_and_content_never_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for source in ("resume", "attacker-controlled"):
        payload: dict[str, JsonValue] = {
            "cwd": "/private/project",
            "hook_event_name": "SessionStart",
            "session_id": f"session-{source}",
            "source": source,
            "transcript_path": "/private/transcript.jsonl",
        }
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
        )

    assert captured[0]["action"] == "claude_session_resume"
    assert captured[1]["action"] == "claude_session"
    for item in captured:
        assert "cwd" not in item
        assert "source" not in item
        assert "transcript_path" not in item


def test_claude_stop_retains_only_the_boolean_loop_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for value in (True, False, "true"):
        observe_hooks.handle_claude_observe(
            event_name="Stop",
            stdin_bytes=canonical_encode(
                {
                    "hook_event_name": "Stop",
                    "session_id": "session-stop",
                    "stop_hook_active": value,
                }
            ),
            stdout=io.BytesIO(),
        )

    assert captured[0]["stop_hook_active"] is True
    assert "stop_hook_active" not in captured[1]
    assert "stop_hook_active" not in captured[2]
