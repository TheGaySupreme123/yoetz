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
from types import MappingProxyType
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
    "reason",
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


_UNKNOWN_EXCEPTION_REASON: Final = "exception_unavailable"
# Closed reviewed registry of exception-class names that may identify an internal failure. Every
# token is a fixed literal; an unlisted class collapses to the sentinel so no class name a caller
# can influence is ever rendered. Storage/authorizer entries carry the durability failures that
# reach the daemon's unexpected-dispatch path.
_EXCEPTION_REASONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ArithmeticError": "exception_arithmetic_error",
        "AssertionError": "exception_assertion_error",
        "AttributeError": "exception_attribute_error",
        "AuthError": "exception_auth_error",
        "BlockingIOError": "exception_blocking_io_error",
        "BrokenPipeError": "exception_broken_pipe_error",
        "BusyError": "exception_busy_error",
        "CantOpenError": "exception_cant_open_error",
        "ConfigError": "exception_config_error",
        "ConnectionError": "exception_connection_error",
        "ConnectionRefusedError": "exception_connection_refused_error",
        "ConnectionResetError": "exception_connection_reset_error",
        "ConstraintError": "exception_constraint_error",
        "ControlError": "exception_control_error",
        "ControlProtocolError": "exception_control_protocol_error",
        "CorruptError": "exception_corrupt_error",
        "EOFError": "exception_eof_error",
        "FileExistsError": "exception_file_exists_error",
        "FileNotFoundError": "exception_file_not_found_error",
        "FullError": "exception_full_error",
        "IndexError": "exception_index_error",
        "InterruptedError": "exception_interrupted_error",
        "IsADirectoryError": "exception_is_a_directory_error",
        "KeyError": "exception_key_error",
        "LifecycleError": "exception_lifecycle_error",
        "LookupError": "exception_lookup_error",
        "MemoryError": "exception_memory_error",
        "MisuseError": "exception_misuse_error",
        "NotADBError": "exception_not_a_db_error",
        "NotADirectoryError": "exception_not_a_directory_error",
        "NotImplementedError": "exception_not_implemented_error",
        "OSError": "exception_os_error",
        "OverflowError": "exception_overflow_error",
        "PathSafetyError": "exception_path_safety_error",
        "PermissionError": "exception_permission_error",
        "ProtocolValueError": "exception_protocol_value_error",
        "PublicOperationError": "exception_public_operation_error",
        "ReadOnlyError": "exception_read_only_error",
        "RecursionError": "exception_recursion_error",
        "ReferenceError": "exception_reference_error",
        "RuntimeError": "exception_runtime_error",
        "SQLError": "exception_sql_error",
        "StopAsyncIteration": "exception_stop_async_iteration",
        "StopIteration": "exception_stop_iteration",
        "StorageUnsafeError": "exception_storage_unsafe_error",
        "SystemError": "exception_system_error",
        "ThreadingViolationError": "exception_threading_violation_error",
        "TimeoutError": "exception_timeout_error",
        "TypeError": "exception_type_error",
        "UnicodeDecodeError": "exception_unicode_decode_error",
        "UnicodeEncodeError": "exception_unicode_encode_error",
        "ValidationError": "exception_validation_error",
        "ValueError": "exception_value_error",
        "ZeroDivisionError": "exception_zero_division_error",
    }
)


def _new_correlation() -> str:
    try:
        return new_id(IdKind.CORRELATION)
    except BaseException:
        return _FALLBACK_CORRELATION


def _exception_reason(exc: BaseException) -> str:
    """Return a reviewed structural exception identity without formatting the exception."""

    # Deriving the token from the class name would let a dynamically created exception class carry
    # caller data onto stderr, so only these reviewed names ever resolve to a reason.
    try:
        name = type(exc).__name__
    except BaseException:
        return _UNKNOWN_EXCEPTION_REASON
    return _EXCEPTION_REASONS.get(name, _UNKNOWN_EXCEPTION_REASON)


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


def record_unexpected_exception_without_raising(
    exc: BaseException,
    *,
    component: str = "process_boundary",
    operation: str = "unexpected_exception",
    request_id: str | None = None,
) -> str:
    """Emit bounded structural identity for an unexpected internal exception.

    ``request_id`` is the caller's own already-public request identity. Passing it lets the
    service-side and bridge-side records for one failed call be joined exactly, instead of
    guessing from method and timestamp when two calls overlap.
    """

    correlation_id = _new_correlation()
    try:
        get_logger(component).error(
            operation,
            correlation_id=correlation_id,
            request_id=request_id,
            outcome="internal_error",
            reason=_exception_reason(exc),
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
