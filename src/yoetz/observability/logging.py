"""Structured, allowlisted, stderr-only process logging."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import Final, cast

from yoetz.config.models import LoggingConfig
from yoetz.domain.values import format_rfc3339_millis
from yoetz.observability.privacy import (
    assert_plaintext_safe,
    redact_diagnostic_value,
)
from yoetz.ports.clock import ClockPort
from yoetz.protocol.ids import IdKind, new_id

__all__ = [
    "LogMode",
    "StructuredLogger",
    "configure_logging",
    "get_logger",
    "record_fatal_exception_without_raising",
    "record_unexpected_exception_without_raising",
]

_FIELD_ORDER: Final = (
    "timestamp",
    "level",
    "component",
    "operation",
    "correlation_id",
    "session_id_hash",
    "request_id",
    "duration_ms",
    "outcome",
    "engine_version",
    "policy_version",
    "sqlite_source_id_hash",
)
_FIELD_SET: Final = frozenset(_FIELD_ORDER)
_CALLER_FIELDS: Final = _FIELD_SET - {"timestamp", "level", "component", "operation"}
_STRUCTURAL_MARKER: Final = "_yoetz_structured_record"
_STRUCTURAL_FIELDS: Final = "_yoetz_structured_fields"
_COMPONENT_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$", re.ASCII)
_MAX_RECORD_BYTES: Final = 4_096
_FALLBACK_CORRELATION: Final = "err_00000000-0000-4000-8000-000000000000"

_LEVEL_BY_NAME: Final = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_configured_lock = Lock()
_handler: _StructuredStderrHandler | None = None
_clock: ClockPort | None = None
_dropped_warning_emitted = False


class LogMode(str, Enum):  # noqa: UP042 - frozen process-mode vocabulary
    SERVICE = "service"
    CLI = "cli"
    MCP_STDIO = "mcp_stdio"
    CONFIDENTIAL_HELPER = "confidential_helper"


class _SystemClock:
    def now_utc(self) -> datetime:
        now = datetime.now(UTC)
        return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)

    def monotonic_seconds(self) -> float:
        # Logging uses only UTC metadata; the method completes ClockPort without another clock API.
        return 0.0


def _new_correlation() -> str:
    try:
        return new_id(IdKind.CORRELATION)
    except BaseException:
        return _FALLBACK_CORRELATION


def _level_name(level_number: int) -> str:
    if level_number >= logging.ERROR:
        return "error"
    if level_number >= logging.WARNING:
        return "warning"
    if level_number >= logging.INFO:
        return "info"
    return "debug"


class _StructuralFilter(logging.Filter):
    """Destroy every unstructured message before it reaches a formatter or sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        marker = getattr(record, _STRUCTURAL_MARKER, False)
        fields = getattr(record, _STRUCTURAL_FIELDS, None)
        if marker is True and type(fields) is dict:
            safe_fields = cast(dict[str, object], fields)
        else:
            safe_fields = {
                "component": "sdk",
                "operation": "filtered_record",
                "correlation_id": _new_correlation(),
            }
        # These assignments intentionally erase the original object graph without formatting it.
        record.msg = ""
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        setattr(record, _STRUCTURAL_MARKER, True)
        setattr(record, _STRUCTURAL_FIELDS, safe_fields)
        return True


