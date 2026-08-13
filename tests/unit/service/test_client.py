"""Unit tests for the shared bounded ordinary service client."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Buffer
from inspect import signature
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from yoetz.adapters.control.unix_socket import AuthenticatedUnixStream
from yoetz.domain.values import JsonObject
from yoetz.ports.control import (
    ControlClientKind,
    ControlError,
    ControlMethod,
    ControlResult,
    ServiceState,
    ServiceStatus,
    ServiceStopResult,
    WorkspaceLocator,
)
from yoetz.ports.privacy import LocalDisclosureReceiptView, PrivacyReceiptPage
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import ReceiptRequest
from yoetz.service.client import (
    GetPrivacyReceiptRequest,
    ListPrivacyReceiptsRequest,
    PrivacyReceiptFound,
    PrivacyReceiptNotFound,
    ServiceClient,
    connect_service_on_demand,
)
from yoetz.service.control_protocol import (
    ControlSession,
    decode_control_frame,
    encode_control_frame,
    parse_control_result,
)

_SERVICE_ID = "svc_00000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeStream:
    def __init__(self) -> None:
        self.peer_identity = object()
        self.sent: list[bytes] = []
        self._incoming = bytearray()
        self._ready = asyncio.Condition()
        self.closed = False

    async def receive(self, max_bytes: int) -> bytes:
        async with self._ready:
            await self._ready.wait_for(lambda: bool(self._incoming) or self.closed)
            if not self._incoming:
                return b""
            size = min(max_bytes, len(self._incoming))
            result = bytes(self._incoming[:size])
            del self._incoming[:size]
            return result

    async def send_all(self, data: Buffer) -> None:
        self.sent.append(bytes(data))

    async def aclose(self) -> None:
        async with self._ready:
            self.closed = True
            self._ready.notify_all()

    async def feed(self, data: bytes) -> None:
        async with self._ready:
            self._incoming.extend(data)
            self._ready.notify_all()


def _status() -> ServiceStatus:
    return ServiceStatus(
        protocol_version="1.0",
        service_version="0.1.0",
        service_instance_id=_SERVICE_ID,
        service_generation="1",
        state=ServiceState.LOCKED,
        state_reason="human_authority_unavailable",
        vault_mode="uninitialized",
        capabilities=(),
        session_monitor="unavailable",
    )


def _session(stream: _FakeStream, kind: ControlClientKind) -> ControlSession:
    methods = tuple(
        sorted(
            (
                {
                    ControlMethod.START,
                    ControlMethod.PUBLISH_WORK,
                    ControlMethod.CHECK,
                    ControlMethod.RESPOND,
                    ControlMethod.STATUS,
                    ControlMethod.RECEIPT,
                }
                if kind is ControlClientKind.MCP_BRIDGE
                else set(ControlMethod)
            ),
            key=lambda method: method.value.encode("ascii"),
        )
    )
    return ControlSession(
        protocol_version="1.0",
        client_kind=kind,
        service_instance_id=_SERVICE_ID,
        service_generation="1",
        allowed_methods=methods,
        peer_identity=stream.peer_identity,
        connection_nonce="0" * 64,
    )


def _client(stream: _FakeStream, kind: ControlClientKind = ControlClientKind.CLI) -> ServiceClient:
    factory = getattr(__import__("yoetz.service.client", fromlist=["x"]), "_connected_client")
    return cast(
        ServiceClient,
        factory(
            cast(AuthenticatedUnixStream, stream),
            _session(stream, kind),
            kind,
        ),
    )


async def _wait_for_sent(stream: _FakeStream, count: int) -> None:
    for _ in range(100):
        if len(stream.sent) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError("client did not write expected frame")


@pytest.mark.anyio
async def test_request_result_correlation_and_lifecycle_conversion() -> None:
    stream = _FakeStream()
    client = _client(stream)
    task = asyncio.create_task(client.service_status())
    await _wait_for_sent(stream, 1)
    request = decode_control_frame(stream.sent[0])
    rpc_id = cast(str, request["rpc_id"])
    assert request["method"] == "service_status"
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=rpc_id,
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.SERVICE_STATUS,
                outcome="ok",
                body=_status(),
            )
        )
    )
    assert await task == _status()
    await client.close()


@pytest.mark.anyio
async def test_valid_stop_result_survives_immediate_eof_and_fails_unrelated_pending_call() -> None:
    stream = _FakeStream()
    client = _client(stream)
    status_task = asyncio.create_task(client.service_status())
    stop_task = asyncio.create_task(client.stop())
    await _wait_for_sent(stream, 2)
    requests = {
        cast(str, frame["method"]): frame
        for frame in (decode_control_frame(encoded) for encoded in stream.sent)
    }
    stop_request = requests["service_stop"]
    expected = ServiceStopResult()
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, stop_request["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.SERVICE_STOP,
                outcome="ok",
                body=expected,
            )
        )
    )
    await stream.aclose()

    assert await stop_task == expected
    with pytest.raises(ControlError, match="frame_invalid"):
        await status_task


@pytest.mark.anyio
async def test_result_correlation_mismatch_remains_a_protocol_failure() -> None:
    stream = _FakeStream()
    client = _client(stream)
    task = asyncio.create_task(client.service_status())
    await _wait_for_sent(stream, 1)
    request = decode_control_frame(stream.sent[0])
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, request["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.SERVICE_LOCK,
                outcome="ok",
                body=_status(),
            )
        )
    )

    with pytest.raises(ControlError, match="frame_invalid"):
        await task
    await client.close()


@pytest.mark.anyio
async def test_mcp_rejects_every_support_method_before_transport() -> None:
    stream = _FakeStream()
    client = _client(stream, ControlClientKind.MCP_BRIDGE)
    with pytest.raises(ControlError, match="method_forbidden"):
        await client.privacy_get_effective(JsonObject({}))
    assert stream.sent == []
    await client.close()


@pytest.mark.anyio
async def test_on_demand_connect_spawns_only_after_absent_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    expected = object()
    calls = 0
    spawned = 0

    async def scripted_connect(
        kind: ControlClientKind,
        **_kwargs: object,
    ) -> object:
        nonlocal calls
        assert kind is ControlClientKind.MCP_BRIDGE
        calls += 1
        if calls == 1:
            raise ControlError("service_unavailable", retryable=True)
        return expected

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "_connect_service_attempt", scripted_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)
    connected = await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.2)
    assert connected is expected
    assert spawned == 1


@pytest.mark.anyio
async def test_on_demand_reuses_workspace_locator_for_every_reconnect_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    expected = object()
    locator = WorkspaceLocator("/private/repository")
    observed: list[WorkspaceLocator | None] = []

    async def scripted_connect(
        kind: ControlClientKind,
        *,
        workspace_locator: WorkspaceLocator | None = None,
        **_kwargs: object,
    ) -> object:
        assert kind is ControlClientKind.MCP_BRIDGE
        observed.append(workspace_locator)
        if len(observed) == 1:
            raise ControlError("service_unavailable", retryable=True)
        return expected

    monkeypatch.setattr(client_module, "_connect_service_attempt", scripted_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", lambda: None)
    connected = await connect_service_on_demand(
        ControlClientKind.MCP_BRIDGE,
        workspace_locator=locator,
        timeout_seconds=0.2,
    )

    assert connected is expected
    assert observed == [locator, locator]


@pytest.mark.anyio
async def test_on_demand_does_not_spawn_after_accepted_service_stalls_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    stream = _FakeStream()
    spawned = 0

    async def connect() -> AuthenticatedUnixStream:
        return cast(AuthenticatedUnixStream, stream)

    async def stalled_handshake(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "connect_control", connect)
    monkeypatch.setattr(client_module, "client_handshake", stalled_handshake)
    monkeypatch.setattr(client_module, "_CONNECT_HANDSHAKE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)

    with pytest.raises(ControlError, match="service_unavailable"):
        await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.2)

    assert stream.closed is True
    assert spawned == 0


@pytest.mark.anyio
async def test_on_demand_tolerates_the_daemon_it_just_spawned_until_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon this call started is still starting, not wedged, while it stays silent."""

    import yoetz.service.client as client_module

    expected = object()
    attempts = 0
    spawned = 0

    async def scripted_connect(
        kind: ControlClientKind,
        **_kwargs: object,
    ) -> object:
        nonlocal attempts
        assert kind is ControlClientKind.MCP_BRIDGE
        attempts += 1
        if attempts == 1:
            raise ControlError("service_unavailable", retryable=True)
        if attempts <= 3:
            raise client_module._AcceptedServiceUnresponsive()  # pyright: ignore[reportPrivateUsage]
        return expected

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "_connect_service_attempt", scripted_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)

    connected = await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=1.0)

    assert connected is expected
    assert spawned == 1
    assert attempts == 4


