"""MCP stdio bridge: six workflow tools plus read-only guidance, over the ordinary local service."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast

import anyio
from mcp import types
from mcp.server import InitializationOptions, NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import AnyUrl, BaseModel, ValidationError

from yoetz import __version__
from yoetz.adapters.mcp_stdio import bounded_stdio_server
from yoetz.config.load import load_config
from yoetz.config.models import LoggingConfig
from yoetz.mcp.descriptors import (
    TOOL_DESCRIPTORS,
    McpRouteProfile,
    ToolDescriptor,
    descriptor_for,
    server_instructions,
)
from yoetz.mcp.errors import (
    authoring_hint,
    build_last_resort_internal_error_result,
    build_public_error_result,
    safe_validation_locations,
    sanitize_unknown_tool_name,
    tool_error_envelope,
)
from yoetz.mcp.resources import (
    GUIDANCE_RESOURCES,
    GuidanceResource,
    GuidanceResourceError,
)
from yoetz.mcp.resources import (
    list_resources as list_guidance_resources,
)
from yoetz.mcp.resources import (
    read_resource as read_guidance_resource,
)
from yoetz.mcp.summaries import render_safe_compact_summary
from yoetz.observability.logging import (
    LogMode,
    configure_logging,
    record_public_error_without_raising,
    record_unexpected_exception_without_raising,
)
from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id, safe_request_id_from
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
    PublishWorkRequest,
    PublishWorkResult,
    ReadGuidanceResult,
    ReceiptRequest,
    ReceiptResult,
    RespondRequest,
    RespondResult,
    StartRequest,
    StartResult,
    StatusRequest,
    StatusResult,
    public_model_to_wire,
)
from yoetz.service.client import ServiceClient, connect_service_on_demand

__all__ = [
    "BRIDGE_RUNTIME",
    "BridgeRuntime",
    "build_bridge_runtime",
    "call_tool",
    "close_bridge_runtime",
    "dispatch_check",
    "dispatch_publish_work",
    "dispatch_read_guidance",
    "dispatch_receipt",
    "dispatch_respond",
    "dispatch_start",
    "dispatch_status",
    "ensure_service_client",
    "list_resources",
    "list_tools",
    "main",
    "read_resource",
    "result_from_public_model",
    "run_stdio",
    "server",
    "structured_error_result",
]

_SERVER_NAME: Final = "yoetz"
_WORKFLOW_RPC_DEADLINE_MS: Final = 30_000
_SEMANTIC_CHECK_RPC_DEADLINE_MS: Final = 300_000
_REGISTERED_TOOL_NAMES: Final = frozenset(
    {"start", "publish_work", "check", "respond", "status", "receipt", "read_guidance"}
)
_GUIDANCE_BY_URI: Final = MappingProxyType(
    {resource.uri: resource for resource in GUIDANCE_RESOURCES}
)
# Reconnecting only helps when the connection itself is the problem. A projection failure is
# answered by the live service and reconnecting around it drops the session for nothing, so both
# projection reasons are handled in place and surfaced with their own remedy.
_RECONNECT_REASONS: Final = frozenset({"service_unavailable", "service_generation_changed"})
_DISCARD_CLIENT_REASONS: Final = _RECONNECT_REASONS | {"vault_locked"}
# One wording for both post-commit projection failures — the service's own (control reason
# `response_projection_failed`) and the bridge's. In both the operation stands and the only safe
# recovery is replaying the same request_id, so the caller must never see them differ.
_RESPONSE_PROJECTION_FAILED_MESSAGE: Final = (
    "The operation completed on the local service, but its response could not be shaped. "
    "Retry with the same request_id to load the stored result."
)
_RESPONSE_PROJECTION_FAILED_DETAILS: Final = {"reason_code": "response_projection_failed"}
# A read never appended, so there is no stored result and no operation record to replay against.
# Telling the caller to reuse the request_id would send it after a recovery that cannot exist.
_READ_PROJECTION_FAILED_MESSAGE: Final = (
    "The read completed on the local service, but its response could not be shaped. No durable "
    "state changed. Repeat the request with a new request_id, or read status view=versions for "
    "the authoritative frontier."
)
_READ_PROJECTION_FAILED_DETAILS: Final = {"reason_code": "read_projection_failed"}
# Two facts are known for certain when envelope-first recovery cannot answer: this body failed
# bridge-local validation, and the recovery oracle could not be reached. The certain, actionable
# one leads; the durability caveat is stated plainly beside it rather than in place of it (issues
# #65, #239). The remedy is always safe: the service is the authority on request identity and will
# replay or reject the id itself.
_PUBLISH_RECOVERY_UNCHECKED_CAVEAT: Final = (
    " A prior operation under this request_id could not be checked; the local service was "
    "unreachable. Correct the named fields and resubmit; the service will replay or reject the "
    "request_id itself if it already names a committed operation."
)
_PUBLISH_RECOVERY_UNAVAILABLE_REASON: Final = "operation_recovery_unavailable"
_WRITE_OPERATIONS: Final = frozenset({"start", "publish_work", "check", "respond", "receipt"})
_REQUEST_TEMPLATES_GUIDANCE_URI: Final = "yoetz://guidance/request-templates.md"
_GUIDANCE_BY_OPERATION: Final = MappingProxyType(
    {
        "start": _REQUEST_TEMPLATES_GUIDANCE_URI,
        "publish_work": _REQUEST_TEMPLATES_GUIDANCE_URI,
        "check": _REQUEST_TEMPLATES_GUIDANCE_URI,
        "respond": _REQUEST_TEMPLATES_GUIDANCE_URI,
        "status": _REQUEST_TEMPLATES_GUIDANCE_URI,
        "receipt": _REQUEST_TEMPLATES_GUIDANCE_URI,
    }
)


def _authoring_hint_for(operation: str, locations: Sequence[Mapping[str, str]]) -> str:
    """Look up the frozen presentation schema for one tool and hint from it, or say nothing."""

    try:
        hint = authoring_hint(descriptor_for(operation).input_schema, locations, tool=operation)
    except Exception:
        # A hint is a convenience. Never let building one turn a clear validation error into an
        # internal error.
        hint = ""
    guidance = _GUIDANCE_BY_OPERATION.get(operation)
    if guidance is None:
        return hint
    # Only the registered URI — never synthesized prose. Manifest verification happens at read.
    suffix = f" Guidance: {guidance}."
    if hint.endswith("."):
        return hint[:-1] + ";" + suffix
    if hint:
        return hint + suffix
    return " Hint:" + suffix


def invalid_request_message(operation: str, locations: Sequence[Mapping[str, str]]) -> str:
    """Compose the public INVALID_REQUEST message (schema hint + guidance URI when known)."""

    return "The tool arguments are invalid." + _authoring_hint_for(operation, locations)


@dataclass(slots=True)
class _ClientSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: ServiceClient | None = None


@dataclass(frozen=True, slots=True)
class BridgeRuntime:
    """Verified static surface plus one private, initially empty ordinary-client slot."""

    route_profile: McpRouteProfile
    descriptors: tuple[ToolDescriptor, ...]
    resources: tuple[GuidanceResource, ...]
    instructions: str
    host_profile: Literal["generic", "cursor"] = "generic"
    workspace_locator: WorkspaceLocator = field(
        default_factory=lambda: WorkspaceLocator(os.fspath(Path.cwd().resolve(strict=True))),
        repr=False,
        compare=False,
    )
    _slot: _ClientSlot = field(default_factory=_ClientSlot, repr=False, compare=False)


def build_bridge_runtime(
    route_profile: McpRouteProfile = "policy",
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> BridgeRuntime:
    """Verify every agent-readable byte and construct an unconnected bridge runtime."""

    if route_profile not in TOOL_DESCRIPTORS:
        raise ValueError("mcp_route_profile_invalid")
    if host_profile not in {"generic", "cursor"}:
        raise ValueError("mcp_host_profile_invalid")
    resources = list_guidance_resources()
    instructions = server_instructions(route_profile)
    if not instructions:
        raise RuntimeError("mcp_instructions_empty")
    # Accessing every schema verifies its checked-in public identity before serving.
    descriptors = TOOL_DESCRIPTORS[route_profile]
    for descriptor in descriptors:
        descriptor.input_schema
        descriptor.output_schema
    build_last_resort_internal_error_result()
    workspace_locator = WorkspaceLocator(os.fspath(Path.cwd().resolve(strict=True)))
    return BridgeRuntime(
        route_profile,
        descriptors,
        resources,
        instructions,
        host_profile,
        workspace_locator,
    )


BRIDGE_RUNTIME: Final = build_bridge_runtime()


async def _close_client(client: ServiceClient) -> None:
    try:
        await client.close()
    except Exception:
        pass


async def _discard_client(runtime: BridgeRuntime, client: ServiceClient) -> None:
    async with runtime._slot.lock:  # pyright: ignore[reportPrivateUsage]
        if runtime._slot.client is client:  # pyright: ignore[reportPrivateUsage]
            runtime._slot.client = None  # pyright: ignore[reportPrivateUsage]
    await _close_client(client)


async def ensure_service_client(runtime: BridgeRuntime = BRIDGE_RUNTIME) -> ServiceClient:
    """Return one live ordinary client, lazily connecting without starting the service."""

    async with runtime._slot.lock:  # pyright: ignore[reportPrivateUsage]
        existing = runtime._slot.client  # pyright: ignore[reportPrivateUsage]
        if existing is not None:
            try:
                await existing.connect()
            except Exception:
                runtime._slot.client = None  # pyright: ignore[reportPrivateUsage]
                await _close_client(existing)
            else:
                return existing
        connected: ServiceClient | None = None
        try:
            connected = await connect_service_on_demand(
                ControlClientKind.MCP_BRIDGE,
                workspace_locator=runtime.workspace_locator,
            )
            await connected.connect()
        except Exception:
            runtime._slot.client = None  # pyright: ignore[reportPrivateUsage]
            if connected is not None:
                await _close_client(connected)
            raise
        runtime._slot.client = connected  # pyright: ignore[reportPrivateUsage]
        return connected


async def close_bridge_runtime(runtime: BridgeRuntime = BRIDGE_RUNTIME) -> None:
    """Close only this bridge's local client connection, never the persistent service."""

    async with runtime._slot.lock:  # pyright: ignore[reportPrivateUsage]
        client = runtime._slot.client  # pyright: ignore[reportPrivateUsage]
        runtime._slot.client = None  # pyright: ignore[reportPrivateUsage]
    if client is not None:
        await _close_client(client)


