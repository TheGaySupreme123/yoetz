"""Six-operation MCP/Codex capability evidence.

Local cells drive all six names through the real MCP stdio transport (``yoetz mcp serve`` via the
pinned SDK client). They prove dispatch and descriptor behavior only — not service conduit or Codex
model activation.

Driving the same slice through interactive/exec Codex requires ``YOETZ_LIVE_CODEX=1`` and a Gate
2/3 live driver. When live authorization is present but no driver exists, the cell fails closed
with ``live_driver_unavailable`` rather than claiming a pass.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from tests.capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    capability_evidence_output_root,
    live_codex_authorized,
    record_and_write,
    runtime_capability_context,
)

from yoetz.mcp.descriptors import TOOL_DESCRIPTOR_SET_DIGEST, TOOL_DESCRIPTORS
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.errors import PublicErrorCode

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_VERSION = "0.139.0"
_EXPECTED = ("start", "publish_work", "check", "respond", "status", "receipt")
_DEGRADED_CODES = frozenset(
    {
        PublicErrorCode.INVALID_REQUEST.value,
        PublicErrorCode.SERVICE_UNAVAILABLE.value,
        PublicErrorCode.VAULT_LOCKED.value,
    }
)


def _serve_parameters(tmp_path: Path) -> StdioServerParameters:
    candidate_python = os.environ.get("YOETZ_CANDIDATE_PYTHON", "").strip()
    home = tmp_path / "mcp-home"
    home.mkdir(mode=0o700, exist_ok=True)
    for directory in ("cache", "config", "data", "runtime", "state"):
        (home / directory).mkdir(mode=0o700, exist_ok=True)
    environment = {
        **os.environ,
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_RUNTIME_DIR": str(home / "runtime"),
        "XDG_STATE_HOME": str(home / "state"),
    }
    if candidate_python:
        return StdioServerParameters(
            command=candidate_python,
            args=["-m", "yoetz", "mcp", "serve"],
            env=environment,
        )
    return StdioServerParameters(
        command="uv",
        args=["run", "yoetz", "mcp", "serve"],
        env=environment,
    )


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
        "actor": {"actor_id": "harness:six-tools", "actor_type": "harness"},
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
            "task_title": "Six-tools MCP conduit",
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


def _assert_dispatch_result(result: types.CallToolResult) -> None:
    assert result.structuredContent is not None
    structured = cast(dict[str, object], result.structuredContent)
    assert "request_id" in structured
    if result.isError:
        error = cast(dict[str, object], structured["error"])
        assert error["code"] in _DEGRADED_CODES
        assert structured.get("ok") is False
    else:
        assert structured.get("ok") is True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_installed_server_advertises_exactly_six_frozen_tools(tmp_path: Path) -> None:
    """List tools through the real MCP stdio server; compare against frozen descriptors."""

    evidence_root = capability_evidence_output_root(tmp_path)
    params = _serve_parameters(tmp_path)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
    names = tuple(tool.name for tool in listed.tools)
    assert names == _EXPECTED
    assert tuple(item.name for item in TOOL_DESCRIPTORS) == _EXPECTED
    for tool, descriptor in zip(listed.tools, TOOL_DESCRIPTORS, strict=True):
        assert tool.inputSchema == _plain_json(descriptor.input_schema)
        assert tool.outputSchema == _plain_json(descriptor.output_schema)
    context = runtime_capability_context(
        fixture_digest=bytes_digest(TOOL_DESCRIPTOR_SET_DIGEST.encode("ascii")),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"descriptor_set": TOOL_DESCRIPTOR_SET_DIGEST}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_mcp_stdio",
        protocol_version="2025-11-25",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SIX-001",
            requirement_id="ADR-005.six-tools",
            claim_id="E-002.six-tools",
            capability_family="codex_six_tools",
            required_observation_codes=frozenset(
                {"tool_count", "descriptor_set_digest", "names_match"}
            ),
            allowed_observation_codes=frozenset(
                {"tool_count", "descriptor_set_digest", "names_match"}
            ),
        ),
        context,
        (
            Observation("descriptor_set_digest", digest_value=TOOL_DESCRIPTOR_SET_DIGEST),
            Observation("names_match", boolean_value=True),
            Observation("tool_count", integer_value=len(names)),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.anyio
async def test_mcp_stdio_six_tool_dispatch_without_claiming_codex_activation(
    tmp_path: Path,
) -> None:
    """Drive all six names through real MCP stdio and their structured validation boundary.

    This observation is MCP dispatch coverage only. It does not call Application methods directly,
    prove service conduit behavior, or claim Codex model activation.
    """

    evidence_root = capability_evidence_output_root(tmp_path)
    arguments = _schema_valid_tool_arguments()
    params = _serve_parameters(tmp_path)
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert tuple(tool.name for tool in listed.tools) == _EXPECTED
            for name in _EXPECTED:
                result = await session.call_tool(
                    name,
                    {"request_id": arguments[name]["request_id"]},
                )
                _assert_dispatch_result(result)

    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"mcp-stdio-six-tools-dispatch"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"profile": "mcp_stdio_dispatch"}),
        external_tool="mcp",
        external_version="1.28.1",
        integration_channel="mcp_stdio",
        protocol_version="2025-11-25",
        sdk_version="1.28.1",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SIX-002",
            requirement_id="ADR-005.six-tools",
            claim_id="E-002.six-tools-mcp-stdio-dispatch",
            capability_family="codex_six_tools",
            required_observation_codes=frozenset(
                {"mcp_stdio_dispatch", "six_tools_called", "structured_result_shape"}
            ),
            allowed_observation_codes=frozenset(
                {"mcp_stdio_dispatch", "six_tools_called", "structured_result_shape"}
            ),
        ),
        context,
        (
            Observation("mcp_stdio_dispatch", boolean_value=True),
            Observation("six_tools_called", integer_value=6),
            Observation("structured_result_shape", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.live
def test_live_codex_drives_six_tools(tmp_path: Path) -> None:
    """Live Codex six-tools driver is Gate 2/3 work; fail closed when unauthorized or unavailable."""

    evidence_root = capability_evidence_output_root(tmp_path)
    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"live-six-tools"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"cell": "live_six_tools"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_mcp_stdio",
        protocol_version="2025-11-25",
    )
    case = CapabilityCase(
        case_id="SIX-LIVE-001",
        requirement_id="ADR-005.six-tools",
        claim_id="E-002.six-tools-live",
        capability_family="codex_six_tools",
        required_observation_codes=frozenset({"live_authorized"}),
        allowed_observation_codes=frozenset({"live_authorized", "live_driver_state"}),
    )
    if not live_codex_authorized():
        evidence = record_and_write(
            case,
            context,
            (Observation("live_authorized", boolean_value=False),),
            EvidenceOutcome.UNSUPPORTED,
            ("live_codex_not_authorized",),
            output_root=evidence_root,
        )
        assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
        return
    # Authorized, but a real Codex interactive/exec driver is Gate 2/3 work and is not present.
    evidence = record_and_write(
        case,
        context,
        (
            Observation("live_authorized", boolean_value=True),
            Observation("live_driver_state", enum_value="live_driver_unavailable"),
        ),
        EvidenceOutcome.UNSUPPORTED,
        ("live_driver_unavailable",),
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
    pytest.skip("live_driver_unavailable: Gate 2/3 Codex driver not implemented")
