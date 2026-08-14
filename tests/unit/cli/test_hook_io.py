"""Event-specific Codex hook stdout contracts (#222)."""

from __future__ import annotations

from yoetz.cli.hook_io import context_output


def test_session_start_and_post_tool_use_emit_additional_context() -> None:
    for event in ("SessionStart", "PostToolUse", "UserPromptSubmit"):
        assert context_output(event, "  bounded advice  ") == {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "bounded advice",
            }
        }


def test_stop_and_subagent_stop_emit_block_decision_not_hook_specific_output() -> None:
    for event in ("Stop", "SubagentStop"):
        emitted = context_output(event, "refresh status before an exact-frontier check")
        assert emitted == {
            "decision": "block",
            "reason": "refresh status before an exact-frontier check",
        }
        assert "hookSpecificOutput" not in emitted


def test_session_end_and_unknown_events_emit_empty_object() -> None:
    assert context_output("SessionEnd", "would be discarded by the host") == {}
    assert context_output("PreCompact", "never an advice channel") == {}
    assert context_output("Stop", "   ") == {}
    assert context_output("SessionStart", "") == {}
