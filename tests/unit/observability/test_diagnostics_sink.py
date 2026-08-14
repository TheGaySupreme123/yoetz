"""Durable owner-only diagnostic ring for correlation-id follow-up."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from builders.clock import frozen_clock
from yoetz.config.models import LoggingConfig
from yoetz.observability.diagnostics import (
    append_diagnostic_record,
    diagnostic_log_path,
    lookup_diagnostic_records,
)
from yoetz.observability.logging import (
    LogMode,
    configure_logging,
    exception_origin,
    record_bounded_counts_without_raising,
    record_unexpected_exception_without_raising,
)
from yoetz.protocol.ids import IdKind, validate_id

_CORRELATION = "err_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_REQUEST = "req_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)


def test_append_and_lookup_round_trip(tmp_path: Path) -> None:
    append_diagnostic_record(
        correlation_id=_CORRELATION,
        component="service.daemon",
        operation="publish_work_response_projection_failed",
        reason="exception_runtime_error",
        request_id=_REQUEST,
        root=tmp_path,
        now=_NOW,
    )
    path = diagnostic_log_path(root=tmp_path)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_text(encoding="ascii")
    line = json.loads(raw)
    assert line == {
        "timestamp": "2026-07-27T12:00:00.000Z",
        "correlation_id": _CORRELATION,
        "component": "service.daemon",
        "operation": "publish_work_response_projection_failed",
        "reason": "exception_runtime_error",
        "request_id": _REQUEST,
    }
    # No payload / exception / path leakage surface.
    assert "traceback" not in raw
    assert "RuntimeError" not in raw
    assert "payload" not in raw

    found = lookup_diagnostic_records(_CORRELATION, root=tmp_path)
    assert len(found) == 1
    assert found[0]["correlation_id"] == _CORRELATION
    assert found[0]["request_id"] == _REQUEST


def test_lookup_miss_returns_empty(tmp_path: Path) -> None:
    append_diagnostic_record(
        correlation_id=_CORRELATION,
        component="service.daemon",
        operation="publish_work_response_projection_failed",
        reason="exception_runtime_error",
        root=tmp_path,
        now=_NOW,
    )
    assert (
        lookup_diagnostic_records("err_00000000-0000-4000-8000-000000000099", root=tmp_path) == ()
    )


def test_size_cap_keeps_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "_MAX_FILE_BYTES", 400)
    for index in range(30):
        append_diagnostic_record(
            correlation_id=f"err_{index:08x}-0000-4000-8000-000000000000",
            component="service.daemon",
            operation="publish_work_response_projection_failed",
            reason="exception_runtime_error",
            request_id=_REQUEST,
            root=tmp_path,
            now=_NOW,
        )
    path = diagnostic_log_path(root=tmp_path)
    size = path.stat().st_size
    assert size <= 400 + 200  # one record may overshoot until the next trim
    text = path.read_text(encoding="ascii")
    assert "err_00000000-0000-4000-8000-000000000000" not in text
    assert text.count("\n") >= 1


def test_record_unexpected_exception_writes_durable_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    configure_logging(
        LoggingConfig(level="error"),  # type: ignore[arg-type]
        LogMode.SERVICE,
        clock=frozen_clock(utc=_NOW, monotonic=0.0),
    )
    correlation_id = record_unexpected_exception_without_raising(
        RuntimeError("must-not-leak"),
        component="service.daemon",
        operation="publish_work_response_projection_failed",
        request_id=_REQUEST,
    )
    assert correlation_id.startswith("err_")
    found = lookup_diagnostic_records(correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["reason"] == "exception_runtime_error"
    assert found[0]["request_id"] == _REQUEST
    err = capsys.readouterr().err
    assert "must-not-leak" not in err
    assert correlation_id in err


def test_unexpected_exception_records_a_bounded_yoetz_origin() -> None:
    """An unexpected exception must say *where* in our code it came from.

    Keeping only the exception class made the production ``status(view=operation)`` AttributeError
    undiagnosable: a dozen guarded call sites raise the same class, so the class alone cannot
    point at any of them and the only route to a fix is a reproduction the durable diagnostic
    exists precisely to avoid needing.
    """

    origin: str | None = None
    try:
        validate_id(IdKind.REQUEST, "not-a-request-id")
    except Exception as exc:
        origin = exception_origin(exc)

    assert origin is not None
    module, _, lineno = origin.partition(":")
    assert module.startswith("yoetz.")
    assert lineno.isdigit()


def test_origin_names_our_frame_not_the_caller_that_caught_it() -> None:
    """The location is where our code raised, not the test frame that observed it."""

    origin: str | None = None
    try:
        validate_id(IdKind.REQUEST, "still-not-valid")
    except Exception as exc:
        origin = exception_origin(exc)

    assert origin is not None
    # The test module is not a ``yoetz`` module, so it must never be the reported origin.
    assert not origin.startswith("tests")
    assert origin.startswith("yoetz.protocol")


def test_origin_is_absent_for_exceptions_with_no_yoetz_frame() -> None:
    """Third-party and traceback-less exceptions report nothing rather than a foreign path."""

    assert exception_origin(ValueError("never raised, so no traceback")) is None


def test_diagnostic_origin_is_rejected_unless_it_is_a_yoetz_source_location(
    tmp_path: Path,
) -> None:
    """The written field can only ever be a yoetz module and a line number.

    The frame walk reads only the module name and traceback line, and the allowlist independently
    guarantees a filesystem path or a user value can never reach the durable record.
    """

    correlation_id = "err_00000000-0000-4000-8000-0000000000f1"
    append_diagnostic_record(
        correlation_id=correlation_id,
        component="service.daemon",
        operation="status_read_projection_failed",
        reason="exception_attribute_error",
        origin="yoetz/application/status.py:12",
        root=tmp_path,
    )
    records = lookup_diagnostic_records(correlation_id, root=tmp_path)
    assert len(records) == 1
    assert records[0]["origin"] == "unavailable"

    good = "err_00000000-0000-4000-8000-0000000000f2"
    append_diagnostic_record(
        correlation_id=good,
        component="service.daemon",
        operation="status_read_projection_failed",
        reason="exception_attribute_error",
        origin="yoetz.application.status:1097",
        root=tmp_path,
    )
    assert lookup_diagnostic_records(good, root=tmp_path)[0]["origin"] == (
        "yoetz.application.status:1097"
    )

    bare = "err_00000000-0000-4000-8000-0000000000f3"
    append_diagnostic_record(
        correlation_id=bare,
        component="service.daemon",
        operation="status_read_projection_failed",
        reason="exception_attribute_error",
        origin="yoetz:12",
        root=tmp_path,
    )
    assert lookup_diagnostic_records(bare, root=tmp_path)[0]["origin"] == "yoetz:12"


def test_bounded_counts_reach_the_durable_ring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loop-health counters are useless on stderr alone; the ring is what an operator reads."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    configure_logging(
        LoggingConfig(level="error"),  # type: ignore[arg-type]
        LogMode.SERVICE,
        clock=frozen_clock(utc=_NOW, monotonic=0.0),
    )

    correlation_id = record_bounded_counts_without_raising(
        component="service.daemon",
        operation="control_plane_saturation_entered",
        outcome="event_loop_lag",
        counts={"duration_ms": 12_345, "operation_count": 7},
    )

    found = lookup_diagnostic_records(correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "control_plane_saturation_entered"
    assert found[0]["reason"] == "event_loop_lag"
    assert found[0]["duration_ms"] == 12_345
    assert found[0]["operation_count"] == 7
