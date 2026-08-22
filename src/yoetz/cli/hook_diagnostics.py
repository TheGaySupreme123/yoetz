"""Owner-private, payload-free diagnostics for degraded Codex hook delivery."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

__all__ = ["hook_diagnostic_summary", "record_hook_diagnostic", "record_hook_timing"]

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
        "mapping_stale",
        "observation_disabled",
        "paused",
        "timeout",
        "drain_budget_exhausted",
        "drain_lease_contended",
        "drain_preflight_failed",
        "auto_attach_retry_failed",
        "runtime_gate_contended",
        "runtime_gate_unsafe",
        "stdout_write_failed",
        # Observability only: the end-to-end hook budget is a contract, not an
        # enforcement point. Aborting mid-hook would drop ingest.
        "hook_budget_exceeded",
        "hook_slo_breached",
    }
)
_STAGES: Final = frozenset(
    {
        "advice",
        "drain",
        "import",
        "store",
        # Formerly unwindowed regions of the pass (#310/#311): workspace
        # resolution and the consent probe before the store window opens, and
        # advice selection, the stdout write and both delivery commits after
        # the drain window closes. Together with 'import' and 'store' they
        # partition the pass, and 'unattributed' names whatever they miss.
        "deliver",
        "resolve",
        "unattributed",
        # Store sub-stage attribution (#290): parse+hydrate of the state file,
        # canonical encode of every size projection and save, and the fsync'd
        # atomic write. The remainder of 'store' is mutation time. Lock wait
        # (#310) is queueing behind another process, not work, and spans the
        # whole pass rather than partitioning any one window.
        "store_encode",
        "store_hydrate",
        "store_lock_wait",
        "store_write",
        "total",
    }
)
_MAX_STAGE_MS: Final = 3_600_000
# The retained file spans days, so an all-time tally reports a failure that was
# diagnosed and fixed two days ago exactly like one happening right now (#310).
# Every count is therefore paired with a recent-window count, and every extreme
# with the moment it was observed, so a reader can date what it is looking at.
_RECENT_WINDOW_SECONDS: Final = 3_600
_SYNC_FALLBACK_PATH: Final = "sync_fallback_spool"
_SYNC_FALLBACK_P95_TARGET_MS: Final = 250
_SYNC_FALLBACK_HARD_CAP_MS: Final = 500
_thread_lock = Lock()


def _closed(value: object, allowed: frozenset[str], fallback: str) -> str:
    if type(value) is str and value in allowed:
        return value
    return fallback


def _render(moment: datetime) -> str:
    return (
        moment.astimezone(UTC)
        .replace(microsecond=(moment.microsecond // 1000) * 1000)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _timestamp() -> str:
    return _render(datetime.now(UTC))


def _parse_timestamp(value: str) -> datetime | None:
    """Return the recorded moment, or None when the row's stamp is unreadable."""

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def record_hook_diagnostic(
    reason: str,
    event: str,
    *,
    _state: Path | None = None,
) -> None:
    """Append one bounded structural hook failure record, rotating one prior file."""

    _append_row(
        {
            "event": _closed(event, _EVENTS, "unknown_event"),
            "reason": _closed(reason, _REASONS, "unknown_reason"),
            "ts": _timestamp(),
        },
        _state=_state,
    )


def _append_row(row: dict[str, object], *, _state: Path | None) -> None:
    root = state_dir() if _state is None else _state
    directory = root / "observation"
    path = directory / _FILE_NAME
    rotated = directory / f"{_FILE_NAME}.1"
    lock_path = directory / _LOCK_NAME
    line = json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
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


def record_hook_timing(
    event: str,
    *,
    ms: int,
    stages: Mapping[str, int],
    path: str | None = None,
    _state: Path | None = None,
) -> None:
    """Append one bounded end-to-end timing row for a hook pass.

    Emitted only over budget or at session boundaries: the diagnostics file is
    64 KiB with one rotation, and a per-hook row would halve the retained
    failure-reason window.
    """

    bounded = {
        name: max(0, min(int(value), _MAX_STAGE_MS))
        for name, value in stages.items()
        if name in _STAGES and type(value) is int and not isinstance(value, bool)
    }
    row: dict[str, object] = {
        "event": _closed(event, _EVENTS, "unknown_event"),
        "kind": "timing",
        "ms": max(0, min(int(ms), _MAX_STAGE_MS)),
        "stages": dict(sorted(bounded.items())),
        "ts": _timestamp(),
    }
    if path in {"sync_fallback_spool", "async_host", "ordinary_sync"}:
        row["path"] = path
    _append_row(row, _state=_state)


@dataclass(slots=True)
class _Recency:
    """One reason's tally paired with the span of moments it actually covers."""

    count: int = 0
    recent: int = 0
    first: datetime | None = None
    last: datetime | None = None

    def observe(self, moment: datetime | None, *, fresh: bool) -> None:
        self.count += 1
        if fresh:
            self.recent += 1
        if moment is None:
            return
        if self.first is None or moment < self.first:
            self.first = moment
        if self.last is None or moment > self.last:
            self.last = moment

    def as_json(self) -> JsonObject:
        return JsonObject(
            {
                "count": self.count,
                "first_seen": None if self.first is None else _render(self.first),
                "last_seen": None if self.last is None else _render(self.last),
                "recent": self.recent,
            }
        )


