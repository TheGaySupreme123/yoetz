"""Event-specific Codex hook stdout contracts (#222)."""

from __future__ import annotations

import io

from yoetz.cli.hook_io import (
    claude_context_output,
    context_output,
    cursor_context_output,
    stdout_json,
)


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


def test_cursor_session_start_emits_cursor_native_additional_context() -> None:
    assert cursor_context_output("sessionStart", "  bounded advice  ") == {
        "additional_context": "bounded advice"
    }


def test_cursor_stop_does_not_auto_submit_a_followup_message() -> None:
    assert cursor_context_output("stop", "submit this as a new user message") == {}


def test_cursor_stop_followup_is_explicitly_opt_in() -> None:
    assert cursor_context_output(
        "stop", "  submit this as a new user message  ", allow_stop_followup=True
    ) == {"followup_message": "submit this as a new user message"}


def test_cursor_outputless_and_unknown_events_emit_empty_object() -> None:
    for event in ("afterFileEdit", "afterMCPExecution", "sessionEnd", "unknown"):
        assert cursor_context_output(event, "advice that has no output channel") == {}


def test_cursor_context_is_bounded_by_the_codex_context_limit() -> None:
    advice = "x" * 2_001
    assert cursor_context_output("sessionStart", advice) == {"additional_context": "x" * 2_000}


def test_codex_context_output_keeps_canonical_stdout_bytes() -> None:
    stream = io.BytesIO()
    assert stdout_json(context_output("SessionStart", "bounded advice"), stream)
    assert (
        stream.getvalue() == b'{"hookSpecificOutput":{"additionalContext":"bounded advice",'
        b'"hookEventName":"SessionStart"}}\n'
    )


def test_claude_stop_emits_documented_non_error_additional_context() -> None:
    """Claude Code documents Stop/SubagentStop additionalContext as non-error feedback (#420)."""

    for event in ("Stop", "SubagentStop"):
        emitted = claude_context_output(event, "  refresh status before an exact-frontier check  ")
        assert emitted == {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "refresh status before an exact-frontier check",
            }
        }
        assert "decision" not in emitted


def test_claude_context_matches_codex_on_shared_additional_context_events() -> None:
    for event in ("SessionStart", "PostToolUse", "UserPromptSubmit", "PreToolUse", "SubagentStart"):
        assert claude_context_output(event, "bounded advice") == context_output(
            event, "bounded advice"
        )


def test_claude_post_tool_failure_preserves_its_host_event_name() -> None:
    assert claude_context_output("PostToolUseFailure", "bounded advice") == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": "bounded advice",
        }
    }


def test_claude_session_end_blank_and_unknown_events_emit_empty_object() -> None:
    assert claude_context_output("SessionEnd", "would be discarded by the host") == {}
    assert claude_context_output("PreCompact", "never an advice channel") == {}
    assert claude_context_output("Stop", "   ") == {}
    assert claude_context_output("SessionStart", "") == {}


def test_claude_context_is_bounded_by_the_shared_context_limit() -> None:
    assert claude_context_output("Stop", "x" * 2_001) == {
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "x" * 2_000}
    }
