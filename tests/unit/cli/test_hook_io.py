"""Event-specific Codex hook stdout contracts (#222)."""

from __future__ import annotations

import io

import pytest

from yoetz.cli.hook_io import (
    claude_context_output,
    context_output,
    cursor_context_output,
    read_cursor_hook_payload,
    stdout_json,
)
from yoetz.protocol.canonical import strict_json_parse
from yoetz.protocol.errors import ProtocolValueError


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


def test_cursor_post_tool_use_emits_cursor_native_additional_context() -> None:
    assert cursor_context_output("postToolUse", "  bounded advice  ") == {
        "additional_context": "bounded advice"
    }


def test_cursor_stop_does_not_auto_submit_a_followup_message() -> None:
    assert cursor_context_output("stop", "submit this as a new user message") == {}


def test_cursor_stop_followup_is_explicitly_opt_in() -> None:
    assert cursor_context_output(
        "stop", "  submit this as a new user message  ", allow_stop_followup=True
    ) == {"followup_message": "submit this as a new user message"}


def test_cursor_outputless_and_unknown_events_emit_empty_object() -> None:
    for event in (
        "postToolUseFailure",
        "afterFileEdit",
        "afterMCPExecution",
        "sessionEnd",
        "unknown",
    ):
        assert cursor_context_output(event, "advice that has no output channel") == {}


def test_cursor_context_is_bounded_by_the_codex_context_limit() -> None:
    advice = "x" * 2_001
    assert cursor_context_output("sessionStart", advice) == {"additional_context": "x" * 2_000}
    assert cursor_context_output("postToolUse", advice) == {"additional_context": "x" * 2_000}


def test_cursor_post_tool_use_stdout_is_canonical_and_bounded() -> None:
    stream = io.BytesIO()
    assert stdout_json(cursor_context_output("postToolUse", "  bounded advice  "), stream)
    assert stream.getvalue() == b'{"additional_context":"bounded advice"}\n'


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


@pytest.mark.parametrize(
    ("literal", "expected"),
    [("428.607", 428), ("428.5", 428), ("428.499", 428), ("0.0", 0), ("1e3", 1_000)],
)
def test_cursor_hook_payload_truncates_fractional_duration_to_canonical_integer(
    literal: str, expected: int
) -> None:
    payload = ('{"duration":' + literal + ',"hook_event_name":"afterMCPExecution"}').encode()

    parsed = read_cursor_hook_payload(payload)

    assert parsed["duration"] == expected
    # The canonical parser remains float-free for every non-Cursor wire surface.
    with pytest.raises(ProtocolValueError, match="float_forbidden"):
        strict_json_parse(payload)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b'{"duration":-1.0}', "invalid_duration"),
        (b'{"duration":NaN}', "float_forbidden"),
        (b'{"duration":1.2,"duration":2.3}', "duplicate_object_key"),
        (b'{"duration":9007199254740992}', "integer_out_of_safe_range"),
        (b'{"duration":9007199254740991.4}', "integer_out_of_safe_range"),
        (b'{"value":"\\u0000"}', "nul_byte_forbidden"),
    ],
)
def test_cursor_hook_payload_preserves_bounded_wire_rejections(payload: bytes, reason: str) -> None:
    with pytest.raises(ProtocolValueError, match=reason):
        read_cursor_hook_payload(payload)


def test_cursor_hook_payload_discards_nested_vendor_floats() -> None:
    parsed = read_cursor_hook_payload(
        b'{"model_params":[{"id":"temperature","value":0.2}],'
        b'"tool_input":{"ratio":1.5},"hook_event_name":"afterMCPExecution"}'
    )

    assert parsed["model_params"] == [{"id": "temperature", "value": None}]
    assert parsed["tool_input"] == {"ratio": None}