@dataclass(slots=True)
class _Timings:
    """Timing rows, kept datable so a one-off extreme is not read as the norm."""

    count: int = 0
    recent: int = 0
    last_ms: int | None = None
    max_ms: int | None = None
    max_at: datetime | None = None
    recent_max_ms: int | None = None
    recent_values: list[int] = field(default_factory=list)
    by_path: dict[str, _Timings] = field(default_factory=dict)

    def observe(
        self,
        ms: int,
        moment: datetime | None,
        *,
        fresh: bool,
        path: str | None = None,
    ) -> None:
        self.count += 1
        self.last_ms = ms
        if self.max_ms is None or ms > self.max_ms:
            self.max_ms = ms
            self.max_at = moment
        if not fresh:
            return
        self.recent += 1
        self.recent_values.append(ms)
        if self.recent_max_ms is None or ms > self.recent_max_ms:
            self.recent_max_ms = ms
        if path is not None:
            self.by_path.setdefault(path, _Timings()).observe(ms, moment, fresh=True)

    def _recent_p95_ms(self) -> int | None:
        if not self.recent_values:
            return None
        ordered = sorted(self.recent_values)
        # Nearest-rank p95 intentionally fails closed: a single retained
        # sample is its own p95, rather than looking healthy by interpolation.
        return ordered[(95 * len(ordered) + 99) // 100 - 1]

    def as_json(self) -> JsonObject:
        return JsonObject(
            {
                "count": self.count,
                "last_ms": self.last_ms,
                "max_ms": self.max_ms,
                "max_ts": None if self.max_at is None else _render(self.max_at),
                "recent_count": self.recent,
                "recent_max_ms": self.recent_max_ms,
                "recent_p95_ms": self._recent_p95_ms(),
                "paths": JsonObject(
                    {
                        path: JsonObject(
                            {
                                "count": item.count,
                                "recent_count": item.recent,
                                "recent_p95_ms": item._recent_p95_ms(),
                                "p95_target_ms": _SYNC_FALLBACK_P95_TARGET_MS
                                if path == _SYNC_FALLBACK_PATH
                                else None,
                                "hard_cap_ms": _SYNC_FALLBACK_HARD_CAP_MS
                                if path == _SYNC_FALLBACK_PATH
                                else None,
                                "recent_hard_cap_breach_count": sum(
                                    value > _SYNC_FALLBACK_HARD_CAP_MS
                                    for value in item.recent_values
                                )
                                if path == _SYNC_FALLBACK_PATH
                                else 0,
                            }
                        )
                        for path, item in sorted(self.by_path.items(), key=lambda item: item[0])
                    }
                ),
            }
        )


def _read_rows(directory: Path) -> tuple[list[dict[str, str]], list[tuple[int, str, str | None]]]:
    """Return retained failure rows and timing rows, oldest retained file first."""

    rows: list[dict[str, str]] = []
    timings: list[tuple[int, str, str | None]] = []
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
                if set(row) in (
                    {"event", "kind", "ms", "stages", "ts"},
                    {"event", "kind", "ms", "stages", "ts", "path"},
                ):
                    # Timing rows are a second shape on the same file; they must
                    # never inflate the failure-reason counts.
                    total = row.get("ms")
                    stamp = row.get("ts")
                    path_value = row.get("path")
                    if (
                        row.get("kind") == "timing"
                        and type(total) is int
                        and type(stamp) is str
                        and (
                            path_value is None
                            or path_value in {"sync_fallback_spool", "async_host", "ordinary_sync"}
                        )
                    ):
                        timings.append((total, stamp, cast(str | None, path_value)))
                    continue
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
        return [], []
    return rows, timings


def hook_diagnostic_summary(
    *,
    _state: Path | None = None,
    _now: datetime | None = None,
) -> JsonObject:
    """Return a bounded structural summary of the current and rotated diagnostics.

    Every tally is reported twice — over everything retained, and over the last
    `window_seconds` — and every count carries the span it covers, so a reader
    can tell a live failure from one that was fixed days ago (#310).
    """

    root = state_dir() if _state is None else _state
    rows, timings = _read_rows(root / "observation")
    now = datetime.now(UTC) if _now is None else _now.astimezone(UTC)
    horizon = now - timedelta(seconds=_RECENT_WINDOW_SECONDS)
    overall = _Recency()
    reasons: dict[str, _Recency] = {}
    for row in rows:
        moment = _parse_timestamp(row["ts"])
        # An unreadable stamp is never counted as recent: an undatable row is
        # exactly the thing this summary must stop presenting as live.
        fresh = moment is not None and horizon <= moment <= now
        overall.observe(moment, fresh=fresh)
        reasons.setdefault(row["reason"], _Recency()).observe(moment, fresh=fresh)
    timing = _Timings()
    for total, stamp, path in timings:
        moment = _parse_timestamp(stamp)
        timing.observe(
            total,
            moment,
            fresh=moment is not None and horizon <= moment <= now,
            path=path,
        )
    last = rows[-1] if rows else None
    return JsonObject(
        {
            "count": overall.count,
            "first_seen": None if overall.first is None else _render(overall.first),
            "last_event": None if last is None else last["event"],
            "last_reason": None if last is None else last["reason"],
            "last_seen": None if overall.last is None else _render(overall.last),
            "reasons": JsonObject(
                {
                    name: tally.as_json()
                    for name, tally in sorted(reasons.items(), key=lambda item: item[0].encode())
                }
            ),
            "recent_count": overall.recent,
            "timings": timing.as_json(),
            "window_seconds": _RECENT_WINDOW_SECONDS,
        }
    )