class _StructuredStderrHandler(logging.Handler):
    """A formatter-free handler that resolves stderr only at emission time."""

    def __init__(self, *, level: int, clock: ClockPort) -> None:
        super().__init__(level=level)
        self._clock = clock
        self.addFilter(_StructuralFilter())

    def set_clock(self, clock: ClockPort) -> None:
        self._clock = clock

    def emit(self, record: logging.LogRecord) -> None:
        try:
            source = getattr(record, _STRUCTURAL_FIELDS, None)
            if type(source) is not dict:
                return
            fields = cast(dict[str, object], source)
            output: dict[str, object] = {
                "timestamp": format_rfc3339_millis(self._clock.now_utc()),
                "level": _level_name(record.levelno),
            }
            for name in _FIELD_ORDER[2:]:
                if name not in fields:
                    continue
                value = redact_diagnostic_value(name, fields[name])
                if value is not None:
                    output[name] = value
            encoded = json.dumps(
                output,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            if len(encoded) > _MAX_RECORD_BYTES:
                return
            assert_plaintext_safe(encoded, "structured_log")
            sys.stderr.write(encoded.decode("ascii") + "\n")
            sys.stderr.flush()
        except BaseException:
            return


class StructuredLogger:
    """Bounded logger with no generic message/argument surface."""

    __slots__ = ("_component", "_logger")

    def __init__(self, component: str) -> None:
        if type(component) is not str or _COMPONENT_PATTERN.fullmatch(component) is None:
            raise ValueError("log_component_invalid")
        self._component = component
        self._logger = logging.getLogger(f"yoetz.{component}")

    def debug(self, operation: str, **fields: object) -> None:
        self._emit(logging.DEBUG, operation, fields)

    def info(self, operation: str, **fields: object) -> None:
        self._emit(logging.INFO, operation, fields)

    def warning(self, operation: str, **fields: object) -> None:
        self._emit(logging.WARNING, operation, fields)

    def error(self, operation: str, **fields: object) -> None:
        self._emit(logging.ERROR, operation, fields)

    def _emit(self, level: int, operation: str, fields: Mapping[str, object]) -> None:
        if type(operation) is not str or _COMPONENT_PATTERN.fullmatch(operation) is None:
            raise ValueError("log_operation_invalid")
        unknown = tuple(name for name in fields if name not in _CALLER_FIELDS)
        if unknown:
            if __debug__:
                raise AssertionError("log_field_not_allowlisted")
            _record_dropped_field_once()

        safe: dict[str, object] = {"component": self._component, "operation": operation}
        for name in _CALLER_FIELDS:
            if name not in fields or fields[name] is None:
                continue
            value = redact_diagnostic_value(name, fields[name])
            safe[name] = value
        try:
            self._logger.log(
                level,
                "",
                extra={_STRUCTURAL_MARKER: True, _STRUCTURAL_FIELDS: safe},
            )
        except KeyboardInterrupt, SystemExit:
            raise
        except BaseException:
            return


def _record_dropped_field_once() -> None:
    global _dropped_warning_emitted
    if _dropped_warning_emitted:
        return
    _dropped_warning_emitted = True
    try:
        StructuredLogger("observability").warning(
            "field_rejected",
            outcome="log_field_dropped",
        )
    except BaseException:
        return


def _remove_existing_handlers() -> None:
    loggers: list[logging.Logger] = [logging.getLogger()]
    for candidate in logging.root.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            loggers.append(candidate)
    for logger in loggers:
        for existing in tuple(logger.handlers):
            logger.removeHandler(existing)
        if logger is not logging.getLogger():
            logger.propagate = True


def configure_logging(
    config: LoggingConfig,
    mode: LogMode,
    *,
    clock: ClockPort | None = None,
) -> None:
    """Install one stderr-only structural sink and sanitize all dependency records."""

    global _clock, _handler
    if type(mode) is not LogMode:
        raise TypeError("log_mode_invalid")
    selected_clock = clock if clock is not None else _SystemClock()
    level = _LEVEL_BY_NAME[config.level]
    with _configured_lock:
        _remove_existing_handlers()
        handler = _StructuredStderrHandler(level=level, clock=selected_clock)
        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(handler)
        logging.lastResort = handler
        logging.raiseExceptions = False
        _clock = selected_clock
        _handler = handler


def get_logger(component: str) -> StructuredLogger:
    """Return a structured logger for one reviewed constant component identity."""

    return StructuredLogger(component)


def record_unexpected_exception_without_raising(exc: BaseException) -> str:
    """Emit only a new correlation identity for an unexpected internal exception."""

    del exc
    correlation_id = _new_correlation()
    try:
        get_logger("process_boundary").error(
            "unexpected_exception",
            correlation_id=correlation_id,
            outcome="internal_error",
        )
    except BaseException:
        pass
    return correlation_id


def record_fatal_exception_without_raising(exc: BaseException) -> None:
    """Best-effort fatal-boundary structural logging with no exception inspection."""

    del exc
    try:
        get_logger("process_boundary").error(
            "fatal_exception",
            correlation_id=_new_correlation(),
            outcome="internal_error",
        )
    except BaseException:
        pass
