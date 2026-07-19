"""Raw MCP negotiation and exact static six-tool/resource inventory."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from typing import cast

import pytest
from mcp import types

from yoetz.mcp.descriptors import TOOL_DESCRIPTORS
from yoetz.mcp.resources import GUIDANCE_RESOURCES
from yoetz.mcp.resources import read_resource as read_guidance_resource
from yoetz.mcp.server import BRIDGE_RUNTIME, list_resources, list_tools


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run_raw(*frames: Mapping[str, object]) -> tuple[list[dict[str, object]], bytes]:
    child = "from yoetz.mcp.server import main; main()"
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", child],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    responses: list[dict[str, object]] = []
    for frame in frames:
        payload = json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        process.stdin.write(payload + b"\n")
        process.stdin.flush()
        if "id" in frame:
            response = process.stdout.readline()
            assert response
            responses.append(cast(dict[str, object], json.loads(response)))
    process.stdin.close()
    assert process.wait(timeout=10) == 0
    stderr = process.stderr.read()
    assert b"Traceback" not in stderr
    return responses, stderr


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): _plain_json(item) for key, item in source.items()}
    if isinstance(value, tuple | list):
        source_sequence = cast(tuple[object, ...] | list[object], value)
        return [_plain_json(item) for item in source_sequence]
    return value


def _initialize(protocol_version: object, request_id: int = 1) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "raw-contract-test", "version": "1.0.0"},
        },
    }


@pytest.mark.anyio
async def test_static_inventory_is_exact_and_verified() -> None:
    tools = await list_tools()
    resources = await list_resources()

    assert [tool.name for tool in tools] == [item.name for item in TOOL_DESCRIPTORS]
    assert len(tools) == 6
    for tool, descriptor in zip(tools, TOOL_DESCRIPTORS, strict=True):
        assert tool.inputSchema == _plain_json(descriptor.input_schema)
        assert tool.outputSchema == _plain_json(descriptor.output_schema)
        assert tool.annotations == types.ToolAnnotations(
            title=descriptor.title,
            readOnlyHint=descriptor.annotations.read_only,
            destructiveHint=False,
            idempotentHint=descriptor.annotations.idempotent,
            openWorldHint=False,
        )
    assert [str(resource.uri) for resource in resources] == [
        item.uri for item in GUIDANCE_RESOURCES
    ]
    assert BRIDGE_RUNTIME.instructions.encode("utf-8") == read_guidance_resource(
        "yoetz://guidance/agent-instructions.md"
    )


def test_raw_initialize_lists_exact_capabilities_tools_and_resources() -> None:
    frames, _stderr = _run_raw(
        _initialize(types.LATEST_PROTOCOL_VERSION),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
    )
    by_id = {frame.get("id"): frame for frame in frames}
    initialize = cast(dict[str, object], by_id[1]["result"])
    assert initialize["protocolVersion"] == types.LATEST_PROTOCOL_VERSION
    assert initialize["capabilities"] == {
        "resources": {"subscribe": False, "listChanged": False},
        "tools": {"listChanged": False},
    }
    assert initialize["instructions"] == BRIDGE_RUNTIME.instructions

    tool_result = cast(dict[str, object], by_id[2]["result"])
    advertised = cast(list[dict[str, object]], tool_result["tools"])
    assert [tool["name"] for tool in advertised] == [item.name for item in TOOL_DESCRIPTORS]
    resource_result = cast(dict[str, object], by_id[3]["result"])
    advertised_resources = cast(list[dict[str, object]], resource_result["resources"])
    assert [resource["uri"] for resource in advertised_resources] == [
        item.uri for item in GUIDANCE_RESOURCES
    ]


def test_unsupported_protocol_is_rejected_before_sdk_negotiation() -> None:
    frames, _stderr = _run_raw(_initialize("1900-01-01"))

    assert frames == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32602,
                "message": "Unsupported protocol version",
                "data": {"reason": "unsupported_protocol_version"},
            },
        }
    ]