@pytest.mark.anyio
async def test_on_demand_accepted_unresponsive_after_spawn_still_ends_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tolerating the startup window must not restore an unbounded wait."""

    import yoetz.service.client as client_module

    attempts = 0
    spawned = 0

    async def scripted_connect(
        kind: ControlClientKind,
        **_kwargs: object,
    ) -> object:
        nonlocal attempts
        assert kind is ControlClientKind.MCP_BRIDGE
        attempts += 1
        if attempts == 1:
            raise ControlError("service_unavailable", retryable=True)
        raise client_module._AcceptedServiceUnresponsive()  # pyright: ignore[reportPrivateUsage]

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "_connect_service_attempt", scripted_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)
    monkeypatch.setattr(client_module, "_CONNECT_HANDSHAKE_TIMEOUT_SECONDS", 0.05)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlError, match="service_unavailable"):
        await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.3)

    assert asyncio.get_running_loop().time() - started < 1.0
    assert spawned == 1
    assert attempts > 2


@pytest.mark.anyio
async def test_on_demand_initial_connect_is_inside_the_total_start_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    spawned = 0

    async def stalled_connect() -> AuthenticatedUnixStream:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "connect_control", stalled_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlError, match="service_unavailable"):
        await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.1)

    assert asyncio.get_running_loop().time() - started < 0.5
    assert spawned == 0


@pytest.mark.anyio
async def test_connect_timeout_does_not_wait_for_never_closing_accepted_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    class _NeverClosingStream(_FakeStream):
        async def aclose(self) -> None:
            await asyncio.Event().wait()

    stream = _NeverClosingStream()
    spawned = 0

    async def connect() -> AuthenticatedUnixStream:
        return cast(AuthenticatedUnixStream, stream)

    async def stalled_handshake(*_args: object, **_kwargs: object) -> object:
        await asyncio.Event().wait()

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "connect_control", connect)
    monkeypatch.setattr(client_module, "client_handshake", stalled_handshake)
    monkeypatch.setattr(client_module, "_CONNECT_HANDSHAKE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(client_module, "_STREAM_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlError, match="service_unavailable"):
        await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.2)

    assert asyncio.get_running_loop().time() - started < 0.1
    assert spawned == 0


def test_on_demand_service_environment_strips_secret_shaped_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    monkeypatch.setenv("FIREWORKS_API_KEY", "must-not-cross")
    monkeypatch.setenv("UNRELATED_TOKEN", "must-not-cross")
    monkeypatch.setenv("YOETZ_UNRELATED_APP_SETTING", "must-not-cross")
    monkeypatch.setenv("YOETZ_LOG_LEVEL", "warning")
    monkeypatch.setenv("PATH", "/safe/bin")
    environment = client_module._service_environment()  # pyright: ignore[reportPrivateUsage]
    assert environment["PATH"] == "/safe/bin"
    assert environment["YOETZ_LOG_LEVEL"] == "warning"
    assert "FIREWORKS_API_KEY" not in environment
    assert "UNRELATED_TOKEN" not in environment
    assert "YOETZ_UNRELATED_APP_SETTING" not in environment
    assert "must-not-cross" not in environment.values()


def test_on_demand_spawn_routes_stderr_to_owner_only_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.service.client as client_module

    log_root = tmp_path / "logs"
    observed: dict[str, object] = {}

    def popen(command: object, **kwargs: object) -> object:
        stderr = cast(BinaryIO, kwargs["stderr"])
        path = log_root / "service.stderr.jsonl"
        observed["command"] = command
        observed["path"] = path
        observed["mode"] = stat.S_IMODE(path.stat().st_mode)
        observed["fd_mode"] = stat.S_IMODE(os.fstat(stderr.fileno()).st_mode)
        return object()

    monkeypatch.setattr(client_module, "log_dir", lambda: log_root)
    monkeypatch.setattr(client_module.subprocess, "Popen", popen)

    client_module._spawn_service_process()  # pyright: ignore[reportPrivateUsage]

    assert observed["path"] == log_root / "service.stderr.jsonl"
    assert observed["mode"] == 0o600
    assert observed["fd_mode"] == 0o600
    assert stat.S_IMODE(log_root.stat().st_mode) == 0o700


@pytest.mark.anyio
async def test_task_cancellation_sends_one_way_distinct_cancel_frame() -> None:
    stream = _FakeStream()
    client = _client(stream)
    task = asyncio.create_task(client.service_status())
    await _wait_for_sent(stream, 1)
    call = decode_control_frame(stream.sent[0])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _wait_for_sent(stream, 2)
    cancel = decode_control_frame(stream.sent[1])
    assert cancel["kind"] == "cancel"
    assert cancel["target_rpc_id"] == call["rpc_id"]
    assert cancel["rpc_id"] != call["rpc_id"]
    await client.close()


@pytest.mark.anyio
async def test_receipt_list_and_get_preserve_snapshot_and_closed_outcome() -> None:
    stream = _FakeStream()
    client = _client(stream)

    list_task = asyncio.create_task(client.privacy_receipts_list(ListPrivacyReceiptsRequest()))
    await _wait_for_sent(stream, 1)
    list_request = decode_control_frame(stream.sent[0])
    assert list_request["method"] == "privacy_receipts_list"
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, list_request["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.PRIVACY_RECEIPTS_LIST,
                outcome="ok",
                body=JsonObject(
                    {
                        "schema_version": "1.0.0",
                        "snapshot_generation": "7",
                        "receipts": (),
                    }
                ),
            )
        )
    )
    assert await list_task == PrivacyReceiptPage(
        snapshot_generation=7, receipts=(), next_cursor=None
    )

    get_task = asyncio.create_task(
        client.privacy_receipts_get(
            GetPrivacyReceiptRequest("egr_00000000-0000-4000-8000-000000000009")
        )
    )
    await _wait_for_sent(stream, 2)
    get_request = decode_control_frame(stream.sent[1])
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, get_request["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.PRIVACY_RECEIPTS_GET,
                outcome="ok",
                body=JsonObject({"schema_version": "1.0.0", "outcome": "not_found"}),
            )
        )
    )
    assert await get_task == PrivacyReceiptNotFound()
    await client.close()


@pytest.mark.anyio
async def test_receipt_get_parses_schema_native_found_view() -> None:
    stream = _FakeStream()
    client = _client(stream)
    task = asyncio.create_task(
        client.privacy_receipts_get(
            GetPrivacyReceiptRequest("egr_00000000-0000-4000-8000-000000000009")
        )
    )
    await _wait_for_sent(stream, 1)
    request = decode_control_frame(stream.sent[0])
    receipt = {
        "schema_version": "1.0.0",
        "receipt_id": "egr_00000000-0000-4000-8000-000000000009",
        "request_id": "req_00000000-0000-4000-8000-000000000002",
        "privacy_proposal_id": "ppr_00000000-0000-4000-8000-000000000003",
        "sink": "local_human_view",
        "outcome": "blocked_by_policy",
        "finished_at": "2026-07-19T00:00:00.000Z",
        "scope": {
            "kind": "machine",
            "installation_id": "ins_00000000-0000-4000-8000-000000000004",
        },
        "purpose": "structural-review",
        "policy": {
            "policy_id": "pvy_00000000-0000-4000-8000-000000000005",
            "version": "1",
            "policy_digest": "sha256:" + "1" * 64,
            "authorization_scope_digest": "sha256:" + "2" * 64,
        },
        "consent_source": "none",
        "approved_categories": (),
        "blocked_categories": ("bounded_structural_metadata",),
        "counts": {
            "candidate_items": "1",
            "included_items": "0",
            "removed_items": "1",
            "approved_items": "0",
            "blocked_items": "1",
            "candidate_bytes": "1",
            "final_bytes": "0",
        },
        "transformations": {
            "minimized_items": "0",
            "redacted_spans": "0",
            "blocked_items": "1",
        },
        "secret_scan": {
            "registry_version": "1.0.0",
            "scanner_profile_digest": "sha256:" + "3" * 64,
            "match_count": "0",
            "passed": True,
        },
        "safe_failure_reason": "policy_denied",
        "audit_store_version": 1,
    }
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, request["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.PRIVACY_RECEIPTS_GET,
                outcome="ok",
                body=JsonObject(
                    {
                        "schema_version": "1.0.0",
                        "outcome": "found",
                        "receipt": {"kind": "local_disclosure", "receipt": receipt},
                    }
                ),
            )
        )
    )
    result = await task
    assert isinstance(result, PrivacyReceiptFound)
    assert isinstance(result.receipt, LocalDisclosureReceiptView)
    assert result.receipt.receipt.receipt_id == receipt["receipt_id"]
    await client.close()


def test_stop_result_parser_and_reconciled_receipt_page_contract() -> None:
    rpc_id = new_id(IdKind.CONTROL_RPC)
    parsed = parse_control_result(
        decode_control_frame(
            encode_control_frame(
                ControlResult(
                    protocol_version="1.0",
                    rpc_id=rpc_id,
                    service_instance_id=_SERVICE_ID,
                    service_generation="1",
                    method=ControlMethod.SERVICE_STOP,
                    outcome="ok",
                    body=ServiceStopResult(),
                )
            )
        )
    )
    assert parsed.body == ServiceStopResult()
    assert (
        PrivacyReceiptPage(snapshot_generation=1, receipts=(), next_cursor=None).snapshot_generation
        == 1
    )
    with pytest.raises(ValueError, match="invalid_privacy_port_value"):
        PrivacyReceiptPage(snapshot_generation=0, receipts=(), next_cursor=None)


def test_no_direct_runtime_or_endpoint_constructor_surface() -> None:
    names = set(signature(ServiceClient).parameters)
    assert not names & {
        "socket_path",
        "data_directory",
        "password",
        "credential",
        "application",
        "provider_client",
    }
    with pytest.raises(TypeError, match="constructor_private"):
        ServiceClient(
            cast(AuthenticatedUnixStream, _FakeStream()),
            cast(ControlSession, object()),
            ControlClientKind.CLI,
            _token=None,
        )


def _receipt_request(seed: int) -> ReceiptRequest:
    identity = f"00000000-0000-4000-8000-{seed:012d}"
    return ReceiptRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": f"req_{identity}",
            "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
            "client": {
                "kind": "cooperative_agent",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
            "session_id": f"ses_{identity}",
            "writer_id": f"wri_{identity}",
            "task_id": f"tsk_{identity}",
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "format": "json",
            "include": "summary",
            "redaction_profile": "default_local_export",
        }
    )


class _BlockedSendStream(_FakeStream):
    def __init__(self, *, allow_count: int) -> None:
        super().__init__()
        self.allow_count = allow_count
        self.send_attempts = 0

    async def send_all(self, data: Buffer) -> None:
        self.send_attempts += 1
        if self.send_attempts > self.allow_count:
            await asyncio.Event().wait()
        await super().send_all(data)


@pytest.mark.anyio
async def test_rpc_deadline_covers_blocked_send_and_bounded_cancel_attempt() -> None:
    stream = _BlockedSendStream(allow_count=0)
    client = _client(stream, ControlClientKind.MCP_BRIDGE)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlError, match="request_timeout"):
        await client.receipt(_receipt_request(20), deadline_ms=10)

    assert asyncio.get_running_loop().time() - started < 0.2
    assert stream.send_attempts == 1
    assert stream.closed is True
    await client.close()


@pytest.mark.anyio
async def test_blocked_cancel_notification_cannot_materially_extend_rpc_deadline() -> None:
    stream = _BlockedSendStream(allow_count=1)
    client = _client(stream, ControlClientKind.MCP_BRIDGE)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ControlError, match="request_timeout"):
        await client.receipt(_receipt_request(21), deadline_ms=10)

    assert asyncio.get_running_loop().time() - started < 0.2
    assert len(stream.sent) == 1
    assert stream.send_attempts == 2
    await client.close()


@pytest.mark.anyio
async def test_late_timed_out_result_is_retired_without_poisoning_concurrent_call() -> None:
    stream = _FakeStream()
    client = _client(stream, ControlClientKind.MCP_BRIDGE)

    with pytest.raises(ControlError, match="request_timeout"):
        await client.receipt(_receipt_request(22), deadline_ms=10)
    first = next(
        frame
        for frame in (decode_control_frame(encoded) for encoded in stream.sent)
        if frame["kind"] == "call"
    )

    healthy = asyncio.create_task(client.receipt(_receipt_request(23), deadline_ms=500))
    await _wait_for_sent(stream, 3)
    second = decode_control_frame(stream.sent[2])
    assert second["kind"] == "call"

    for frame in (first, second):
        await stream.feed(
            encode_control_frame(
                ControlResult(
                    protocol_version="1.0",
                    rpc_id=cast(str, frame["rpc_id"]),
                    service_instance_id=_SERVICE_ID,
                    service_generation="1",
                    method=ControlMethod.RECEIPT,
                    outcome="error",
                    body=ControlError("privacy_projection_unavailable", retryable=True),
                )
            )
        )

    with pytest.raises(ControlError, match="privacy_projection_unavailable"):
        await healthy
    assert stream.closed is False
    assert client._retired_rpc_ids == set()  # pyright: ignore[reportPrivateUsage]
    await client.close()


@pytest.mark.anyio
async def test_late_result_during_bounded_cancel_window_uses_tombstone_first() -> None:
    class _BlockedCancelOnceStream(_FakeStream):
        def __init__(self) -> None:
            super().__init__()
            self.send_attempts = 0

        async def send_all(self, data: Buffer) -> None:
            self.send_attempts += 1
            if self.send_attempts == 2:
                await asyncio.Event().wait()
            await super().send_all(data)

    stream = _BlockedCancelOnceStream()
    client = _client(stream, ControlClientKind.MCP_BRIDGE)
    timed_out = asyncio.create_task(client.receipt(_receipt_request(24), deadline_ms=10))
    await _wait_for_sent(stream, 1)
    first = decode_control_frame(stream.sent[0])
    for _ in range(100):
        if stream.send_attempts == 2:
            break
        await asyncio.sleep(0.001)
    assert stream.send_attempts == 2

    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, first["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.RECEIPT,
                outcome="error",
                body=ControlError("privacy_projection_unavailable", retryable=True),
            )
        )
    )
    with pytest.raises(ControlError, match="request_timeout"):
        await timed_out

    healthy = asyncio.create_task(client.receipt(_receipt_request(25), deadline_ms=500))
    await _wait_for_sent(stream, 2)
    second = decode_control_frame(stream.sent[1])
    await stream.feed(
        encode_control_frame(
            ControlResult(
                protocol_version="1.0",
                rpc_id=cast(str, second["rpc_id"]),
                service_instance_id=_SERVICE_ID,
                service_generation="1",
                method=ControlMethod.RECEIPT,
                outcome="error",
                body=ControlError("privacy_projection_unavailable", retryable=True),
            )
        )
    )
    with pytest.raises(ControlError, match="privacy_projection_unavailable"):
        await healthy
    assert stream.closed is False
    await client.close()


@pytest.mark.anyio
async def test_unanswered_timeout_tombstones_bound_new_admission() -> None:
    stream = _FakeStream()
    client = _client(stream, ControlClientKind.MCP_BRIDGE)

    for seed in range(30, 62):
        with pytest.raises(ControlError, match="request_timeout"):
            await client.receipt(_receipt_request(seed), deadline_ms=1)

    assert len(client._retired_rpc_ids) == 32  # pyright: ignore[reportPrivateUsage]
    sent_before = len(stream.sent)
    with pytest.raises(ControlError, match="service_unavailable"):
        await client.receipt(_receipt_request(63), deadline_ms=1)
    assert len(stream.sent) == sent_before
    assert stream.closed is True
    await client.close()


@pytest.mark.anyio
async def test_projection_errors_reach_the_caller_without_closing_the_connection() -> None:
    """A caller that cannot get one receipt format must be able to ask for another one.

    Collapsing these two reasons into a retryable teardown is what left a real agent retrying the
    single shape that could never succeed, so the surviving connection is the property under test.
    """

    stream = _FakeStream()
    client = _client(stream, ControlClientKind.MCP_BRIDGE)
    projection_reasons = (
        ("privacy_projection_blocked", False),
        ("privacy_projection_unavailable", True),
    )

    for index, (reason, retryable) in enumerate(projection_reasons, start=1):
        task = asyncio.create_task(client.receipt(_receipt_request(index)))
        await _wait_for_sent(stream, index)
        request = decode_control_frame(stream.sent[index - 1])
        await stream.feed(
            encode_control_frame(
                ControlResult(
                    protocol_version="1.0",
                    rpc_id=cast(str, request["rpc_id"]),
                    service_instance_id=_SERVICE_ID,
                    service_generation="1",
                    method=ControlMethod.RECEIPT,
                    outcome="error",
                    body=ControlError(reason, retryable=retryable),
                )
            )
        )
        with pytest.raises(ControlError) as raised:
            await task
        # The exact reason survives: rewriting it to service_unavailable hides the remedy.
        assert raised.value.reason == reason
        assert raised.value.retryable is retryable
        assert stream.closed is False

    # The same connection still carries a following call rather than requiring a reconnect.
    follow_up = asyncio.create_task(client.receipt(_receipt_request(3)))
    await _wait_for_sent(stream, 3)
    assert decode_control_frame(stream.sent[2])["method"] == "receipt"
    follow_up.cancel()
    await asyncio.gather(follow_up, return_exceptions=True)
    await client.close()