def _result_text(
    wire: Mapping[str, object],
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> str:
    """Project one result onto the model-visible text channel for the selected host.

    Cursor 3.17.x does not reliably deliver MCP ``structuredContent`` to the model. Its native
    plugin therefore opts into an exact canonical-JSON copy in ``content`` while retaining the
    structured result. Other hosts keep the privacy-minimized bounded summary. The Cursor copy
    adds no fields: it is byte-for-byte a second encoding of the already-authorized wire body.
    """

    if host_profile == "cursor":
        return canonical_encode(cast(JsonValue, dict(wire))).decode("utf-8")
    if host_profile != "generic":
        raise ValueError("mcp_host_profile_invalid")
    return render_safe_compact_summary(wire)


def result_from_public_model(
    result: object,
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> types.CallToolResult:
    """Validate and project one public result to structured plus host-compatible text."""

    wire = public_model_to_wire(result)
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=_result_text(wire, host_profile=host_profile))
        ],
        structuredContent=cast(dict[str, object], wire),
        isError=wire.get("ok") is False,
    )


def _result_from_wire(
    wire: Mapping[str, object],
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> types.CallToolResult:
    structured = dict(wire)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=_result_text(structured, host_profile=host_profile),
            )
        ],
        structuredContent=structured,
        isError=True,
    )


