"""Gate 1 — MCP protocol conformance via the pinned SDK client and raw framing.

This gate proves protocol conformance and conduit behavior only; it says nothing about model
activation. Live Codex agent behavior is Gate 2/3 work and is intentionally out of scope here.

Every required observation below is also enrolled under ``release/capability-policy.json`` for the
``mcp_protocol_conformance`` / MCP SDK ``1.28.1`` / ``linux_x86_64`` cell.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import AnyUrl
from tests.capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    capability_evidence_output_root,
    record_and_write,
    runtime_capability_context,
)

from yoetz.mcp.descriptors import TOOL_DESCRIPTORS
from yoetz.mcp.resources import GUIDANCE_RESOURCES, read_resource
from yoetz.mcp.server import BRIDGE_RUNTIME
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.errors import PublicErrorCode

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_PINNED_MCP = "1.28.1"
_PROTOCOL = "2025-11-25"
_FAMILY = "mcp_protocol_conformance"
_CHANNEL = "mcp_stdio"
# Capability CI runs the MCP matrix on ubuntu; policy cells bind to this platform tag.
_POLICY_PLATFORM = "linux_x86_64"

_DEGRADED_CODES = frozenset(
    {
        PublicErrorCode.SERVICE_UNAVAILABLE.value,
        PublicErrorCode.VAULT_LOCKED.value,
    }
)

_TOOL_NAMES = tuple(item.name for item in TOOL_DESCRIPTORS)


def _id(kind: str, seed: int) -> str:
    prefixes = {
        "request": "req_",
        "task": "tsk_",
        "session": "ses_",
        "writer": "wri_",
        "finding": "fnd_",
        "event": "evt_",
    }
    return f"{prefixes[kind]}00000000-0000-4000-8000-{seed:012d}"


def _base(seed: int) -> dict[str, JsonValue]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": _id("request", seed),
        "actor": {"actor_id": "harness:capability-gate1", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
    }


def _schema_valid_tool_arguments() -> dict[str, dict[str, JsonValue]]:
    frontier: dict[str, JsonValue] = {"sequence": "0", "head_digest": "genesis"}
    identity: dict[str, JsonValue] = {
        "session_id": _id("session", 1),
        "writer_id": _id("writer", 1),
    }
    return {
        "start": {
            **_base(1),
            "mode": "create",
            "task_title": "Gate1 conduit",
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


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): _plain_json(item) for key, item in source.items()}
    if isinstance(value, tuple | list):
        source_sequence = cast(tuple[object, ...] | list[object], value)
        return [_plain_json(item) for item in source_sequence]
    return value


def _initialize_frame(protocol_version: object, request_id: int = 1) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "capability-gate1", "version": "1.0.0"},
        },
    }


def _serve_command() -> list[str]:
    return ["uv", "run", "yoetz", "mcp", "serve"]


def _run_raw(*frames: Mapping[str, object]) -> tuple[list[dict[str, object]], bytes, bytes]:
    process = subprocess.Popen(
        _serve_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    responses: list[dict[str, object]] = []
    stdout_bytes = b""
    for frame in frames:
        payload = json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        process.stdin.write(payload + b"\n")
        process.stdin.flush()
        if "id" in frame:
            response = process.stdout.readline()
            assert response
            stdout_bytes += response
            responses.append(cast(dict[str, object], json.loads(response)))
    process.stdin.close()
    remainder = process.stdout.read()
    stdout_bytes += remainder
    assert process.wait(timeout=15) == 0
    stderr = process.stderr.read()
    assert b"Traceback" not in stderr
    return responses, stdout_bytes, stderr


@asynccontextmanager
async def _sdk_session() -> AsyncGenerator[tuple[ClientSession, types.InitializeResult]]:
    params = StdioServerParameters(command="uv", args=["run", "yoetz", "mcp", "serve"])
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize = await session.initialize()
            yield session, initialize


def _gate1_case(case_id: str, requirement_id: str, observation: str) -> CapabilityCase:
    return CapabilityCase(
        case_id=case_id,
        requirement_id=requirement_id,
        claim_id=f"E-005.{requirement_id}",
        capability_family=_FAMILY,
        required_observation_codes=frozenset({observation}),
        allowed_observation_codes=frozenset({observation}),
    )


def _gate1_context(*, fixture: bytes, protocol_version: str = _PROTOCOL):
    return runtime_capability_context(
        fixture_digest=bytes_digest(fixture),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest(
            {"channel": _CHANNEL, "protocol": protocol_version, "sdk": _PINNED_MCP, "gate": "1"}
        ),
        external_tool="mcp",
        external_version=_PINNED_MCP,
        integration_channel=_CHANNEL,
        protocol_version=protocol_version,
        sdk_version=_PINNED_MCP,
    )


def _record_pass(
    tmp_path: Path,
    *,
    case_id: str,
    requirement_id: str,
    observation: str,
    fixture: bytes,
    value: bool | int | str,
) -> None:
    context = _gate1_context(fixture=fixture)
    if context.platform_tag != _POLICY_PLATFORM:
        pytest.skip(
            f"gate1_policy_platform_mismatch:{context.platform_tag}",
            allow_module_level=False,
        )
    if type(value) is bool:
        obs = Observation(observation, boolean_value=value)
    elif type(value) is int:
        obs = Observation(observation, integer_value=value)
    else:
        obs = Observation(observation, digest_value=cast(str, value))
    evidence = record_and_write(
        _gate1_case(case_id, requirement_id, observation),
        context,
        (obs,),
        EvidenceOutcome.PASS,
        output_root=capability_evidence_output_root(tmp_path),
    )
    assert evidence.outcome is EvidenceOutcome.PASS


def _assert_tool_result_shape(result: types.CallToolResult) -> dict[str, object]:
    assert result.structuredContent is not None
    structured = cast(dict[str, object], result.structuredContent)
    assert "protocol_version" in structured
    assert "schema_version" in structured
    assert "request_id" in structured
    if result.isError:
        error = cast(dict[str, object], structured["error"])
        assert (
            error["code"] in _DEGRADED_CODES
            or error["code"] == PublicErrorCode.INVALID_REQUEST.value
        )
        assert structured.get("ok") is False
    else:
        assert structured.get("ok") is True
    return structured


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_mcp_initialize_all_supported_versions(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    negotiated: list[str] = []
    for index, version in enumerate(SUPPORTED_PROTOCOL_VERSIONS, start=1):
        frames, _stdout, _stderr = _run_raw(_initialize_frame(version, request_id=index))
        assert len(frames) == 1
        assert "error" not in frames[0]
        result = cast(dict[str, object], frames[0]["result"])
        asserted = cast(str, result["protocolVersion"])
        assert asserted == version
        negotiated.append(asserted)
    assert negotiated == list(SUPPORTED_PROTOCOL_VERSIONS)
    _record_pass(
        tmp_path,
        case_id="MCP-G1-INIT-ALL",
        requirement_id="mcp_initialize_all_supported_versions",
        observation="initialize_all_supported_versions",
        fixture=b"gate1-initialize-all-supported",
        value=True,
    )


def test_mcp_unknown_version_fallback(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    frames, _stdout, _stderr = _run_raw(_initialize_frame("1900-01-01"))
    assert len(frames) == 1
    assert "error" not in frames[0]
    result = cast(dict[str, object], frames[0]["result"])
    assert result["protocolVersion"] == types.LATEST_PROTOCOL_VERSION
    _record_pass(
        tmp_path,
        case_id="MCP-G1-INIT-FALLBACK",
        requirement_id="mcp_unknown_version_fallback",
        observation="unknown_version_fallback",
        fixture=b"gate1-unknown-version-fallback",
        value=True,
    )


@pytest.mark.anyio
async def test_mcp_capability_declaration_exact(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    async with _sdk_session() as (_session, initialize):
        capabilities = initialize.capabilities
        assert capabilities.tools is not None
        assert capabilities.resources is not None
        assert capabilities.experimental is None
        assert capabilities.prompts is None
        assert capabilities.logging is None
        assert initialize.instructions == BRIDGE_RUNTIME.instructions
        assert initialize.protocolVersion == types.LATEST_PROTOCOL_VERSION
    _record_pass(
        tmp_path,
        case_id="MCP-G1-CAPS",
        requirement_id="mcp_capability_declaration_exact",
        observation="capability_declaration_exact",
        fixture=b"gate1-capability-declaration",
        value=True,
    )


@pytest.mark.anyio
async def test_mcp_tools_list_exact_six(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    async with _sdk_session() as (session, _initialize):
        listed = await session.list_tools()
        assert [tool.name for tool in listed.tools] == list(_TOOL_NAMES)
        assert len(listed.tools) == 6
        for tool, descriptor in zip(listed.tools, TOOL_DESCRIPTORS, strict=True):
            assert tool.description == descriptor.description
            assert tool.inputSchema == _plain_json(descriptor.input_schema)
            assert tool.outputSchema == _plain_json(descriptor.output_schema)
            assert tool.annotations is not None
            assert tool.annotations.title == descriptor.title
            assert tool.annotations.readOnlyHint is descriptor.annotations.read_only
            assert tool.annotations.destructiveHint is False
            assert tool.annotations.idempotentHint is descriptor.annotations.idempotent
            assert tool.annotations.openWorldHint is False
    _record_pass(
        tmp_path,
        case_id="MCP-G1-TOOLS-LIST",
        requirement_id="mcp_tools_list_exact_six",
        observation="tools_list_exact_six",
        fixture=b"gate1-tools-list-exact-six",
        value=6,
    )


@pytest.mark.anyio
async def test_mcp_resources_list_read_all(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    async with _sdk_session() as (session, _initialize):
        listed = await session.list_resources()
        assert [str(resource.uri) for resource in listed.resources] == [
            item.uri for item in GUIDANCE_RESOURCES
        ]
        digests: list[str] = []
        for resource in GUIDANCE_RESOURCES:
            content = await session.read_resource(AnyUrl(resource.uri))
            assert len(content.contents) == 1
            block = content.contents[0]
            assert isinstance(block, types.TextResourceContents)
            packaged = read_resource(resource.uri)
            assert block.text.encode("utf-8") == packaged
            digests.append(f"sha256:{hashlib.sha256(packaged).hexdigest()}")
        digest = canonical_digest({"resources": cast(JsonValue, digests)})
    _record_pass(
        tmp_path,
        case_id="MCP-G1-RESOURCES",
        requirement_id="mcp_resources_list_read_all",
        observation="resources_list_read_all",
        fixture=b"gate1-resources-list-read-all",
        value=digest,
    )


@pytest.mark.anyio
async def test_mcp_tools_call_all_six_conduit(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation.

    With no unlocked local service the truthful degraded shape (SERVICE_UNAVAILABLE / VAULT_LOCKED)
    is accepted; a reachable service returning ok=true also passes.
    """

    arguments = _schema_valid_tool_arguments()
    async with _sdk_session() as (session, _initialize):
        shapes: list[str] = []
        for name in _TOOL_NAMES:
            result = await session.call_tool(name, cast(dict[str, object], arguments[name]))
            structured = _assert_tool_result_shape(result)
            shapes.append("ok" if structured.get("ok") is True else "degraded")
        assert len(shapes) == 6
    _record_pass(
        tmp_path,
        case_id="MCP-G1-TOOLS-CALL",
        requirement_id="mcp_tools_call_all_six_conduit",
        observation="tools_call_all_six_conduit",
        fixture=b"gate1-tools-call-all-six",
        value=True,
    )


