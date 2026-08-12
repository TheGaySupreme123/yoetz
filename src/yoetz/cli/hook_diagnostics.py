"""Owner-private, payload-free diagnostics for degraded Codex hook delivery."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.observation import ObservationGapCode
from yoetz.domain.values import JsonObject

try:
    import fcntl
except ImportError:  # pragma: no cover - supported hook hosts are POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = ["hook_diagnostic_summary", "record_hook_diagnostic"]

_MAX_DIAGNOSTIC_BYTES: Final = 64 * 1024
_FILE_NAME: Final = "hook-diagnostics.jsonl"
_LOCK_NAME: Final = ".hook-diagnostics.lock"
_EVENTS: Final = frozenset(
    {
        "SessionStart",
        "SessionEnd",
        "Stop",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "observe",
        "drain",
    }
)
_REASONS: Final = frozenset(
    {
        *(item.value for item in ObservationGapCode),
        "invalid_session",
        "observe",
        "outbox_overflow",
        "service_unavailable",
        "vault_locked",
        "mapping_missing",
        "observation_disabled",
        "paused",
        "timeout",
        "drain_budget_exhausted",
        "drain_preflight_failed",
        "runtime_gate_unsafe",
    }
)
_thread_lock = Lock()


def _closed(value: object, allowed: frozenset[str], fallback: str) -> str:
    if type(value) is str and value in allowed:
        return value
    return fallback


def _timestamp() -> str:
    now = datetime.now(UTC)
    return (
        now.replace(microsecond=(now.microsecond // 1000) * 1000).isoformat().replace("+00:00", "Z")
    )


def record_hook_diagnostic(
    reason: str,
    event: str,
    *,
    _state: Path | None = None,
) -> None:
    """Append one bounded structural hook failure record, rotating one prior file."""

    root = state_dir() if _state is None else _state
    directory = root / "observation"
    path = directory / _FILE_NAME
    rotated = directory / f"{_FILE_NAME}.1"
    lock_path = directory / _LOCK_NAME
    line = (
        json.dumps(
            {
                "event": _closed(event, _EVENTS, "unknown_event"),
                "reason": _closed(reason, _REASONS, "unknown_reason"),
                "ts": _timestamp(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    try:
        ensure_owner_only_dir(directory)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        with _thread_lock:
            lock_descriptor = os.open(lock_path, flags, 0o600)
            try:
                os.fchmod(lock_descriptor, 0o600)
                if fcntl is not None:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                if path.is_symlink() or rotated.is_symlink():
                    return
                try:
                    size = path.lstat().st_size
                except FileNotFoundError:
                    size = 0
                if size + len(line) > _MAX_DIAGNOSTIC_BYTES:
                    rotated.unlink(missing_ok=True)
                    if path.exists():
                        if size <= _MAX_DIAGNOSTIC_BYTES:
                            os.replace(path, rotated)
                            rotated_descriptor = os.open(
                                rotated,
                                os.O_RDONLY
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                            )
                            try:
                                os.fchmod(rotated_descriptor, 0o600)
                            finally:
                                os.close(rotated_descriptor)
                        else:
                            path.unlink()
                descriptor = os.open(
                    path,
                    os.O_WRONLY
                    | os.O_APPEND
                    | os.O_CREAT
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    written = 0
                    while written < len(line):
                        written += os.write(descriptor, line[written:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(lock_descriptor)
    except OSError, PathSafetyError, ValueError:
        return


def hook_diagnostic_summary(*, _state: Path | None = None) -> JsonObject:
    """Return a bounded structural summary of the current and rotated diagnostics."""

    root = state_dir() if _state is None else _state
    directory = root / "observation"
    rows: list[dict[str, str]] = []
    try:
        ensure_owner_only_dir(directory)
        for path in (
            directory / f"{_FILE_NAME}.1",
            directory / _FILE_NAME,
        ):
            if path.is_symlink():
                continue
            try:
                facts = path.lstat()
                if (
                    facts.st_uid != os.geteuid()
                    or facts.st_mode & 0o077
                    or facts.st_size <= 0
                    or facts.st_size > _MAX_DIAGNOSTIC_BYTES
                ):
                    continue
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            for line in raw.splitlines():
                try:
                    parsed: object = json.loads(line)
                except UnicodeError, json.JSONDecodeError:
                    continue
                if type(parsed) is not dict:
                    continue
                row = cast(dict[str, object], parsed)
                if set(row) != {"event", "reason", "ts"} or any(
                    type(row.get(key)) is not str for key in ("event", "reason", "ts")
                ):
                    continue
                rows.append(
                    {
                        "event": cast(str, row["event"]),
                        "reason": cast(str, row["reason"]),
                        "ts": cast(str, row["ts"]),
                    }
                )
    except OSError, PathSafetyError:
        return JsonObject(
            {"count": 0, "last_event": None, "last_reason": None, "reasons": JsonObject({})}
        )
    reasons: dict[str, int] = {}
    for row in rows:
        reason = row["reason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    last = rows[-1] if rows else None
    return JsonObject(
        {
            "count": len(rows),
            "last_event": None if last is None else last["event"],
            "last_reason": None if last is None else last["reason"],
            "reasons": JsonObject(dict(sorted(reasons.items(), key=lambda item: item[0].encode()))),
        }
    )
