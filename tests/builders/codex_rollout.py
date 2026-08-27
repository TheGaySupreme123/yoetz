"""Synthetic Codex rollout JSONL builders for unit and integration tests."""

from __future__ import annotations

import json
from typing import Any

_TS = "2026-08-22T12:00:00.000Z"


def _row(
    wrapper: str,
    payload: dict[str, Any],
    *,
    ordinal: int | None = None,
    timestamp: str = _TS,
) -> dict[str, Any]:
    row: dict[str, Any] = {"payload": payload, "timestamp": timestamp, "type": wrapper}
    if ordinal is not None:
        row["ordinal"] = ordinal
    return row


def session_meta(
    *,
    cli_version: str = "0.148.0",
    history_mode: str = "legacy",
    session_id: str = "019f8b27-b98e-7061-bbb5-d0b897594de6",
    ordinal: int | None = None,
    cwd: str = "/tmp/yoetz-rollout",
) -> dict[str, Any]:
    return _row(
        "session_meta",
        {
            "cli_version": cli_version,
            "cwd": cwd,
            "history_mode": history_mode,
            "id": session_id,
            "originator": "user",
        },
        ordinal=ordinal,
    )


def response_item(
    payload: dict[str, Any],
    *,
    ordinal: int | None = None,
) -> dict[str, Any]:
    return _row("response_item", payload, ordinal=ordinal)


def function_call(
    *,
    name: str,
    call_id: str,
    arguments: str = "{}",
    ordinal: int | None = None,
) -> dict[str, Any]:
    return response_item(
        {"arguments": arguments, "call_id": call_id, "name": name, "type": "function_call"},
        ordinal=ordinal,
    )


def function_call_output(
    *,
    call_id: str,
    output: str = "ok",
    exit_code: int | None = 0,
    ordinal: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "call_id": call_id,
        "output": output,
        "status": "completed",
        "type": "function_call_output",
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    return response_item(payload, ordinal=ordinal)


def encode_lines(*rows: dict[str, Any], terminated: bool = True) -> bytes:
    chunks = [json.dumps(row, separators=(",", ":")).encode("utf-8") for row in rows]
    joined = b"\n".join(chunks)
    if terminated and chunks:
        return joined + b"\n"
    return joined


def failed_shell_rollout(*, call_id: str = "i1") -> bytes:
    return encode_lines(
        session_meta(),
        function_call(name="shell", call_id=call_id, arguments='{"command":"echo"}'),
        function_call_output(call_id=call_id, output="failed", exit_code=1),
    )


def completed_shell_rollout(*, call_id: str = "i1") -> bytes:
    return encode_lines(
        session_meta(),
        function_call(name="shell", call_id=call_id, arguments='{"command":"echo"}'),
        function_call_output(call_id=call_id, output="ok", exit_code=0),
    )
