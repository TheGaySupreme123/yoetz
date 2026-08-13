"""Report control-plane saturation from a thread a blocked event loop cannot stall."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Final

from yoetz.observability.logging import record_bounded_counts_without_raising

__all__ = ["ControlPlaneWatchdog"]

_COMPONENT: Final = "service.daemon"
# The loop-side task rewrites the heartbeat at this cadence; lag is measured against it, so a
# healthy loop reads as roughly zero lag rather than one interval of it.
_HEARTBEAT_INTERVAL_SECONDS: Final = 0.5
_WATCHDOG_SAMPLE_SECONDS: Final = 1.0
# Lag at which the control plane is no longer serviceable: a handshake deadline is 5 s, so three
# seconds of unserviced loop is already most of a client's budget.
_SATURATION_ENTER_SECONDS: Final = 3.0
_SATURATION_CLEAR_SECONDS: Final = 1.0
# A saturation that lasts minutes must stay visible without becoming its own log storm.
_SATURATION_REPEAT_SECONDS: Final = 60.0


def _record(operation: str, seconds: float, connections: int) -> None:
    record_bounded_counts_without_raising(
        component=_COMPONENT,
        operation=operation,
        outcome="event_loop_lag",
        counts={"duration_ms": int(seconds * 1_000), "operation_count": connections},
    )


class ControlPlaneWatchdog:
    """Say, while it is happening, that the daemon's event loop stopped serving.

    An asyncio-only probe cannot fire while the loop is blocked -- exactly the state worth
    reporting -- so an outage would only ever be described after it ended. A plain OS thread
    samples a monotonic heartbeat the loop writes and emits on its own, which is why a
    multi-minute control-plane outage can be seen while it is still going on (#238).
    """

    def __init__(
        self,
        *,
        connections_in_flight: Callable[[], int],
        monotonic: Callable[[], float] = time.monotonic,
        emit: Callable[[str, float, int], None] = _record,
    ) -> None:
        self._connections_in_flight = connections_in_flight
        self._monotonic = monotonic
        self._emit = emit
        self._beat = monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._saturated = False
        self._entered_at = 0.0
        self._last_report = 0.0

    async def run(self) -> None:
        """Rewrite the heartbeat from the event loop until the watchdog is closed."""

        while not self._stop.is_set():
            self.note_heartbeat()
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    def note_heartbeat(self) -> None:
        self._beat = self._monotonic()

    def sample(self) -> None:
        """Take one observation. Separated from the thread body so it can be driven directly."""

        now = self._monotonic()
        lag = now - self._beat - _HEARTBEAT_INTERVAL_SECONDS
        if not self._saturated:
            if lag >= _SATURATION_ENTER_SECONDS:
                self._saturated = True
                self._entered_at = now
                self._last_report = now
                self._emit("control_plane_saturation_entered", lag, self._in_flight())
            return
        if lag < _SATURATION_CLEAR_SECONDS:
            self._saturated = False
            self._emit(
                "control_plane_saturation_cleared",
                now - self._entered_at,
                self._in_flight(),
            )
            return
        if (
            lag >= _SATURATION_ENTER_SECONDS
            and now - self._last_report >= _SATURATION_REPEAT_SECONDS
        ):
            self._last_report = now
            self._emit("control_plane_saturation_persists", lag, self._in_flight())

    def start_thread(self) -> None:
        if self._thread is not None:
            raise RuntimeError("control_plane_watchdog_already_started")
        thread = threading.Thread(
            target=self._run_thread,
            name="yoetz-control-plane-watchdog",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def close(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=_WATCHDOG_SAMPLE_SECONDS * 2)

    def _run_thread(self) -> None:
        while not self._stop.wait(_WATCHDOG_SAMPLE_SECONDS):
            try:
                self.sample()
            except Exception:
                # A watchdog that can raise is a watchdog that can take the process with it.
                return

    def _in_flight(self) -> int:
        try:
            value = self._connections_in_flight()
        except Exception:
            return 0
        return value if type(value) is int and value >= 0 else 0
