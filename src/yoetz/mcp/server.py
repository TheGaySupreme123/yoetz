"""Six-tool MCP stdio bridge to the ordinary local Yoetz service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, cast

import anyio
from mcp import types
from mcp.server import InitializationOptions, NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import AnyUrl, BaseModel, ValidationError

from yoetz import __version__
from yoetz.adapters.mcp_stdio import bounded_stdio_server
from yoetz.mcp.descriptors import TOOL_DESCRIPTORS, ToolDescriptor, server_instructions
from yoetz.mcp.errors import (
    build_last_resort_internal_error_result,
    build_public_error_result,
    safe_validation_locations,
    sanitize_unknown_tool_name,
)
from yoetz.mcp.resources import (
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
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.ids import IdKind, new_id, safe_request_id_from
from yoetz.protocol.models import (
    CheckRequest,
    CheckResult,
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
_REGISTERED_TOOL_NAMES: Final = frozenset(
    {"start", "publish_work", "check", "respond", "status", "receipt"}
)
_RECONNECT_REASONS: Final = frozenset(
    {"service_unavailable", "privacy_projection_unavailable", "service_generation_changed"}
)


@dataclass(slots=True)
class _ClientSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client: ServiceClient | None = None


@dataclass(frozen=True, slots=True)
class BridgeRuntime:
    """Verified static surface plus one private, initially empty ordinary-client slot."""

    descriptors: tuple[ToolDescriptor, ...]
    resources: tuple[GuidanceResource, ...]
    instructions: str
    _slot: _ClientSlot = field(default_factory=_ClientSlot, repr=False, compare=False)


def build_bridge_runtime() -> BridgeRuntime:
    """Verify every agent-readable byte and construct an unconnected bridge runtime."""

    resources = list_guidance_resources()
    instructions = server_instructions()
    if not instructions:
        raise RuntimeError("mcp_instructions_empty")
    # Accessing every schema verifies its checked-in public identity before serving.
    for descriptor in TOOL_DESCRIPTORS:
        descriptor.input_schema
        descriptor.output_schema
    build_last_resort_internal_error_result()
    return BridgeRuntime(TOOL_DESCRIPTORS, resources, instructions)


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
            connected = await connect_service_on_demand(ControlClientKind.MCP_BRIDGE)
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


def result_from_public_model(result: object) -> types.CallToolResult:
    """Validate and project one public result to structured content and weaker text."""

    wire = public_model_to_wire(result)
    summary = render_safe_compact_summary(wire)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=summary)],
        structuredContent=cast(dict[str, object], wire),
        isError=wire.get("ok") is False,
    )


def _result_from_wire(wire: Mapping[str, object]) -> types.CallToolResult:
    structured = dict(wire)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=render_safe_compact_summary(structured))],
        structuredContent=structured,
        isError=True,
    )


def structured_error_result(
    code: PublicErrorCode,
    message: str,
    *,
    retryable: bool = False,
    request_id: str | None = None,
    safe_details: object | None = None,
) -> types.CallToolResult:
    """Build a bounded structured tool error with a prevalidated nested fallback."""

    try:
        wire = build_public_error_result(
            code,
            message,
            retryable,
            new_id(IdKind.CORRELATION),
            request_id=request_id,
            safe_details=safe_details,
        )
        return _result_from_wire(wire)
    except Exception:
        fallback = build_last_resort_internal_error_result()
        try:
            return _result_from_wire(fallback)
        except Exception:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Error INTERNAL_ERROR.")],
                structuredContent=fallback,
                isError=True,
            )


def _control_error_result(error: ControlError, request_id: str | None) -> types.CallToolResult:
    if error.reason == "vault_locked":
        return structured_error_result(
            PublicErrorCode.VAULT_LOCKED,
            (
                "The local service vault is locked or uninitialized. Unlock from a local "
                "terminal (`yoetz service unlock`). If the vault is still uninitialized and no "
                "user-owned TTY is available, prepare `vault_initialize` via "
                "`yoetz consent catalog` / `prepare` (ADR-015); never send secrets over MCP."
            ),
            request_id=request_id,
        )
    if error.reason == "request_cancelled":
        return structured_error_result(
            PublicErrorCode.CANCELLED,
            "The operation was cancelled.",
            request_id=request_id,
        )
    if error.reason in {"service_unavailable", "service_draining", "request_timeout"}:
        return structured_error_result(
            PublicErrorCode.SERVICE_UNAVAILABLE,
            "The local service is unavailable; retry after it is ready.",
            retryable=True,
            request_id=request_id,
        )
    return structured_error_result(
        PublicErrorCode.INTERNAL_ERROR,
        "The bridge could not complete the operation.",
        request_id=request_id,
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
        if not error.retryable or error.reason not in _RECONNECT_REASONS:
            raise
        await _discard_client(runtime, client)
        replacement = await ensure_service_client(runtime)
        return await invoke(replacement, request)


async def _dispatch[RequestT: BaseModel, ResultT: BaseModel](
    arguments: Mapping[str, object],
    request_type: type[RequestT],
    result_type: type[ResultT],
    invoke: Callable[[ServiceClient, RequestT], Awaitable[ResultT]],
    runtime: BridgeRuntime,
) -> types.CallToolResult:
    request_id = safe_request_id_from(arguments)
    try:
        request = request_type.model_validate(arguments)
    except ValidationError as exc:
        locations = safe_validation_locations(exc)
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            "The tool arguments are invalid.",
            request_id=request_id,
            safe_details=locations[0] if locations else None,
        )
    except Exception:
        return structured_error_result(
            PublicErrorCode.INVALID_REQUEST,
            "The tool arguments are invalid.",
            request_id=request_id,
        )
    try:
        result = await _invoke_with_reconnect(runtime, request, invoke)
        wire = public_model_to_wire(result)
        validated = result_type.model_validate(wire)
        return result_from_public_model(validated)
    except ControlError as exc:
        return _control_error_result(exc, request_id)
    except Exception:
        return structured_error_result(
            PublicErrorCode.INTERNAL_ERROR,
            "The bridge could not complete the operation.",
            request_id=request_id,
        )


async def dispatch_start(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        StartRequest,
        StartResult,
        lambda client, request: client.start(request),
        runtime,
    )


async def dispatch_publish_work(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        PublishWorkRequest,
        PublishWorkResult,
        lambda client, request: client.publish_work(request),
        runtime,
    )


async def dispatch_check(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        CheckRequest,
        CheckResult,
        lambda client, request: client.check(request),
        runtime,
    )


async def dispatch_respond(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        RespondRequest,
        RespondResult,
        lambda client, request: client.respond(request),
        runtime,
    )


async def dispatch_status(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        StatusRequest,
        StatusResult,
        lambda client, request: client.status(request),
        runtime,
    )


async def dispatch_receipt(
    arguments: Mapping[str, object], runtime: BridgeRuntime = BRIDGE_RUNTIME
) -> types.CallToolResult:
    return await _dispatch(
        arguments,
        ReceiptRequest,
        ReceiptResult,
        lambda client, request: client.receipt(request),
        runtime,
    )


async def list_tools() -> list[types.Tool]:
    """Return the exact reviewed six-tool inventory in stable order."""

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
        for descriptor in BRIDGE_RUNTIME.descriptors
    ]


async def call_tool(name: str, arguments: dict[str, object]) -> types.CallToolResult:
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
    }.get(name)
    if dispatcher is None:
        raise McpError(
            types.ErrorData(code=types.INVALID_PARAMS, message=sanitize_unknown_tool_name(name))
        )
    return await dispatcher(arguments)


async def _handle_call_tool_request(req: types.CallToolRequest) -> types.ServerResult:
    """Own CallToolRequest so unknown names never reach the SDK's name-echoing cache path."""

    name = req.params.name
    if name not in _REGISTERED_TOOL_NAMES:
        # Raise before any logging that would interpolate the caller-controlled name.
        raise McpError(
            types.ErrorData(code=types.INVALID_PARAMS, message=sanitize_unknown_tool_name(name))
        )
    arguments = dict(req.params.arguments or {})
    result = await call_tool(name, arguments)
    return types.ServerResult(result)