# Bridge operation names are repository literals ("check", "publish_work", …); the guard keeps a
# malformed one from silently voiding the sink record, whose ``operation`` field is required.
_SINK_OPERATION: Final = re.compile(r"^[a-z][a-z0-9_]{0,47}$", re.ASCII)


def _public_error_operation(operation: str | None) -> str:
    """Return the bounded sink operation token for a bridge-minted public error."""

    if type(operation) is str and _SINK_OPERATION.fullmatch(operation) is not None:
        return f"{operation}_public_error"
    return "public_error"


def structured_error_result(
    code: PublicErrorCode,
    message: str,
    *,
    retryable: bool = False,
    request_id: str | None = None,
    safe_details: object | None = None,
    details_reason_code: str | None = None,
    correlation_id: str | None = None,
    operation: str | None = None,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> types.CallToolResult:
    """Build a bounded structured tool error with a prevalidated nested fallback.

    ``correlation_id`` is an id some sink already recorded — service-minted, or returned by the
    exception recorder — and is reused verbatim. When it is absent this mints the id *and* writes
    the durable diagnostic line under it, because an id minted straight into the envelope resolves
    to nothing (issue #191). ``operation`` only names the failing tool in that record; it never
    reaches the wire. ``details_reason_code`` rides beside field locations, which no other
    ``safe_details`` shape can carry.
    """

    resolved_correlation_id = (
        correlation_id
        if correlation_id is not None
        else record_public_error_without_raising(
            component="mcp.bridge",
            operation=_public_error_operation(operation),
            reason=code.value.lower(),
            request_id=request_id,
        )
    )
    try:
        wire = build_public_error_result(
            code,
            message,
            retryable,
            resolved_correlation_id,
            request_id=request_id,
            safe_details=safe_details,
            details_reason_code=details_reason_code,
        )
        return _result_from_wire(wire, host_profile=host_profile)
    except Exception:
        fallback = build_last_resort_internal_error_result()
        try:
            return _result_from_wire(fallback, host_profile=host_profile)
        except Exception:
            text = (
                canonical_encode(cast(JsonValue, fallback)).decode("utf-8")
                if host_profile == "cursor"
                else "Error INTERNAL_ERROR."
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                structuredContent=fallback,
                isError=True,
            )


def _control_error_result(
    error: ControlError,
    request_id: str | None,
    operation: str,
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> types.CallToolResult:
    # Prefer the service-minted diagnostic id when present so the agent-facing public error
    # resolves the same durable sink record the daemon already wrote. Never mint a second id for
    # a failure the service already correlated.
    service_correlation_id = error.correlation_id
    if error.reason == "vault_locked":
        # The daemon's retryable flag distinguishes a soft lock or transient re-unlock
        # failure (heals on a later attempt) from a hard lock or missing setup (needs a
        # trusted ceremony). Asserting the terminal cause for a transient one told agents
        # to abandon evidence and receipts that a single retry would have recorded (#276).
        if error.retryable:
            message = (
                "The local service vault is soft-locked or a transient unlock attempt just "
                "failed; it re-opens automatically. Retry this operation shortly. If the "
                "error persists, on a local terminal run `yoetz service unlock` or "
                "`yoetz service auto-unlock repair`. Never send secrets over MCP."
            )
        else:
            message = (
                "The local service vault is locked or uninitialized. After trusted setup with "
                "platform auto-unlock, soft locks (idle/session) re-open automatically; this "
                "error means a hard lock or missing setup. On a local terminal run "
                "`yoetz service unlock` (uses the platform credential store when provisioned) "
                "or `yoetz service auto-unlock repair` if that entry is stale. If still "
                "uninitialized, run `yoetz setup` or prepare `vault_initialize` via "
                "`yoetz consent catalog` / `prepare` (ADR-015). Never send secrets over MCP."
            )
        return structured_error_result(
            PublicErrorCode.VAULT_LOCKED,
            message,
            retryable=error.retryable,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            host_profile=host_profile,
        )
    if error.reason == "request_cancelled":
        return structured_error_result(
            PublicErrorCode.CANCELLED,
            "The operation was cancelled.",
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            host_profile=host_profile,
        )
    if error.reason == "privacy_projection_blocked":
        return structured_error_result(
            PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED,
            (
                "The receipt is durably recorded, but JSON projection to agent context is blocked "
                "by the active privacy policy. Re-request with format markdown or text, or widen "
                "the agent-context policy from a local terminal."
            ),
            retryable=False,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            safe_details={
                "reason_code": "receipt_json_projection_blocked",
                "operation": "receipt",
                "field": "format",
            },
            host_profile=host_profile,
        )
    if error.reason == "response_projection_failed":
        # Accepted durable publish_work usually returns the reduced total-acceptance envelope
        # instead. This mapping remains for non-publish writes and for the genuinely impossible
        # case where even the minimal publish envelope cannot be built. When the daemon attached
        # accepted_state (sequence/head_digest/count), surface those structural facts so the
        # agent does not need a second status call to learn where the write landed.
        details: dict[str, object] = dict(_RESPONSE_PROJECTION_FAILED_DETAILS)
        if error.accepted_state:
            details.update(error.accepted_state)
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            _RESPONSE_PROJECTION_FAILED_MESSAGE,
            retryable=True,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            safe_details=details,
            host_profile=host_profile,
        )
    if error.reason == "read_projection_failed":
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            _READ_PROJECTION_FAILED_MESSAGE,
            retryable=True,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            safe_details=dict(_READ_PROJECTION_FAILED_DETAILS),
            host_profile=host_profile,
        )
    if error.reason == "privacy_projection_unavailable":
        return structured_error_result(
            PublicErrorCode.SERVICE_UNAVAILABLE,
            "Receipt projection is temporarily unavailable; retry after the local service is ready.",
            retryable=True,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            safe_details={"reason_code": "privacy_projection_unavailable"},
            host_profile=host_profile,
        )
    if error.reason == "request_timeout":
        if operation in _WRITE_OPERATIONS:
            message = (
                "The local operation timed out and may still have committed. Retry with the same "
                "request_id to recover the stored outcome; for an existing Yoetz session, status "
                "view=operation can inspect that request_id."
            )
        else:
            message = (
                "The local status read timed out and requested no write. Repeat the read with a "
                "new request_id."
            )
        return structured_error_result(
            PublicErrorCode.SERVICE_UNAVAILABLE,
            message,
            retryable=True,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            host_profile=host_profile,
        )
    if error.reason in {"service_unavailable", "service_draining"}:
        return structured_error_result(
            PublicErrorCode.SERVICE_UNAVAILABLE,
            "The local service is unavailable; retry after it is ready.",
            retryable=True,
            request_id=request_id,
            correlation_id=service_correlation_id,
            operation=operation,
            host_profile=host_profile,
        )
    if service_correlation_id is not None:
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            "The bridge could not complete the operation.",
            request_id=request_id,
            correlation_id=service_correlation_id,
            host_profile=host_profile,
        )
    correlation_id = record_unexpected_exception_without_raising(
        error,
        component="mcp.bridge",
        operation=f"{operation}_internal_error",
        request_id=request_id,
    )
    return structured_error_result(
        PublicErrorCode.INTERNAL_ERROR,
        "The bridge could not complete the operation.",
        request_id=request_id,
        correlation_id=correlation_id,
        host_profile=host_profile,
    )


