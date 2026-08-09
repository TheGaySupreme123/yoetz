from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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

_CONTROL_STOP_DAEMON_PROBE = r"""
import asyncio, os
from datetime import UTC, datetime
from pathlib import Path

import yoetz.adapters.control.unix_socket as unix_socket
from yoetz.adapters.control.unix_socket import bind_control_listener
from yoetz.service.daemon import ServiceComposition, ServiceDaemon
from yoetz.service.lifecycle import LifecycleError, ServiceLifecycle

runtime_path = Path(os.environ["YOETZ_TEST_RUNTIME"])
unix_socket._runtime_directory = lambda: runtime_path

class Clock:
    def now_utc(self): return datetime(2026, 8, 8, tzinfo=UTC)
    def monotonic_seconds(self): return 1.0
class Store:
    def advance(self, instance_id): return 1
class Mode:
    value = "uninitialized"
class Vault:
    ready = False
    generation = 0
    mode = Mode()
    async def lock(self): pass
    async def close(self):
        # Hold teardown past endpoint removal so peer EOF reproduces the old cancellation race.
        await asyncio.sleep(0.5)
class DeferredListener:
    def __init__(self):
        self.listener = None
        self.closed = False
    def install(self, listener):
        if self.listener is not None or self.closed:
            raise RuntimeError("listener_install_invalid")
        self.listener = listener
    async def accept(self):
        if self.listener is None or self.closed:
            raise RuntimeError("listener_unavailable")
        return await self.listener.accept()
    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        listener, self.listener = self.listener, None
        if listener is not None:
            await listener.aclose()

async def run():
    listener = DeferredListener()
    async def publish(_instance):
        listener.install(await bind_control_listener())
    async def cleanup(_instance):
        await listener.aclose()
    lifecycle = ServiceLifecycle(Clock(), generation_store=Store(),
        process_start_identity_commitment="sha256:" + "3" * 64,
        singleton_lock_path=Path(os.environ["YOETZ_TEST_LOCK"]),
        endpoint_publisher=publish, endpoint_cleanup=cleanup)
    composition = ServiceComposition(lifecycle, listener, None, None, None, None, Vault())
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

_CONTROL_STOP_CLIENT_PROBE = r"""
import asyncio, os
from pathlib import Path

import yoetz.adapters.control.unix_socket as unix_socket
from yoetz.ports.control import ControlClientKind
from yoetz.service.client import connect_service

unix_socket._runtime_directory = lambda: Path(os.environ["YOETZ_TEST_RUNTIME"])

async def run():
    client = await connect_service(ControlClientKind.CLI)
    try:
        result = await client.stop()
        if result.accepted is not True or result.state != "draining":
            raise RuntimeError("stop_result_invalid")
        print("accepted draining", flush=True)
    finally:
        await client.close()

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


def _control_environment(lock_path: Path, runtime_path: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.fspath(Path.cwd() / "src"),
        "YOETZ_TEST_LOCK": os.fspath(lock_path),
        "YOETZ_TEST_RUNTIME": os.fspath(runtime_path),
    }


def _spawn_control_daemon(lock_path: Path, runtime_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and in-repo probe
        [sys.executable, "-c", _CONTROL_STOP_DAEMON_PROBE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_control_environment(lock_path, runtime_path),
    )


def _run_control_stop(lock_path: Path, runtime_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and in-repo probe
        [sys.executable, "-c", _CONTROL_STOP_CLIENT_PROBE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=_control_environment(lock_path, runtime_path),
    )


def _readline_bounded(process: subprocess.Popen[str], seconds: float = 10.0) -> str:
    stdout = process.stdout
    assert stdout is not None
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    reader = threading.Thread(
        target=lambda: lines.put(stdout.readline()),
        name="yoetz-test-daemon-stdout",
        daemon=True,
    )
    reader.start()
    try:
        line = lines.get(timeout=seconds)
    except queue.Empty as exc:
        raise AssertionError("daemon did not publish bounded startup status") from exc
    if line:
        return line.strip()
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


def test_control_stop_exits_unlinks_endpoint_and_releases_singleton(tmp_path: Path) -> None:
    lock_path = tmp_path / "service.lock"
    # AF_UNIX pathname limits are materially shorter than pytest's private full-suite basetemp.
    # The production runtime directory is short; use the same shape here so this remains a
    # control-stop lifecycle test rather than a platform pathname-limit probe.
    runtime_path = Path(tempfile.mkdtemp(prefix="yoetz-sock-", dir="/private/tmp"))
    runtime_path.chmod(0o700)
    endpoint_path = runtime_path / "control.sock"
    owner = _spawn_control_daemon(lock_path, runtime_path)
    successor: subprocess.Popen[str] | None = None
    try:
        assert _readline_bounded(owner) == "locked"
        assert endpoint_path.exists()

        stop = _run_control_stop(lock_path, runtime_path)
        assert stop.returncode == 0
        assert stop.stdout.strip() == "accepted draining"
        assert stop.stderr == ""

        owner_stdout, owner_stderr = owner.communicate(timeout=10)
        assert owner.returncode == 0
        assert owner_stdout == ""
        assert owner_stderr == ""
        assert not endpoint_path.exists()

        successor = _spawn_control_daemon(lock_path, runtime_path)
        assert _readline_bounded(successor) == "locked"
        time.sleep(0.05)
        successor.send_signal(signal.SIGTERM)
        successor_stdout, successor_stderr = successor.communicate(timeout=10)
        assert successor.returncode == 0
        assert successor_stdout == ""
        assert successor_stderr == ""
        assert not endpoint_path.exists()
    finally:
        if successor is not None and successor.poll() is None:
            successor.kill()
            successor.wait(timeout=5)
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        shutil.rmtree(runtime_path, ignore_errors=True)
