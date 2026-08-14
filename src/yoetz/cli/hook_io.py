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

__all__ = ["context_output", "read_hook_payload", "stderr_line", "stdout_json"]

_MAX_STDIN_BYTES: Final = 262_144
_MAX_CONTEXT_CHARS: Final = 2_000
_MAX_STDERR_CHARS: Final = 200


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
    text = additional_context.strip()
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[:_MAX_CONTEXT_CHARS]
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def read_hook_payload(raw: bytes | None = None) -> Mapping[str, JsonValue]:
    """Read a bounded Codex hook JSON object from stdin (or supplied bytes)."""

    data = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1) if raw is None else raw
    if not data or len(data) > _MAX_STDIN_BYTES:
        raise ProtocolValueError("invalid_event_value_type")
    parsed = strict_json_parse(data)
    if not isinstance(parsed, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return cast(Mapping[str, JsonValue], parsed)