def _mutable_json(value: object) -> object:
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): _mutable_json(item) for key, item in source.items()}
    if isinstance(value, tuple | list):
        source_sequence = cast(tuple[object, ...] | list[object], value)
        return [_mutable_json(item) for item in source_sequence]
    return value


async def _invoke_with_reconnect[RequestT: BaseModel, ResultT: BaseModel](
    runtime: BridgeRuntime,
    request: RequestT,
    invoke: Callable[[ServiceClient, RequestT], Awaitable[ResultT]],
) -> ResultT:
    client = await ensure_service_client(runtime)
    try:
        return await invoke(client, request)
    except ControlError as error:
        if error.reason in _DISCARD_CLIENT_REASONS:
            await _discard_client(runtime, client)
        if not error.retryable or error.reason not in _RECONNECT_REASONS:
            raise
        replacement = await ensure_service_client(runtime)
        return await invoke(replacement, request)


async def _dispatch[RequestT: BaseModel, ResultT: BaseModel](
    arguments: Mapping[str, object],
    request_type: type[RequestT],
    result_type: type[ResultT],
    invoke: Callable[[ServiceClient, RequestT], Awaitable[ResultT]],
    runtime: BridgeRuntime,
    operation: str,
) -> types.CallToolResult:
    request_id = safe_request_id_from(arguments)
    try:
        request = request_type.model_validate(arguments)
    except ValidationError as exc:
        locations = safe_validation_locations(exc)
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            invalid_request_message(operation, locations),
            request_id=request_id,
            safe_details=locations if locations else None,
            operation=operation,
            host_profile=runtime.host_profile,
        )
    except Exception as exc:
        # Only non-ValidationError failures reach here, so the validator itself crashed. That is an
        # unexpected bridge error, not caller fault.
        correlation_id = record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation=f"{operation}_request_internal_error",
            request_id=request_id,
        )
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            "The bridge could not complete the operation.",
            request_id=request_id,
            correlation_id=correlation_id,
            host_profile=runtime.host_profile,
        )
    # Invoke first. Once this returns, a write may already be durable; response shaping must not
    # collapse that into a non-retryable INTERNAL_ERROR that steers agents away from same-id resume.
    try:
        result = await _invoke_with_reconnect(runtime, request, invoke)
    except PublicOperationError as exc:
        # Defense in depth: the ordinary client normally returns ok:false bodies, but if a
        # PublicOperationError escapes the service boundary, keep the exact public code.
        try:
            bound = (
                exc
                if exc.correlation_id is not None
                else exc.bind_correlation_id(
                    record_public_error_without_raising(
                        component="mcp.bridge",
                        operation=_public_error_operation(operation),
                        reason=exc.code.value.lower(),
                        request_id=request_id,
                    )
                )
            )
            return _result_from_wire(
                tool_error_envelope(bound, request_id=request_id),
                host_profile=runtime.host_profile,
            )
        except Exception as mapping_exc:
            correlation_id = record_unexpected_exception_without_raising(
                mapping_exc,
                component="mcp.bridge",
                operation=f"{operation}_public_error_internal_error",
                request_id=request_id,
            )
            return structured_error_result(
                PublicErrorCode.INTERNAL_ERROR,
                "The bridge could not complete the operation.",
                request_id=request_id,
                correlation_id=correlation_id,
                host_profile=runtime.host_profile,
            )
    except ControlError as exc:
        return _control_error_result(
            exc,
            request_id,
            operation,
            host_profile=runtime.host_profile,
        )
    except Exception as exc:
        correlation_id = record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation=f"{operation}_internal_error",
            request_id=request_id,
        )
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            "The bridge could not complete the operation.",
            request_id=request_id,
            correlation_id=correlation_id,
            host_profile=runtime.host_profile,
        )

    try:
        wire = public_model_to_wire(result)
        validated = result_type.model_validate(wire)
        return result_from_public_model(validated, host_profile=runtime.host_profile)
    except Exception as exc:
        correlation_id = record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation=f"{operation}_response_projection_failed",
            request_id=request_id,
        )
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            _RESPONSE_PROJECTION_FAILED_MESSAGE,
            retryable=True,
            request_id=request_id,
            correlation_id=correlation_id,
            safe_details=dict(_RESPONSE_PROJECTION_FAILED_DETAILS),
            host_profile=runtime.host_profile,
        )


