from __future__ import annotations

import io
from collections.abc import Mapping
from typing import cast

import pytest

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
