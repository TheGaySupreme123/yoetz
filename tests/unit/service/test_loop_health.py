"""Control-plane saturation must be reportable while the event loop is still blocked."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import yoetz.observability.diagnostics as diagnostics
import yoetz.service.loop_health as loop_health
from yoetz.domain.values import parse_rfc3339_millis
from yoetz.observability.diagnostics import diagnostic_log_path
from yoetz.service.loop_health import ControlPlaneWatchdog


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(slots=True)
class _Clock:
    now: float = 100.0

    def monotonic(self) -> float:
        return self.now


def test_watchdog_reports_entry_and_exit_with_bounded_cadence() -> None:
    clock = _Clock()
    records: list[tuple[str, int, int]] = []

    def emit(operation: str, seconds: float, connections: int) -> None:
        records.append((operation, int(seconds * 1_000), connections))

    watchdog = ControlPlaneWatchdog(
        connections_in_flight=lambda: 4,
        monotonic=clock.monotonic,
        emit=emit,
    )
    watchdog.note_heartbeat()

    clock.now = 101.0
    watchdog.sample()
    assert records == []

    clock.now = 105.0
    watchdog.sample()
    assert [record[0] for record in records] == ["control_plane_saturation_entered"]
    assert records[0][1] >= 3_000
    assert records[0][2] == 4

    # Still saturated, but inside the repeat window: silence is the bound, not a missed sample.
    clock.now = 140.0
    watchdog.sample()
    assert len(records) == 1

    clock.now = 166.0
    watchdog.sample()
    assert len(records) == 2
    assert records[1][0] == "control_plane_saturation_persists"

    watchdog.note_heartbeat()
    clock.now = 166.4
    watchdog.sample()
    assert len(records) == 3
    assert records[2][0] == "control_plane_saturation_cleared"
    assert records[2][1] == int((166.4 - 105.0) * 1_000)


@pytest.mark.anyio
async def test_watchdog_emits_while_the_loop_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outage must be described during the outage; an asyncio-only probe cannot do this."""

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    monkeypatch.setattr(loop_health, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(loop_health, "_WATCHDOG_SAMPLE_SECONDS", 0.05)
    monkeypatch.setattr(loop_health, "_SATURATION_ENTER_SECONDS", 0.15)
    monkeypatch.setattr(loop_health, "_SATURATION_CLEAR_SECONDS", 0.05)

    watchdog = ControlPlaneWatchdog(connections_in_flight=lambda: 2)
    watchdog.start_thread()
    beating = asyncio.create_task(watchdog.run())
    try:
        await asyncio.sleep(0.1)
        time.sleep(0.6)
        resumed = datetime.now(UTC)
    finally:
        watchdog.close()
        beating.cancel()
        await asyncio.gather(beating, return_exceptions=True)

    path = diagnostic_log_path(root=tmp_path)
    assert path.is_file()
    lines = [
        json.loads(line) for line in path.read_text(encoding="ascii").splitlines() if line.strip()
    ]
    entered = [line for line in lines if line["operation"] == "control_plane_saturation_entered"]
    assert len(entered) == 1
    assert entered[0]["duration_ms"] >= 150
    assert entered[0]["operation_count"] == 2
    assert parse_rfc3339_millis(entered[0]["timestamp"]) <= resumed
