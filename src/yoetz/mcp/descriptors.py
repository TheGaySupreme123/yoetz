"""Frozen, honesty-bounded MCP tool descriptors and initialize instructions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Final, cast

from yoetz.mcp.resources import read_resource
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.schemas import SCHEMA_NAMESPACE, load_schema_catalog, schema_document_for

__all__ = [
    "ORDINARY_MCP_PUBLISH_EVENT_FAMILIES",
    "PRESENTATION_INPUT_SCHEMA_BUDGETS",
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

_SCHEMA_VERSION: Final = "1.0.0"
_INSTRUCTIONS_URI: Final = "yoetz://guidance/agent-instructions.md"
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
                "max_defs_count": 8,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 4_000,
            }
        ),
        "publish-work-request": MappingProxyType(
            {
                "max_oneof_nodes": 8,
                "max_oneof_branches": 28,
                "max_defs_count": 20,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 28_000,
            }
        ),
        "check-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_defs_count": 12,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 8_000,
            }
        ),
        "respond-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
                "max_defs_count": 12,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 8_000,
            }
        ),
        "status-request": MappingProxyType(
            {
                "max_oneof_nodes": 4,
                "max_oneof_branches": 8,
                "max_defs_count": 20,
                "max_defs_nest_depth": 1,
                "max_encoded_bytes": 10_000,
            }
        ),
        "receipt-request": MappingProxyType(
            {
                "max_oneof_nodes": 0,
                "max_oneof_branches": 0,
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
    }
    return f"{prefixes[kind]}_00000000-0000-4000-8000-{seed:012d}"


_EXAMPLE_ACTOR: Final[dict[str, JsonValue]] = {
    "actor_id": "harness:mcp-example",
    "actor_type": "harness",
}
_EXAMPLE_CLIENT: Final[dict[str, JsonValue]] = {
    "kind": "cooperative_agent",
    "version": "0.1.0",
    "integration": "cooperative_mcp",
}

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
        "publish-work-request": (
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": _example_id("request", 3),
                "session_id": _example_id("session", 1),
                "writer_id": _example_id("writer", 1),
                "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
                "event_drafts": [
                    {
                        "event_id": _example_id("event", 1),
                        "schema": {"name": "plan_published", "version": "1.0.0"},
                        "occurred_at": "2026-01-01T00:00:00.000Z",
                        "causal_parents": [],
                        "payload": {
                            "plan_version": 1,
                            "summary": "Initial plan",
                            "obligation_refs": [],
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    }
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
            if (
                type(ref) is str
                and ref.startswith(prefix)
                and set(mapping.keys()) == {"$ref"}
                and "/" not in ref[len(prefix) :]
            ):
                child_key = ref[len(prefix) :]
                target = nested_defs.get(child_key)
                if target is None:
                    raise RuntimeError("mcp_schema_nested_def_missing")
                return resolve(_mutable_json(target))
            return {key: resolve(item) for key, item in mapping.items() if key != "$defs"}
        if isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            return [resolve(item) for item in sequence]
        return candidate

    return resolve(source)


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


def _prune_unreferenced_defs(schema: dict[str, JsonValue]) -> None:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    while True:
        referenced = _referenced_top_level_defs(schema)
        unused = [key for key in definitions if key not in referenced]
        if not unused:
            return
        for key in unused:
            del definitions[key]


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
    _prune_unreferenced_defs(bundled_dict)
    examples = _INPUT_SCHEMA_EXAMPLES.get(name)
    if examples is not None:
        bundled_dict["examples"] = [_mutable_json(item) for item in examples]
    return MappingProxyType(bundled_dict)


def presentation_schema_metrics(schema: Mapping[str, JsonValue]) -> dict[str, int]:
    """Return keyword-budget metrics for one advertised MCP JSON Schema object."""

    oneof_nodes = 0
    oneof_branches = 0

    def visit(candidate: JsonValue) -> int:
        nonlocal oneof_nodes, oneof_branches
        nest = 0
        if isinstance(candidate, Mapping):
            mapping = cast(Mapping[str, JsonValue], candidate)
            one_of = mapping.get("oneOf")
            if isinstance(one_of, list):
                oneof_nodes += 1
                oneof_branches += len(cast(list[JsonValue], one_of))
            defs = mapping.get("$defs")
            child_nest = 0
            if isinstance(defs, Mapping):
                child_nest = max(
                    visit(item) for item in cast(Mapping[str, JsonValue], defs).values()
                )
                nest = max(nest, child_nest + 1)
            for key, item in mapping.items():
                if key != "$defs":
                    nest = max(nest, visit(item))
            return nest
        if isinstance(candidate, tuple | list):
            sequence = cast(tuple[JsonValue, ...] | list[JsonValue], candidate)
            for item in sequence:
                nest = max(nest, visit(item))
            return nest
        return 0

    defs_nest_depth = visit(schema)
    definitions = schema.get("$defs")
    defs_count = len(definitions) if isinstance(definitions, Mapping) else 0
    encoded = json.dumps(_mutable_json(dict(schema)), ensure_ascii=True, separators=(",", ":"))
    return {
        "oneof_nodes": oneof_nodes,
        "oneof_branches": oneof_branches,
        "defs_count": defs_count,
        "defs_nest_depth": defs_nest_depth,
        "encoded_bytes": len(encoded.encode("utf-8")),
    }


def ordinary_publish_families_in_presentation(
    schema: Mapping[str, JsonValue],
) -> frozenset[str]:
    """Return event family names advertised in a publish_work presentation schema."""

    families: set[str] = set()
    definitions = schema.get("$defs")
    if not isinstance(definitions, Mapping):
        return frozenset()
    for body in cast(Mapping[str, JsonValue], definitions).values():
        if not isinstance(body, Mapping):
            continue
        one_of = cast(Mapping[str, JsonValue], body).get("oneOf")
        if not isinstance(one_of, list):
            continue
        for branch in cast(list[JsonValue], one_of):
            if not isinstance(branch, Mapping):
                continue
            family = _event_family_from_draft_branch(cast(Mapping[str, JsonValue], branch))
            if family is not None:
                families.add(family)
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
        annotations=ToolAnnotations(read_only=read_only, idempotent=idempotent),
    )


TOOL_DESCRIPTORS: Final = (
    _descriptor(
        "start",
        "Start or resume a work session",
        "Call for material multi-step, delegated, resumable, or verification-heavy work before "
        "substantive work; skip trivial questions or edits. Records or resumes a cooperative work "
        "session and returns its compact record. It does not show that work outside the published "
        "record occurred.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "publish_work",
        "Publish recorded work",
        "Records a bounded batch of agent-published work events and returns the accepted event "
        "range and coverage. It has no information about work outside that batch. After publishing "
        "the material claim and evidence, call check before claiming completion.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "check",
        "Check recorded work",
        "Runs the requested recorded-work checks and records the result; it returns at most "
        "max_findings findings plus a suppressed count, and status with view=findings reads the "
        "rest. A no_issue_detected verdict does not mean the work is correct.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "respond",
        "Respond to a finding",
        "Records an acknowledgement, rejection, or bounded waiver for one finding at its recorded "
        "frontier. It does not resolve other findings or establish that underlying work changed.",
        read_only=False,
        idempotent=True,
    ),
    _descriptor(
        "status",
        "Read recorded status",
        "Reads one bounded, paginated view: advice, assignment, candidate_findings, compact, "
        "evidence, findings, history, obligations, or versions. Advice items carry a "
        "recommended_next_action. Call it when uncertain what you already did or committed to, "
        "rather than reconstructing from memory. view=findings reads recorded findings; "
        "view=candidate_findings returns unrecorded deterministic candidates without verdicts or "
        "IDs.",
        read_only=True,
        idempotent=True,
    ),
    _descriptor(
        "receipt",
        "Record and read a receipt",
        "Records and returns a receipt of the recorded conclusion and coverage limitations at one "
        "frontier. It does not establish correctness beyond that recorded coverage.",
        read_only=False,
        idempotent=True,
    ),
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
TOOL_DESCRIPTOR_DIGESTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "start": "sha256:42509100525d5c866aa21c02cfa33942163967f79968ef1c7c7e00e15fb0e696",
        "publish_work": "sha256:9bcecb769503844f9a0740ed171574afbf4f4e3f2bdd1d7061150bc3ffbeb819",
        "check": "sha256:bd78bde8d0586896318534abcc248aa8d87f30ff4952046593549dc57f394500",
        "respond": "sha256:740e576f822636bdcdf4f246a86192a336e7d0284aae611bbc6421ee62ed469a",
        "status": "sha256:99b92f8092623c90f9706f0427f4f81e1cc5f4532571e197344b088e1855351e",
        "receipt": "sha256:75a8a26a45689c4d0fec54ee20784eda43096b8726fd59f924d599f4bd27d095",
    }
)
TOOL_DESCRIPTOR_SET_DIGEST: Final = (
    "sha256:4107e839aa5002347c7da5820733a37f30dd62ce838d68ef31fc95693ef7267f"
)


def _lint_descriptor_set() -> None:
    names = tuple(descriptor.name for descriptor in TOOL_DESCRIPTORS)
    if names != ("start", "publish_work", "check", "respond", "status", "receipt"):
        raise RuntimeError("descriptor_registry_invalid")
    if len(set(names)) != len(names):
        raise RuntimeError("descriptor_registry_invalid")
    for descriptor in TOOL_DESCRIPTORS:
        if _FORBIDDEN_CLAIMS.search(descriptor.description) is not None:
            raise RuntimeError("descriptor_honesty_lint_failed")
        if _BOUNDARY_TERMS.search(descriptor.description) is not None:
            raise RuntimeError("descriptor_boundary_lint_failed")
        if _digest_descriptor(descriptor) != TOOL_DESCRIPTOR_DIGESTS[descriptor.name]:
            raise RuntimeError("descriptor_digest_mismatch")
    set_bytes = b"\n".join(_canonical_descriptor_bytes(item) for item in TOOL_DESCRIPTORS)
    if "sha256:" + hashlib.sha256(set_bytes).hexdigest() != TOOL_DESCRIPTOR_SET_DIGEST:
        raise RuntimeError("descriptor_set_digest_mismatch")


_DESCRIPTOR_BY_NAME: Final[Mapping[str, ToolDescriptor]] = MappingProxyType(
    {descriptor.name: descriptor for descriptor in TOOL_DESCRIPTORS}
)


def descriptor_for(name: str) -> ToolDescriptor:
    """Return one exact registered descriptor or fail without echoing its input."""

    if type(name) is not str:
        raise TypeError("tool_descriptor_name_wrong_type")
    try:
        return _DESCRIPTOR_BY_NAME[name]
    except KeyError:
        raise KeyError("unregistered_tool_descriptor") from None


def server_instructions() -> str:
    """Return the manifest-verified initialize instructions as strict UTF-8 text."""

    return read_resource(_INSTRUCTIONS_URI).decode("utf-8", errors="strict")


_lint_descriptor_set()

# Eagerly build presentation schemas so import fails closed on projection errors.
for item in TOOL_DESCRIPTORS:
    _ = item.input_schema
