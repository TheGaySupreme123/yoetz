"""Console entry point that fast-paths the observe hook past the typer graph.

Loading ``yoetz.cli.app`` costs ~232 ms of typer/pydantic/protocol-schema
imports that a Codex hook never uses (#242). Only ``hooks observe`` with the
exact options it declares is fast-pathed; everything else falls through to the
full CLI unchanged, so usage errors and ``--help`` stay byte-identical.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Final

# Sampled before any yoetz module resolves, so the hook can measure the import
# term it otherwise cannot see. Interpreter start itself is not portably
# measurable and is documented (~20 ms), never guessed.
_ENTRY_MONOTONIC: Final = time.monotonic()

__all__ = ["UNSUPPORTED_PLATFORM_MESSAGE", "main"]

# Yoetz is POSIX-only: the service listens on an owner-only AF_UNIX socket, and every path,
# vault, and lock check compares ``st_uid`` against ``os.geteuid()``. On native Windows the path
# probe raised ``AttributeError`` inside the catch-all and every stateful command printed
# ``internal_error`` (issue #709). Refusing here names the real condition and the way out.
_NATIVE_WINDOWS: Final = "nt"
_PLATFORM_EXEMPT_COMMANDS: Final = frozenset({"version"})
_PLATFORM_EXEMPT_FLAGS: Final = frozenset({"--help", "-h", "--version"})
# ``service_unavailable`` exits 20 in ``yoetz.cli.exits``: no Yoetz service can ever be reached
# from this process, so the exit matches the public code an absent service already carries.
_UNSUPPORTED_PLATFORM_EXIT: Final = 20
UNSUPPORTED_PLATFORM_MESSAGE: Final = (
    "unsupported_platform: Yoetz runs on macOS and Linux; native Windows is not supported.\n"
    "On Windows, install and run Yoetz inside WSL 2 (Ubuntu). Setup guide:\n"
    "  https://github.com/TheGaySupreme123/yoetz/blob/main/docs/usage/install-and-first-run.md#windows"
)


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


def _cursor_observe_fast_path(arguments: list[str]) -> int | None:
    """Run ``hooks cursor-observe`` without loading typer."""

    event: str | None = None
    workspace: str | None = None
    observation_profile: str | None = None
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
        elif token == "--observation-profile" and observation_profile is None:
            observation_profile = value
        else:
            return None
        index += 2
    if event is None:
        return None
    try:
        from yoetz.cli.observe_hooks import handle_cursor_observe

        return handle_cursor_observe(
            event_name=event,
            workspace=workspace,
            observation_profile=observation_profile,
            _entry_monotonic=_ENTRY_MONOTONIC,
        )
    except BaseException:
        try:
            from yoetz.cli.hook_io import stdout_json

            stdout_json({})
        except BaseException:
            pass
    return 0


def _claude_observe_fast_path(arguments: list[str]) -> int | None:
    """Run ``hooks claude-observe`` without loading the full Typer graph.

    Claude's ordinary native profile fires for every generic tool event.  The
    command therefore must stay on the same lightweight path as the Codex and
    Cursor ingress commands; falling through to ``cli.app`` makes a fresh hook
    process spend most of its host timeout importing unused command modules.
    """

    event: str | None = None
    workspace: str | None = None
    observation_profile: str | None = None
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
        elif token == "--observation-profile" and observation_profile is None:
            observation_profile = value
        else:
            return None
        index += 2
    if event is None:
        return None
    try:
        from yoetz.cli.observe_hooks import handle_claude_observe

        return handle_claude_observe(
            event_name=event,
            workspace=workspace,
            observation_profile=observation_profile,
            _entry_monotonic=_ENTRY_MONOTONIC,
        )
    except BaseException:
        try:
            from yoetz.cli.hook_io import stdout_json

            stdout_json({})
        except BaseException:
            pass
    return 0


def _unsupported_platform_exit(arguments: list[str], *, os_name: str | None = None) -> int | None:
    """Return the bounded exit for native Windows, or None where Yoetz can run.

    ``version``, ``--version``, and ``--help`` stay available so a user or agent can still see what
    was installed; everything else would only reach ``internal_error``.
    """

    if (os.name if os_name is None else os_name) != _NATIVE_WINDOWS:
        return None
    if arguments and arguments[0] in _PLATFORM_EXEMPT_COMMANDS:
        return None
    if any(token in _PLATFORM_EXEMPT_FLAGS for token in arguments):
        return None
    try:
        sys.stderr.write(UNSUPPORTED_PLATFORM_MESSAGE + "\n")
        sys.stderr.flush()
    except OSError:
        pass
    return _UNSUPPORTED_PLATFORM_EXIT


def _run_full_cli() -> None:
    """Load the typer graph and dispatch; split out so tests can prove it never loads on Windows."""

    from yoetz.cli.app import main as app_main

    app_main()


def main() -> None:
    """Installed console entry point."""

    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "observe":
        code = _observe_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "cursor-observe":
        code = _cursor_observe_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "claude-observe":
        code = _claude_observe_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    if len(argv) >= 2 and argv[0] == "hooks" and argv[1] == "spool":
        code = _spool_fast_path(argv[2:])
        if code is not None:
            raise SystemExit(code)
    code = _unsupported_platform_exit(argv)
    if code is not None:
        raise SystemExit(code)
    _run_full_cli()
