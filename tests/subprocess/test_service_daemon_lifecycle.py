from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_DAEMON_PROBE = r"""
import asyncio, os, sys
from datetime import UTC, datetime
from pathlib import Path
from yoetz.service.daemon import ServiceComposition, ServiceDaemon
from yoetz.service.lifecycle import LifecycleError, ServiceLifecycle

class Clock:
    def now_utc(self): return datetime(2026, 7, 19, tzinfo=UTC)
    def monotonic_seconds(self): return 1.0
class Store:
    def advance(self, instance_id): return 1
class Capability:
    active = True
class Monitor:
    capability = Capability()
    async def start(self, callback): self.callback = callback
    async def close(self): pass
class Listener:
    def __init__(self): self.closed = asyncio.Event()
    async def accept(self):
        await self.closed.wait()
        raise RuntimeError("closed")
    async def aclose(self): self.closed.set()
class Mode:
    value = "uninitialized"
class Vault:
    ready = False
    generation = 0
    mode = Mode()
    async def lock(self): pass
    async def close(self): pass

async def run():
    lifecycle = ServiceLifecycle(Clock(), generation_store=Store(),
        process_start_identity_commitment="sha256:" + "2" * 64,
        singleton_lock_path=Path(os.environ["YOETZ_TEST_LOCK"]))
    listener = Listener()
    composition = ServiceComposition(lifecycle, listener, None, None, None, Monitor(), Vault())
    daemon = ServiceDaemon(_composition=composition)
    try:
        await daemon.start()
    except LifecycleError as exc:
        print(exc.reason, flush=True)
        raise SystemExit(73)
    print("locked", flush=True)
    await daemon.serve()

asyncio.run(run())
"""


def _spawn(lock_path: Path) -> subprocess.Popen[str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.fspath(Path.cwd() / "src"),
        "YOETZ_TEST_LOCK": os.fspath(lock_path),
    }
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and in-repo probe
        [sys.executable, "-c", _DAEMON_PROBE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


def _readline_bounded(process: subprocess.Popen[str], seconds: float = 10.0) -> str:
    assert process.stdout is not None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line:
            return line.strip()
        if process.poll() is not None:
            break
        time.sleep(0.01)
    raise AssertionError("daemon did not publish bounded startup status")


def test_singleton_rejection_and_signal_driven_foreground_shutdown(tmp_path: Path) -> None:
    lock_path = tmp_path / "service.lock"
    owner = _spawn(lock_path)
    contender: subprocess.Popen[str] | None = None
    try:
        assert _readline_bounded(owner) == "locked"
        contender = _spawn(lock_path)
        contender_stdout, contender_stderr = contender.communicate(timeout=10)
        assert contender.returncode == 73
        assert contender_stdout.strip() == "service_already_running"
        assert contender_stderr == ""

        owner.send_signal(signal.SIGTERM)
        owner_stdout, owner_stderr = owner.communicate(timeout=10)
        assert owner.returncode == 0
        assert owner_stdout == ""
        assert owner_stderr == ""
    finally:
        if contender is not None and contender.poll() is None:
            contender.kill()
            contender.wait(timeout=5)
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
