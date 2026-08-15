"""Agent-facing correlation ids on ControlError must resolve to the service diagnostic sink.

Plan 05 (run-4 residual): the id written to the durable diagnostic ring and the id handed to the
caller must be the same string for any single failure. The bridge reuses a service-supplied id;
only bridge-local faults mint a fresh one.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import yoetz.mcp.server as bridge
from yoetz.config.models import LoggingConfig
from yoetz.observability.diagnostics import append_diagnostic_record, lookup_diagnostic_records
from yoetz.observability.logging import LogMode, configure_logging
from yoetz.ports.control import ControlError
from yoetz.protocol.errors import PublicErrorCode

_CORRELATION = "err_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_REQUEST = "req_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_bridge_reuses_service_correlation_for_response_projection_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check-shaped write failure must surface the daemon's id, not a second mint."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    append_diagnostic_record(
        correlation_id=_CORRELATION,
        component="service.daemon",
        operation="check_response_projection_failed",
        reason="exception_validation_error",
        request_id=_REQUEST,
        root=tmp_path,
    )

    result = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError(
            "response_projection_failed",
            retryable=True,
            correlation_id=_CORRELATION,
        ),
        request_id=_REQUEST,
        operation="check",
    )

    assert result.isError is True
    structured = cast(dict[str, object], result.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["code"] == PublicErrorCode.INTERNAL_ERROR.value
    assert error["retryable"] is True
    assert error["correlation_id"] == _CORRELATION
    found = lookup_diagnostic_records(_CORRELATION, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "check_response_projection_failed"
    # Sink stays structural — no exception text / payload / path.
    for record in found:
        for forbidden in ("traceback", "exception", "payload", "path", "message"):
            assert forbidden not in record


def test_bridge_reuses_service_correlation_for_read_projection_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A status read failure must surface the daemon's id so diagnostics resolve."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    append_diagnostic_record(
        correlation_id=_CORRELATION,
        component="service.daemon",
        operation="status_read_projection_failed",
        reason="exception_runtime_error",
        request_id=_REQUEST,
        root=tmp_path,
    )

    result = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError("read_projection_failed", retryable=True, correlation_id=_CORRELATION),
        request_id=_REQUEST,
        operation="status",
    )

    assert result.isError is True
    structured = cast(dict[str, object], result.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["correlation_id"] == _CORRELATION
    found = lookup_diagnostic_records(cast(str, error["correlation_id"]), root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "status_read_projection_failed"


def test_bridge_level_failure_without_service_id_still_resolves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bridge-originated ControlError without a service id mints and records under its own id."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    configure_logging(LoggingConfig(level="error"), LogMode.MCP_STDIO)  # type: ignore[arg-type]

    result = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError("internal_error"),
        request_id=_REQUEST,
        operation="check",
    )

    assert result.isError is True
    structured = cast(dict[str, object], result.structuredContent)
    error = cast(dict[str, object], structured["error"])
    correlation_id = cast(str, error["correlation_id"])
    assert correlation_id.startswith("err_")
    found = lookup_diagnostic_records(correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["component"] == "mcp.bridge"
    assert found[0]["operation"] == "check_internal_error"
    # stderr structural line matches the public id when the sink is installed.
    err = capsys.readouterr().err
    assert correlation_id in err
    assert "traceback" not in err.lower()


def test_bridge_does_not_re_record_when_service_id_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reusing a service id must not mint a second diagnostic record under a different id."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    append_diagnostic_record(
        correlation_id=_CORRELATION,
        component="service.daemon",
        operation="check_internal_error",
        reason="exception_runtime_error",
        request_id=_REQUEST,
        root=tmp_path,
    )
    recorded: list[str] = []

    def track(
        exc: BaseException,
        *,
        component: str = "process_boundary",
        operation: str = "unexpected_exception",
        request_id: str | None = None,
    ) -> str:
        del exc, component, operation, request_id
        recorded.append("minted")
        return "err_cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    monkeypatch.setattr(bridge, "record_unexpected_exception_without_raising", track)

    result = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError("internal_error", correlation_id=_CORRELATION),
        request_id=_REQUEST,
        operation="check",
    )

    structured = cast(dict[str, object], result.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["correlation_id"] == _CORRELATION
    assert recorded == []
    assert len(lookup_diagnostic_records(_CORRELATION, root=tmp_path)) == 1
    assert (
        lookup_diagnostic_records("err_cccccccc-cccc-4ccc-8ccc-cccccccccccc", root=tmp_path) == ()
    )


def test_vault_locked_message_and_retryable_follow_the_daemon_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retryable soft/transient lock must not be described as a terminal hard lock (#276).

    The daemon distinguishes a soft lock (heals on the next attempt) from a hard lock or
    missing setup (needs a ceremony) via ``retryable``. Discarding that flag once told an
    agent to abandon evidence and receipts a single retry would have recorded.
    """

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)

    transient = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError("vault_locked", retryable=True),
        request_id=_REQUEST,
        operation="publish_work",
    )
    structured = cast(dict[str, object], transient.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["code"] == PublicErrorCode.VAULT_LOCKED.value
    assert error["retryable"] is True
    message = cast(str, error["message"])
    assert "Retry this operation" in message
    assert "hard lock or missing setup" not in message

    terminal = bridge._control_error_result(  # pyright: ignore[reportPrivateUsage]
        ControlError("vault_locked", retryable=False),
        request_id=_REQUEST,
        operation="publish_work",
    )
    structured = cast(dict[str, object], terminal.structuredContent)
    error = cast(dict[str, object], structured["error"])
    assert error["code"] == PublicErrorCode.VAULT_LOCKED.value
    assert error["retryable"] is False
    message = cast(str, error["message"])
    assert "hard lock or missing setup" in message
