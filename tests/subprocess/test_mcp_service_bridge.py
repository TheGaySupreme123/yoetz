"""Transport-only MCP bridge behavior across local-service outcomes."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Iterator
from typing import cast

import pytest
from pydantic import BaseModel

import yoetz.mcp.server as bridge
from yoetz.config.models import LoggingConfig
from yoetz.observability.logging import LogMode, configure_logging
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
        self.route_profiles: list[tuple[str, str | None]] = []
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

    async def check(
        self, request: CheckRequest, *, route_profile: str | None = None
    ) -> CheckResult:
        self.route_profiles.append(("check", route_profile))
        return await self._call("check", request, CheckResult)

    async def respond(self, request: RespondRequest) -> RespondResult:
        return await self._call("respond", request, RespondResult)

    async def status(
        self, request: StatusRequest, *, route_profile: str | None = None
    ) -> StatusResult:
        self.route_profiles.append(("status", route_profile))
        return await self._call("status", request, StatusResult)

    async def receipt(self, request: ReceiptRequest) -> ReceiptResult:
        return await self._call("receipt", request, ReceiptResult)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _install_clients(
    monkeypatch: pytest.MonkeyPatch,
    clients: list[_FakeClient],
    observed_locators: list[object] | None = None,
) -> list[_FakeClient]:
    remaining = list(clients)

    async def connect(_kind: object, *, workspace_locator: object = None) -> object:
        if observed_locators is not None:
            observed_locators.append(workspace_locator)
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
async def test_strict_bridge_attaches_private_route_profile_after_public_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _install_clients(monkeypatch, [client])
    runtime = bridge.build_bridge_runtime("strict")

    result = await bridge.dispatch_check(_requests()["check"], runtime)

    assert result.isError is True
    assert client.route_profiles == [("check", "strict")]
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_agent_cannot_supply_or_clear_the_private_route_profile() -> None:
    runtime = bridge.build_bridge_runtime("strict")
    arguments = {**_requests()["check"], "route_profile": "policy"}

    result = await bridge.dispatch_check(arguments, runtime)

    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "INVALID_REQUEST"
    assert runtime._slot.client is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_response_loss_reconnects_once_with_identical_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _FakeClient(ControlError("service_unavailable", retryable=True))
    replacement = _FakeClient()
    observed_locators: list[object] = []
    _install_clients(monkeypatch, [stale, replacement], observed_locators)
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_start(_requests()["start"], runtime)

    assert result.isError is True
    assert stale.closed is True
    assert len(stale.calls) == len(replacement.calls) == 1
    assert stale.calls[0][1] is replacement.calls[0][1]
    assert observed_locators == [runtime.workspace_locator, runtime.workspace_locator]


@pytest.mark.anyio
async def test_locked_error_is_structured_and_resources_never_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = _FakeClient(ControlError("vault_locked"))
    unlocked = _FakeClient()
    remaining = _install_clients(monkeypatch, [locked, unlocked])
    runtime = bridge.build_bridge_runtime()

    resources = await bridge.list_resources()
    assert len(resources) == 5
    assert len(remaining) == 2

    result = await bridge.dispatch_start(_requests()["start"], runtime)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "VAULT_LOCKED"
    assert "unlock" not in {tool.name for tool in await bridge.list_tools()}
    assert locked.closed is True
    assert runtime._slot.client is None  # pyright: ignore[reportPrivateUsage]

    after_unlock = await bridge.dispatch_start(_requests()["start"], runtime)
    assert after_unlock.isError is True
    assert unlocked.calls and unlocked.calls[0][0] == "start"
    assert runtime._slot.client is unlocked  # pyright: ignore[reportPrivateUsage]
    await bridge.close_bridge_runtime(runtime)


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


def _reference_mirror_error() -> PublicOperationError:
    """Build the real reference-mirror rejection the publish application produces.

    Hand-writing the message here would let the bridge case pass while the production text drifted,
    so the case rides on whatever `prepare_publication` actually raises.
    """

    from typing import cast as _cast

    from builders.replay import replay_records
    from yoetz.application.publish_work import Application, prepare_publication
    from yoetz.domain.events import EventPayload, LedgerRecord, encode_payload
    from yoetz.protocol.coverage import PublicationChannel
    from yoetz.protocol.models import PublishWorkRequestModel

    class _App:
        def authorizes_import_publication(self, request: PublishWorkRequest) -> bool:
            del request
            return False

    def draft(family: str) -> tuple[dict[str, JsonValue], LedgerRecord]:
        record = next(
            row for row in replay_records("all-event-families") if row.schema.name == family
        )
        assert record.payload is not None
        return {
            "event_id": record.event_id,
            "schema": {"name": record.schema.name, "version": record.schema.version},
            "occurred_at": record.occurred_at.wire,
            "causal_parents": list(record.causal_parents),
            "payload": encode_payload(_cast(EventPayload, record.payload)),
            "artifact_refs": list(record.artifact_refs),
            "evidence_refs": list(record.evidence_refs),
        }, record

    broken, record = draft("result_recorded")
    mirrored = cast(list[JsonValue], broken["evidence_refs"])
    # Break the mirror in whichever direction the recorded draft leaves available.
    broken["evidence_refs"] = [] if mirrored else ["evd_00000000-0000-4000-8000-0000000000ff"]
    request = PublishWorkRequestModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": _id("request", 2),
            "session_id": record.session_id,
            "writer_id": record.writer.writer_id,
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "event_drafts": (broken,),
            "actor": {"actor_id": "harness:mcp", "actor_type": "harness"},
            "client": {
                "kind": "cooperative_agent",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
        }
    )
    with pytest.raises(PublicOperationError) as captured:
        prepare_publication(
            request,
            channel=PublicationChannel.LOCAL_CLI,
            app=cast(Application, _App()),
        )
    return captured.value


@pytest.mark.anyio
async def test_reference_mirror_correction_survives_the_bridge_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corrective sentence only helps if it reaches the wire, not just the application layer."""

    failure = _reference_mirror_error()
    _install_clients(monkeypatch, [_FakeClient(failure)])
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_requests()["publish_work"], runtime)

    assert result.isError is True
    assert result.structuredContent is not None
    error = cast(dict[str, JsonValue], result.structuredContent["error"])
    assert error["code"] == "EVENT_INVALID"
    assert error["code"] != "INTERNAL_ERROR"
    details = cast(dict[str, JsonValue], error["safe_details"])
    assert details["reason_code"] == "ref_mirror_mismatch"
    assert details["field"] == "/event_drafts/0/evidence_refs"
    message = cast(str, error["message"])
    assert "evidence_refs" in message
    assert "yoetz://guidance/publication-policy.md" in message
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
        request_id: str | None = None,
    ) -> str:
        assert type(exc) is RuntimeError
        print(
            json.dumps(
                {
                    "component": component,
                    "operation": operation,
                    "correlation_id": correlation_id,
                    "request_id": request_id,
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
async def test_service_post_commit_projection_failure_maps_to_the_same_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service's own post-commit failure must read exactly like the bridge's.

    The daemon raises ``response_projection_failed`` when shaping an already-completed operation
    fails. An agent cannot tell which side lost the response, so both must produce one identical
    retryable remedy rather than a bare "the bridge could not complete the operation".
    """

    service_correlation = "err_00000000-0000-4000-8000-000000000096"
    client = _FakeClient(
        ControlError(
            "response_projection_failed",
            retryable=True,
            correlation_id=service_correlation,
        )
    )
    _install_clients(monkeypatch, [client])
    request_id = cast(str, _requests()["publish_work"]["request_id"])
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_requests()["publish_work"], runtime)

    assert result.isError is True
    assert result.structuredContent is not None
    error = cast(dict[str, object], result.structuredContent["error"])
    assert error["code"] == "INTERNAL_ERROR"
    assert error["retryable"] is True
    # Bridge reuses the service-minted id rather than generating a second one.
    assert error["correlation_id"] == service_correlation
    assert result.structuredContent["request_id"] == request_id
    assert "same request_id" in cast(str, error["message"])
    assert error.get("safe_details") == {"reason_code": "response_projection_failed"}
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_post_commit_response_shaping_failure_is_retryable_with_same_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable invoke must not collapse into non-retryable INTERNAL_ERROR on shape failure."""

    correlation_id = "err_00000000-0000-4000-8000-000000000097"
    client = _FakeClient()
    _install_clients(monkeypatch, [client])
    request_id = cast(str, _requests()["publish_work"]["request_id"])

    def boom(_result: object) -> object:
        raise RuntimeError("post-commit-shape-failure")

    def record(
        exc: BaseException,
        *,
        component: str,
        operation: str,
        request_id: str | None = None,
    ) -> str:
        del exc, component, operation, request_id
        return correlation_id

    monkeypatch.setattr(bridge, "public_model_to_wire", boom)
    monkeypatch.setattr(bridge, "record_unexpected_exception_without_raising", record)
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_requests()["publish_work"], runtime)

    assert result.isError is True
    assert result.structuredContent is not None
    error = cast(dict[str, object], result.structuredContent["error"])
    assert error["code"] == "INTERNAL_ERROR"
    assert error["retryable"] is True
    assert error["correlation_id"] == correlation_id
    assert result.structuredContent["request_id"] == request_id
    assert "same request_id" in cast(str, error["message"])
    assert error.get("safe_details") == {"reason_code": "response_projection_failed"}
    await bridge.close_bridge_runtime(runtime)


def test_publish_work_validation_names_event_drafts_field() -> None:
    """`event_drafts` is untyped JsonValue to Pydantic, so this exercises schema translation.

    The rejection can only come from `_validate_model_against_schema` re-raising
    `SchemaInstanceInvalid.absolute_path`; a bare Pydantic field error cannot produce this
    location. The r4 dogfood returned an empty pointer here, giving the agent nothing to fix.
    """

    from yoetz.mcp.errors import safe_validation_locations
    from yoetz.protocol.models import PublishWorkRequest

    def arguments(event_id: str) -> dict[str, object]:
        return {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": _id("request", 9),
            "session_id": _id("session", 1),
            "writer_id": _id("writer", 1),
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "actor": {"actor_id": "harness:mcp", "actor_type": "harness"},
            "client": {
                "kind": "cooperative_agent",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
            "event_drafts": [
                {
                    "event_id": event_id,
                    "schema": {"name": "plan_published", "version": "1.0.0"},
                    "occurred_at": "2026-01-01T00:00:00.000Z",
                    "causal_parents": [],
                    "payload": {
                        "plan_version": 1,
                        "summary": "Plan",
                        "obligation_refs": [],
                    },
                    "artifact_refs": [],
                    "evidence_refs": [],
                }
            ],
        }

    # Identical payload with a schema-valid event_id is accepted, so the only difference driving
    # the rejection below is the field the pointer names.
    PublishWorkRequest.model_validate(arguments(_id("event", 1)))

    with pytest.raises(Exception) as captured:
        PublishWorkRequest.model_validate(arguments("not-an-id"))

    locations = safe_validation_locations(captured.value)
    assert [item["field"] for item in locations] == ["/event_drafts/0/event_id"]


@pytest.fixture
def _restore_process_logging() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """``configure_logging`` mutates process-global state; restore it for every other test."""

    root = logging.getLogger()
    root_handlers = tuple(root.handlers)
    root_level = root.level
    last_resort = logging.lastResort
    raise_exceptions = logging.raiseExceptions
    yield
    root.handlers.clear()
    root.handlers.extend(root_handlers)
    root.setLevel(root_level)
    logging.lastResort = last_resort
    logging.raiseExceptions = raise_exceptions


def test_bridge_entry_point_installs_the_stdout_safe_structural_sink(
    monkeypatch: pytest.MonkeyPatch,
    _restore_process_logging: None,
) -> None:
    """Without this call the recorder mints correlation ids that reach no sink at all."""

    installed: list[LogMode] = []

    def configure(config: LoggingConfig, mode: LogMode) -> None:
        del config
        installed.append(mode)

    def run(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(bridge, "configure_logging", configure)
    monkeypatch.setattr(bridge.anyio, "run", run)

    bridge.main()

    assert installed == [LogMode.MCP_STDIO]


@pytest.mark.anyio
async def test_unexpected_bridge_error_emits_a_real_structured_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _restore_process_logging: None,
) -> None:
    """Exercise the installed sink rather than a stand-in for the recorder."""

    client = _FakeClient(RuntimeError("must-not-reach-public-output"))
    _install_clients(monkeypatch, [client])
    configure_logging(LoggingConfig(), LogMode.MCP_STDIO)
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_check(_requests()["check"], runtime)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert result.structuredContent is not None
    error = cast(dict[str, object], result.structuredContent["error"])
    emitted = json.loads(captured.err.strip().splitlines()[-1])
    assert emitted["correlation_id"] == error["correlation_id"]
    assert emitted["component"] == "mcp.bridge"
    assert emitted["operation"] == "check_internal_error"
    assert emitted["reason"] == "exception_runtime_error"
    assert "must-not-reach-public-output" not in captured.err
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


@pytest.mark.anyio
async def test_blocked_receipt_projection_keeps_the_client_and_names_the_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy-blocked receipt format must be answered on the live client, not reconnected around.

    This is the shape a real agent hit five times in a row: a retryable-looking failure plus a
    torn-down connection taught it to repeat the one request that could never succeed.
    """

    client = _FakeClient(ControlError("privacy_projection_blocked", retryable=False))
    remaining = _install_clients(monkeypatch, [client])
    runtime = bridge.build_bridge_runtime()

    blocked = await bridge.dispatch_receipt(_requests()["receipt"], runtime)

    assert blocked.isError is True
    assert blocked.structuredContent is not None
    error = cast(dict[str, JsonValue], blocked.structuredContent["error"])
    assert error["code"] == "PRIVACY_AUTHORITY_REQUIRED"
    assert error["retryable"] is False
    details = cast(dict[str, JsonValue], error["safe_details"])
    assert details["reason_code"] == "receipt_json_projection_blocked"
    message = cast(str, error["message"])
    # The message has to carry the remedy; the reason code alone is not actionable.
    assert "markdown" in message and "text" in message
    # No reconnect was attempted and the durable session was never dropped.
    assert client.closed is False
    assert len(remaining) == 0

    # The next receipt call reaches the same client, so switching format needs no new connection.
    follow_up = await bridge.dispatch_receipt(_requests()["receipt"], runtime)
    assert follow_up.isError is True
    assert [name for name, _request in client.calls] == ["receipt", "receipt"]
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_transient_projection_failure_stays_retryable_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transient reservation failure keeps its retryable meaning and its own reason code."""

    client = _FakeClient(ControlError("privacy_projection_unavailable", retryable=True))
    _install_clients(monkeypatch, [client])
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_receipt(_requests()["receipt"], runtime)

    assert result.structuredContent is not None
    error = cast(dict[str, JsonValue], result.structuredContent["error"])
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert error["retryable"] is True
    details = cast(dict[str, JsonValue], error["safe_details"])
    assert details["reason_code"] == "privacy_projection_unavailable"
    await bridge.close_bridge_runtime(runtime)