async def dispatch_start(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        StartRequest,
        StartResult,
        lambda client, request: client.start(request, deadline_ms=_WORKFLOW_RPC_DEADLINE_MS),
        runtime,
        "start",
    )


def _publish_envelope_fields(
    arguments: Mapping[str, object],
) -> tuple[str, str, str, Mapping[str, object], Mapping[str, object]] | None:
    """Return ``(request_id, session_id, writer_id, actor, client)`` when the envelope is complete.

    Recovery is keyed only on these fields. Event drafts are intentionally ignored so a body that
    fails schema validation can still reach the operation-identity recovery path.
    """

    request_id = safe_request_id_from(arguments)
    session_id = arguments.get("session_id")
    writer_id = arguments.get("writer_id")
    actor = arguments.get("actor")
    client = arguments.get("client")
    protocol_version = arguments.get("protocol_version")
    schema_version = arguments.get("schema_version")
    if (
        type(request_id) is not str
        or type(session_id) is not str
        or type(writer_id) is not str
        or not isinstance(actor, Mapping)
        or not isinstance(client, Mapping)
        or protocol_version != "0.1"
        or schema_version != "1.0.0"
    ):
        return None
    return (
        request_id,
        session_id,
        writer_id,
        cast(Mapping[str, object], actor),
        cast(Mapping[str, object], client),
    )


class _PublishRecoveryKind(Enum):
    """Closed tri-state for envelope-first publish recovery lookup."""

    FOUND = "found"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class _PublishRecoveryOutcome:
    """Result of looking up a request_id after body validation failed.

    Only FOUND replaces the authoring diagnostic. ABSENT yields the original field-pointed
    validation result. UNAVAILABLE annotates it with a durability caveat rather than replacing it:
    an uncertain durability claim must never displace the certain authoring one (issue #239).
    """

    kind: _PublishRecoveryKind
    result: types.CallToolResult | None = None


def _publish_is_declared_dry_run(arguments: Mapping[str, object]) -> bool:
    """True only for the exact boolean ``dry_run: true``.

    A dry run appends nothing, so no prior-operation lookup can change the answer to a body that
    already failed local validation, and the oracle's deadline would be pure tax on the authoring
    loop (issues #239, #240). Anything other than the literal boolean is itself a validation
    failure and must not buy a durability shortcut.
    """

    return arguments.get("dry_run") is True


