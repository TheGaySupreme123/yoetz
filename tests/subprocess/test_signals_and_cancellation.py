"""Installed idle-signal shutdown and descriptor-level cancellation safety."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest
from helpers.child import ChildLimits, ChildSpec, assert_no_owned_children, spawn_installed
from helpers.frame_driver import drive_frames, encode_valid_frame, partial_write_schedule
from test_process_owner_fencing import (
    cleanup_environment,
    isolated_environment,
    spawn_service,
    stop_service_public,
    terminate_service,
    wait_status,
)

_ECHO = r"""
import os
data = bytearray()
while True:
    chunk = os.read(0, 65536)
    if not chunk:
        break
    data.extend(chunk)
view = memoryview(data)
while view:
    written = os.write(1, view)
    if written <= 0:
        raise SystemExit(70)
    view = view[written:]
"""


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_idle_daemon_signal_shutdown_is_clean_and_reopenable(
    tmp_path: Path, signum: signal.Signals
) -> None:
    environment = isolated_environment(tmp_path / f"installation-{signum.value}")
    first = spawn_service(environment)
    successor = None
    try:
        before = wait_status(environment, (first,))
        os.killpg(first.pid, signum)
        first_stdout, first_stderr = first.communicate(timeout=10)
        assert first.returncode == 0
        assert first_stdout == first_stderr == b""

        successor = spawn_service(environment)
        after = wait_status(environment, (successor,))
        assert after["generation"] == int(before["generation"]) + 1
        stop_service_public(environment, successor)
    finally:
        terminate_service(first)
        if successor is not None:
            terminate_service(successor)
        cleanup_environment(environment)


def test_one_byte_frame_delivery_and_eof_remain_synchronized() -> None:
    frame = encode_valid_frame({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    handle = spawn_installed(
        ChildSpec(
            executable=Path(sys.executable),
            argv=("-I", "-c", _ECHO),
            limits=ChildLimits(wall_time_seconds=10.0, max_output_bytes=65_536),
        ),
        {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    observation = drive_frames(handle, partial_write_schedule(frame))
    assert observation.exit_code == 0
    assert observation.signal is None
    assert observation.stderr == b""
    assert observation.raw_output == frame
    assert observation.frames == (
        {"id": 1, "jsonrpc": "2.0", "method": "tools/list", "params": {}},
    )
    assert observation.write_count == len(frame)
    assert observation.read_count >= 1
    assert_no_owned_children(handle.temp_root)


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"jsonrpc":"2.0","id":1,"result":{}}',
        b'{"jsonrpc":"2.0","id":1,"result":{},"id":2}\n',
        b'{"jsonrpc":"2.0","id":9007199254740992,"result":{}}\n',
        b'\xef\xbb\xbf{"jsonrpc":"2.0","id":1,"result":{}}\n',
        b'{"jsonrpc":"2.0","id":1,"result":"\\ud800"}\n',
        b'{"jsonrpc":"2.0","result":{}}\n',
    ],
)
def test_exact_protocol_oracle_rejects_partial_duplicate_unsafe_and_bom(encoded: bytes) -> None:
    from helpers.frame_driver import parse_protocol_output_exact

    with pytest.raises(ValueError):
        parse_protocol_output_exact(encoded)
