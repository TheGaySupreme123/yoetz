"""Leaf hook IO helpers: stdin payload, stdout JSON, stderr line, context output.

Deliberately imports no Yoetz modules beyond ``yoetz.protocol``. ``yoetz.cli.hooks``
re-exports these names unchanged; the split exists so a hook process does not
pay for ``service.client`` → ``control_protocol`` → ``protocol.schemas`` →
jsonschema just to write one JSON object to stdout (#242).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import BinaryIO, Final, cast

from yoetz.protocol.canonical import (
    MAX_JSON_DEPTH,
    JsonValue,
    canonical_encode,
    ensure_canonical_value,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "ADDITIONAL_CONTEXT_EVENTS",
    "CLAUDE_ADDITIONAL_CONTEXT_EVENTS",
    "STOP_CONTROL_EVENTS",
    "claude_context_output",
    "context_output",
    "cursor_context_output",
    "read_cursor_hook_payload",
    "read_hook_payload",
    "stderr_line",
    "stdout_json",
]

_MAX_STDIN_BYTES: Final = 262_144
_MAX_CONTEXT_CHARS: Final = 2_000
_MAX_STDERR_CHARS: Final = 200
_MAX_SAFE_INTEGER: Final = 2**53 - 1
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
# Codex Stop / SubagentStop admit only the common output fields plus
# decision/reason. The current hooks reference (learn.chatgpt.com/docs/hooks,
# re-read 2026-08-28 for #420) still lists no hookSpecificOutput for either
# event, and a build that received one marked the hook Failed with "invalid
# stop hook JSON output" (#222). Per that reference, `decision: block` does not
# reject the turn: Codex continues with `reason` as a new continuation prompt,
# so `stop_hook_active` plus delivery identity remain the loop guard.
STOP_CONTROL_EVENTS: Final = frozenset({"Stop", "SubagentStop"})
# Claude Code events whose output schema admits hookSpecificOutput.additionalContext.
# Unlike Codex, Claude Code documents Stop / SubagentStop `additionalContext` as
# non-error feedback (code.claude.com/docs/en/hooks, re-read 2026-08-28): the
# conversation continues so the model can act on it, but it is shown as hook
# feedback rather than the `decision: block` hook error.
CLAUDE_ADDITIONAL_CONTEXT_EVENTS: Final = frozenset(
    {
        "PostToolUse",
        "PostToolUseFailure",
        "PreToolUse",
        "SessionStart",
        "Stop",
        "SubagentStart",
        "SubagentStop",
        "UserPromptSubmit",
    }
)


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


def claude_context_output(event_name: str, additional_context: str) -> dict[str, JsonValue]:
    """Return the Claude Code-valid stdout object for one event's advice text.

    Every supported advice-bearing event, including Stop / SubagentStop, injects
    ``hookSpecificOutput.additionalContext``. At Stop / SubagentStop that is
    Claude Code's documented non-error feedback channel: it continues through
    the same loop protections as ``decision: block`` but is labelled as feedback
    instead of an error. SessionEnd and every undocumented event emit ``{}``.
    """

    text = additional_context.strip()
    if not text:
        return {}
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[:_MAX_CONTEXT_CHARS]
    if event_name in CLAUDE_ADDITIONAL_CONTEXT_EVENTS:
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


def _parse_cursor_integer(literal: str) -> int:
    """Parse a Cursor host integer with the same safe bound as canonical JSON."""

    if literal == "-0":
        raise ProtocolValueError("float_forbidden")
    try:
        value = int(literal)
    except ValueError as exc:
        raise ProtocolValueError("integer_out_of_safe_range") from exc
    if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
        raise ProtocolValueError("integer_out_of_safe_range")
    return value


def _reject_cursor_constant(_: str) -> object:
    raise ProtocolValueError("float_forbidden")


def _normalize_cursor_duration(value: Decimal) -> int:
    """Truncate one finite, nonnegative Cursor duration to canonical milliseconds."""

    if not value.is_finite() or value < 0 or (value.is_zero() and value.is_signed()):
        raise ProtocolValueError("invalid_duration")
    if value > _MAX_SAFE_INTEGER:
        raise ProtocolValueError("integer_out_of_safe_range")
    return int(value)


def _normalize_cursor_value(value: object, *, key: str | None, depth: int) -> JsonValue:
    """Convert Cursor floats without admitting them to the canonical payload.

    The host may place ordinary decimal numbers in discarded vendor fields such
    as tool metadata. Those values remain transient and are replaced with
    ``null``; only the top-level duration is retained after normalization.
    """

    if isinstance(value, Decimal):
        if key == "duration" and depth == 1:
            return _normalize_cursor_duration(value)
        if depth == 0:
            raise ProtocolValueError("float_forbidden")
        return None
    if value is None or type(value) in {bool, int, str}:
        return cast(JsonValue, value)
    if type(value) is list:
        if depth >= MAX_JSON_DEPTH:
            raise ProtocolValueError("nesting_too_deep")
        return [
            _normalize_cursor_value(item, key=None, depth=depth + 1)
            for item in cast(list[object], value)
        ]
    if type(value) is dict:
        if depth >= MAX_JSON_DEPTH:
            raise ProtocolValueError("nesting_too_deep")
        normalized: dict[str, JsonValue] = {}
        for child_key, item in cast(dict[object, object], value).items():
            if type(child_key) is not str:
                raise ProtocolValueError("object_key_not_string")
            normalized[child_key] = _normalize_cursor_value(
                item,
                key=child_key,
                depth=depth + 1,
            )
        return normalized
    raise ProtocolValueError("unsupported_json_type")


def read_cursor_hook_payload(raw: bytes | None = None) -> Mapping[str, JsonValue]:
    """Read Cursor's bounded host JSON, normalizing its decimal duration field.

    Cursor reports MCP hook durations as fractional milliseconds. That vendor shape
    is admitted only here; canonical ledger and all other host parsers remain
    float-free. Unknown or nested numeric floats are replaced with ``null`` before
    structural filtering, while all host-controlled values are still discarded
    by the caller.
    """

    data = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1) if raw is None else raw
    if type(data) is bytearray:
        data = bytes(data)
    elif type(data) is not bytes:
        raise ProtocolValueError("input_not_bytes")
    if not data or len(data) > _MAX_STDIN_BYTES:
        raise ProtocolValueError("invalid_event_value_type")
    if b"\x00" in data:
        raise ProtocolValueError("nul_byte_forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolValueError("invalid_utf8") from exc
    if text.startswith("\ufeff"):
        raise ProtocolValueError("byte_order_mark_forbidden")

    def _decode_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = dict(pairs)
        if len(result) != len(pairs):
            raise ProtocolValueError("duplicate_object_key")
        return result

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_decode_object_pairs,
            parse_float=Decimal,
            parse_int=_parse_cursor_integer,
            parse_constant=_reject_cursor_constant,
        )
    except json.JSONDecodeError as exc:
        raise ProtocolValueError("malformed_json") from exc
    except RecursionError as exc:
        raise ProtocolValueError("nesting_too_deep") from exc
    except InvalidOperation as exc:
        raise ProtocolValueError("float_forbidden") from exc

    normalized = _normalize_cursor_value(parsed, key=None, depth=0)
    ensure_canonical_value(normalized)
    if not isinstance(normalized, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return cast(Mapping[str, JsonValue], normalized)
