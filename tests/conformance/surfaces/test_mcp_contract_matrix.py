"""Foundation contract matrix for the frozen MCP surface."""

from __future__ import annotations

import re
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, ValidationError

from yoetz.mcp import resources as resource_module
from yoetz.mcp.descriptors import (
    ADVERTISED_SURFACE_BUDGET,
    INITIALIZE_GUIDANCE_URIS,
    ORDINARY_MCP_PUBLISH_EVENT_FAMILIES,
    PRESENTATION_INPUT_SCHEMA_BUDGETS,
    SERVER_INSTRUCTIONS_BUDGET,
    TOOL_DESCRIPTOR_DIGESTS,
    TOOL_DESCRIPTOR_SET_DIGEST,
    TOOL_DESCRIPTORS,
    McpRouteProfile,
    advertised_surface_metrics,
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
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import SAFE_DETAIL_KEYS, PublicErrorCode
from yoetz.protocol.models import (
    FRONTIER_LEAVES,
    REGISTERED_GUIDANCE_URIS,
    PublishWorkRequest,
    StartRequest,
    StatusRequest,
)
from yoetz.protocol.schemas import validate_schema_instance

_EXPECTED_TOOL_NAMES = (
    "start",
    "publish_work",
    "check",
    "respond",
    "status",
    "receipt",
    "read_guidance",
)
_WORKFLOW_TOOL_NAMES = (
    "start",
    "publish_work",
    "check",
    "respond",
    "status",
    "receipt",
)
_EXPECTED_RESOURCE_URIS = (
    "yoetz://guidance/agent-instructions.md",
    "yoetz://guidance/workflow.md",
    "yoetz://guidance/publication-policy.md",
    "yoetz://guidance/coverage-and-receipts.md",
    "yoetz://guidance/request-templates.md",
)
_FORBIDDEN_DESCRIPTOR_CLAIMS = re.compile(
    r"\b(?:authenticated|enforces?|gates?|observes?|proved|proves?|verified)\b",
    re.IGNORECASE | re.ASCII,
)


def test_fallback_error_object_is_admitted() -> None:
    fallback = build_last_resort_internal_error_result()

    for operation in _WORKFLOW_TOOL_NAMES:
        validate_schema_instance(f"{operation.replace('_', '-')}-result", "1.0.0", fallback)
    validate_schema_instance("read-guidance-result", "1.0.0", fallback)


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

    assert any(item["field"] == "/event_drafts/1/payload/action_kind" for item in locations)
    assert secret not in repr(locations)
    from yoetz.mcp.errors import authoring_hint

    hint = authoring_hint(descriptor_for("publish_work").input_schema, locations)
    assert "action_kind admits" in hint
    assert secret not in hint


def test_unknown_nested_payload_key_keeps_the_extra_forbidden_reason() -> None:
    """Both producers of `extra_forbidden` are pinned here: the envelope model and jsonschema.

    Top-level extras come from `_ClosedModel`'s `extra="forbid"`; a key inside an event payload is
    admitted only by the frozen event schema, and its verdict reaches this projection through
    `SchemaInstanceInvalid` (issue #240).
    """

    examples = descriptor_for("publish_work").input_schema["examples"]
    assert isinstance(examples, list)
    base = cast(dict[str, object], examples[0])
    good = cast(dict[str, object], cast(list[object], base["event_drafts"])[0])
    secret = "never_echo_this_unknown_payload_key"
    payload = {**cast(dict[str, object], good["payload"]), secret: "x"}

    with pytest.raises(ValidationError) as captured:
        PublishWorkRequest.model_validate({**base, "event_drafts": [{**good, "payload": payload}]})
    locations = safe_validation_locations(captured.value)

    assert {"field": "/event_drafts/0/payload", "reason": "extra_forbidden"}.items() <= (
        locations[0].items()
    )
    assert secret not in repr(locations)
    # Only the two wire keys are projected; the family and count stay inside the process.
    wire = build_public_error_result(
        PublicErrorCode.INVALID_REQUEST,
        "The request is invalid.",
        False,
        "err_00000000-0000-4000-8000-000000000002",
        safe_details=locations,
    )
    details = cast(dict[str, object], cast(dict[str, object], wire["error"])["safe_details"])
    assert details == {"fields": ["/event_drafts/0/payload"], "reasons": ["extra_forbidden"]}


def test_unknown_tool_message_is_sanitized() -> None:
    raw_name = "../../private/secret-tool"
    message = sanitize_unknown_tool_name(raw_name)

    assert message == "The requested tool is not registered."
    assert raw_name not in message


def test_descriptor_text_is_frozen_and_honest() -> None:
    assert tuple(TOOL_DESCRIPTORS) == ("policy", "strict")
    assert tuple(TOOL_DESCRIPTOR_DIGESTS) == ("policy", "strict")
    assert TOOL_DESCRIPTOR_SET_DIGEST == {
        "policy": "sha256:955be2650e68a62d1647e29ea6cf06400bd9f85ad8e97754327cbdd117d051ed",
        "strict": "sha256:55bb7931ecec7a92be167ecc9d7b7650abc0991b1559a17d61d570cc3790d0a2",
    }
    for profile, descriptors in TOOL_DESCRIPTORS.items():
        assert tuple(item.name for item in descriptors) == _EXPECTED_TOOL_NAMES
        assert tuple(TOOL_DESCRIPTOR_DIGESTS[profile]) == _EXPECTED_TOOL_NAMES
    for name in _EXPECTED_TOOL_NAMES:
        assert "yoetz://guidance/" in descriptor_for(name).description
    # The check descriptor carries the full mode decision rule, including semantic_required.
    check_description = descriptor_for("check").description
    assert "semantic_if_configured for most material implementation" in check_description
    assert "semantic_required when the claim depends on qualitative correctness" in (
        check_description
    )
    assert "Omitting mode resolves through the configured verification policy" in check_description
    respond_description = descriptor_for("respond").description
    assert "result frontier of the check that returned it" in respond_description
    assert "not its subject_frontier" in respond_description
    assert descriptor_for("start").description.startswith(
        "Call for material multi-step, delegated, resumable, or verification-heavy work"
    )
    # The two argument conventions a first-time caller cannot infer from prose alone. Both cost a
    # rejected start call in the 2026-07-30 dogfood before the descriptor named them.
    start_description = descriptor_for("start").description
    assert "fresh req_ prefixed random UUID" in start_description
    assert "workspace_ref and external_ref are admitted only as a pair" in start_description
    assert (
        "unique and already in ascending ASCII order" in descriptor_for("publish_work").description
    )
    for descriptors in TOOL_DESCRIPTORS.values():
        assert {item.name for item in descriptors if item.annotations.read_only} == {
            "status",
            "read_guidance",
        }
        assert all(not item.annotations.destructive for item in descriptors)
        assert all(item.annotations.idempotent for item in descriptors)
        assert all(
            _FORBIDDEN_DESCRIPTOR_CLAIMS.search(item.description) is None for item in descriptors
        )
    assert descriptor_for("check", "policy").annotations.open_world is True
    assert descriptor_for("check", "strict").annotations.open_world is False
    assert (
        "this route will not request external semantic review"
        in descriptor_for("check", "strict").description
    )
    for name in set(_EXPECTED_TOOL_NAMES) - {"check"}:
        assert descriptor_for(name, "policy") == descriptor_for(name, "strict")
    assert "uncertain what you already did or committed to" in descriptor_for("status").description
    assert "recommended_next_action" in descriptor_for("status").description
    assert "caller-asserted occurred_at beside the service-stamped accepted_at" in (
        descriptor_for("status").description
    )
    assert "call receipt before claiming completion" in descriptor_for("publish_work").description
    assert "do not copy the illustrative example timestamp" in (
        descriptor_for("publish_work").description
    )
    assert "Service accepted_at" in descriptor_for("publish_work").description
    assert "frontier-bound" in descriptor_for("publish_work").description
    assert "ingestion sequence" in descriptor_for("publish_work").description
    base_instructions = read_resource("yoetz://guidance/agent-instructions.md").decode("utf-8")
    workflow = read_resource("yoetz://guidance/workflow.md").decode("utf-8")
    coverage = read_resource("yoetz://guidance/coverage-and-receipts.md").decode("utf-8")
    assert INITIALIZE_GUIDANCE_URIS == ("yoetz://guidance/agent-instructions.md",)
    assert server_instructions().startswith(base_instructions.rstrip())
    assert server_instructions() == f"{base_instructions.rstrip()}\n\nRoute profile: policy. " + (
        "External semantic review follows the configured policy.\n"
    )
    # Inlining these two cost 24 KB on every advertised tool description (#300). They are fetched
    # on demand instead; the catalog paragraph below is what makes that reachable.
    assert workflow.rstrip() not in server_instructions()
    assert coverage.rstrip() not in server_instructions()
    assert "Route profile: policy." in server_instructions()
    assert "Do not call `resources/list` or `list_mcp_resources` to find Yoetz guidance" in (
        server_instructions()
    )
    # The on-demand catalog is only reachable because the inlined document names both the URI set
    # and the read_guidance recovery path. Losing either line strands the un-inlined documents.
    assert "yoetz://guidance/workflow.md" in server_instructions()
    assert "yoetz://guidance/coverage-and-receipts.md" in server_instructions()
    assert "call `read_guidance` with the same URI" in server_instructions()
    strict_instructions = server_instructions("strict")
    assert "Route profile: strict." in strict_instructions
    assert "This route will not request external semantic review" in strict_instructions

    with pytest.raises(KeyError, match="unregistered_tool_descriptor") as captured:
        descriptor_for("secret-tool")
    assert "secret-tool" not in str(captured.value)


def test_respond_agent_surface_names_every_admitted_disposition() -> None:
    """The advertised respond text is the only place an MCP caller learns the per-disposition
    field rules: ``_mcp_presentation_schema`` drops respond-request's ``allOf`` before advertising
    it. A disposition added to the enum without a rule here is an undiscoverable wire value."""

    descriptor = descriptor_for("respond")
    schema = cast(dict[str, Any], dict(descriptor.input_schema))
    disposition = cast(dict[str, Any], cast(dict[str, Any], schema["properties"])["disposition"])
    admitted = tuple(cast(list[str], disposition["enum"]))
    assert admitted == ("acknowledged", "provenance_disputed", "rejected", "waived")
    assert "allOf" not in schema
    rules = cast(str, disposition["description"]).lower()
    for value in admitted:
        assert value in rules, f"{value} has no advertised field rule"
    assert "requires reason" in rules
    # The tool description is the first text a caller reads, and it is digest-pinned.
    assert "provenance dispute" in descriptor.description
    assert "provenance_disputed" in descriptor.description


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
    assert GUIDANCE_RESOURCES[4].annotations.priority <= 0.9


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
    for descriptor in TOOL_DESCRIPTORS["policy"]:
        schema_name = f"{descriptor.name.replace('_', '-')}-request"
        metrics = presentation_schema_metrics(descriptor.input_schema)
        budget = PRESENTATION_INPUT_SCHEMA_BUDGETS[schema_name]
        assert metrics["oneof_nodes"] <= budget["max_oneof_nodes"]
        assert metrics["oneof_branches"] <= budget["max_oneof_branches"]
        assert metrics["ref_nodes"] <= budget["max_ref_nodes"]
        assert metrics["conditional_nodes"] <= budget["max_conditional_nodes"]
        assert metrics["defs_count"] <= budget["max_defs_count"]
        assert metrics["defs_nest_depth"] <= budget["max_defs_nest_depth"]
        assert metrics["encoded_bytes"] <= budget["max_encoded_bytes"]
        encoded = repr(dict(descriptor.input_schema))
        assert "common/client-info" not in encoded
        assert "common/actor-assertion" not in encoded
        assert "common/frontier" not in encoded


def test_advertised_surface_honors_instructions_and_aggregate_budgets() -> None:
    """#300: the instructions block grew to 41 KB unnoticed because nothing bounded it, while the
    adjacent input schemas have been bounded since #128. A host may charge `instructions` once per
    advertised tool, so an unbounded edit here is multiplied, not merely added."""

    profiles: tuple[McpRouteProfile, ...] = ("policy", "strict")
    for profile in profiles:
        metrics = advertised_surface_metrics(profile)
        assert metrics["tool_count"] == len(_EXPECTED_TOOL_NAMES)
        assert (
            metrics["instructions_encoded_bytes"] <= SERVER_INSTRUCTIONS_BUDGET["max_encoded_bytes"]
        ), f"{profile} initialize instructions exceed their reviewed budget"
        assert (
            metrics["replicated_encoded_bytes"] <= ADVERTISED_SURFACE_BUDGET["max_encoded_bytes"]
        ), f"{profile} advertised surface exceeds its reviewed budget"
        # The aggregate must actually be an aggregate: a per-item bound cannot catch total growth.
        assert metrics["replicated_encoded_bytes"] == (
            metrics["instructions_encoded_bytes"] * metrics["tool_count"]
            + metrics["description_encoded_bytes"]
            + metrics["schema_encoded_bytes"]
        )
    # Guard the reason the aggregate budget is small enough to bite: exactly one inlined document.
    assert len(INITIALIZE_GUIDANCE_URIS) == 1


def test_publish_work_presentation_matches_ordinary_admission_families() -> None:
    schema = descriptor_for("publish_work").input_schema
    advertised = ordinary_publish_families_in_presentation(schema)
    assert advertised == ORDINARY_MCP_PUBLISH_EVENT_FAMILIES
    event_drafts = cast(dict[str, Any], cast(dict[str, Any], schema["properties"])["event_drafts"])
    items = cast(dict[str, Any], event_drafts["items"])
    branches = cast(list[dict[str, Any]], items["oneOf"])
    evidence_versions: set[str] = set()
    for branch in branches:
        branch_properties = cast(dict[str, Any], branch["properties"])
        schema_node = cast(dict[str, Any], branch_properties["schema"])
        schema_properties = cast(dict[str, Any], schema_node["properties"])
        if cast(dict[str, Any], schema_properties["name"])["const"] == "evidence_recorded":
            version_node = cast(dict[str, Any], schema_properties["version"])
            evidence_versions.add(cast(str, version_node["const"]))
    assert evidence_versions == {"1.0.0", "1.1.0"}
    encoded = repr(dict(schema))
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
    first_payload = first_draft["payload"]
    assert isinstance(first_payload, dict)
    assert first_payload["obligation_refs"] == []
    assert first_payload["no_obligations_reason"] == "single_atomic_change"

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


def test_publish_work_examples_validate_against_advertised_input_schema() -> None:
    """Worked examples must validate against the contract MCP clients actually receive."""

    schema = descriptor_for("publish_work").input_schema
    examples = cast(list[JsonValue], schema["examples"])
    validator = cast(Any, Draft202012Validator(cast(Any, schema)))

    for index, example in enumerate(examples):
        errors = sorted(validator.iter_errors(example), key=lambda error: list(error.path))
        assert not errors, (index, tuple(error.json_path for error in errors))


def test_presentation_input_schema_is_projection_of_catalog_shape() -> None:
    for descriptor in TOOL_DESCRIPTORS["policy"]:
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


def test_read_guidance_uri_literal_matches_registered_resources() -> None:
    assert REGISTERED_GUIDANCE_URIS == tuple(item.uri for item in GUIDANCE_RESOURCES)


def test_read_guidance_descriptor_is_read_only_and_names_a_guidance_uri() -> None:
    descriptor = descriptor_for("read_guidance")
    assert descriptor.annotations.read_only is True
    assert descriptor.annotations.idempotent is True
    assert descriptor.annotations.destructive is False
    assert descriptor.annotations.open_world is False
    assert "yoetz://guidance/" in descriptor.description
    assert "512-byte summary" in descriptor.description
    assert "not a ledger operation" in descriptor.description
