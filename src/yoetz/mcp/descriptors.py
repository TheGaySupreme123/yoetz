"""Frozen, honesty-bounded MCP tool descriptors and initialize instructions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import cache
from types import MappingProxyType
from typing import Final, Literal, cast

from yoetz.mcp.resources import read_resource
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.schemas import SCHEMA_NAMESPACE, load_schema_catalog, schema_document_for

__all__ = [
    "ORDINARY_MCP_PUBLISH_EVENT_FAMILIES",
    "PRESENTATION_INPUT_SCHEMA_BUDGETS",
    "McpRouteProfile",
    "TOOL_DESCRIPTOR_DIGESTS",
    "TOOL_DESCRIPTOR_SET_DIGEST",
    "TOOL_DESCRIPTORS",
    "ToolAnnotations",
    "ToolDescriptor",
    "descriptor_for",
    "ordinary_publish_families_in_presentation",
    "presentation_schema_metrics",
    "server_instructions",
]

type McpRouteProfile = Literal["policy", "strict"]

_SCHEMA_VERSION: Final = "1.0.0"
_INSTRUCTIONS_URI: Final = "yoetz://guidance/agent-instructions.md"
_GUIDANCE_URI: Final = re.compile(r"yoetz://guidance/[a-z0-9.-]+\.md", re.ASCII)
_FORBIDDEN_CLAIMS: Final = re.compile(
    r"\b(?:authenticated|enforces?|gates?|observes?|proved|proves?|verified)\b",
    re.IGNORECASE | re.ASCII,
)
_BOUNDARY_TERMS: Final = re.compile(
    r"(?:/|\\|\b(?:claude|codex|cursor|gemini|host|model|openai|provider|version)\b)",
    re.IGNORECASE | re.ASCII,
)

# Mirrors application/publish_work ordinary cooperative_mcp|local_cli admission. Advertised
# publish_work schemas project to this set; catalog admission schemas remain authoritative.
ORDINARY_MCP_PUBLISH_EVENT_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "plan_published",
        "obligation_published",
        "assignment_recorded",
        "decision_recorded",
        "action_recorded",
        "result_recorded",
        "evidence_recorded",
        "claim_recorded",
        "plan_revised",
    }
)

_COMMON_INLINE_SCHEMA_IDS: Final[frozenset[str]] = frozenset(
    {
        f"{SCHEMA_NAMESPACE}common/actor-assertion-1.0.0.schema.json",
        f"{SCHEMA_NAMESPACE}common/client-info-1.0.0.schema.json",
        f"{SCHEMA_NAMESPACE}common/frontier-1.0.0.schema.json",
    }
)
_EVENT_DRAFT_SCHEMA_ID: Final = f"{SCHEMA_NAMESPACE}events/event-draft-1.0.0.schema.json"
_OPAQUE_EVENT_DRAFT_SCHEMA_ID: Final = (
    f"{SCHEMA_NAMESPACE}events/opaque-unknown-event-draft-1.0.0.schema.json"
)

# Reviewed keyword budgets for tools/list presentation schemas (agent-usability guardrails).
PRESENTATION_INPUT_SCHEMA_BUDGETS: Final[Mapping[str, Mapping[str, int]]] = MappingProxyType(
    {
        "start-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 8,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 4_000,
            }
        ),
        "publish-work-request": MappingProxyType(
            {
                "max_oneof_nodes": 8,
                "max_oneof_branches": 28,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 20,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 28_000,
            }
        ),
        "check-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 12,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 8_000,
            }
        ),
        "respond-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 12,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 8_000,
            }
        ),
        "status-request": MappingProxyType(
            {
                "max_oneof_nodes": 4,
                "max_oneof_branches": 8,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 20,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 10_000,
            }
        ),
        "receipt-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_ref_nodes": 0,
                "max_conditional_nodes": 0,
                "max_defs_count": 12,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 6_000,
            }
        ),
    }
)


def _example_id(kind: str, seed: int) -> str:
    prefixes = {
        "request": "req",
        "session": "ses",
        "writer": "wri",
        "event": "evt",
        "action": "act",
        "claim": "clm",
        "evidence": "evd",
        "finding": "fnd",
        "obligation": "obl",
        "result": "res",
        "task": "tsk",
    }
    return f"{prefixes[kind]}_00000000-0000-4000-8000-{seed:012d}"


# Illustrative only: do not copy into live drafts. Prefer the best real RFC 3339 ms UTC time.
_EXAMPLE_OCCURRED_AT: Final = "2026-01-01T00:00:00.000Z"


def _example_draft(seed: int, family: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """One minimal valid draft envelope for a family, so agents copy shape rather than guess it.

    ``occurred_at`` is intentionally a fixed illustrative placeholder. Live drafts must use the
    best real caller-asserted event time; service acceptance time is stamped separately.
    """

    return {
        "event_id": _example_id("event", seed),
        "schema": {"name": family, "version": "1.0.0"},
        "occurred_at": _EXAMPLE_OCCURRED_AT,
        "causal_parents": [],
        "payload": payload,
        "artifact_refs": [],
        "evidence_refs": [],
    }


_EXAMPLE_ACTOR: Final[dict[str, JsonValue]] = {
    "actor_id": "harness:mcp-example",
    "actor_type": "harness",
}
_EXAMPLE_CLIENT: Final[dict[str, JsonValue]] = {
    "kind": "cooperative_agent",
    "version": "0.1.0",
    "integration": "cooperative_mcp",
}
# A syntactically valid non-genesis head. Read the real one from status; never reuse this value.
_EXAMPLE_HEAD_DIGEST: Final = "sha256:" + "0" * 64

_INPUT_SCHEMA_EXAMPLES: Final[Mapping[str, tuple[dict[str, JsonValue], ...]]] = MappingProxyType(
    {
        "start-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 1),
                "mode": "create",
                "task_title": "Example task",
                "requested_view": "compact",
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
        # check, respond, and receipt had no worked example at all. Every tool an agent must call
        # to reach a completion claim now shows one, so authoring never depends on reading source.
        "check-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 7),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "1", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "mode": "semantic_if_configured",
                "max_findings": "3",
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
        "respond-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 8),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "1", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "finding_id": _example_id("finding", 1),
                "finding_frontier": {"sequence": "1", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "disposition": "acknowledged",
                "reason": "The finding is accurate; the obligation stays open until it is fixed.",
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
        "receipt-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 9),
                "task_id": _example_id("task", 1),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "1", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "format": "markdown",
                "include": "standard",
                "redaction_profile": "default_local_export",
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
        "status-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 2),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "view": "compact",
                "limit": "10",
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
        # One worked draft per ordinary publishable family. The plan example alone left agents
        # hand-deriving action/result/evidence/claim shapes from a large oneOf, which is where
        # routine publication attempts actually fail.
        "publish-work-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 3),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
                "event_drafts": [
                    _example_draft(
                        1,
                        "plan_published",
                        {
                            "plan_version": 1,
                            "summary": "One atomic change with no independent obligation split.",
                            "obligation_refs": [],
                            "no_obligations_reason": "single_atomic_change",
                        },
                    )
                ],
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 4),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "1", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "event_drafts": [
                    _example_draft(
                        2,
                        "obligation_published",
                        {
                            "obligation_id": _example_id("obligation", 1),
                            "description": "State the outcome this work owes.",
                            "evidence_expectation": "A named test run or reviewed diff.",
                            "status": "open",
                        },
                    ),
                    _example_draft(
                        3,
                        "assignment_recorded",
                        {
                            "assignee_actor_id": "harness:mcp-example",
                            "obligation_ids": [_example_id("obligation", 1)],
                            "scope_description": "One independently reviewable work package.",
                        },
                    ),
                    _example_draft(
                        4,
                        "decision_recorded",
                        {
                            "statement": "Keep the existing adapter instead of adding one.",
                            "rationale": "The current path already covers the requested case.",
                            "authority": "harness:mcp-example",
                        },
                    ),
                ],
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 5),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "4", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "event_drafts": [
                    _example_draft(
                        5,
                        "action_recorded",
                        {
                            "action_id": _example_id("action", 1),
                            "action_kind": "command",
                            # action_kind "command" additionally requires command.
                            "command": "pytest -q",
                            "description": "Ran the focused test slice for the touched module.",
                        },
                    ),
                    _example_draft(
                        6,
                        "result_recorded",
                        {
                            "result_id": _example_id("result", 1),
                            "action_id": _example_id("action", 1),
                            "outcome": "success",
                            "summary": "The focused slice passed.",
                        },
                    ),
                    _example_draft(
                        7,
                        "evidence_recorded",
                        {
                            "evidence_id": _example_id("evidence", 1),
                            "evidence_kind": "test_result",
                            # Each strength requires its own proof field; content_digest
                            # requires content_digest.
                            "strength": "content_digest",
                            "content_digest": _EXAMPLE_HEAD_DIGEST,
                            "observed_at": "2026-01-01T00:00:00.000Z",
                            "description": "Focused test slice for the touched module.",
                        },
                    ),
                    _example_draft(
                        8,
                        "claim_recorded",
                        {
                            "claim_id": _example_id("claim", 1),
                            "claim_kind": "completion",
                            "statement": "The requested change is implemented and covered.",
                            "supporting_refs": [_example_id("evidence", 1)],
                        },
                    ),
                ],
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 6),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "8", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "event_drafts": [
                    _example_draft(
                        9,
                        "plan_revised",
                        {
                            "plan_version": 2,
                            "supersedes_plan_version": 1,
                            "reason": "The reviewed scope changed after inspecting the source.",
                            "summary": "Revised plan",
                            "obligation_changes": [
                                {
                                    "obligation_id": _example_id("obligation", 1),
                                    "change": "carried",
                                }
                            ],
                        },
                    )
                ],
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
            # Obligation resolution: repeat meaning fields byte-for-byte; only status and
            # resolution_evidence_refs may change. See publication-policy.md#obligation-resolution.
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 10),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "2", "head_digest": _EXAMPLE_HEAD_DIGEST},
                "event_drafts": [
                    _example_draft(
                        10,
                        "obligation_published",
                        {
                            "obligation_id": _example_id("obligation", 2),
                            "description": "State the outcome this work owes.",
                            "acceptance_criteria": "A focused test slice passes at the claimed state.",
                            "evidence_expectation": "A named test run or reviewed diff.",
                            "status": "open",
                            "requested_items": [{"item_kind": "command", "value": "pytest -q"}],
                        },
                    ),
                    _example_draft(
                        11,
                        "evidence_recorded",
                        {
                            "evidence_id": _example_id("evidence", 2),
                            "evidence_kind": "test_result",
                            "strength": "content_digest",
                            "content_digest": _EXAMPLE_HEAD_DIGEST,
                            "observed_at": "2026-01-01T00:00:00.000Z",
                            "description": "Focused test slice for the claimed outcome.",
                        },
                    ),
                    _example_draft(
                        12,
                        "obligation_published",
                        {
                            "obligation_id": _example_id("obligation", 2),
                            "description": "State the outcome this work owes.",
                            "acceptance_criteria": "A focused test slice passes at the claimed state.",
                            "evidence_expectation": "A named test run or reviewed diff.",
                            "status": "resolved",
                            "requested_items": [{"item_kind": "command", "value": "pytest -q"}],
                            "resolution_evidence_refs": [_example_id("evidence", 2)],
                        },
                    ),
                ],
                "actor": dict(_EXAMPLE_ACTOR),
                "client": dict(_EXAMPLE_CLIENT),
            },
        ),
    }
)


def _mutable_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        source = cast(Mapping[str, JsonValue], value)
        return {key: _mutable_json(item) for key, item in source.items()}
    if isinstance(value, tuple | list):
        sequence = cast(tuple[JsonValue, ...] | list[JsonValue], value)
        return [_mutable_json(item) for item in sequence]
    return value


def _strip_schema_metadata(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        key: _mutable_json(item)
        for key, item in value.items()
        if key not in {"$id", "$schema", "title"}
    }


def _event_family_from_draft_branch(branch: Mapping[str, JsonValue]) -> str | None:
    properties = branch.get("properties")
    if not isinstance(properties, Mapping):
        return None
    props = cast(Mapping[str, JsonValue], properties)
    schema_node = props.get("schema")
    if isinstance(schema_node, Mapping):
        schema_map = cast(Mapping[str, JsonValue], schema_node)
        nested_props = schema_map.get("properties")
        if isinstance(nested_props, Mapping):
            name_node = cast(Mapping[str, JsonValue], nested_props).get("name")
            if isinstance(name_node, Mapping):
                const_name = cast(Mapping[str, JsonValue], name_node).get("const")
                if type(const_name) is str:
                    return const_name
    payload = props.get("payload")
    if not isinstance(payload, Mapping):
        return None
    ref = cast(Mapping[str, JsonValue], payload).get("$ref")
    if type(ref) is not str or not ref.startswith(SCHEMA_NAMESPACE):
        return None
    uri = ref.partition("#")[0]
    marker = "/events/"
    if marker not in uri or not uri.endswith("-1.0.0.schema.json"):
        return None
    slug = uri.rsplit(marker, 1)[1].removesuffix("-1.0.0.schema.json")
    return slug.replace("-", "_")


def _project_event_draft_for_ordinary_mcp(
    event_draft: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    projected = _strip_schema_metadata(event_draft)
    one_of = projected.get("oneOf")
    if not isinstance(one_of, list):
        raise RuntimeError("mcp_event_draft_projection_invalid")
    kept: list[JsonValue] = []
    for branch in cast(list[JsonValue], one_of):
        if not isinstance(branch, Mapping):
            continue
        branch_map = cast(Mapping[str, JsonValue], branch)
        ref = branch_map.get("$ref")
        if type(ref) is str and ref.partition("#")[0] == _OPAQUE_EVENT_DRAFT_SCHEMA_ID:
            continue
        family = _event_family_from_draft_branch(branch_map)
        if family in ORDINARY_MCP_PUBLISH_EVENT_FAMILIES:
            kept.append(_mutable_json(branch_map))
    if len(kept) != len(ORDINARY_MCP_PUBLISH_EVENT_FAMILIES):
        raise RuntimeError("mcp_event_draft_projection_incomplete")
    projected["oneOf"] = kept
    return projected


def _resolved_external_document(
    uri: str, *, project_ordinary_event_draft: bool
) -> Mapping[str, JsonValue]:
    catalog = load_schema_catalog()
    document = catalog.by_id.get(uri)
    if document is None:
        raise RuntimeError("mcp_schema_reference_unknown")
    if project_ordinary_event_draft and uri == _EVENT_DRAFT_SCHEMA_ID:
        return _project_event_draft_for_ordinary_mcp(document.json_schema)
    return document.json_schema


def _external_schema_documents(
    value: JsonValue, *, project_ordinary_event_draft: bool = False
) -> dict[str, Mapping[str, JsonValue]]:
    documents: dict[str, Mapping[str, JsonValue]] = {}

    def visit(candidate: JsonValue) -> None:
        if isinstance(candidate, Mapping):
            source = cast(Mapping[str, JsonValue], candidate)
            ref = source.get("$ref")
            if isinstance(ref, str) and ref.startswith(SCHEMA_NAMESPACE):
                uri = ref.partition("#")[0]
                if uri == _OPAQUE_EVENT_DRAFT_SCHEMA_ID and project_ordinary_event_draft:
                    return
                if uri not in documents:
                    nested = _resolved_external_document(
                        uri, project_ordinary_event_draft=project_ordinary_event_draft
                    )
                    documents[uri] = nested
                    visit(nested)
            for item in source.values():
                visit(item)
        elif isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            for item in sequence:
                visit(item)

    visit(value)
    return documents


def _bundle_key(uri: str) -> str:
    return "__yoetz_" + hashlib.sha256(uri.encode("ascii")).hexdigest()[:16]


def _rewrite_schema_refs(
    value: JsonValue,
    *,
    current_uri: str,
    root_uri: str,
    inline_uris: frozenset[str] = frozenset(),
) -> JsonValue:
    if isinstance(value, Mapping):
        source = cast(Mapping[str, JsonValue], value)
        rewritten_ref = source.get("$ref")
        if isinstance(rewritten_ref, str):
            if rewritten_ref.startswith(SCHEMA_NAMESPACE):
                uri, separator, fragment = rewritten_ref.partition("#")
                if uri in inline_uris and not separator and set(source.keys()) == {"$ref"}:
                    catalog = load_schema_catalog()
                    document = catalog.by_id.get(uri)
                    if document is None:
                        raise RuntimeError("mcp_schema_reference_unknown")
                    return _rewrite_schema_refs(
                        _strip_schema_metadata(document.json_schema),
                        current_uri=uri,
                        root_uri=root_uri,
                        inline_uris=inline_uris,
                    )
                rewritten_ref = f"#/$defs/{_bundle_key(uri)}"
                if separator and fragment:
                    rewritten_ref += fragment
            elif rewritten_ref.startswith("#") and current_uri != root_uri:
                rewritten_ref = f"#/$defs/{_bundle_key(current_uri)}{rewritten_ref[1:]}"
        return {
            key: (
                rewritten_ref
                if key == "$ref" and isinstance(rewritten_ref, str)
                else _rewrite_schema_refs(
                    item,
                    current_uri=current_uri,
                    root_uri=root_uri,
                    inline_uris=inline_uris,
                )
            )
            for key, item in source.items()
            if key not in {"$id", "$schema"}
        }
    if isinstance(value, tuple | list):
        sequence = cast(tuple[JsonValue, ...] | list[JsonValue], value)
        return [
            _rewrite_schema_refs(
                item,
                current_uri=current_uri,
                root_uri=root_uri,
                inline_uris=inline_uris,
            )
            for item in sequence
        ]
    return value


def _inline_nested_local_defs(parent_key: str, body: JsonValue) -> JsonValue:
    """Inline ``#/$defs/<parent>/$defs/<child>`` targets and drop nested ``$defs``."""

    if not isinstance(body, Mapping):
        return body
    source = cast(Mapping[str, JsonValue], body)
    nested = source.get("$defs")
    if not isinstance(nested, Mapping):
        return _mutable_json(source)
    nested_defs = cast(Mapping[str, JsonValue], nested)
    prefix = f"#/$defs/{parent_key}/$defs/"

    def resolve(candidate: JsonValue) -> JsonValue:
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            ref = mapping.get("$ref")
            if type(ref) is str and ref.startswith(prefix) and "/" not in ref[len(prefix) :]:
                child_key = ref[len(prefix) :]
                target = nested_defs.get(child_key)
                if target is None:
                    raise RuntimeError("mcp_schema_nested_def_missing")
                resolved_target = resolve(_mutable_json(target))
                if not isinstance(resolved_target, Mapping):
                    raise RuntimeError("mcp_schema_nested_def_invalid")
                merged = dict(cast(Mapping[str, JsonValue], resolved_target))
                for key, item in mapping.items():
                    if key == "$ref":
                        continue
                    resolved_item = resolve(item)
                    if key in merged and merged[key] != resolved_item:
                        raise RuntimeError("mcp_schema_ref_sibling_conflict")
                    merged[key] = resolved_item
                return merged
            return {key: resolve(item) for key, item in mapping.items() if key != "$defs"}
        if isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            return [resolve(item) for item in sequence]
        return candidate

    return resolve(source)


