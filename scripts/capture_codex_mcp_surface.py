#!/usr/bin/env python3
"""Capture one Codex app-server MCP inventory for compatibility evidence.

The caller owns scratch-home creation and MCP registration. This script never edits Codex
configuration; it starts the selected binary against that scratch home, requests the full MCP
server inventory, and writes the bounded JSON-RPC responses to the requested output path.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import TextIO, cast

_TIMEOUT_SECONDS = 30.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-binary", required=True, type=Path)
    parser.add_argument("--codex-testing-home", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def _drain_lines(stream: TextIO, sink: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            sink.put(line)
    finally:
        sink.put(None)


def _send(stream: TextIO, message: dict[str, object]) -> None:
    stream.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
    stream.flush()


def _read_response(
    lines: queue.Queue[str | None], request_id: int, stderr_lines: list[str]
) -> dict[str, object]:
    while True:
        try:
            line = lines.get(timeout=_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise RuntimeError(
                f"codex_app_server_response_timeout:{request_id}:" + "".join(stderr_lines[-20:])
            ) from exc
        if line is None:
            raise RuntimeError(
                f"codex_app_server_closed:{request_id}:" + "".join(stderr_lines[-20:])
            )
        message = json.loads(line)
        if not isinstance(message, dict):
            continue
        if message.get("id") == request_id:
            return cast(dict[str, object], message)


def main() -> int:
    args = _parse_args()
    codex_binary = cast(Path, args.codex_binary).resolve(strict=True)
    codex_testing_home = cast(Path, args.codex_testing_home).resolve(strict=True)
    output = cast(Path, args.output)
    if not codex_testing_home.is_dir() or codex_testing_home.is_symlink():
        raise RuntimeError("codex_testing_home_invalid")
    if output.exists() and output.is_symlink():
        raise RuntimeError("capture_output_symlink_forbidden")

    environment = os.environ.copy()
    environment["CODEX_TESTING_HOME"] = str(codex_testing_home)
    process = subprocess.Popen(
        [str(codex_binary), "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("codex_app_server_pipe_unavailable")

    stdout_lines: queue.Queue[str | None] = queue.Queue()
    stderr_lines: list[str] = []
    threading.Thread(
        target=_drain_lines,
        args=(process.stdout, stdout_lines),
        daemon=True,
    ).start()

    def drain_stderr() -> None:
        stderr_lines.extend(process.stderr)

    threading.Thread(target=drain_stderr, daemon=True).start()
    try:
        _send(
            process.stdin,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "yoetz-boundary-probe",
                        "title": "Yoetz boundary probe",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "experimentalApi": True,
                        "requestAttestation": False,
                        "mcpServerOpenaiFormElicitation": False,
                    },
                },
            },
        )
        initialize = _read_response(stdout_lines, 1, stderr_lines)
        _send(
            process.stdin,
            {
                "method": "mcpServerStatus/list",
                "id": 2,
                "params": {"detail": "full", "limit": 10},
            },
        )
        inventory = _read_response(stdout_lines, 2, stderr_lines)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {"initialize": initialize, "inventory": inventory},
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
