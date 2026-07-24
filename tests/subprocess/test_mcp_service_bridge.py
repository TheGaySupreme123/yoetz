"""Transport-only MCP bridge behavior across local-service outcomes."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from pydantic import BaseModel

import yoetz.mcp.server as bridge
from yoetz.ports.control import ControlError
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    PublicRequestModel,
    PublishWorkRequest,
    PublishWorkResult,
    ReceiptRequest,
    ReceiptResult,
    RespondRequest,
    RespondResult,
    StartRequest,
    StartResult,
    StatusRequest,
    StatusResult,
)

_PREFIXES = {
    "request": "req_",
    "task": "tsk_",
    "session": "ses_",
    "writer": "wri_",
    "finding": "fnd_",
    "event": "evt_",
}


def _id(kind: str, seed: int) -> str:
    return f"{_PREFIXES[kind]}00000000-0000-4000-8000-{seed:012d}"


def _base(seed: int) -> dict[str, JsonValue]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": _id("request", seed),
        "actor": {"actor_id": "harness:mcp", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
    }


def _requests() -> dict[str, dict[str, JsonValue]]:
    frontier: dict[str, JsonValue] = {"sequence": "0", "head_digest": "genesis"}
    identity: dict[str, JsonValue] = {
        "session_id": _id("session", 1),
        "writer_id": _id("writer", 1),
    }
    return {
        "start": {
            **_base(1),
            "mode": "create",
            "task_title": "Bridge contract",
            "requested_view": "compact",
        },
        "publish_work": {
            **_base(2),
            **identity,
            "expected_frontier": frontier,
            "event_drafts": [
                {
                    "event_id": _id("event", 2),
                    "schema": {"name": "plan_published", "version": "1.0.0"},
                    "occurred_at": "2026-01-01T00:00:00.000Z",
                    "causal_parents": [],
                    "payload": {"plan_version": 1, "summary": "Plan", "obligation_refs": []},
                    "artifact_refs": [],
                    "evidence_refs": [],
                }
            ],
        },
        "check": {
            **_base(3),
            **identity,
            "expected_frontier": frontier,
            "mode": "deterministic_only",
        },
        "respond": {
            **_base(4),
            **identity,
            "expected_frontier": frontier,
            "finding_id": _id("finding", 4),
            "finding_frontier": frontier,
            "disposition": "acknowledged",
        },
        "status": {**_base(5), **identity, "view": "compact", "limit": "10"},
        "receipt": {
            **_base(6),
            **identity,
            "task_id": _id("task", 6),
            "expected_frontier": frontier,
            "format": "json",
            "include": "summary",
            "redaction_profile": "default_local_export",
        },
    }


def _failure[ResultT: BaseModel](result_type: type[ResultT], request_id: str) -> ResultT:
    return result_type.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "SESSION_CONFLICT",
                "message": "The session conflicts with the request.",
                "retryable": False,
                "correlation_id": "err_00000000-0000-4000-8000-000000000099",
            },
        }
    )


class _FakeClient:
    def __init__(
        self,
        failure: ControlError | PublicOperationError | RuntimeError | None = None,
    ) -> None:
        self.failure = failure
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    async def connect(self) -> None:
        return None

    async def _call[ResultT: BaseModel](
        self, name: str, request: PublicRequestModel, result_type: type[ResultT]
    ) -> ResultT:
        self.calls.append((name, request))
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        return _failure(result_type, request.request_id)

    async def start(self, request: StartRequest) -> StartResult:
        return await self._call("start", request, StartResult)

    async def publish_work(self, request: PublishWorkRequest) -> PublishWorkResult:
        return await self._call("publish_work", request, PublishWorkResult)

    async def check(self, request: CheckRequest) -> CheckResult:
        return await self._call("check", request, CheckResult)

    async def respond(self, request: RespondRequest) -> RespondResult:
        return await self._call("respond", request, RespondResult)

    async def status(self, request: StatusRequest) -> StatusResult:
        return await self._call("status", request, StatusResult)

    async def receipt(self, request: ReceiptRequest) -> ReceiptResult:
        return await self._call("receipt", request, ReceiptResult)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _install_clients(
    monkeypatch: pytest.MonkeyPatch, clients: list[_FakeClient]
) -> list[_FakeClient]:
    remaining = list(clients)

    async def connect(_kind: object) -> object:
        return remaining.pop(0)

    monkeypatch.setattr(
        bridge,
        "connect_service_on_demand",
        cast(Callable[[object], Awaitable[object]], connect),
    )
    return remaining


@pytest.mark.anyio
async def test_exact_six_dispatchers_use_one_ordinary_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _install_clients(monkeypatch, [client])
    runtime = bridge.build_bridge_runtime()

    for name, arguments in _requests().items():
        result = await getattr(bridge, f"dispatch_{name}")(arguments, runtime)
        assert result.isError is True
        assert result.structuredContent is not None
        assert result.structuredContent["error"]["code"] == "SESSION_CONFLICT"

    assert [name for name, _request in client.calls] == [
        "start",
        "publish_work",
        "check",
        "respond",
        "status",
        "receipt",
    ]
    assert client.closed is False
    await bridge.close_bridge_runtime(runtime)
    assert client.closed is True


@pytest.mark.anyio
async def test_response_loss_reconnects_once_with_identical_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _FakeClient(ControlError("service_unavailable", retryable=True))
    replacement = _FakeClient()
    _install_clients(monkeypatch, [stale, replacement])
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_start(_requests()["start"], runtime)

    assert result.isError is True
    assert stale.closed is True
    assert len(stale.calls) == len(replacement.calls) == 1
    assert stale.calls[0][1] is replacement.calls[0][1]


@pytest.mark.anyio
async def test_locked_error_is_structured_and_resources_never_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = _FakeClient(ControlError("vault_locked"))
    remaining = _install_clients(monkeypatch, [locked])
    runtime = bridge.build_bridge_runtime()

    resources = await bridge.list_resources()
    assert len(resources) == 4
    assert len(remaining) == 1

    result = await bridge.dispatch_start(_requests()["start"], runtime)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "VAULT_LOCKED"
    assert "unlock" not in {tool.name for tool in await bridge.list_tools()}


@pytest.mark.anyio
async def test_public_operation_error_keeps_event_invalid_not_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        PublicOperationError(
            PublicErrorCode.EVENT_INVALID,
            "The event batch is invalid.",
            False,
            safe_details={"reason_code": "unsorted_set_field"},
        )
    )
    _install_clients(monkeypatch, [client])
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_requests()["publish_work"], runtime)

    assert result.isError is True
    assert result.structuredContent is not None
    error = result.structuredContent["error"]
    assert error["code"] == "EVENT_INVALID"
    assert error["safe_details"] == {"reason_code": "unsorted_set_field"}
    assert error["code"] != "INTERNAL_ERROR"
    assert result.structuredContent["request_id"] == _requests()["publish_work"]["request_id"]
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_unexpected_bridge_error_logs_public_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    correlation_id = "err_00000000-0000-4000-8000-000000000098"
    client = _FakeClient(RuntimeError("must-not-reach-public-output"))
    _install_clients(monkeypatch, [client])

    def record(
        exc: BaseException,
        *,
        component: str,
        operation: str,
    ) -> str:
        assert type(exc) is RuntimeError
        print(
            json.dumps(
                {
                    "component": component,
                    "operation": operation,
                    "correlation_id": correlation_id,
                    "reason": "exception_runtime_error",
                }
            ),
            file=sys.stderr,
        )
        return correlation_id

    monkeypatch.setattr(bridge, "record_unexpected_exception_without_raising", record)
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_check(_requests()["check"], runtime)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert correlation_id in captured.err
    assert "check_internal_error" in captured.err
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "INTERNAL_ERROR"
    assert result.structuredContent["error"]["correlation_id"] == correlation_id
    assert "must-not-reach-public-output" not in str(result.structuredContent)
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_cancellation_propagates_without_becoming_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CancellingClient(_FakeClient):
        async def start(self, request: StartRequest) -> StartResult:
            del request
            raise asyncio.CancelledError

    _install_clients(monkeypatch, [_CancellingClient()])
    runtime = bridge.build_bridge_runtime()

    with pytest.raises(asyncio.CancelledError):
        await bridge.dispatch_start(_requests()["start"], runtime)