def _local_def_key(ref: JsonValue) -> str | None:
    if type(ref) is not str or not ref.startswith("#/$defs/"):
        return None
    remainder = ref[len("#/$defs/") :]
    if not remainder or "/" in remainder:
        return None
    return remainder


def _event_payload_definitions(
    definitions: Mapping[str, JsonValue],
) -> frozenset[str]:
    """Return event payload definitions and their shared definition dependencies."""

    event_body = definitions.get(_bundle_key(_EVENT_DRAFT_SCHEMA_ID))
    if not isinstance(event_body, Mapping):
        return frozenset()
    one_of = cast(Mapping[str, JsonValue], event_body).get("oneOf")
    if not isinstance(one_of, list):
        raise RuntimeError("mcp_event_draft_projection_invalid")
    roots: set[str] = set()
    for branch in cast(list[JsonValue], one_of):
        if not isinstance(branch, Mapping):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        properties = cast(Mapping[str, JsonValue], branch).get("properties")
        if not isinstance(properties, Mapping):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        payload = cast(Mapping[str, JsonValue], properties).get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        key = _local_def_key(cast(Mapping[str, JsonValue], payload).get("$ref"))
        if key is None:
            raise RuntimeError("mcp_event_draft_projection_invalid")
        roots.add(key)

    retained = set(roots)
    pending = list(roots)
    while pending:
        key = pending.pop()
        body = definitions.get(key)
        if body is None:
            raise RuntimeError("mcp_schema_reference_unknown")
        for dependency in _referenced_top_level_defs(body):
            if dependency not in retained:
                retained.add(dependency)
                pending.append(dependency)
    return frozenset(retained)


