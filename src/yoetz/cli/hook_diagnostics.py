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
from yoetz.domain.observation import (
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestResult,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, JsonValue, validate_commitment
from yoetz.ports.control_reasons import CONTROL_ERROR_REASONS
from yoetz.protocol.ids import IdKind, validate_id

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
        "PermissionDenied",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "observe",
        "drain",
        # Not a host hook event: the MCP bridge's own startup, which is the only place
        # a process can compare its serving route against the applied-route record
        # without paying for a host subprocess (issue #537).
        "mcp_serve",
    }
)
_CONTROL_REASONS: Final = CONTROL_ERROR_REASONS
_REASONS: Final = frozenset(
    {
        *(item.value for item in ObservationGapCode),
        *("control_" + reason for reason in _CONTROL_REASONS),
        "drain_diagnostic_unavailable",
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
        # Why a consented SessionStart (or its turn-boundary retry) produced no
        # mapping (#459). Before these, every auto-attach failure collapsed to a
        # silent None, so an invalid request was indistinguishable from a
        # daemon that was merely still starting.
        "auto_attach_workspace_unbound",
        "auto_attach_request_invalid",
        "auto_attach_conflict",
        "auto_attach_refused",
        "auto_attach_result_invalid",
        "auto_attach_mapping_write_failed",
        "auto_attach_recovery_busy",
        "auto_attach_binding_ambiguous",
        "privacy_authority_required",
        "runtime_gate_contended",
        "runtime_gate_unsafe",
        "stdout_write_failed",
        # Cursor's vendor envelope uses fractional millisecond durations. A
        # malformed or otherwise unsafe host payload is fail-open, but must be
        # distinguishable from a hook that never ran (#593).
        "cursor_payload_invalid",
        # Workspace binding outcomes for every host ingress (#420/#435): an
        # explicit locator that cannot be canonicalized, and a canonical
        # locator that carries no active consent. Without them a dropped hook
        # is indistinguishable from a hook that never fired.
        "workspace_unconsented",
        "workspace_unresolvable",
        "cursor_session_ambiguous",
        # Storage outcomes of a session status read, lowercase of the public
        # error code so the hook advisory, this file, and `observe status`
        # share one vocabulary (#338).
        "storage_corrupt",
        "storage_unsafe",
        # A resume/compact status read that the daemon's repository fence
        # refused (issue #578): the hook connection carried no workspace
        # locator, or the locator resolved to a different repository than the
        # mapped task's route. Neither means the mapping is stale, so neither
        # may be reported as `mapping_stale`.
        "status_workspace_unbound",
        "status_workspace_mismatch",
        # Where a refused status probe got its repository locator (issue #659):
        # the rendered `--workspace`, the host payload's session cwd, the hook's
        # own cwd, no context at all, or a supplied context that could not be
        # canonicalized. Recorded only beside a fence refusal.
        "locator_source_explicit",
        "locator_source_host_payload",
        "locator_source_cwd",
        "locator_absent",
        "locator_unresolvable",
        # A scoped successful `start` post-hook that produced no mapping
        # (issue #581): the host result was not in an admitted shape, its ids
        # failed validation, or the mapping write failed. Before these the
        # bind failed silently and observation kept routing to the old task.
        "start_bind_unparsed",
        "start_bind_invalid_ids",
        "start_bind_child_lane_unbound",
        "start_bind_deferred",
        "start_bind_write_failed",
        # Observability only: the end-to-end hook budget is a contract, not an
        # enforcement point. Aborting mid-hook would drop ingest.
        "hook_budget_exceeded",
        "hook_followup_deferred",
        "hook_slo_breached",
        # A host's automatic tool-call reviewer (Claude Code auto mode) denied a
        # scoped semantic ``check`` before Yoetz received it, or a permission
        # rule / another hook did (issue #467). Host tool-call authorization,
        # not a Yoetz semantic result: no semantic status can be inferred.
        "host_auto_review_denied",
        "host_permission_rule_denied",
        # The durable applied route and the live host registration disagree:
        # policy was applied but the host now serves strict (or vice versa),
        # so a fresh Codex process is still on the old route (issue #537).
        "registration_drift",
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
    candidate_count: int | None = None,
    _state: Path | None = None,
) -> None:
    """Append one bounded structural hook failure record, rotating one prior file."""

    row: dict[str, object] = {
        "event": _closed(event, _EVENTS, "unknown_event"),
        "reason": _closed(reason, _REASONS, "unknown_reason"),
        "ts": _timestamp(),
    }
    if (
        reason == "auto_attach_binding_ambiguous"
        and type(candidate_count) is int
        and 2 <= candidate_count <= 1_000_000
    ):
        row["candidate_count"] = candidate_count
    _append_row(row, _state=_state)


def record_drain_failure(
    failure: ObservationIngestResult,
    envelope: ObservationEnvelope,
    disposition: str,
    *,
    _state: Path | None,
) -> bool:
    """Keep bounded causal facts; source identity is a commitment, never raw input."""

    from yoetz.application.observation_drain import ObservationControlFailure

    control = failure if isinstance(failure, ObservationControlFailure) else None
    reason = failure.reason
    correlation = None if control is None else control.correlation_id
    stage = "typed_ingest" if control is None else control.failure_stage
    try:
        session = validate_commitment(envelope.session_commitment)
        source = validate_commitment(envelope.cursor.last_source_commitment)
        if correlation is not None:
            validate_id(IdKind.CORRELATION, correlation)
        if control is not None and control.control_reason not in _CONTROL_REASONS:
            return False
        if reason not in _REASONS or stage not in {
            "control",
            "request_encode",
            "response_decode",
            "typed_ingest",
        }:
            return False
        if disposition not in {"retry", "quarantine"}:
            return False
    except ValueError, TypeError:
        return False
    return _append_row(
        {
            "kind": "drain_failure",
            "event": "drain",
            "ts": _timestamp(),
            "reason": reason,
            "control_reason": None if control is None else control.control_reason,
            "control_retryable": None if control is None else control.control_retryable,
            "stage": stage,
            "disposition": disposition,
            "correlation_id": correlation,
            "session_commitment": session,
            "source_commitment": source,
            "source": envelope.source.value,
            "generation": envelope.cursor.source_generation,
            "position": envelope.cursor.event_position,
        },
        _state=_state,
    )


def _append_row(row: dict[str, object], *, _state: Path | None) -> bool:
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
                    return False
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
        return False
    return True


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


def _empty_recent_values() -> list[int]:
    return []


def _empty_timings_by_path() -> dict[str, _Timings]:
    return {}


@dataclass(slots=True)
class _Timings:
    """Timing rows, kept datable so a one-off extreme is not read as the norm."""

    count: int = 0
    recent: int = 0
    last_ms: int | None = None
    max_ms: int | None = None
    max_at: datetime | None = None
    recent_max_ms: int | None = None
    recent_values: list[int] = field(default_factory=_empty_recent_values)
    by_path: dict[str, _Timings] = field(default_factory=_empty_timings_by_path)

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


def _read_rows(
    directory: Path,
) -> tuple[list[dict[str, str]], list[tuple[int, str, str | None]], list[JsonObject]]:
    """Return retained failure rows and timing rows, oldest retained file first."""

    rows: list[dict[str, str]] = []
    timings: list[tuple[int, str, str | None]] = []
    attempts: list[JsonObject] = []
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
                if row.get("kind") == "drain_failure":
                    allowed = {
                        "kind",
                        "event",
                        "ts",
                        "reason",
                        "control_reason",
                        "control_retryable",
                        "stage",
                        "disposition",
                        "correlation_id",
                        "session_commitment",
                        "source_commitment",
                        "source",
                        "generation",
                        "position",
                    }
                    if set(row) != allowed:
                        continue
                    # Files are mutable local input: validate every projected value.
                    try:
                        reason = row["control_reason"]
                        if reason is None:
                            if (
                                row["reason"] not in _REASONS
                                or row["stage"] != "typed_ingest"
                                or row["control_retryable"] is not None
                            ):
                                continue
                        elif type(reason) is not str or reason not in _CONTROL_REASONS:
                            continue
                        elif (
                            row["reason"] != "control_" + reason
                            or type(row["control_retryable"]) is not bool
                        ):
                            continue
                        if row["event"] != "drain":
                            continue
                        for key in ("session_commitment", "source_commitment"):
                            validate_commitment(cast(str, row[key]))
                        if row["correlation_id"] is not None:
                            validate_id(IdKind.CORRELATION, cast(str, row["correlation_id"]))
                        if row["stage"] not in {
                            "control",
                            "request_encode",
                            "response_decode",
                            "typed_ingest",
                        }:
                            continue
                        if row["disposition"] not in {"retry", "quarantine"}:
                            continue
                        if row["source"] not in {item.value for item in ObservationSource}:
                            continue
                        if any(
                            type(row[key]) is not int or not 0 <= cast(int, row[key]) <= 2**53 - 1
                            for key in ("generation", "position")
                        ):
                            continue
                        if type(row["ts"]) is not str or _parse_timestamp(row["ts"]) is None:
                            continue
                        attempts.append(JsonObject(cast(dict[str, JsonValue], row)))
                    except ValueError, TypeError:
                        continue
                    continue
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
        return [], [], []
    return rows, timings, attempts


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
    rows, timings, attempts = _read_rows(root / "observation")
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
            "drain_failures": tuple(attempts[-32:]),
            "drain_failure_retained_count": len(attempts),
            "drain_failure_history_complete": False,
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
