"""Foundation contract matrix for the frozen MCP surface."""

from __future__ import annotations

import re
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from yoetz.mcp import resources as resource_module
from yoetz.mcp.descriptors import (
    ORDINARY_MCP_PUBLISH_EVENT_FAMILIES,
    PRESENTATION_INPUT_SCHEMA_BUDGETS,
    TOOL_DESCRIPTOR_DIGESTS,
    TOOL_DESCRIPTOR_SET_DIGEST,
    TOOL_DESCRIPTORS,
    descriptor_for,
    ordinary_publish_families_in_presentation,
    presentation_schema_metrics,
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
from yoetz.protocol.errors import SAFE_DETAIL_KEYS, PublicErrorCode
from yoetz.protocol.models import (
    FRONTIER_LEAVES,
    PublishWorkRequest,
    StartRequest,
    StatusRequest,
)
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
    all_locations = (
        {"field": "/request_id", "reason": "missing"},
        {"field": "/client", "reason": "extra_forbidden"},
    )
    multi_location_result = build_public_error_result(
        PublicErrorCode.INVALID_REQUEST,
        "The request is invalid.",
        False,
        correlation_id,
        safe_details=all_locations,
    )
    multi_location_error = cast(dict[str, object], multi_location_result["error"])
    assert multi_location_error["safe_details"] == {
        "fields": ["/request_id", "/client"],
        "reasons": ["missing", "extra_forbidden"],
    }

    class _Request(BaseModel):
        model_config = ConfigDict(extra="forbid")

        request_id: str

    with pytest.raises(ValidationError) as captured:
        _Request.model_validate({"password": "never-echo-this"})
    locations = safe_validation_locations(captured.value)
    assert locations == ({"field": "/request_id", "reason": "missing"},)
    assert "password" not in repr(locations)
    assert "never-echo-this" not in repr(locations)


def test_forbidden_client_id_projects_parent_path_in_safe_details() -> None:
    class _ClientInfo(BaseModel):
        model_config = ConfigDict(extra="forbid")

        kind: str
        version: str
        integration: str

    class _Request(BaseModel):
        model_config = ConfigDict(extra="forbid")

        client: _ClientInfo

    with pytest.raises(ValidationError) as captured:
        _Request.model_validate(
            {
                "client": {
                    "kind": "cooperative_agent",
                    "version": "0.1.0",
                    "integration": "cooperative_mcp",
                    "id": "invented-client-id",
                }
            }
        )
    locations = safe_validation_locations(captured.value)
    assert locations == ({"field": "/client", "reason": "extra_forbidden"},)
    assert "/client/id" not in repr(locations)
    assert "invented-client-id" not in repr(locations)
    # "id" must not be a generally trusted location segment.
    assert '"id"' not in repr(locations)


def test_frontier_leaf_names_are_locatable_in_safe_details() -> None:
    """A wrong frontier key must name the missing leaf, not just the object holding it.

    The 2026-07-26 dogfood sent ``digest`` instead of ``head_digest``; the whole diagnostic was
    "/expected_frontier missing", which does not say what to write. Frontier leaves are frozen
    schema names already trusted as ``SAFE_DETAIL_KEYS``, so they are safe to locate.
    """

    assert set(FRONTIER_LEAVES) <= set(SAFE_DETAIL_KEYS)

    examples = descriptor_for("publish_work").input_schema["examples"]
    assert isinstance(examples, list)
    base = examples[0]
    assert isinstance(base, dict)

    with pytest.raises(ValidationError) as captured:
        PublishWorkRequest.model_validate(
            {**base, "expected_frontier": {"sequence": "10", "digest": f"sha256:{'a' * 64}"}}
        )
    locations = safe_validation_locations(captured.value)

    assert {"field": "/expected_frontier/head_digest", "reason": "missing"} in locations
    # The caller-supplied extra key is still projected to its parent and never named.
    assert {"field": "/expected_frontier", "reason": "extra_forbidden"} in locations
    assert not any(item["field"].endswith("/digest") for item in locations)


def test_rejected_event_draft_is_located_by_ordinal() -> None:
    """A bad draft in a batch must be identified by its ordinal, never by echoing its content."""

    examples = descriptor_for("publish_work").input_schema["examples"]
    assert isinstance(examples, list)
    base = cast(dict[str, object], examples[0])
    good = cast(list[object], base["event_drafts"])[0]
    assert isinstance(good, dict)
    secret = "NOT-A-REAL-ENUM-never-echo-this"
    bad: dict[str, object] = {
        **good,
        "event_id": "evt_00000000-0000-4000-8000-000000000099",
        "schema": {"name": "action_recorded", "version": "1.0.0"},
        "payload": {
            "action_id": "act_00000000-0000-4000-8000-000000000098",
            "action_kind": secret,
            "description": "x",
        },
    }

    with pytest.raises(ValidationError) as captured:
        PublishWorkRequest.model_validate({**base, "event_drafts": [good, bad]})
    locations = safe_validation_locations(captured.value)

    assert any(item["field"] == "/event_drafts/1" for item in locations)
    assert secret not in repr(locations)


def test_unknown_tool_message_is_sanitized() -> None:
    raw_name = "../../private/secret-tool"
    message = sanitize_unknown_tool_name(raw_name)

    assert message == "The requested tool is not registered."
    assert raw_name not in message


def test_descriptor_text_is_frozen_and_honest() -> None:
    assert tuple(item.name for item in TOOL_DESCRIPTORS) == _EXPECTED_TOOL_NAMES
    assert tuple(TOOL_DESCRIPTOR_DIGESTS) == _EXPECTED_TOOL_NAMES
    assert TOOL_DESCRIPTOR_SET_DIGEST == (
        "sha256:c812b652374aa5c80677447055460e84ffacde437d7f7e69ca1a001548b10752"
    )
    # The check descriptor carries the full mode decision rule, including semantic_required.
    check_description = descriptor_for("check").description
    assert "semantic_if_configured for most material implementation" in check_description
    assert "semantic_required when the claim depends on qualitative correctness" in (
        check_description
    )
    assert "Omitting mode resolves through the configured verification policy" in check_description
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
    assert "recommended_next_action" in descriptor_for("status").description
    assert "call receipt before claiming completion" in descriptor_for("publish_work").description
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


def test_advertised_input_schemas_honor_presentation_keyword_budgets() -> None:
    for descriptor in TOOL_DESCRIPTORS:
        schema_name = f"{descriptor.name.replace('_', '-')}-request"
        metrics = presentation_schema_metrics(descriptor.input_schema)
        budget = PRESENTATION_INPUT_SCHEMA_BUDGETS[schema_name]
        assert metrics["oneof_nodes"] <= budget["max_oneof_nodes"]
        assert metrics["oneof_branches"] <= budget["max_oneof_branches"]
        assert metrics["defs_count"] <= budget["max_defs_count"]
        assert metrics["defs_nest_depth"] <= budget["max_defs_nest_depth"]
        assert metrics["encoded_bytes"] <= budget["max_encoded_bytes"]
        encoded = repr(dict(descriptor.input_schema))
        assert "common/client-info" not in encoded
        assert "common/actor-assertion" not in encoded
        assert "common/frontier" not in encoded


def test_publish_work_presentation_matches_ordinary_admission_families() -> None:
    advertised = ordinary_publish_families_in_presentation(
        descriptor_for("publish_work").input_schema
    )
    assert advertised == ORDINARY_MCP_PUBLISH_EVENT_FAMILIES
    encoded = repr(dict(descriptor_for("publish_work").input_schema))
    assert "opaque-unknown-event-draft" not in encoded
    assert "opaque_unknown" not in encoded


def test_presentation_examples_admit_under_catalog_models() -> None:
    start_examples = descriptor_for("start").input_schema["examples"]
    status_examples = descriptor_for("status").input_schema["examples"]
    publish_examples = descriptor_for("publish_work").input_schema["examples"]
    assert isinstance(start_examples, list) and len(start_examples) == 1
    assert isinstance(status_examples, list) and len(status_examples) == 1
    assert isinstance(publish_examples, list) and publish_examples
    start_example = start_examples[0]
    status_example = status_examples[0]
    publish_example = publish_examples[0]
    assert isinstance(start_example, dict)
    assert isinstance(status_example, dict)
    assert isinstance(publish_example, dict)
    StartRequest.model_validate(start_example)
    StatusRequest.model_validate(status_example)
    event_drafts = publish_example["event_drafts"]
    assert isinstance(event_drafts, list) and event_drafts
    first_draft = event_drafts[0]
    assert isinstance(first_draft, dict)
    schema = first_draft["schema"]
    assert isinstance(schema, dict)
    # The first example stays the smallest possible starting publication.
    assert schema["name"] == "plan_published"

    # Every ordinary publishable family carries a worked example, and each one is a request an
    # agent could send unchanged. Copying a valid shape is the whole point of shipping these.
    exercised: set[str] = set()
    for example in publish_examples:
        assert isinstance(example, dict)
        PublishWorkRequest.model_validate(example)
        drafts = example["event_drafts"]
        assert isinstance(drafts, list)
        for draft in drafts:
            assert isinstance(draft, dict)
            draft_schema = draft["schema"]
            assert isinstance(draft_schema, dict)
            exercised.add(cast(str, draft_schema["name"]))
    assert exercised == ORDINARY_MCP_PUBLISH_EVENT_FAMILIES


def test_presentation_input_schema_is_projection_of_catalog_shape() -> None:
    for descriptor in TOOL_DESCRIPTORS:
        presented = descriptor.input_schema
        catalog = descriptor.catalog_input_schema
        assert presented["type"] == catalog["type"]
        assert presented["additionalProperties"] == catalog["additionalProperties"]
        presented_required = presented["required"]
        catalog_required = catalog["required"]
        presented_properties = presented["properties"]
        catalog_properties = catalog["properties"]
        assert isinstance(presented_required, list)
        assert isinstance(catalog_required, list)
        assert isinstance(presented_properties, dict)
        assert isinstance(catalog_properties, dict)
        assert set(presented_required) == set(catalog_required)
        assert set(presented_properties) == set(catalog_properties)
        # tools/list may attach examples; catalog admission schemas do not require them.
        assert "examples" not in catalog
