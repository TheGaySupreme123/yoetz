"""Leaf hook IO helpers: stdin payload, stdout JSON, stderr line, context output.

Deliberately imports nothing beyond ``yoetz.protocol``. ``yoetz.cli.hooks``
re-exports these names unchanged; the split exists so a hook process does not
pay for ``service.client`` → ``control_protocol`` → ``protocol.schemas`` →
jsonschema just to write one JSON object to stdout (#242).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import BinaryIO, Final, cast

from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "ADDITIONAL_CONTEXT_EVENTS",
    "STOP_CONTROL_EVENTS",
    "context_output",
    "cursor_context_output",
    "read_hook_payload",
    "stderr_line",
    "stdout_json",
]

_MAX_STDIN_BYTES: Final = 262_144
_MAX_CONTEXT_CHARS: Final = 2_000
_MAX_STDERR_CHARS: Final = 200
# Codex events whose output schema admits hookSpecificOutput.additionalContext.
ADDITIONAL_CONTEXT_EVENTS: Final = frozenset(
    {
        "PostToolUse",
        "PreToolUse",
        "SessionStart",
        "SubagentStart",
        "UserPromptSubmit",
    }
)
# Codex Stop / SubagentStop admit only universal fields plus decision/reason.
# hookSpecificOutput is invalid and the host marks the hook Failed (#222).
STOP_CONTROL_EVENTS: Final = frozenset({"Stop", "SubagentStop"})


def stderr_line(message: str) -> None:
    text = message.replace("\n", " ").replace("\r", " ")[:_MAX_STDERR_CHARS]
    try:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
    except OSError:
        pass


def stdout_json(value: JsonValue, stream: BinaryIO | None = None) -> bool:
    out = sys.stdout.buffer if stream is None else stream
    try:
        out.write(canonical_encode(value) + b"\n")
        out.flush()
        return True
    except BrokenPipeError, OSError, ValueError:
        return False


def context_output(event_name: str, additional_context: str) -> dict[str, JsonValue]:
    """Return the Codex-valid stdout object for one event's advice text.

    SessionStart / PostToolUse / UserPromptSubmit inject model-visible
    ``additionalContext``. Stop / SubagentStop have no such field: the only
    way to reach the model is ``decision: block`` plus ``reason``. Every other
    event, including SessionEnd (stdout discarded), emits ``{}``.
    """

    text = additional_context.strip()
    if not text:
        return {}
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[:_MAX_CONTEXT_CHARS]
    if event_name in STOP_CONTROL_EVENTS:
        return {"decision": "block", "reason": text}
    if event_name in ADDITIONAL_CONTEXT_EVENTS:
        return {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            }
        }
    return {}


def cursor_context_output(
    raw_event: str,
    text: str,
    *,
    allow_stop_followup: bool = False,
) -> dict[str, JsonValue]:
    """Return the Cursor-native stdout object for one raw hook event.

    Cursor's output contract is independent from the Codex hook contract:
    ``sessionStart`` accepts ``additional_context``, while ``stop`` can
    optionally submit a ``followup_message``.  Stop follow-ups are disabled
    by default because Cursor treats them as a new user message.  The other
    Cursor hook events currently have no consumable output channel and emit
    ``{}``.
    """

    bounded = text.strip()
    if not bounded:
        return {}
    if len(bounded) > _MAX_CONTEXT_CHARS:
        bounded = bounded[:_MAX_CONTEXT_CHARS]
    if raw_event == "sessionStart":
        return {"additional_context": bounded}
    if raw_event == "stop" and allow_stop_followup:
        return {"followup_message": bounded}
    return {}


def read_hook_payload(raw: bytes | None = None) -> Mapping[str, JsonValue]:
    """Read a bounded Codex hook JSON object from stdin (or supplied bytes)."""

    data = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1) if raw is None else raw
    if not data or len(data) > _MAX_STDIN_BYTES:
        raise ProtocolValueError("invalid_event_value_type")
    parsed = strict_json_parse(data)
    if not isinstance(parsed, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return cast(Mapping[str, JsonValue], parsed)
