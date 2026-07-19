"""Authenticated fixed-path Unix transport integration coverage."""

from __future__ import annotations

import asyncio
import os
import pickle
import shutil
import socket
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import yoetz.adapters.control as control_package
from yoetz.adapters.control.unix_socket import (
    CONTROL_ENDPOINT_BASENAME,
    ENDPOINT_MODE,
    HUMAN_CONTROL_ENDPOINT_BASENAME,
    RUNTIME_DIRECTORY_MODE,
    SECRET_ENDPOINT_BASENAME,
    EndpointKind,
    LocalControlTransportError,
    authenticate_peer,
    bind_control_listener,
    bind_human_control_listener,
    bind_secret_listener,
    close_endpoint,
    connect_control,
    connect_human_control,
    connect_secret,
    remove_stale_endpoint,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def runtime_directory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    runtime = Path(tempfile.mkdtemp(prefix=".yctl-", dir=Path.cwd()))
    runtime.chmod(RUNTIME_DIRECTORY_MODE)
    monkeypatch.setattr(
        "yoetz.adapters.control.unix_socket._runtime_directory",
        lambda: runtime,
    )
    try:
        yield runtime
    finally:
        shutil.rmtree(runtime)


class _HeldServiceLock:
    def assert_held(self) -> None:
        return None


class _MissingServiceLock:
    def assert_held(self) -> None:
        raise RuntimeError("not held")


def test_control_package_marker_is_inert() -> None:
    assert control_package.__all__ == []


@pytest.mark.anyio
async def test_three_fixed_endpoints_are_owner_only_and_bidirectional(
    runtime_directory: Path,
) -> None:
    listeners = (
        await bind_control_listener(),
        await bind_secret_listener(),
        await bind_human_control_listener(),
    )
    try:
        assert stat.S_IMODE(runtime_directory.lstat().st_mode) == RUNTIME_DIRECTORY_MODE
        for basename in (
            CONTROL_ENDPOINT_BASENAME,
            SECRET_ENDPOINT_BASENAME,
            HUMAN_CONTROL_ENDPOINT_BASENAME,
        ):
            facts = (runtime_directory / basename).lstat()
            assert stat.S_ISSOCK(facts.st_mode)
            assert stat.S_IMODE(facts.st_mode) == ENDPOINT_MODE
            assert facts.st_uid == os.geteuid()
            assert facts.st_nlink == 1

        for listener, connect in zip(
            listeners,
            (connect_control, connect_secret, connect_human_control),
            strict=True,
        ):
            accept_task = asyncio.create_task(listener.accept())
            client = await connect()
            server = await accept_task
            try:
                assert repr(client.peer_identity) == "PeerIdentityHandle(<redacted>)"
                assert repr(server.peer_identity) == "PeerIdentityHandle(<redacted>)"
                with pytest.raises(TypeError, match="not_serializable"):
                    pickle.dumps(client.peer_identity)
                assert not os.get_inheritable(client.fileno())
                assert not os.get_inheritable(server.fileno())
                assert not os.get_inheritable(listener.fileno())
                await client.send_all(b"client payload")
                assert await server.receive() == b"client payload"
                await server.send_all(bytearray(b"server payload"))
                assert await client.receive() == b"server payload"
                with pytest.raises(ValueError, match="receive_size_invalid"):
                    await client.receive(65_537)
            finally:
                await client.aclose()
                await server.aclose()
    finally:
        for listener in listeners:
            await close_endpoint(listener)

    assert not tuple(runtime_directory.glob("*.sock"))


@pytest.mark.anyio
async def test_endpoint_type_mode_and_identity_fail_closed_before_payload(
    runtime_directory: Path,
) -> None:
    endpoint = runtime_directory / CONTROL_ENDPOINT_BASENAME
    endpoint.write_bytes(b"not a socket")
    endpoint.chmod(ENDPOINT_MODE)
    with pytest.raises(LocalControlTransportError, match="endpoint_unsafe"):
        await connect_control()
    endpoint.unlink()

    unsafe_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    unsafe_socket.bind(os.fspath(endpoint))
    endpoint.chmod(0o666)
    try:
        with pytest.raises(LocalControlTransportError, match="endpoint_unsafe"):
            await connect_control()
    finally:
        unsafe_socket.close()
        endpoint.unlink()

    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(LocalControlTransportError, match="peer_untrusted"):
            authenticate_peer(left, _expected_effective_uid=os.geteuid() + 1)
    finally:
        left.close()
        right.close()


@pytest.mark.anyio
async def test_stale_removal_requires_lock_and_never_removes_live_endpoint(
    runtime_directory: Path,
) -> None:
    listener = await bind_control_listener()
    endpoint = runtime_directory / CONTROL_ENDPOINT_BASENAME
    live_inode = endpoint.lstat().st_ino
    with pytest.raises(LocalControlTransportError, match="endpoint_exists"):
        await bind_control_listener()
    assert endpoint.lstat().st_ino == live_inode
    with pytest.raises(LocalControlTransportError, match="service_lock_required"):
        await remove_stale_endpoint(EndpointKind.CONTROL, _MissingServiceLock())
    with pytest.raises(LocalControlTransportError, match="endpoint_in_use"):
        await remove_stale_endpoint(EndpointKind.CONTROL, _HeldServiceLock())
    assert endpoint.exists()
    await close_endpoint(listener)

    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(os.fspath(endpoint))
    os.chmod(endpoint, ENDPOINT_MODE)
    stale.close()
    await remove_stale_endpoint(EndpointKind.CONTROL, _HeldServiceLock())
    assert not endpoint.exists()


@pytest.mark.anyio
async def test_close_never_unlinks_a_replaced_endpoint(runtime_directory: Path) -> None:
    listener = await bind_control_listener()
    endpoint = runtime_directory / CONTROL_ENDPOINT_BASENAME
    endpoint.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(os.fspath(endpoint))
    os.chmod(endpoint, ENDPOINT_MODE)
    try:
        with pytest.raises(LocalControlTransportError, match="endpoint_unsafe"):
            await close_endpoint(listener)
        assert endpoint.exists()
    finally:
        replacement.close()
        endpoint.unlink()