def test_mcp_unknown_tool_sanitized(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    injected = "evil\n\x1b[31m" + ("A" * 200) + "\nTOOL_NAME_INJECTION_MARKER"
    frames, _stdout, stderr = _run_raw(
        _initialize_frame(types.LATEST_PROTOCOL_VERSION),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "not_a_registered_tool", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": injected, "arguments": {}},
        },
    )
    by_id = {frame.get("id"): frame for frame in frames}
    for request_id in (2, 3):
        error = cast(dict[str, object], by_id[request_id]["error"])
        assert error["code"] == -32602
        message = cast(str, error["message"])
        assert message == "The requested tool is not registered."
        assert injected not in message
        assert "TOOL_NAME_INJECTION_MARKER" not in message
        assert "\n" not in message
    assert b"TOOL_NAME_INJECTION_MARKER" not in stderr
    _record_pass(
        tmp_path,
        case_id="MCP-G1-UNKNOWN-TOOL",
        requirement_id="mcp_unknown_tool_sanitized",
        observation="unknown_tool_sanitized",
        fixture=b"gate1-unknown-tool-sanitized",
        value=True,
    )


def test_mcp_malformed_framing(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    child = r"""
import anyio
from yoetz.adapters.mcp_stdio import bounded_stdio_server

async def main():
    async with bounded_stdio_server(512) as (read_stream, write_stream):
        async with write_stream:
            async for message in read_stream:
                await write_stream.send(message)

anyio.run(main)
"""
    data = b'\n\xef\xbb\xbf{}\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    result = subprocess.run(
        [sys.executable, "-I", "-c", child],
        input=data,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "src"},
        timeout=5,
    )
    assert result.returncode == 0
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows[0]["id"] is None
    assert rows[0]["error"]["code"] == -32700
    _record_pass(
        tmp_path,
        case_id="MCP-G1-MALFORMED",
        requirement_id="mcp_malformed_framing",
        observation="malformed_framing",
        fixture=b"gate1-malformed-framing",
        value=True,
    )


