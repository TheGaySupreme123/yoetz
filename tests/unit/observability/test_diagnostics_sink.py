"""Durable owner-only diagnostic ring for correlation-id follow-up."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yoetz.observability.diagnostics import (
    append_diagnostic_record,
    diagnostic_log_path,
    lookup_diagnostic_records,
)
from yoetz.observability.logging import (
    LogMode,
    configure_logging,
    record_unexpected_exception_without_raising,
)
from yoetz.config.models import LoggingConfig
from builders.clock import frozen_clock

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
    assert lookup_diagnostic_records(
        "err_00000000-0000-4000-8000-000000000099", root=tmp_path
    ) == ()


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
