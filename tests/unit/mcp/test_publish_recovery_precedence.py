"""Envelope-first publish recovery must not mask a known authoring error (issue #65).

When body validation fails, the bridge still looks up the request_id so a committed operation can
be recovered. Only an authoritative found state replaces the field-pointed INVALID_REQUEST. An
unavailable recovery oracle returns an ambiguity-safe same-ID remedy — never the nested status
read's "no durable state changed / new request_id" message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import pytest
from mcp import types

import yoetz.mcp.server as bridge
from yoetz.ports.control import ControlError
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

_REQUEST = "req_00000000-0000-4000-8000-000000000065"
_SESSION = "ses_00000000-0000-4000-8000-000000000001"
_WRITER = "wri_00000000-0000-4000-8000-000000000001"
_EVENT = "evt_00000000-0000-4000-8000-000000000001"
_DIGEST = "sha256:" + "a" * 64
# Dogfood-shaped authority: fails the actor_id pattern (whitespace), not a non-string type that
# confuses the event-schema oneOf into pointing at /event_drafts/0/schema/name.
_MALFORMED_AUTHORITY = "bad authority"
_HOSTILE_PAYLOAD = "SECRET_MUST_NOT_LEAK"


def _malformed_decision_arguments(
    *,
    request_id: str = _REQUEST,
    dry_run: bool | None = None,
    authority: object = _MALFORMED_AUTHORITY,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": request_id,
        "session_id": _SESSION,
        "writer_id": _WRITER,
        "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
        "actor": {"actor_id": "harness:mcp", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
        "event_drafts": [
            {
                "event_id": _EVENT,
                "schema": {"name": "decision_recorded", "version": "1.0.0"},
                "occurred_at": "2026-01-01T00:00:00.000Z",
                "causal_parents": [],
                "payload": {
                    "statement": "ship it",
                    "rationale": "because",
                    "authority": authority,
                },
                "artifact_refs": [],
                "evidence_refs": [],
            }
        ],
    }
    if dry_run is not None:
        arguments["dry_run"] = dry_run
    return arguments


def _operation_wire(state: str) -> dict[str, object]:
    page: dict[str, object] = {
        "operation_request_id": _REQUEST,
        "found": state != "absent",
        "state": state,
    }
    if state != "absent":
        page["operation_kind"] = "publish_work"
    if state == "complete":
        page["outcome"] = "accepted"
        page["subject_frontier"] = {"sequence": "1", "head_digest": _DIGEST}
        page["result_frontier"] = {"sequence": "1", "head_digest": _DIGEST}
        page["accepted_events"] = [
            {
                "event_id": _EVENT,
                "entry_digest": _DIGEST,
                "ingestion_sequence": "1",
                "writer_sequence": "1",
                "projection_status": "projected",
            }
        ]
    return {"ok": True, "page": page}


def _install_recovery_oracle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_wire: Mapping[str, object] | None = None,
    raise_on_status: BaseException | None = None,
) -> list[object]:
    """Drive the recovery status path without a live service client."""

    seen: list[object] = []

    async def ensure(_runtime: object = bridge.BRIDGE_RUNTIME) -> object:
        return object()

    async def invoke(
        _runtime: object,
        request: object,
        _call: Callable[[object, object], Awaitable[object]],
    ) -> object:
        seen.append(request)
        if raise_on_status is not None:
            raise raise_on_status
        return object()

    def wire(model: object) -> dict[str, object]:
        del model
        if status_wire is None:
            raise AssertionError("public_model_to_wire should not run without a status wire")
        return dict(status_wire)

    monkeypatch.setattr(bridge, "ensure_service_client", ensure)
    monkeypatch.setattr(bridge, "_invoke_with_reconnect", invoke)
    if status_wire is not None:
        monkeypatch.setattr(bridge, "public_model_to_wire", wire)
    return seen


def _error(result: types.CallToolResult) -> dict[str, object]:
    structured = cast(dict[str, object], result.structuredContent)
    return cast(dict[str, object], structured["error"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_malformed_authority_absent_returns_field_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoritative absence yields the original dogfood field pointer, not recovery noise."""

    _install_recovery_oracle(monkeypatch, status_wire=_operation_wire("absent"))
    runtime = bridge.build_bridge_runtime()
    arguments = _malformed_decision_arguments()

    result = await bridge.dispatch_publish_work(arguments, runtime)

    assert result.isError is True
    error = _error(result)
    assert error["code"] == PublicErrorCode.INVALID_REQUEST.value
    assert error["retryable"] is False
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    details = cast(dict[str, object], error["safe_details"])
    assert details["fields"] == ["/event_drafts/0/payload/authority"]
    assert details["reasons"] == ["invalid_type_or_value"]
    assert _HOSTILE_PAYLOAD not in str(result.structuredContent)
    assert _MALFORMED_AUTHORITY not in str(result.structuredContent)
    assert "No durable state changed" not in cast(str, error["message"])
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_completed_operation_wins_over_invalid_retry_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #47 guarantee: a committed request_id recovers even when the retry body is malformed."""

    _install_recovery_oracle(monkeypatch, status_wire=_operation_wire("complete"))
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    assert result.isError is True
    error = _error(result)
    assert error["code"] == PublicErrorCode.REQUEST_IDENTITY_CONFLICT.value
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    details = cast(dict[str, object], error["safe_details"])
    assert details["reason_code"] == "request_identity_conflict"
    assert details["count"] == 1
    assert details["sequence"] == 1
    assert details["head_digest"] == _DIGEST
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_pending_operation_retains_bounded_pending_meaning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recovery_oracle(monkeypatch, status_wire=_operation_wire("pending"))
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    error = _error(result)
    assert error["code"] == PublicErrorCode.OPERATION_PENDING.value
    assert error["retryable"] is True
    assert "still pending" in cast(str, error["message"])
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    # Must not be the recovery-unavailable shape.
    assert error.get("safe_details") is None or "operation_recovery_unavailable" not in str(
        error.get("safe_details")
    )
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_quarantined_operation_retains_storage_corrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recovery_oracle(monkeypatch, status_wire=_operation_wire("quarantined"))
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    error = _error(result)
    assert error["code"] == PublicErrorCode.STORAGE_CORRUPT.value
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_read_projection_failed_returns_operation_recovery_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dogfood defect: nested status read failure must not become the outer publish result."""

    _install_recovery_oracle(
        monkeypatch,
        raise_on_status=ControlError(
            "read_projection_failed",
            retryable=True,
            correlation_id="err_00000000-0000-4000-8000-000000000096",
        ),
    )
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    assert result.isError is True
    error = _error(result)
    assert error["code"] == PublicErrorCode.OPERATION_PENDING.value
    assert error["retryable"] is True
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    details = cast(dict[str, object], error["safe_details"])
    assert details["reason_code"] == "operation_recovery_unavailable"
    assert details["field"] == "/event_drafts/0/payload/authority"
    message = cast(str, error["message"])
    assert "same request_id" in message
    assert "No durable state changed" not in message
    assert "new request_id" not in message
    assert "read_projection_failed" not in message
    assert _MALFORMED_AUTHORITY not in message
    assert _HOSTILE_PAYLOAD not in str(result.structuredContent)
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_public_operation_error_during_recovery_is_unavailable_not_nested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recovery_oracle(
        monkeypatch,
        raise_on_status=PublicOperationError(
            PublicErrorCode.INTERNAL_ERROR,
            "Nested internal failure must not become outer durability advice.",
            True,
            safe_details={"reason_code": "read_projection_failed"},
        ),
    )
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    error = _error(result)
    assert error["code"] == PublicErrorCode.OPERATION_PENDING.value
    details = cast(dict[str, object], error["safe_details"])
    assert details["reason_code"] == "operation_recovery_unavailable"
    assert "Nested internal failure" not in cast(str, error["message"])
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_connection_failure_during_recovery_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recovery_oracle(
        monkeypatch,
        raise_on_status=ControlError("service_unavailable", retryable=True),
    )
    runtime = bridge.build_bridge_runtime()

    result = await bridge.dispatch_publish_work(_malformed_decision_arguments(), runtime)

    error = _error(result)
    assert error["code"] == PublicErrorCode.OPERATION_PENDING.value
    details = cast(dict[str, object], error["safe_details"])
    assert details["reason_code"] == "operation_recovery_unavailable"
    assert result.structuredContent is not None
    assert result.structuredContent["request_id"] == _REQUEST
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_malformed_dry_run_absent_returns_validation_not_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run creates no operation record; absent lookup returns the authoring pointer."""

    seen = _install_recovery_oracle(monkeypatch, status_wire=_operation_wire("absent"))
    runtime = bridge.build_bridge_runtime()
    arguments = _malformed_decision_arguments(dry_run=True)

    result = await bridge.dispatch_publish_work(arguments, runtime)

    assert len(seen) == 1  # recovery still consults operation identity; does not special-case
    error = _error(result)
    assert error["code"] == PublicErrorCode.INVALID_REQUEST.value
    details = cast(dict[str, object], error["safe_details"])
    assert details["fields"] == ["/event_drafts/0/payload/authority"]
    # No claim about durable append / frontier / request-id consumption.
    message = cast(str, error["message"])
    assert "No durable state changed" not in message
    assert "accepted" not in message.lower() or "invalid" in message.lower()
    await bridge.close_bridge_runtime(runtime)


@pytest.mark.anyio
async def test_hostile_authority_text_never_appears_in_unavailable_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_recovery_oracle(
        monkeypatch,
        raise_on_status=ControlError("read_projection_failed", retryable=True),
    )
    runtime = bridge.build_bridge_runtime()
    arguments = _malformed_decision_arguments(authority=f"x {_HOSTILE_PAYLOAD} y")

    result = await bridge.dispatch_publish_work(arguments, runtime)

    blob = str(result.structuredContent)
    assert _HOSTILE_PAYLOAD not in blob
    error = _error(result)
    assert cast(dict[str, object], error["safe_details"])["reason_code"] == (
        "operation_recovery_unavailable"
    )
    await bridge.close_bridge_runtime(runtime)