def _publish_validation_recovery_unavailable_result(
    request_id: str | None,
    locations: Sequence[Mapping[str, str]] = (),
    *,
    host_profile: Literal["generic", "cursor"] = "generic",
) -> types.CallToolResult:
    """Field-pointed INVALID_REQUEST primary, unreachable-oracle caveat alongside."""

    message = (
        "The tool arguments are invalid."
        + _PUBLISH_RECOVERY_UNCHECKED_CAVEAT
        + _authoring_hint_for("publish_work", locations)
    )
    if not locations:
        # Nothing locatable: keep the availability signal machine-readable and say no more.
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            message,
            request_id=request_id,
            safe_details={"reason_code": _PUBLISH_RECOVERY_UNAVAILABLE_REASON},
            operation="publish_work_recovery_unavailable",
            host_profile=host_profile,
        )
    return structured_error_result(
        PublicErrorCode.INVALID_REQUEST,
        message,
        request_id=request_id,
        safe_details=tuple(locations),
        details_reason_code=_PUBLISH_RECOVERY_UNAVAILABLE_REASON,
        operation="publish_work_recovery_unavailable",
        host_profile=host_profile,
    )


async def _publish_recovery_from_envelope(
    arguments: Mapping[str, object],
    runtime: BridgeRuntime,
    request_id: str | None,
) -> _PublishRecoveryOutcome:
    """Look up the envelope request_id without body validation.

    Returns a closed tri-state: FOUND (pending/complete/quarantined), ABSENT (lookup succeeded
    and no operation exists), or UNAVAILABLE (connection, timeout, projection, or unexpected
    recovery failure). Incomplete envelopes that cannot form a recovery read are ABSENT so the
    original field-pointed validation result surfaces.
    """

    envelope = _publish_envelope_fields(arguments)
    if envelope is None:
        return _PublishRecoveryOutcome(_PublishRecoveryKind.ABSENT)
    op_request_id, session_id, writer_id, actor, client = envelope
    recovery_request_id = request_id if request_id is not None else op_request_id
    status_request_id = new_id(IdKind.REQUEST)
    try:
        status_request = StatusRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": status_request_id,
                "session_id": session_id,
                "writer_id": writer_id,
                "view": "operation",
                "limit": "1",
                "filter": {"operation_request_id": op_request_id},
                "actor": dict(actor),
                "client": dict(client),
            }
        )
    except ValidationError:
        # Envelope fields are not a valid status request; fall through to body validation error.
        return _PublishRecoveryOutcome(_PublishRecoveryKind.ABSENT)
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation="publish_work_recovery_request_internal_error",
            request_id=recovery_request_id,
        )
        return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)

    try:
        await ensure_service_client(runtime)
        status_result = await _invoke_with_reconnect(
            runtime,
            status_request,
            lambda service, request: service.status(request, deadline_ms=_WORKFLOW_RPC_DEADLINE_MS),
        )
    except PublicOperationError as exc:
        # A nested public failure (session conflict, projection, etc.) does not prove the
        # operation is absent or found. Do not promote it over the outer authoring diagnostic,
        # and do not forward nested "no durable state changed" remedies for a write path.
        del exc
        return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)
    except ControlError as exc:
        # Including read_projection_failed: a failed recovery oracle must not become the outer
        # publish result with a read-only durability claim and a new-request_id remedy.
        del exc
        return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation="publish_work_recovery_status_internal_error",
            request_id=recovery_request_id,
        )
        return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)

    try:
        wire = public_model_to_wire(status_result)
        if wire.get("ok") is not True:
            return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)
        page = wire.get("page")
        if type(page) is not dict:
            return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)
        page_map = cast(dict[str, object], page)
        state = page_map.get("state")
        if state == "absent":
            return _PublishRecoveryOutcome(_PublishRecoveryKind.ABSENT)
        if state == "pending":
            return _PublishRecoveryOutcome(
                _PublishRecoveryKind.FOUND,
                structured_error_result(
                    PublicErrorCode.OPERATION_PENDING,
                    "The operation is still pending.",
                    retryable=True,
                    request_id=recovery_request_id,
                    operation="publish_work_recovery",
                    host_profile=runtime.host_profile,
                ),
            )
        if state == "quarantined":
            return _PublishRecoveryOutcome(
                _PublishRecoveryKind.FOUND,
                structured_error_result(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "The stored operation is quarantined.",
                    request_id=recovery_request_id,
                    operation="publish_work_recovery",
                    host_profile=runtime.host_profile,
                ),
            )
        if state != "complete":
            return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)
        result_frontier = page_map.get("result_frontier")
        accepted = page_map.get("accepted_events")
        count = len(cast(Sequence[object], accepted)) if isinstance(accepted, Sequence) else 0
        details: dict[str, object] = {"reason_code": "request_identity_conflict", "count": count}
        if type(result_frontier) is dict:
            frontier_map = cast(dict[str, object], result_frontier)
            sequence = frontier_map.get("sequence")
            head_digest = frontier_map.get("head_digest")
            if type(sequence) is str and sequence.isdigit():
                details["sequence"] = int(sequence)
            if type(head_digest) is str:
                details["head_digest"] = head_digest
        return _PublishRecoveryOutcome(
            _PublishRecoveryKind.FOUND,
            structured_error_result(
                PublicErrorCode.REQUEST_IDENTITY_CONFLICT,
                (
                    "The request ID was already used with a different request body. "
                    "Read status view=operation for the stored result of that request_id."
                ),
                request_id=recovery_request_id,
                safe_details=details,
                operation="publish_work_recovery",
                host_profile=runtime.host_profile,
            ),
        )
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation="publish_work_recovery_response_internal_error",
            request_id=recovery_request_id,
        )
        return _PublishRecoveryOutcome(_PublishRecoveryKind.UNAVAILABLE)