async def list_resources() -> list[types.Resource]:
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
        for resource in BRIDGE_RUNTIME.resources
    ]


async def read_resource(uri: object) -> list[ReadResourceContents]:
    """Read one exact registered guidance URI without interpreting it as a path."""

    try:
        payload = read_guidance_resource(str(uri)).decode("utf-8", errors="strict")
    except GuidanceResourceError as exc:
        raise ValueError("guidance_resource_unavailable") from exc
    return [ReadResourceContents(content=payload, mime_type="text/markdown")]


server: Final = Server(_SERVER_NAME, version=__version__, instructions=BRIDGE_RUNTIME.instructions)
server.list_tools()(list_tools)
# Register a Yoetz-owned CallToolRequest handler instead of Server.call_tool so an unregistered
# name becomes a sanitized JSON-RPC error and never reaches the SDK path that logs the raw name.
server.request_handlers[types.CallToolRequest] = _handle_call_tool_request
server.list_resources()(list_resources)
server.read_resource()(read_resource)


def _initialization_options(runtime: BridgeRuntime) -> InitializationOptions:
    capabilities = server.get_capabilities(NotificationOptions(), {}).model_copy(
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

    async with bounded_stdio_server(drain_pending_responses=True) as (read_stream, write_stream):
        try:
            await server.run(
                read_stream,
                write_stream,
                _initialization_options(runtime),
                raise_exceptions=False,
            )
        finally:
            await close_bridge_runtime(runtime)


def main() -> None:
    """Run the MCP bridge on stdio using the SDK-supported latest protocol contract."""

    if types.LATEST_PROTOCOL_VERSION not in SUPPORTED_PROTOCOL_VERSIONS:
        raise RuntimeError("mcp_sdk_protocol_registry_invalid")
    anyio.run(run_stdio)