@pytest.mark.anyio
async def test_mcp_idempotent_retry_stable(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    arguments = _schema_valid_tool_arguments()["status"]
    async with _sdk_session() as (session, _initialize):
        first = await session.call_tool("status", cast(dict[str, object], arguments))
        second = await session.call_tool("status", cast(dict[str, object], arguments))
        first_wire = _assert_tool_result_shape(first)
        second_wire = _assert_tool_result_shape(second)
        # Structural identity for degraded/success results: same request_id and error/ok shape.
        assert first_wire["request_id"] == second_wire["request_id"] == arguments["request_id"]
        assert first.isError is second.isError
        assert first_wire.get("ok") is second_wire.get("ok")
        error_code: JsonValue = None
        if first.isError:
            first_error = cast(dict[str, object], first_wire["error"])
            second_error = cast(dict[str, object], second_wire["error"])
            assert first_error["code"] == second_error["code"]
            assert first_error["retryable"] == second_error["retryable"]
            error_code = cast(str, first_error["code"])
        identity_material: dict[str, JsonValue] = {
            "code": error_code,
            "is_error": first.isError,
            "ok": cast(JsonValue, first_wire.get("ok")),
            "request_id": cast(str, first_wire["request_id"]),
        }
        identity = canonical_digest(identity_material)
    _record_pass(
        tmp_path,
        case_id="MCP-G1-IDEMPOTENT",
        requirement_id="mcp_idempotent_retry_stable",
        observation="idempotent_retry_stable",
        fixture=b"gate1-idempotent-retry",
        value=identity,
    )


def test_mcp_cancellation_eof_clean(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    process = subprocess.Popen(
        _serve_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ},
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    payload = json.dumps(
        _initialize_frame(types.LATEST_PROTOCOL_VERSION),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    process.stdin.write(payload + b"\n")
    process.stdin.flush()
    assert process.stdout.readline()
    # Close stdin mid-session (EOF) without a clean shutdown notification.
    process.stdin.close()
    assert process.wait(timeout=15) == 0
    stderr = process.stderr.read()
    assert b"Traceback" not in stderr
    assert b"Exception" not in stderr
    _record_pass(
        tmp_path,
        case_id="MCP-G1-EOF",
        requirement_id="mcp_cancellation_eof_clean",
        observation="cancellation_eof_clean",
        fixture=b"gate1-cancellation-eof",
        value=True,
    )


def test_mcp_stdout_purity(tmp_path: Path) -> None:
    """This gate proves protocol conformance and conduit behavior only; it says nothing about model activation."""

    frames, stdout_bytes, _stderr = _run_raw(
        _initialize_frame(types.LATEST_PROTOCOL_VERSION),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    lines = [line for line in stdout_bytes.splitlines() if line]
    assert lines
    for line in lines:
        parsed = json.loads(line)
        assert parsed["jsonrpc"] == "2.0"
        assert "id" in parsed or "method" in parsed or "result" in parsed or "error" in parsed
    assert len(frames) == 2
    _record_pass(
        tmp_path,
        case_id="MCP-G1-STDOUT",
        requirement_id="mcp_stdout_purity",
        observation="stdout_purity",
        fixture=b"gate1-stdout-purity",
        value=True,
    )


@pytest.mark.anyio
async def test_mcp_sdk_unknown_tool_raises_sanitized_error() -> None:
    """SDK client path: unknown tool becomes a protocol error, not a tool result."""

    async with _sdk_session() as (session, _initialize):
        with pytest.raises(McpError) as raised:
            await session.call_tool("not_a_registered_tool", {})
        message = str(raised.value)
        assert "not registered" in message.lower() or "invalid" in message.lower()
        assert "not_a_registered_tool" not in message or "registered" in message.lower()
