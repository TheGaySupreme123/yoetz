"""Console entry point that fast-paths the observe hook past the typer graph.

Loading ``yoetz.cli.app`` costs ~232 ms of typer/pydantic/protocol-schema
imports that a Codex hook never uses (#242). Only ``hooks observe`` with the
exact options it declares is fast-pathed; everything else falls through to the
full CLI unchanged, so usage errors and ``--help`` stay byte-identical.
"""

from __future__ import annotations

import sys
import time
from typing import Final

# Sampled before any yoetz module resolves, so the hook can measure the import
# term it otherwise cannot see. Interpreter start itself is not portably
# measurable and is documented (~20 ms), never guessed.
_ENTRY_MONOTONIC: Final = time.monotonic()

__all__ = ["main"]


def _observe_fast_path(arguments: list[str]) -> int | None:
    """Run ``hooks observe`` directly, or return None to fall through to typer.

    Any unrecognised token — ``--help``, an unknown flag, a repeated or
    value-less option, a missing ``--event`` — returns None. A mis-sniff would
    turn a user-facing usage error into a silent exit 0.
    """

    event: str | None = None
    workspace: str | None = None
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if index + 1 >= len(arguments):
            return None
        value = arguments[index + 1]
        if value.startswith("-"):
            return None
        if token == "--event" and event is None:
            event = value
        elif token == "--workspace" and workspace is None:
            workspace = value
        else:
            return None
        index += 2
    if event is None:
        return None
    try:
        from yoetz.cli.observe_hooks import handle_observe

        return handle_observe(
            event_name=event,
            workspace=workspace,
            _entry_monotonic=_ENTRY_MONOTONIC,
        )
    except BaseException:
        # Same contract as the typer command: a hook never fails its host.
        try:
            from yoetz.cli.hook_io import stdout_json

            stdout_json({})
        except BaseException:
            pass
    return 0


def _spool_fast_path(arguments: list[str]) -> int | None:
    """Run the legacy synchronous spool writer without loading typer."""

    event: str | None = None
    workspace: str | None = None
    index = 0
    while index < len(arguments):
        if index + 1 >= len(arguments):
            return None
        token, value = arguments[index], arguments[index + 1]
        if value.startswith("-"):
            return None
        if token == "--event" and event is None:
            event = value
        elif token == "--workspace" and workspace is None:
            workspace = value
        else:
            return None
        index += 2
    if event is None or workspace is None:
        return None
    try:
        from yoetz.cli.observe_hooks import handle_spool

        return handle_spool(
            event_name=event,
            workspace=workspace,
            _entry_monotonic=_ENTRY_MONOTONIC,
        )
    except BaseException:
        try:
            from yoetz.cli.hook_io import stdout_json

            stdout_json({})
        except BaseException:
            pass
    return 0


def main() -> None:
    """Installed console entry point."""

    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "observe":
        code = _observe_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "spool":
        code = _spool_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    from yoetz.cli.app import main as app_main

    app_main()
