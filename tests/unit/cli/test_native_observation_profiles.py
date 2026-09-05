"""Opt-in native-host ordinary observation and content-consent tests."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.claude_code_integration import (
    CLAUDE_CODE_ORDINARY_HOOK_EVENTS,
    render_claude_code_plugin,
)
from yoetz.adapters.integrations.cursor_integration import (
    CURSOR_ORDINARY_HOOK_EVENTS,
    render_cursor_plugin,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe as observe_cli
from yoetz.cli import observe_hooks
from yoetz.domain.observation import ObservationSource
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_HOOK_MAPPING_VERSION,
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_HOOK_MAPPING_VERSION,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.values import JsonObject, JsonValue
from yoetz.ports.plugin_artifacts import PluginFormatProfile
from yoetz.protocol.canonical import canonical_encode, strict_json_parse


def test_content_profiles_are_independent_and_user_controls_are_reversible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = str(tmp_path)
    store = LocalObservationStore(_state=tmp_path / "state")
    commitment = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(commitment)

    assert (
        observe_cli.enable_observation_content(
            workspace=workspace,
            profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
            _state=tmp_path / "state",
        )
        == 0
    )
    assert (
        observe_cli.enable_observation_content(
            workspace=workspace,
            profile=CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
            _state=tmp_path / "state",
        )
        == 0
    )
    assert LocalObservationStore(_state=tmp_path / "state").content_capture_profiles(commitment) == (
        CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
        CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
    )

    assert (
        observe_cli.disable_observation_content(
            workspace=workspace,
            profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
            _state=tmp_path / "state",
        )
        == 0
    )
    assert LocalObservationStore(_state=tmp_path / "state").content_capture_profiles(commitment) == (
        CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
    )
    capsys.readouterr()  # discard the human-readable enable/disable lines
    assert (
        observe_cli.observation_content_status(
            workspace=workspace,
            json_output=True,
            _state=tmp_path / "state",
        )
        == 0
    )
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is True
    assert status["content_capture_profiles"] == [CURSOR_ORDINARY_OBSERVATION_PROFILE_ID]


def test_claude_ordinary_profile_forwards_redacted_content_only_when_explicitly_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "hook_event_name": "PostToolUse",
        "session_id": "claude-ordinary-session",
        "tool_name": "Bash",
        "tool_use_id": "tool-ordinary-1",
        "tool_input": JsonObject({"command": "printf ordinary"}),
        "tool_response": JsonObject({"output": "ordinary result"}),
        "error": "secret-error-text",
    }
    observe_hooks.handle_claude_observe(
        event_name="PostToolUse",
        stdin_bytes=canonical_encode(payload),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path / "state",
        observation_profile=CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    )
    structural = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(structural, dict)
    assert structural["capability_profile_id"] == CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    assert structural["mapping_hint"] == CLAUDE_CODE_ORDINARY_HOOK_MAPPING_VERSION
    assert structural["tool_name"] == "Bash"
    assert structural["tool_use_id"] == "tool-ordinary-1"
    assert "secret-error-text" not in canonical_encode(structural).decode()
    assert captured["_content_capture_profile"] == CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    assert canonical_encode(cast(JsonValue, captured["_content_payload"])) == canonical_encode(
        payload
    )
    assert captured["source"] is ObservationSource.CLAUDE_HOOK


def test_cursor_ordinary_profile_uses_generic_tool_identity_and_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "hook_event_name": "postToolUseFailure",
        "session_id": "cursor-ordinary-session",
        "conversation_id": "cursor-ordinary-conversation",
        "tool_name": "shell",
        "tool_use_id": "tool-ordinary-2",
        "result": JsonObject({"output": "failure output"}),
        "exit_code": 7,
        "failure_type": "error",
        "workspace_roots": (str(tmp_path),),
    }
    observe_hooks.handle_cursor_observe(
        event_name="postToolUseFailure",
        stdin_bytes=canonical_encode(payload),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path / "state",
        observation_profile=CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
    )
    structural = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(structural, dict)
    assert structural["capability_profile_id"] == CURSOR_ORDINARY_OBSERVATION_PROFILE_ID
    assert structural["mapping_hint"] == CURSOR_ORDINARY_HOOK_MAPPING_VERSION
    assert structural["action"] == "cursor_tool_failure"
    assert structural["tool_call_id"] == "tool-ordinary-2"
    assert structural["success"] is False
    assert structural["exit_status"] == 7
    assert captured["_content_capture_profile"] == CURSOR_ORDINARY_OBSERVATION_PROFILE_ID
    assert canonical_encode(cast(JsonValue, captured["_content_payload"])) == canonical_encode(
        payload
    )


def test_renderers_keep_old_default_and_emit_bounded_ordinary_subscriptions(tmp_path: Path) -> None:
    launcher = tmp_path / "yoetz"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)

    claude = render_claude_code_plugin(
        yoetz_launcher=launcher,
        observation_profile="ordinary",
    )
    claude_hooks = json.loads(claude.members["hooks/hooks.json"])["hooks"]
    assert tuple(sorted(claude_hooks)) == CLAUDE_CODE_ORDINARY_HOOK_EVENTS
    assert all(
        "--observation-profile claude-code-ordinary-observation-v1" in item["command"]
        for definitions in claude_hooks.values()
        for item in definitions[0]["hooks"]
    )
    assert "FileChanged" not in claude_hooks
    assert "PostToolBatch" not in claude_hooks

    cursor = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        yoetz_launcher=launcher,
        observation_profile="ordinary",
    )
    cursor_hooks = json.loads(cursor.members["hooks/hooks.json"])["hooks"]
    assert tuple(sorted(cursor_hooks)) == CURSOR_ORDINARY_HOOK_EVENTS
    assert all(
        "--observation-profile cursor-ordinary-observation-v1" in item["command"]
        for definitions in cursor_hooks.values()
        for item in definitions
    )
    assert "beforeShellExecution" not in cursor_hooks
    assert "afterFileEdit" not in cursor_hooks
