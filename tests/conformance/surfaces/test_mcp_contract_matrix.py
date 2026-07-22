"""Foundation contract matrix for the frozen MCP surface."""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from yoetz.mcp import resources as resource_module
from yoetz.mcp.descriptors import (
    TOOL_DESCRIPTOR_DIGESTS,
    TOOL_DESCRIPTOR_SET_DIGEST,
    TOOL_DESCRIPTORS,
    descriptor_for,
    server_instructions,
)
from yoetz.mcp.errors import (
    build_last_resort_internal_error_result,
    build_public_error_result,
    safe_validation_locations,
    sanitize_unknown_tool_name,
)
from yoetz.mcp.resources import (
    GUIDANCE_RESOURCES,
    GuidanceResourceError,
    list_resources,
    read_resource,
)
from yoetz.mcp.summaries import render_safe_compact_summary
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.schemas import validate_schema_instance

_EXPECTED_TOOL_NAMES = ("start", "publish_work", "check", "respond", "status", "receipt")
_EXPECTED_RESOURCE_URIS = (
    "yoetz://guidance/agent-instructions.md",
    "yoetz://guidance/workflow.md",
    "yoetz://guidance/publication-policy.md",
    "yoetz://guidance/coverage-and-receipts.md",
)
_FORBIDDEN_DESCRIPTOR_CLAIMS = re.compile(
    r"\b(?:authenticated|enforces?|gates?|observes?|proved|proves?|verified)\b",
    re.IGNORECASE | re.ASCII,
)


def test_fallback_error_object_is_admitted() -> None:
    fallback = build_last_resort_internal_error_result()

    for operation in _EXPECTED_TOOL_NAMES:
        validate_schema_instance(f"{operation.replace('_', '-')}-result", "1.0.0", fallback)


def test_public_error_and_validation_summaries_are_sanitized() -> None:
    correlation_id = "err_00000000-0000-4000-8000-000000000001"
    result = build_public_error_result(
        PublicErrorCode.INVALID_REQUEST,
        "The request is invalid.",
        False,
        correlation_id,
        safe_details={"field": "/request_id"},
    )
    assert result == {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "ok": False,
        "error": {
            "code": "INVALID_REQUEST",
            "message": "The request is invalid.",
            "retryable": False,
            "correlation_id": correlation_id,
            "safe_details": {"field": "/request_id"},
        },
    }
    summary = render_safe_compact_summary(result)
    assert summary == f"Error INVALID_REQUEST; retryable: no; correlation: {correlation_id}."

    class _Request(BaseModel):
        model_config = ConfigDict(extra="forbid")

        request_id: str

    with pytest.raises(ValidationError) as captured:
        _Request.model_validate({"password": "never-echo-this"})
    locations = safe_validation_locations(captured.value)
    assert locations == ({"field": "/request_id", "reason": "missing"},)
    assert "password" not in repr(locations)
    assert "never-echo-this" not in repr(locations)


def test_unknown_tool_message_is_sanitized() -> None:
    raw_name = "../../private/secret-tool"
    message = sanitize_unknown_tool_name(raw_name)

    assert message == "The requested tool is not registered."
    assert raw_name not in message


def test_descriptor_text_is_frozen_and_honest() -> None:
    assert tuple(item.name for item in TOOL_DESCRIPTORS) == _EXPECTED_TOOL_NAMES
    assert tuple(TOOL_DESCRIPTOR_DIGESTS) == _EXPECTED_TOOL_NAMES
    assert TOOL_DESCRIPTOR_SET_DIGEST == (
        "sha256:fed4821789eb054b73919233b785c2750696f65af7ebe2ea3d98dbc407bbae6f"
    )
    assert descriptor_for("start").description.startswith(
        "Call for material multi-step, delegated, resumable, or verification-heavy work"
    )
    assert {item.name for item in TOOL_DESCRIPTORS if item.annotations.read_only} == {"status"}
    assert all(not item.annotations.destructive for item in TOOL_DESCRIPTORS)
    assert all(item.annotations.idempotent for item in TOOL_DESCRIPTORS)
    assert all(
        _FORBIDDEN_DESCRIPTOR_CLAIMS.search(item.description) is None for item in TOOL_DESCRIPTORS
    )
    assert "uncertain what you already did or committed to" in descriptor_for("status").description
    assert server_instructions().encode("utf-8") == read_resource(
        "yoetz://guidance/agent-instructions.md"
    )

    with pytest.raises(KeyError, match="unregistered_tool_descriptor") as captured:
        descriptor_for("secret-tool")
    assert "secret-tool" not in str(captured.value)


def test_guidance_resources_are_exact_and_static() -> None:
    assert tuple(item.uri for item in GUIDANCE_RESOURCES) == _EXPECTED_RESOURCE_URIS
    assert list_resources() is GUIDANCE_RESOURCES
    for item in GUIDANCE_RESOURCES:
        assert item.bytes == read_resource(item.uri)
        assert item.text.encode("utf-8") == item.bytes
        assert item.size == len(item.bytes)
        assert item.media_type == "text/markdown"
        assert item.annotations.audience == ("assistant",)
        assert 0.0 <= item.annotations.priority <= 1.0
        assert "Read " in item.description or item.description.startswith("Read")
    assert GUIDANCE_RESOURCES[0].annotations.priority == 1.0
    assert GUIDANCE_RESOURCES[1].annotations.priority == 0.9
    assert all(item.annotations.priority <= 0.9 for item in GUIDANCE_RESOURCES[1:])
    assert GUIDANCE_RESOURCES[2].annotations.priority == GUIDANCE_RESOURCES[3].annotations.priority


def test_resource_uri_is_a_key_not_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []

    def _unexpected_read(logical_name: str) -> bytes:
        reads.append(logical_name)
        raise AssertionError("package reader reached")

    monkeypatch.setattr(resource_module, "read_verified_resource", _unexpected_read)
    invalid_uris = (
        "guidance/workflow.md",
        "yoetz://guidance/{name}.md",
        "yoetz://guidance/../workflow.md",
        "yoetz://guidance//workflow.md",
        "file:///guidance/workflow.md",
    )
    for uri in invalid_uris:
        with pytest.raises(GuidanceResourceError, match="guidance_resource_uri_unregistered"):
            read_resource(uri)
    assert reads == []