async def dispatch_publish_work(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    request_id = safe_request_id_from(arguments)
    try:
        PublishWorkRequest.model_validate(arguments)
    except ValidationError as exc:
        # Envelope-first recovery still runs for real submissions so a request_id that already
        # names a commit is never reported as a bare INVALID_REQUEST (PR #47). A declared dry run
        # cannot append, so the lookup can only cost the oracle deadline and confuse the answer
        # (issues #239, #240).
        locations = safe_validation_locations(exc)
        if not _publish_is_declared_dry_run(arguments):
            recovery = await _publish_recovery_from_envelope(arguments, runtime, request_id)
            if recovery.kind is _PublishRecoveryKind.FOUND and recovery.result is not None:
                return recovery.result
            if recovery.kind is _PublishRecoveryKind.UNAVAILABLE:
                return _publish_validation_recovery_unavailable_result(
                    request_id,
                    locations,
                    host_profile=runtime.host_profile,
                )
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            invalid_request_message("publish_work", locations),
            request_id=request_id,
            safe_details=locations if locations else None,
            operation="publish_work",
            host_profile=runtime.host_profile,
        )
    except Exception as exc:
        correlation_id = record_unexpected_exception_without_raising(
            exc,
            component="mcp.bridge",
            operation="publish_work_request_internal_error",
            request_id=request_id,
        )
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            "The bridge could not complete the operation.",
            request_id=request_id,
            correlation_id=correlation_id,
            host_profile=runtime.host_profile,
        )
    return await _dispatch(
        arguments,
        PublishWorkRequest,
        PublishWorkResult,
        lambda client, request: client.publish_work(request, deadline_ms=_WORKFLOW_RPC_DEADLINE_MS),
        runtime,
        "publish_work",
    )


async def dispatch_check(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        CheckRequest,
        CheckResult,
        lambda client, request: client.check(
            request,
            deadline_ms=_SEMANTIC_CHECK_RPC_DEADLINE_MS,
            route_profile=runtime.route_profile,
        ),
        runtime,
        "check",
    )


async def dispatch_respond(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        RespondRequest,
        RespondResult,
        lambda client, request: client.respond(request, deadline_ms=_WORKFLOW_RPC_DEADLINE_MS),
        runtime,
        "respond",
    )


async def dispatch_status(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        StatusRequest,
        StatusResult,
        lambda client, request: client.status(
            request,
            deadline_ms=_WORKFLOW_RPC_DEADLINE_MS,
            route_profile=runtime.route_profile,
        ),
        runtime,
        "status",
    )


async def dispatch_receipt(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        ReceiptRequest,
        ReceiptResult,
        lambda client, request: client.receipt(request, deadline_ms=_WORKFLOW_RPC_DEADLINE_MS),
        runtime,
        "receipt",
    )


async def dispatch_read_guidance(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    """Return one registered guidance document as tool text. Does not touch the service."""

    extra_keys = sorted(str(key) for key in arguments if key != "uri")
    if extra_keys:
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            "read_guidance rejects extra argument keys.",
            safe_details={"argument_count": len(arguments)},
            operation="read_guidance",
            host_profile=runtime.host_profile,
        )
    raw_uri = arguments.get("uri")
    if type(raw_uri) is not str or not raw_uri:
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            "read_guidance requires a registered guidance URI.",
            safe_details={"argument_count": len(arguments)},
            operation="read_guidance",
            host_profile=runtime.host_profile,
        )
    resource = _GUIDANCE_BY_URI.get(raw_uri)
    if resource is None:
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            "read_guidance rejects an unknown guidance URI.",
            safe_details={"argument_count": len(arguments)},
            operation="read_guidance",
            host_profile=runtime.host_profile,
        )
    text = resource.text
    result = ReadGuidanceResult.model_validate(
        {
            "ok": True,
            "uri": resource.uri,
            "media_type": resource.media_type,
            "byte_count": len(text.encode("utf-8")),
            "text": text,
        }
    )
    wire = public_model_to_wire(result)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=cast(dict[str, object], wire),
        isError=False,
    )


async def list_tools(runtime: BridgeRuntime = BRIDGE_RUNTIME) -> list[types.Tool]:
    """Return the exact reviewed tool inventory in stable order."""

    return [
        types.Tool(
            name=descriptor.name,
            title=descriptor.title,
            description=descriptor.description,
            inputSchema=cast(dict[str, object], _mutable_json(descriptor.input_schema)),
            outputSchema=cast(dict[str, object], _mutable_json(descriptor.output_schema)),
            annotations=types.ToolAnnotations(
                title=descriptor.title,
                readOnlyHint=descriptor.annotations.read_only,
                destructiveHint=descriptor.annotations.destructive,
                idempotentHint=descriptor.annotations.idempotent,
                openWorldHint=descriptor.annotations.open_world,
            ),
        )
        for descriptor in runtime.descriptors
    ]