def _inline_presentation_refs(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Inline local presentation refs except the bounded event-payload definition graph."""

    raw_definitions = schema.get("$defs")
    if not isinstance(raw_definitions, Mapping):
        raise RuntimeError("mcp_schema_invalid")
    definitions = cast(Mapping[str, JsonValue], raw_definitions)
    retained = _event_payload_definitions(definitions)
    resolving: set[str] = set()

    def resolve(candidate: JsonValue) -> JsonValue:
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            key = _local_def_key(mapping.get("$ref"))
            if key is not None and key not in retained:
                if key in resolving:
                    raise RuntimeError("mcp_schema_reference_cycle")
                target = definitions.get(key)
                if target is None:
                    raise RuntimeError("mcp_schema_reference_unknown")
                resolving.add(key)
                try:
                    resolved_target = resolve(_mutable_json(target))
                finally:
                    resolving.remove(key)
                if not isinstance(resolved_target, Mapping):
                    raise RuntimeError("mcp_schema_def_invalid")
                merged = dict(cast(Mapping[str, JsonValue], resolved_target))
                for sibling_key, item in mapping.items():
                    if sibling_key == "$ref":
                        continue
                    resolved_item = resolve(item)
                    if sibling_key in merged and merged[sibling_key] != resolved_item:
                        raise RuntimeError("mcp_schema_ref_sibling_conflict")
                    merged[sibling_key] = resolved_item
                return merged
            return {
                item_key: resolve(item) for item_key, item in mapping.items() if item_key != "$defs"
            }
        if isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            return [resolve(item) for item in sequence]
        return candidate

    resolved = resolve(schema)
    if not isinstance(resolved, dict):
        raise RuntimeError("mcp_schema_invalid")
    if retained:
        resolved["$defs"] = {
            key: _mutable_json(definitions[key]) for key in definitions if key in retained
        }
    return resolved


def _flatten_frontier_conditions(candidate: JsonValue) -> JsonValue:
    """Keep a host-authorable frontier shape while catalog admission retains exact coupling."""

    if isinstance(candidate, Mapping):
        mapping = {
            key: (_mutable_json(item) if key == "$defs" else _flatten_frontier_conditions(item))
            for key, item in cast(Mapping[str, JsonValue], candidate).items()
        }
        properties = mapping.get("properties")
        required = mapping.get("required")
        if (
            isinstance(properties, dict)
            and {"sequence", "head_digest"}.issubset(properties)
            and isinstance(required, list)
            and {"sequence", "head_digest"}.issubset(required)
            and "allOf" in mapping
        ):
            mapping.pop("allOf")
            head_digest = properties.get("head_digest")
            if not isinstance(head_digest, dict):
                raise RuntimeError("mcp_frontier_projection_invalid")
            head_digest["pattern"] = "^(genesis|sha256:[0-9a-f]{64})$"
            head_digest["description"] = "Genesis is valid only with sequence 0."
        return mapping
    if isinstance(candidate, tuple | list):
        sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
        return [_flatten_frontier_conditions(item) for item in sequence]
    return candidate


def _describe_presentation_schema(name: str, schema: dict[str, JsonValue]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("mcp_schema_invalid")

    request_id = properties.get("request_id")
    if isinstance(request_id, dict):
        request_id["description"] = "Use a fresh req_ prefixed random UUID for each request."

    expected_frontier = properties.get("expected_frontier")
    if isinstance(expected_frontier, dict):
        expected_frontier["description"] = (
            "Use the sequence and head digest returned by status. Genesis is valid only with "
            "sequence 0."
        )

    if name == "start-request":
        mode = properties.get("mode")
        if isinstance(mode, dict):
            mode["description"] = (
                "mode attach requires session_id, or both workspace_ref and external_ref with no "
                "session_id."
            )
    elif name == "publish-work-request":
        event_drafts = properties.get("event_drafts")
        if not isinstance(event_drafts, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        event_drafts["description"] = (
            "Send one bounded batch per material transition. Each schema.name selects the "
            "payload shape."
        )
        items = event_drafts.get("items")
        if not isinstance(items, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        item_properties = items.get("properties")
        if not isinstance(item_properties, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        schema_property = item_properties.get("schema")
        if not isinstance(schema_property, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        schema_properties = schema_property.get("properties")
        if not isinstance(schema_properties, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        family_name = schema_properties.get("name")
        if not isinstance(family_name, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        family_name["enum"] = cast(list[JsonValue], sorted(ORDINARY_MCP_PUBLISH_EVENT_FAMILIES))
        family_name["description"] = "Selects the event family and its payload fields."
        payload = item_properties.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("mcp_event_draft_projection_invalid")
        payload["type"] = "object"
        payload["description"] = (
            "Fields depend on schema.name; use the matching event family template."
        )
    elif name == "check-request":
        scope = properties.get("scope")
        if isinstance(scope, dict):
            scope["description"] = (
                "Omit for the whole case, or send both claim_ids and obligation_ids. Two empty "
                "arrays also mean the whole case."
            )
    elif name == "respond-request":
        disposition = properties.get("disposition")
        if isinstance(disposition, dict):
            disposition["description"] = (
                "Acknowledged accepts no waiver fields. Rejected requires reason and accepts no "
                "waiver fields. Waived requires reason and waiver_scope."
            )
    elif name == "status-request":
        filter_property = properties.get("filter")
        if isinstance(filter_property, dict):
            filter_property["description"] = (
                "Fields must match the selected view; omit filter when that view accepts none."
            )


def _referenced_top_level_defs(value: JsonValue) -> set[str]:
    found: set[str] = set()

    def visit(candidate: JsonValue) -> None:
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            ref = mapping.get("$ref")
            if type(ref) is str and ref.startswith("#/$defs/"):
                remainder = ref[len("#/$defs/") :]
                found.add(remainder.split("/", 1)[0])
            for item in mapping.values():
                visit(item)
        elif isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            for item in sequence:
                visit(item)

    visit(value)
    return found


@cache
def _mcp_schema(name: str, version: str) -> Mapping[str, JsonValue]:
    """Full catalog bundle used for dual-surface projection checks (not tools/list)."""

    document = schema_document_for(name, version)
    root = document.json_schema
    documents = _external_schema_documents(root)
    bundled = _rewrite_schema_refs(
        root,
        current_uri=document.schema_id,
        root_uri=document.schema_id,
    )
    if not isinstance(bundled, dict):
        raise RuntimeError("mcp_schema_invalid")
    bundled_dict = bundled
    existing_definitions = bundled_dict.get("$defs")
    if existing_definitions is None:
        definitions: dict[str, JsonValue] = {}
        bundled_dict["$defs"] = definitions
    elif isinstance(existing_definitions, Mapping):
        definitions = dict(cast(Mapping[str, JsonValue], existing_definitions))
        bundled_dict["$defs"] = definitions
    else:
        raise RuntimeError("mcp_schema_invalid")
    for uri, external in sorted(documents.items(), key=lambda item: item[0].encode("ascii")):
        definitions[_bundle_key(uri)] = _rewrite_schema_refs(
            external,
            current_uri=uri,
            root_uri=document.schema_id,
        )
    return MappingProxyType(bundled_dict)


@cache
def _mcp_presentation_schema(name: str, version: str) -> Mapping[str, JsonValue]:
    """Agent-facing tools/list projection of a catalog request/result schema."""

    document = schema_document_for(name, version)
    root = _mutable_json(document.json_schema)
    project_ordinary = name == "publish-work-request"
    documents = _external_schema_documents(root, project_ordinary_event_draft=project_ordinary)
    bundled = _rewrite_schema_refs(
        root,
        current_uri=document.schema_id,
        root_uri=document.schema_id,
        inline_uris=_COMMON_INLINE_SCHEMA_IDS,
    )
    if not isinstance(bundled, dict):
        raise RuntimeError("mcp_schema_invalid")
    bundled_dict = bundled
    existing_definitions = bundled_dict.get("$defs")
    if existing_definitions is None:
        definitions: dict[str, JsonValue] = {}
        bundled_dict["$defs"] = definitions
    elif isinstance(existing_definitions, Mapping):
        definitions = dict(cast(Mapping[str, JsonValue], existing_definitions))
        bundled_dict["$defs"] = definitions
    else:
        raise RuntimeError("mcp_schema_invalid")
    for uri, external in sorted(documents.items(), key=lambda item: item[0].encode("ascii")):
        if uri in _COMMON_INLINE_SCHEMA_IDS or uri == _OPAQUE_EVENT_DRAFT_SCHEMA_ID:
            continue
        definitions[_bundle_key(uri)] = _rewrite_schema_refs(
            external,
            current_uri=uri,
            root_uri=document.schema_id,
            inline_uris=_COMMON_INLINE_SCHEMA_IDS,
        )
    for key, body in list(definitions.items()):
        definitions[key] = _inline_nested_local_defs(key, body)
    bundled_dict = _inline_presentation_refs(bundled_dict)
    if name in {"start-request", "respond-request", "status-request"}:
        bundled_dict.pop("allOf", None)
    flattened = _flatten_frontier_conditions(bundled_dict)
    if not isinstance(flattened, dict):
        raise RuntimeError("mcp_schema_invalid")
    bundled_dict = flattened
    _describe_presentation_schema(name, bundled_dict)
    examples = _INPUT_SCHEMA_EXAMPLES.get(name)
    if examples is not None:
        bundled_dict["examples"] = [_mutable_json(item) for item in examples]
    return MappingProxyType(bundled_dict)


def presentation_schema_metrics(schema: Mapping[str, JsonValue]) -> dict[str, int]:
    """Return keyword-budget metrics for one advertised MCP JSON Schema object."""

    oneof_nodes = 0
    oneof_branches = 0
    ref_nodes = 0
    conditional_nodes = 0
    top_level_definitions = schema.get("$defs")
    retained_payload_defs: frozenset[str]
    if isinstance(top_level_definitions, Mapping):
        retained_payload_defs = frozenset(
            cast(Mapping[str, JsonValue], top_level_definitions).keys()
        )
    else:
        retained_payload_defs = frozenset()

    def visit(candidate: JsonValue, *, inside_payload_defs: bool = False) -> int:
        nonlocal conditional_nodes, oneof_nodes, oneof_branches, ref_nodes
        nest = 0
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            ref_key = _local_def_key(mapping.get("$ref"))
            if (
                "$ref" in mapping
                and not inside_payload_defs
                and ref_key not in retained_payload_defs
            ):
                ref_nodes += 1
            if not inside_payload_defs:
                conditional_nodes += sum(
                    keyword in mapping for keyword in ("allOf", "if", "then", "else")
                )
            one_of = mapping.get("oneOf")
            if isinstance(one_of, list):
                oneof_nodes += 1
                oneof_branches += len(cast(list[JsonValue], one_of))
            defs = mapping.get("$defs")
            child_nest = 0
            if isinstance(defs, Mapping):
                child_nest = max(
                    visit(item, inside_payload_defs=True)
                    for item in cast(Mapping[str, JsonValue], defs).values()
                )
                nest = max(nest, child_nest + 1)
            for key, item in mapping.items():
                if key != "$defs":
                    nest = max(
                        nest,
                        visit(item, inside_payload_defs=inside_payload_defs),
                    )
            return nest
        if isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            for item in sequence:
                nest = max(nest, visit(item, inside_payload_defs=inside_payload_defs))
            return nest
        return 0

    defs_nest_depth = visit(schema)
    definitions = schema.get("$defs")
    defs_count = len(definitions) if isinstance(definitions, Mapping) else 0
    encoded = json.dumps(_mutable_json(dict(schema)), ensure_ascii=True, separators=(",", ":"))
    return {
        "oneof_nodes": oneof_nodes,
        "oneof_branches": oneof_branches,
        "ref_nodes": ref_nodes,
        "conditional_nodes": conditional_nodes,
        "defs_count": defs_count,
        "defs_nest_depth": defs_nest_depth,
        "encoded_bytes": len(encoded.encode("utf-8")),
    }


def ordinary_publish_families_in_presentation(
    schema: Mapping[str, JsonValue],
) -> frozenset[str]:
    """Return event family names advertised in a publish_work presentation schema."""

    families: set[str] = set()

    def visit(candidate: JsonValue) -> None:
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            one_of = mapping.get("oneOf")
            if isinstance(one_of, list):
                for branch in cast(list[JsonValue], one_of):
                    if not isinstance(branch, Mapping):
                        continue
                    family = _event_family_from_draft_branch(cast(Mapping[str, JsonValue], branch))
                    if family is not None:
                        families.add(family)
            for item in mapping.values():
                visit(item)
        elif isinstance(candidate, tuple | list):
            for item in cast(tuple[JsonValue, ...] | list[JsonValue], candidate):
                visit(item)

    visit(schema)
    return frozenset(families)


@dataclass(frozen=True, slots=True)
class ToolAnnotations:
    """The exact MCP annotation hints for one public operation."""

    read_only: bool
    idempotent: bool
    destructive: bool = False
    open_world: bool = False

    def as_mcp_dict(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Static identity, text, schemas, and annotations for one MCP tool."""

    name: str
    title: str
    description: str
    input_schema_ref: str
    output_schema_ref: str
    annotations: ToolAnnotations

    @property
    def input_schema(self) -> Mapping[str, JsonValue]:
        return _mcp_presentation_schema(f"{self.name.replace('_', '-')}-request", _SCHEMA_VERSION)

    @property
    def output_schema(self) -> Mapping[str, JsonValue]:
        return _mcp_schema(f"{self.name.replace('_', '-')}-result", _SCHEMA_VERSION)

    @property
    def catalog_input_schema(self) -> Mapping[str, JsonValue]:
        """Full catalog-bundled input schema (admission dual-surface; not tools/list)."""

        return _mcp_schema(f"{self.name.replace('_', '-')}-request", _SCHEMA_VERSION)


def _descriptor(
    name: str,
    title: str,
    description: str,
    *,
    read_only: bool,
    idempotent: bool,
    open_world: bool = False,
) -> ToolDescriptor:
    schema_name = name.replace("_", "-")
    return ToolDescriptor(
        name=name,
        title=title,
        description=description,
        input_schema_ref=(
            f"{SCHEMA_NAMESPACE}operations/{schema_name}-request-{_SCHEMA_VERSION}.schema.json"
        ),
        output_schema_ref=(
            f"{SCHEMA_NAMESPACE}operations/{schema_name}-result-{_SCHEMA_VERSION}.schema.json"
        ),
        annotations=ToolAnnotations(
            read_only=read_only,
            idempotent=idempotent,
            open_world=open_world,
        ),
    )


_POLICY_TOOL_DESCRIPTORS: Final = (
    _descriptor(
        "start",
        "Start or resume a work session",
        "Call for material multi-step, delegated, resumable, or verification-heavy work before "
        "substantive work; skip trivial questions or edits. Records or resumes a cooperative work "
        "session and returns its compact record. It does not show that work outside the published "
        "record occurred. Every request_id across these tools is a fresh req_ prefixed random "
        "UUID, and workspace_ref and external_ref are admitted only as a pair. Call it once per "
        "task. task_title and requested_view are required. Attach selectors are exactly one of: "
        "(1) session_id for the session you hold, or "
        "(2) workspace_ref + external_ref as a pair with no session_id — mode=create_or_attach "
        "creates on first use and attaches on later conversations. task_id is not an accepted "
        "field. workspace_ref is the stable project identity (remote URL or absolute repository "
        "root); external_ref is the stable task identity within that project (branch, issue, or "
        "plan slug). Both refs are redacted one-shot values; only HMAC commitments are persisted, "
        "so do not self-censor into unstable refs. Author the request from this input schema plus "
        "the guidance below, never from memory. Guidance: yoetz://guidance/workflow.md.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "publish_work",
        "Publish recorded work",
        "Records a bounded batch of agent-published work events and returns the accepted event "
        "range and coverage. It has no information about work outside that batch. Each draft "
        "occurred_at is a caller-asserted RFC 3339 UTC time with millisecond precision: use the "
        "best real time available and do not copy the illustrative example timestamp. Ledger order "
        "follows ingestion sequence; receipt freshness is frontier-bound. Service accepted_at is "
        "independent acceptance metadata, not a freshness or ordering key. Set dry_run true to "
        "validate a batch and preview what would be accepted without appending; the preview is not "
        "evidential and is not citable as a check, publication, or coverage source. After "
        "publishing the material claim and evidence, call check, disposition any findings with "
        "respond, then call receipt before claiming completion. Every reference list, in a draft "
        "envelope and in a payload alike, is admitted only when its members are unique and already "
        "in ascending ASCII order; a rejection names the owning field. Cadence: one batch per "
        "material transition, usually one to eight events and never one batch per file, per tool "
        "call, or per message; a batch admits up to 100 drafts, so keep one transition together "
        "rather than splitting it. Reading, searching, formatting, and unchanged state are not "
        "publishable. Guidance: yoetz://guidance/publication-policy.md.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "check",
        "Check recorded work",
        "Runs the requested recorded-work checks and records the result; it returns at most "
        "max_findings findings plus a suppressed count, and status with view=findings reads the "
        "rest. A no_issue_detected verdict does not mean the work is correct. Choose mode "
        "deliberately: semantic_if_configured for most material implementation or review claims; "
        "semantic_required when the claim depends on qualitative correctness, design conformance, "
        "security or privacy reasoning, interoperability, or whether the code satisfies the ask; "
        "deterministic_only only for explicitly local or structural checks, a semantic-disabled "
        "policy, or a deliberate no-egress choice, and then disclose that limitation. Omitting "
        "mode resolves through the configured verification policy. This call cannot widen privacy "
        "authority: an active semantic route was selected by the owner during setup as a bounded "
        "standing policy, and check cannot change its route, workspace, scope, categories, "
        "retention ceiling, or credential authority. Whether a case is dispatched stays enforced "
        "by the installed route binding and privacy policy. Omit scope for the whole case, "
        "or send both claim_ids and obligation_ids as arrays of unique ids; two empty arrays also "
        "mean the whole case, and sending only one of the two keys is rejected. Call it after "
        "publishing the completion claim and its evidence, and again after any material edit, "
        "new evidence, or "
        "finding response; a check with no new events since the last one adds nothing. "
        "awaiting_human is the one nonterminal result: it carries a continuation with the pending "
        "id, its expiry, and the exact command to run. Show that command, do not create a new "
        "check request, do not inspect Yoetz storage or source, and replay this same request with "
        "the same request_id after the decision. Semantic "
        "review that does not succeed is a coverage gap rather than a retry problem: "
        "not_configured, blocked_by_policy, and human_denied will not change without owner "
        "action; unavailable and timeout already spent that job's own attempt budget; refused, "
        "invalid, and failed are not retried inside the job at all. When a second job in one "
        "session again returns no judgment, stop requesting semantic review, run "
        "deterministic_only, and disclose the gap with the recorded status and reason. Guidance: "
        "yoetz://guidance/coverage-and-receipts.md.",
        read_only=False,
        idempotent=True,
        open_world=True,
    ),
    _descriptor(
        "respond",
        "Respond to a finding",
        "Records an acknowledgement, rejection, or bounded waiver for one finding at its recorded "
        "frontier. It does not resolve other findings or establish that underlying work changed. "
        "No disposition clears the finding it answers: every actionable finding recorded in a task "
        "keeps the receipt conclusion at unresolved_findings_remain, so repair the record and word "
        "the conclusion accordingly rather than calling the finding resolved. Call it once per "
        "finding, then recheck. Guidance: yoetz://guidance/publication-policy.md.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "status",
        "Read recorded status",
        "Reads one bounded, paginated view: advice, assignment, candidate_findings, compact, "
        "evidence, findings, history, obligations, operation, or versions. Advice items carry a "
        "recommended_next_action. Call it when uncertain what you already did or committed to, "
        "rather than reconstructing from memory. view=history returns each event's caller-asserted "
        "occurred_at beside the service-stamped accepted_at; order follows ingestion sequence. "
        "view=operation takes filter.operation_request_id and returns that operation's stored "
        "result for recovery without resending the body. view=findings reads recorded findings; "
        "view=candidate_findings returns unrecorded deterministic candidates without verdicts or "
        "IDs. Read closure_readiness on any result before spending a check or a receipt: it names "
        "the open obligations, unresolved findings, and declared gaps that currently bound a "
        "conclusion. Call it after a resume, a compaction, or a delegate handoff, and before a "
        "completion claim, rather than between routine tool calls. Guidance: "
        "yoetz://guidance/workflow.md.",
        read_only=True,
        idempotent=True,
    ),
    _descriptor(
        "receipt",
        "Record and read a receipt",
        "Records and returns a receipt of the recorded conclusion and coverage limitations at one "
        "frontier. It does not establish correctness beyond that recorded coverage. Prefer format "
        "markdown or text; json is an owner-export format that stricter agent-context policies may "
        "block. Call it once at the end, and again only if material state changed since the "
        "previous receipt. Keep the final answer no stronger than this receipt's weakest material "
        "coverage, freshness, unresolved findings, and limitations. Guidance: "
        "yoetz://guidance/coverage-and-receipts.md.",
        read_only=False,
        idempotent=True,
    ),
)

_STRICT_CHECK_DESCRIPTION_SUFFIX: Final = (
    " Under the strict route profile, this route will not request external semantic review."
)
_STRICT_TOOL_DESCRIPTORS: Final = tuple(
    replace(
        descriptor,
        description=descriptor.description + _STRICT_CHECK_DESCRIPTION_SUFFIX,
        annotations=replace(descriptor.annotations, open_world=False),
    )
    if descriptor.name == "check"
    else descriptor
    for descriptor in _POLICY_TOOL_DESCRIPTORS
)
TOOL_DESCRIPTORS: Final[Mapping[McpRouteProfile, tuple[ToolDescriptor, ...]]] = MappingProxyType(
    {
        "policy": _POLICY_TOOL_DESCRIPTORS,
        "strict": _STRICT_TOOL_DESCRIPTORS,
    }
)


def _canonical_descriptor_bytes(descriptor: ToolDescriptor) -> bytes:
    return json.dumps(
        {
            "annotations": descriptor.annotations.as_mcp_dict(),
            "description": descriptor.description,
            "input_schema_ref": descriptor.input_schema_ref,
            "name": descriptor.name,
            "output_schema_ref": descriptor.output_schema_ref,
            "title": descriptor.title,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest_descriptor(descriptor: ToolDescriptor) -> str:
    return "sha256:" + hashlib.sha256(_canonical_descriptor_bytes(descriptor)).hexdigest()


# These are reviewed golden identities, not values supplied by a host or environment.
TOOL_DESCRIPTOR_DIGESTS: Final[Mapping[McpRouteProfile, Mapping[str, str]]] = MappingProxyType(
    {
        "policy": MappingProxyType(
            {
                "start": "sha256:44ba40c96180d4e1f69e3a3044c635ff311a632d6b413f441fc5d36b098c9b6d",
                "publish_work": "sha256:e29c8b514d8eeab7efdc4d7b16181f766d45824f91b6960eb6c93ff0a9071d34",
                "check": "sha256:cdd4c9e9910ee1cb43f98620bc6ae01b900cc19adca141ad8692e5338e545769",
                "respond": "sha256:7af2775e5204a902a116eefb24e4588eb66645df39f6748f22975ba44a7896e6",
                "status": "sha256:f50314514f180a19f912662e191fec7880e2e41a6fc8dd475a063c2263eafa61",
                "receipt": "sha256:b5b2429e478f7e1fd68edd1ade7a90cd572592278f2baeea693f8a97d82200fa",
            }
        ),
        "strict": MappingProxyType(
            {
                "start": "sha256:44ba40c96180d4e1f69e3a3044c635ff311a632d6b413f441fc5d36b098c9b6d",
                "publish_work": "sha256:e29c8b514d8eeab7efdc4d7b16181f766d45824f91b6960eb6c93ff0a9071d34",
                "check": "sha256:bbec4681f3ec06f5e44b81a9cc3f0f6a089bc2ff39c610928b40f7153ea93db4",
                "respond": "sha256:7af2775e5204a902a116eefb24e4588eb66645df39f6748f22975ba44a7896e6",
                "status": "sha256:f50314514f180a19f912662e191fec7880e2e41a6fc8dd475a063c2263eafa61",
                "receipt": "sha256:b5b2429e478f7e1fd68edd1ade7a90cd572592278f2baeea693f8a97d82200fa",
            }
        ),
    }
)
TOOL_DESCRIPTOR_SET_DIGEST: Final[Mapping[McpRouteProfile, str]] = MappingProxyType(
    {
        "policy": "sha256:ff9b2dc64ccb6d3a0a12119688d3a447b11b22db9fe4fa5655e4e723017ec1c8",
        "strict": "sha256:35f00bbf132d2e85b042b53969c326684746dd1efa99dd70d122f21cc30bf007",
    }
)


def _presentation_description_strings(schema: Mapping[str, JsonValue]) -> tuple[str, ...]:
    found: list[str] = []

    def visit(candidate: JsonValue) -> None:
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            description = mapping.get("description")
            if type(description) is str:
                found.append(description)
            for item in mapping.values():
                visit(item)
        elif isinstance(candidate, tuple | list):
            for item in cast(tuple[JsonValue, ...] | list[JsonValue], candidate):
                visit(item)

    visit(schema)
    return tuple(found)


def _lint_descriptor_sets() -> None:
    for profile, descriptors in TOOL_DESCRIPTORS.items():
        names = tuple(descriptor.name for descriptor in descriptors)
        if names != ("start", "publish_work", "check", "respond", "status", "receipt"):
            raise RuntimeError("descriptor_registry_invalid")
        if len(set(names)) != len(names):
            raise RuntimeError("descriptor_registry_invalid")
        for descriptor in descriptors:
            if _FORBIDDEN_CLAIMS.search(descriptor.description) is not None:
                raise RuntimeError("descriptor_honesty_lint_failed")
            # Packaged guidance URIs are the one reviewed path exception; strip them before the
            # boundary scan so tool text can name the covering resource without looking like a
            # filesystem or host path.
            without_guidance = _GUIDANCE_URI.sub("yoetz-guidance-resource", descriptor.description)
            if _BOUNDARY_TERMS.search(without_guidance) is not None:
                raise RuntimeError("descriptor_boundary_lint_failed")
            for schema_description in _presentation_description_strings(descriptor.input_schema):
                if _FORBIDDEN_CLAIMS.search(schema_description) is not None:
                    raise RuntimeError("descriptor_honesty_lint_failed")
                without_guidance = _GUIDANCE_URI.sub("yoetz-guidance-resource", schema_description)
                if _BOUNDARY_TERMS.search(without_guidance) is not None:
                    raise RuntimeError("descriptor_boundary_lint_failed")
            if _digest_descriptor(descriptor) != TOOL_DESCRIPTOR_DIGESTS[profile][descriptor.name]:
                raise RuntimeError("descriptor_digest_mismatch")
        set_bytes = b"\n".join(_canonical_descriptor_bytes(item) for item in descriptors)
        if "sha256:" + hashlib.sha256(set_bytes).hexdigest() != TOOL_DESCRIPTOR_SET_DIGEST[profile]:
            raise RuntimeError("descriptor_set_digest_mismatch")


_DESCRIPTOR_BY_NAME: Final[Mapping[McpRouteProfile, Mapping[str, ToolDescriptor]]] = (
    MappingProxyType(
        {
            profile: MappingProxyType({descriptor.name: descriptor for descriptor in descriptors})
            for profile, descriptors in TOOL_DESCRIPTORS.items()
        }
    )
)


def descriptor_for(name: str, profile: McpRouteProfile = "policy") -> ToolDescriptor:
    """Return one exact registered descriptor or fail without echoing its input."""

    if type(name) is not str:
        raise TypeError("tool_descriptor_name_wrong_type")
    if profile not in TOOL_DESCRIPTORS:
        raise ValueError("mcp_route_profile_invalid")
    try:
        return _DESCRIPTOR_BY_NAME[profile][name]
    except KeyError:
        raise KeyError("unregistered_tool_descriptor") from None


def server_instructions(profile: McpRouteProfile = "policy") -> str:
    """Return the manifest-verified initialize instructions as strict UTF-8 text."""

    if profile not in TOOL_DESCRIPTORS:
        raise ValueError("mcp_route_profile_invalid")
    base = read_resource(_INSTRUCTIONS_URI).decode("utf-8", errors="strict").rstrip()
    return (
        f"{base}\n\nRoute profile: {profile}. "
        + (
            "External semantic review follows the configured policy."
            if profile == "policy"
            else "This route will not request external semantic review for this process lifetime."
        )
        + "\n"
    )


_lint_descriptor_sets()

# Eagerly build presentation schemas so import fails closed on projection errors.
for descriptor_set in TOOL_DESCRIPTORS.values():
    for item in descriptor_set:
        _ = item.input_schema
