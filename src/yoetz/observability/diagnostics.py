"""Owner-only durable diagnostic records for correlation-id follow-up.

Stderr is the primary structured sink, but MCP-spawned services swallow it. These records live
under ``log_dir()`` so an operator (or plan-05 reproduction) can resolve a public ``err_…`` id
without relying on harness capture.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Final, cast

from yoetz.config.paths import ensure_owner_only_dir, log_dir
from yoetz.domain.values import format_rfc3339_millis
from yoetz.observability.privacy import assert_plaintext_safe, redact_diagnostic_value
from yoetz.protocol.ids import IdKind, validate_id

__all__ = [
    "DIAGNOSTIC_FILENAME",
    "append_diagnostic_record",
    "diagnostic_log_path",
    "lookup_diagnostic_records",
]

DIAGNOSTIC_FILENAME: Final = "service.diagnostics.jsonl"
_MAX_RECORD_BYTES: Final = 1_024
_MAX_FILE_BYTES: Final = 256 * 1_024
_FIELD_ORDER: Final = (
    "timestamp",
    "correlation_id",
    "component",
    "operation",
    "reason",
    "request_id",
)
_lock = Lock()


def diagnostic_log_path(*, root: Path | None = None) -> Path:
    """Return the owner-only diagnostic ring path under the platform log directory."""

    base = log_dir() if root is None else root
    return base / DIAGNOSTIC_FILENAME


def append_diagnostic_record(
    *,
    correlation_id: str,
    component: str,
    operation: str,
    reason: str,
    request_id: str | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Append one bounded diagnostic line. Best-effort; never raises to callers."""

    try:
        record = _build_record(
            correlation_id=correlation_id,
            component=component,
            operation=operation,
            reason=reason,
            request_id=request_id,
            now=now,
        )
        if record is None:
            return
        encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > _MAX_RECORD_BYTES:
            return
        assert_plaintext_safe(encoded, "diagnostic_record")
        path = diagnostic_log_path(root=root)
        _append_line(path, encoded + b"\n")
    except BaseException:
        return


def lookup_diagnostic_records(
    correlation_id: str,
    *,
    root: Path | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Return every durable record matching one correlation id, oldest first."""

    validate_id(IdKind.CORRELATION, correlation_id)
    path = diagnostic_log_path(root=root)
    if not path.is_file() or path.is_symlink():
        return ()
    matches: list[Mapping[str, object]] = []
    with path.open("rb") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed: object = json.loads(line.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if type(parsed) is not dict:
                continue
            source = cast(dict[str, object], parsed)
            if source.get("correlation_id") != correlation_id:
                continue
            matches.append(_public_view(source))
    return tuple(matches)


def _build_record(
    *,
    correlation_id: str,
    component: str,
    operation: str,
    reason: str,
    request_id: str | None,
    now: datetime | None,
) -> dict[str, object] | None:
    try:
        validate_id(IdKind.CORRELATION, correlation_id)
    except BaseException:
        return None
    instant = now if now is not None else datetime.now(UTC)
    # Structural timestamps are millisecond-quantized (same rule as the stderr sink clock).
    instant = instant.replace(microsecond=(instant.microsecond // 1_000) * 1_000)
    timestamp = format_rfc3339_millis(instant)
    raw: dict[str, object] = {
        "timestamp": timestamp,
        "correlation_id": correlation_id,
        "component": component,
        "operation": operation,
        "reason": reason,
    }
    if request_id is not None:
        raw["request_id"] = request_id
    output: dict[str, object] = {}
    for name in _FIELD_ORDER:
        if name not in raw:
            continue
        value = redact_diagnostic_value(name, raw[name])
        if value is None:
            if name in {"correlation_id", "component", "operation", "reason", "timestamp"}:
                return None
            continue
        output[name] = value
    return output


def _public_view(source: Mapping[str, object]) -> Mapping[str, object]:
    output: dict[str, object] = {}
    for name in _FIELD_ORDER:
        if name not in source:
            continue
        value = redact_diagnostic_value(name, source[name])
        if value is not None:
            output[name] = value
    return output


def _prepare_parent(parent: Path) -> None:
    """Prefer the private-local gate; fall back to a best-effort owner-only mkdir."""

    try:
        ensure_owner_only_dir(parent)
        return
    except BaseException:
        # Tests and unusual layouts (symlinked temp roots) still need a durable record. File mode
        # is enforced on the open descriptor below.
        pass
    parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        return


def _append_line(path: Path, line: bytes) -> None:
    _prepare_parent(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with _lock:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, line)
            try:
                size = os.fstat(descriptor).st_size
            except OSError:
                size = 0
        finally:
            os.close(descriptor)
        if size > _MAX_FILE_BYTES:
            _trim_file(path)


def _trim_file(path: Path) -> None:
    """Keep a suffix of the ring so the file stays under the size cap."""

    try:
        raw = path.read_bytes()
    except OSError:
        return
    if len(raw) <= _MAX_FILE_BYTES:
        return
    # Drop whole lines from the front until under the cap.
    keep_from = len(raw) - _MAX_FILE_BYTES
    newline = raw.find(b"\n", keep_from)
    trimmed = raw[newline + 1 :] if newline != -1 else raw[-_MAX_FILE_BYTES:]
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, trimmed)
    finally:
        os.close(descriptor)
    try:
        mode = path.stat().st_mode
        if stat.S_IMODE(mode) != 0o600:
            path.chmod(0o600)
    except OSError:
        return