async def call_tool(
    name: str,
    arguments: dict[str, object],
    runtime: BridgeRuntime = BRIDGE_RUNTIME,
) -> types.CallToolResult:
    """Dispatch one registered operation with bridge-owned strict validation.

    Unregistered names raise ``McpError`` so the low-level session answers with a JSON-RPC
    error rather than a tool execution result. Registered-tool validation failures stay as
    structured tool results.
    """

    dispatcher = {
        "start": dispatch_start,
        "publish_work": dispatch_publish_work,
        "check": dispatch_check,
        "respond": dispatch_respond,
        "status": dispatch_status,
        "receipt": dispatch_receipt,
        "read_guidance": dispatch_read_guidance,
    }.get(name)
    if dispatcher is None:
        raise McpError(
            types.ErrorData(code=types.INVALID_PARAMS, message=sanitize_unknown_tool_name(name))
        )
    return await dispatcher(arguments, runtime)


async def _handle_call_tool_request(
    req: types.CallToolRequest,
    runtime: BridgeRuntime = BRIDGE_RUNTIME,
) -> types.ServerResult:
    """Own CallToolRequest so unknown names never reach the SDK's name-echoing cache path."""

    name = req.params.name
    if name not in _REGISTERED_TOOL_NAMES:
        # Raise before any logging that would interpolate the caller-controlled name.
        raise McpError(
            types.ErrorData(code=types.INVALID_PARAMS, message=sanitize_unknown_tool_name(name))
        )
    arguments = dict(req.params.arguments or {})
    result = await call_tool(name, arguments, runtime)
    return types.ServerResult(result)


async def list_resources(runtime: BridgeRuntime = BRIDGE_RUNTIME) -> list[types.Resource]:
    """List only static manifest-verified guidance; never touch the service slot."""

    return [
        types.Resource(
            uri=AnyUrl(resource.uri),
            name=resource.name,
            title=resource.title,
            description=resource.description,
            mimeType=resource.media_type,
            size=resource.size,
            annotations=types.Annotations(
                audience=cast(list[types.Role], list(resource.annotations.audience)),
                priority=resource.annotations.priority,
            ),
        )
        for resource in runtime.resources
    ]


async def read_resource(uri: object) -> list[ReadResourceContents]:
    """Read one exact registered guidance URI without interpreting it as a path."""

    try:
        payload = read_guidance_resource(str(uri)).decode("utf-8", errors="strict")
    except GuidanceResourceError as exc:
        raise ValueError("guidance_resource_unavailable") from exc
    resource = _GUIDANCE_BY_URI[str(uri)]
    return [ReadResourceContents(content=payload, mime_type=resource.media_type)]


def _build_server(runtime: BridgeRuntime) -> Server[object]:
    active: Server[object] = Server(
        _SERVER_NAME,
        version=__version__,
        instructions=runtime.instructions,
    )

    async def runtime_list_tools() -> list[types.Tool]:
        return await list_tools(runtime)

    async def runtime_call_tool(req: types.CallToolRequest) -> types.ServerResult:
        return await _handle_call_tool_request(req, runtime)

    async def runtime_list_resources() -> list[types.Resource]:
        return await list_resources(runtime)

    active.list_tools()(runtime_list_tools)
    # Register a Yoetz-owned CallToolRequest handler instead of Server.call_tool so an unregistered
    # name becomes a sanitized JSON-RPC error and never reaches the SDK path that logs the raw name.
    active.request_handlers[types.CallToolRequest] = runtime_call_tool
    active.list_resources()(runtime_list_resources)
    active.read_resource()(read_resource)
    return active


server: Final = _build_server(BRIDGE_RUNTIME)


def _initialization_options(
    runtime: BridgeRuntime,
    active_server: Server[object] = server,
) -> InitializationOptions:
    capabilities = active_server.get_capabilities(NotificationOptions(), {}).model_copy(
        update={"experimental": None}
    )
    return InitializationOptions(
        server_name=_SERVER_NAME,
        server_version=__version__,
        capabilities=capabilities,
        instructions=runtime.instructions,
    )


async def run_stdio(runtime: BridgeRuntime = BRIDGE_RUNTIME) -> None:
    """Run bounded stdio and let the SDK negotiate protocol versions conformantly."""

    active_server = server if runtime is BRIDGE_RUNTIME else _build_server(runtime)
    async with bounded_stdio_server(drain_pending_responses=True) as (read_stream, write_stream):
        try:
            await active_server.run(
                read_stream,
                write_stream,
                _initialization_options(runtime, active_server),
                raise_exceptions=False,
            )
        finally:
            await close_bridge_runtime(runtime)


def _bridge_logging_config() -> LoggingConfig:
    # An unreadable or invalid config must never keep the bridge from installing its
    # stdout-safe sink; the built-in defaults are the same shape the loader would return.
    try:
        return load_config({}, os.environ, None).logging
    except Exception:
        return LoggingConfig()


def main(
    *,
    semantic: Literal["on", "off"] = "on",
    host: Literal["generic", "cursor"] = "generic",
) -> None:
    """Run the MCP bridge on stdio using the SDK-supported latest protocol contract."""

    if semantic not in {"on", "off"}:
        raise ValueError("mcp_semantic_profile_invalid")
    if host not in {"generic", "cursor"}:
        raise ValueError("mcp_host_profile_invalid")
    if types.LATEST_PROTOCOL_VERSION not in SUPPORTED_PROTOCOL_VERSIONS:
        raise RuntimeError("mcp_sdk_protocol_registry_invalid")
    configure_logging(_bridge_logging_config(), LogMode.MCP_STDIO)
    anyio.run(
        run_stdio,
        build_bridge_runtime(
            "strict" if semantic == "off" else "policy",
            host_profile=host,
        ),
    )
