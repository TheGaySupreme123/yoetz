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
)
from yoetz.ports.privacy import LocalDisclosureReceiptView, PrivacyReceiptPage
from yoetz.protocol.ids import IdKind, new_id
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

    async def scripted_connect(kind: ControlClientKind) -> object:
        nonlocal calls
        assert kind is ControlClientKind.MCP_BRIDGE
        calls += 1
        if calls == 1:
            raise ControlError("service_unavailable", retryable=True)
        return expected

    def spawn() -> None:
        nonlocal spawned
        spawned += 1

    monkeypatch.setattr(client_module, "connect_service", scripted_connect)
    monkeypatch.setattr(client_module, "_spawn_service_process", spawn)
    connected = await connect_service_on_demand(ControlClientKind.MCP_BRIDGE, timeout_seconds=0.2)
    assert connected is expected
    assert spawned == 1


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
