"""Deterministic public JSON Schema generator and parity checker for the Yoetz protocol.

Generates and verifies the reviewable JSON Schema files for the public six-operation
request/result models, the read-only guidance tool, shared common values, durable event
payloads, configuration, findings,
receipts, privacy, local-control, and version-report contracts. This is a repository maintainer
tool, not runtime code: installed users never run it. It never imports application, adapter, CLI,
MCP, provider, key, storage, or package-resource modules; model discovery is an explicit ordered
registry, not module walking.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from pydantic import TypeAdapter
from pydantic_core import core_schema

from yoetz.protocol.canonical import JsonValue, canonical_encode

__all__ = [
    "SCHEMA_NAMESPACE",
    "SchemaDiff",
    "SchemaDocument",
    "build_schema_documents",
    "compare_tree",
    "main",
    "render_schema",
    "validate_schema_document",
    "write_tree",
]


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCHEMA_NAMESPACE: Final = "https://schemas.yoetz.dev/0.1/"
_DRAFT_2020_12: Final = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_MEDIA_TYPE: Final = "application/schema+json"
_MAX_OUTPUT_BYTES: Final = 20_000_000


class SchemaGenerationError(Exception):
    """A bounded, traceback-free failure while building the frozen schema registry."""

    def __init__(self, reason: str, *, entries: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.entries = entries


@dataclass(frozen=True, slots=True)
class SchemaDocument:
    schema_kind: str
    artifact_role: str
    schema_name: str
    schema_version: str
    schema_id: str
    relative_path: str
    canonical_digest: str
    schema_bytes: bytes
    json_schema: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not (self.missing or self.extra or self.changed)


# --------------------------------------------------------------------------
# Custom-type shims for schema generation only (no source-file changes)
# --------------------------------------------------------------------------


def _install_type_shims() -> None:
    """Teach pydantic how to introspect two hand-rolled immutable domain types.

    ``JsonObject`` (an immutable ``Mapping[str, JsonValue]``) and ``ControlError`` (a bounded
    exception with two typed attributes) are not pydantic-native. Rather than modify their owning
    modules, the generator attaches ``__get_pydantic_core_schema__`` to the already-imported class
    objects for the duration of this process only.
    """

    from yoetz.domain.values import JsonObject
    from yoetz.ports.control import ControlError

    def _json_object_schema(cls: type, source: type, handler: object) -> core_schema.CoreSchema:
        return core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())

    def _control_error_schema(cls: type, source: type, handler: object) -> core_schema.CoreSchema:
        return core_schema.typed_dict_schema(
            {
                "reason": core_schema.typed_dict_field(core_schema.str_schema()),
                "retryable": core_schema.typed_dict_field(core_schema.bool_schema()),
                # Optional; present when the service already minted a diagnostic identity.
                "correlation_id": core_schema.typed_dict_field(
                    core_schema.str_schema(), required=False
                ),
            }
        )

    JsonObject.__get_pydantic_core_schema__ = classmethod(_json_object_schema)  # type: ignore[attr-defined]
    ControlError.__get_pydantic_core_schema__ = classmethod(_control_error_schema)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RegistryEntry:
    relative_path: str
    schema_name: str
    schema_version: str
    schema_kind: str
    artifact_role: str
    loader: Callable[[], object] | None
    """Zero-arg callable returning the Python type/type-alias to introspect via ``TypeAdapter``.

    ``None`` marks a registry entry whose owning Python type is not yet materialized by another
    build wave (tracked explicitly rather than guessed); ``build_schema_documents`` reports these
    as a bounded, named ``SchemaGenerationError`` instead of fabricating a schema.
    """


def _operation_result_schema() -> object:
    from yoetz.protocol.models import OperationFailureModel

    return OperationFailureModel


def _version_manifest_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Update the reviewed closed version schema from current runtime inventory constants.

    The version report intentionally has a stronger, finite contract than Pydantic can infer from
    its tuple-backed runtime dataclass. Preserve that reviewed schema shape while regenerating the
    exact request/result version map and resource inventory cardinalities through this owning tool.
    """

    from yoetz.version import build_version_manifest

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/version/version-manifest-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        request_versions = cast(dict[str, JsonValue], definitions["request_result_schema_versions"])
        event_versions = cast(dict[str, JsonValue], definitions["event_schema_versions"])
        resource_counts = cast(dict[str, JsonValue], definitions["resource_counts"])
        resources = cast(dict[str, JsonValue], document["properties"])["resources"]
        if not isinstance(resources, dict):
            raise TypeError
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "version_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    manifest = build_version_manifest()
    version_pairs = dict(manifest.request_result_schema_versions)
    request_versions.clear()
    request_versions.update(
        cast(
            dict[str, JsonValue],
            {
                "additionalProperties": False,
                "maxProperties": len(version_pairs),
                "minProperties": len(version_pairs),
                "properties": {
                    name: {"const": version}
                    for name, version in sorted(
                        version_pairs.items(), key=lambda item: item[0].encode()
                    )
                },
                "required": sorted(version_pairs, key=str.encode),
                "type": "object",
            },
        )
    )
    event_pairs = dict(manifest.event_schema_versions)
    event_versions.clear()
    event_versions.update(
        cast(
            dict[str, JsonValue],
            {
                "additionalProperties": False,
                "maxProperties": len(event_pairs),
                "minProperties": len(event_pairs),
                "properties": {
                    name: {"const": version}
                    for name, version in sorted(
                        event_pairs.items(), key=lambda item: item[0].encode()
                    )
                },
                "required": sorted(event_pairs, key=str.encode),
                "type": "object",
            },
        )
    )

    counts = dict(manifest.resource_counts)
    count_properties: dict[str, JsonValue] = {
        name: {"const": value}
        for name, value in sorted(counts.items(), key=lambda item: item[0].encode())
    }
    resource_counts["properties"] = count_properties
    resource_counts["required"] = cast(list[JsonValue], sorted(counts, key=str.encode))
    total = int(counts["total"])
    resources["maxItems"] = total
    resources["oneOf"] = [{"maxItems": 0}, {"maxItems": total, "minItems": total}]
    properties = cast(dict[str, JsonValue], document["properties"])
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz version manifest {entry.schema_version}"
    properties["schema_version"] = {"const": entry.schema_version}
    for field_name in (
        "application_id",
        "bundle_schema_version",
        "catalog_schema_version",
        "control_protocol_version",
        "egress_receipt_schema_version",
        "engine_version",
        "object_format_version",
        "privacy_classifier_ruleset_version",
        "privacy_policy_schema_version",
        "projection_version",
        "protocol_version",
    ):
        properties[field_name] = {"const": cast(JsonValue, getattr(manifest, field_name))}
    return document


def _frozen_version_manifest_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Preserve the released v2.0 version report while newer reports append."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        return cast(dict[str, JsonValue], json.loads(source.read_bytes()))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "version_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc


def _evidence_payload_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Derive v1.1 from the frozen v1.0 bytes without rewriting historical identity."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/events/evidence-recorded-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        properties = cast(dict[str, JsonValue], document["properties"])
        all_of = cast(list[JsonValue], document["allOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "evidence_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    definitions = cast(dict[str, JsonValue], document.setdefault("$defs", {}))
    definitions.update(
        {
            "evidence_content_availability": {
                "enum": ["captured", "digest_only", "withheld"],
                "type": "string",
            },
            "evidence_digest_provenance": {
                "enum": ["approved_check", "caller_asserted", "import_observed"],
                "type": "string",
            },
            "evidence_digest_subject": {
                "enum": [
                    "approved_check_receipt",
                    "artifact_bytes",
                    "bounded_excerpt",
                    "command_stdout",
                    "import_report",
                    "source_diff",
                    "static_analysis_report",
                    "test_report",
                    "test_stdout",
                ],
                "type": "string",
            },
            "evidence_digest_binding": {
                "additionalProperties": False,
                "properties": {
                    "approval_commitment": {
                        "pattern": "^sha256:[0-9a-f]{64}$",
                        "type": "string",
                    },
                    "approved_check_result_digest": {
                        "pattern": "^sha256:[0-9a-f]{64}$",
                        "type": "string",
                    },
                    "byte_count": {
                        "maximum": 9_007_199_254_740_991,
                        "minimum": 0,
                        "type": "integer",
                    },
                    "content_availability": {"$ref": "#/$defs/evidence_content_availability"},
                    "provenance": {"$ref": "#/$defs/evidence_digest_provenance"},
                    "subject": {"$ref": "#/$defs/evidence_digest_subject"},
                },
                "required": ["subject", "content_availability", "byte_count", "provenance"],
                "type": "object",
            },
        }
    )
    properties["digest_binding"] = {"$ref": "#/$defs/evidence_digest_binding"}

    all_of.extend(
        [
            {
                "if": {"required": ["content_digest"]},
                "then": {"required": ["digest_binding"]},
            },
            {
                "if": {"required": ["digest_binding"]},
                "then": {"required": ["content_digest"]},
            },
            {
                "if": {
                    "properties": {
                        "digest_binding": {
                            "properties": {"content_availability": {"const": "captured"}},
                            "required": ["content_availability"],
                        }
                    },
                    "required": ["digest_binding"],
                },
                "then": {"required": ["captured_object_id"]},
            },
            {
                "if": {
                    "properties": {
                        "digest_binding": {
                            "properties": {
                                "content_availability": {"enum": ["digest_only", "withheld"]}
                            },
                            "required": ["content_availability"],
                        }
                    },
                    "required": ["digest_binding"],
                },
                "then": {"not": {"required": ["captured_object_id"]}},
            },
        ]
    )
    binding = cast(dict[str, JsonValue], definitions["evidence_digest_binding"])
    binding["allOf"] = [
        {
            "if": {
                "properties": {"provenance": {"const": "approved_check"}},
                "required": ["provenance"],
            },
            "then": {"required": ["approval_commitment", "approved_check_result_digest"]},
            "else": {
                "not": {
                    "anyOf": [
                        {"required": ["approval_commitment"]},
                        {"required": ["approved_check_result_digest"]},
                    ]
                }
            },
        },
        {
            "if": {
                "properties": {"subject": {"const": "approved_check_receipt"}},
                "required": ["subject"],
            },
            "then": {
                "properties": {"provenance": {"const": "approved_check"}},
                "required": ["provenance"],
            },
        },
        {
            "if": {
                "properties": {"subject": {"const": "import_report"}},
                "required": ["subject"],
            },
            "then": {
                "properties": {"provenance": {"const": "import_observed"}},
                "required": ["provenance"],
            },
        },
    ]
    compatible = {
        "artifact": ["artifact_bytes", "bounded_excerpt", "source_diff"],
        "command_output": [
            "approved_check_receipt",
            "command_stdout",
            "static_analysis_report",
            "test_report",
            "test_stdout",
        ],
        "test_result": [
            "approved_check_receipt",
            "static_analysis_report",
            "test_report",
            "test_stdout",
        ],
        "research_source": ["artifact_bytes", "bounded_excerpt"],
        "import_report": ["import_report"],
        "other": ["bounded_excerpt"],
    }
    for kind, subjects in compatible.items():
        all_of.append(
            {
                "if": {
                    "properties": {"evidence_kind": {"const": kind}},
                    "required": ["evidence_kind", "digest_binding"],
                },
                "then": {
                    "properties": {
                        "digest_binding": {
                            "properties": {"subject": {"enum": subjects}},
                            "required": ["subject"],
                        }
                    }
                },
            }
        )
    return document


def _claim_payload_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Derive v1.1 correction fields from the frozen v1.0 claim bytes."""

    source = (
        Path(__file__).resolve().parent.parent / "schemas/events/claim-recorded-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        properties = cast(dict[str, JsonValue], document["properties"])
        required = cast(list[JsonValue], document["required"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "claim_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    properties["limitation_refs"] = {
        "items": {
            "pattern": "^res_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            "type": "string",
        },
        "maxItems": 64,
        "type": "array",
        "uniqueItems": True,
    }
    properties["supersedes_claim_refs"] = {
        "items": {
            "pattern": "^clm_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            "type": "string",
        },
        "maxItems": 16,
        "type": "array",
        "uniqueItems": True,
    }
    required.extend(("limitation_refs", "supersedes_claim_refs"))
    required.sort(key=lambda value: cast(str, value).encode("ascii"))
    return document


def _event_draft_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add exact additive 1.1 pairs to the reviewed draft structural union."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        branches = cast(list[JsonValue], document["oneOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    definitions["schema_identity_evidence_recorded_1_1"] = {
        "additionalProperties": False,
        "properties": {
            "name": {"const": "evidence_recorded"},
            "version": {"const": "1.1.0"},
        },
        "required": ["name", "version"],
        "type": "object",
    }
    definitions["evidence_recorded_1_1_schema"] = {
        "$ref": "#/$defs/schema_identity_evidence_recorded_1_1"
    }
    branch: dict[str, JsonValue] = {
        "properties": {
            "payload": {
                "$ref": ("https://schemas.yoetz.dev/0.1/events/evidence-recorded-1.1.0.schema.json")
            },
            "schema": {"$ref": "#/$defs/evidence_recorded_1_1_schema"},
        },
        "required": ["schema", "payload"],
    }
    branches[:] = [
        item
        for item in branches
        if not (
            isinstance(item, dict) and "evidence-recorded-1.1.0.schema.json" in json.dumps(item)
        )
    ]
    legacy_index = next(
        (
            index
            for index, item in enumerate(branches)
            if isinstance(item, dict) and "evidence-recorded-1.0.0.schema.json" in json.dumps(item)
        ),
        None,
    )
    if legacy_index is None:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        )
    branches.insert(legacy_index + 1, branch)
    definitions["schema_identity_claim_recorded_1_1"] = {
        "additionalProperties": False,
        "properties": {
            "name": {"const": "claim_recorded"},
            "version": {"const": "1.1.0"},
        },
        "required": ["name", "version"],
        "type": "object",
    }
    definitions["claim_recorded_1_1_schema"] = {
        "$ref": "#/$defs/schema_identity_claim_recorded_1_1"
    }
    claim_branch: dict[str, JsonValue] = {
        "properties": {
            "payload": {
                "$ref": "https://schemas.yoetz.dev/0.1/events/claim-recorded-1.1.0.schema.json"
            },
            "schema": {"$ref": "#/$defs/claim_recorded_1_1_schema"},
        },
        "required": ["schema", "payload"],
    }
    branches[:] = [
        item
        for item in branches
        if not (isinstance(item, dict) and "claim-recorded-1.1.0.schema.json" in json.dumps(item))
    ]
    claim_legacy_index = next(
        (
            index
            for index, item in enumerate(branches)
            if isinstance(item, dict) and "claim-recorded-1.0.0.schema.json" in json.dumps(item)
        ),
        None,
    )
    if claim_legacy_index is None:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        )
    branches.insert(claim_legacy_index + 1, claim_branch)
    return document


def _opaque_unknown_event_draft_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Exclude every exact-known pair, including additive 1.1 schemas, from opaque drafts."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        unknown = cast(dict[str, JsonValue], definitions["unknown_event_schema"])
        current_not = cast(dict[str, JsonValue], unknown["not"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "opaque_unknown_event_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    legacy = current_not
    if "anyOf" in current_not:
        values = cast(list[JsonValue], current_not["anyOf"])
        legacy = cast(dict[str, JsonValue], values[0])
    unknown["not"] = {
        "anyOf": [
            legacy,
            {
                "additionalProperties": False,
                "properties": {
                    "name": {"const": "evidence_recorded"},
                    "version": {"const": "1.1.0"},
                },
                "required": ["name", "version"],
                "type": "object",
            },
            {
                "additionalProperties": False,
                "properties": {
                    "name": {"const": "claim_recorded"},
                    "version": {"const": "1.1.0"},
                },
                "required": ["name", "version"],
                "type": "object",
            },
        ]
    }
    return document


def _start_result_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed start-result schema with its model-owned authoring scaffold.

    The start result deliberately uses shared external references and finite reviewed value
    shapes that Pydantic's generic projection does not preserve.  Keep that public contract as
    the template, while regenerating the additive projection-only scaffold here so ``--write``
    cannot replace the curated wire shape with framework-specific definitions.
    """

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        success = cast(dict[str, JsonValue], definitions["success"])
        properties = cast(dict[str, JsonValue], success["properties"])
        required = cast(list[JsonValue], success["required"])
        if not all(name in definitions for name in ("compact_view", "version_slice")):
            raise TypeError
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "start_result_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    empty_array: dict[str, JsonValue] = {
        "items": {"const": "", "type": "string"},
        "maxItems": 0,
        "type": "array",
    }

    def event_draft_template(schema_name: str, payload_ref: str) -> dict[str, JsonValue]:
        return {
            "additionalProperties": False,
            "properties": {
                "artifact_refs": dict(empty_array),
                "causal_parents": dict(empty_array),
                "event_id": {"const": "", "type": "string"},
                "evidence_refs": dict(empty_array),
                "occurred_at": {"const": "", "type": "string"},
                "payload": {"$ref": payload_ref},
                "schema": {
                    "additionalProperties": False,
                    "properties": {
                        "name": {"const": schema_name, "type": "string"},
                        "version": {"const": "1.0.0", "type": "string"},
                    },
                    "required": ["name", "version"],
                    "type": "object",
                },
            },
            "required": [
                "artifact_refs",
                "causal_parents",
                "event_id",
                "evidence_refs",
                "occurred_at",
                "payload",
                "schema",
            ],
            "type": "object",
        }

    definitions.update(
        cast(
            dict[str, JsonValue],
            {
                "start_next_request_actor_template": {
                    "additionalProperties": False,
                    "properties": {
                        "actor_id": {"const": "", "type": "string"},
                        "actor_type": {"const": "", "type": "string"},
                    },
                    "required": ["actor_id", "actor_type"],
                    "type": "object",
                },
                "start_next_request_client_template": {
                    "additionalProperties": False,
                    "properties": {
                        "integration": {"const": "", "type": "string"},
                        "kind": {"const": "", "type": "string"},
                        "version": {"const": "", "type": "string"},
                    },
                    "required": ["integration", "kind", "version"],
                    "type": "object",
                },
                "start_next_request_plan_payload_template": {
                    "additionalProperties": False,
                    "properties": {
                        "obligation_refs": {
                            "items": {"const": "", "type": "string"},
                            "maxItems": 1,
                            "minItems": 1,
                            "type": "array",
                        },
                        "plan_version": {"const": 1, "type": "integer"},
                        "summary": {"const": "", "type": "string"},
                    },
                    "required": ["obligation_refs", "plan_version", "summary"],
                    "type": "object",
                },
                "start_next_request_obligation_payload_template": {
                    "additionalProperties": False,
                    "properties": {
                        "acceptance_criteria": {"const": "", "type": "string"},
                        "description": {"const": "", "type": "string"},
                        "evidence_expectation": {"const": "", "type": "string"},
                        "obligation_id": {"const": "", "type": "string"},
                        "status": {"const": "open", "type": "string"},
                    },
                    "required": [
                        "acceptance_criteria",
                        "description",
                        "evidence_expectation",
                        "obligation_id",
                        "status",
                    ],
                    "type": "object",
                },
                "start_next_request_plan_event_draft_template": event_draft_template(
                    "plan_published",
                    "#/$defs/start_next_request_plan_payload_template",
                ),
                "start_next_request_obligation_event_draft_template": event_draft_template(
                    "obligation_published",
                    "#/$defs/start_next_request_obligation_payload_template",
                ),
                "start_publish_work_request_template": {
                    "additionalProperties": False,
                    "properties": {
                        "actor": {"$ref": "#/$defs/start_next_request_actor_template"},
                        "client": {"$ref": "#/$defs/start_next_request_client_template"},
                        "event_drafts": {
                            "items": False,
                            "maxItems": 2,
                            "minItems": 2,
                            "prefixItems": [
                                {"$ref": ("#/$defs/start_next_request_plan_event_draft_template")},
                                {
                                    "$ref": (
                                        "#/$defs/start_next_request_obligation_event_draft_template"
                                    )
                                },
                            ],
                            "type": "array",
                        },
                        "expected_frontier": {
                            "$ref": (
                                "https://schemas.yoetz.dev/0.1/common/frontier-1.0.0.schema.json"
                            )
                        },
                        "protocol_version": {"const": "0.1", "type": "string"},
                        "request_id": {"const": "", "type": "string"},
                        "schema_version": {"const": "1.0.0", "type": "string"},
                        "session_id": {"$ref": "#/$defs/session_id"},
                        "writer_id": {"$ref": "#/$defs/writer_id"},
                    },
                    "required": [
                        "actor",
                        "client",
                        "event_drafts",
                        "expected_frontier",
                        "protocol_version",
                        "request_id",
                        "schema_version",
                        "session_id",
                        "writer_id",
                    ],
                    "type": "object",
                },
                "start_next_request_template": {
                    "additionalProperties": False,
                    "properties": {
                        "arguments": {"$ref": "#/$defs/start_publish_work_request_template"},
                        "evidential": {"const": False, "type": "boolean"},
                        "operation": {"const": "publish_work", "type": "string"},
                    },
                    "required": ["arguments", "evidential", "operation"],
                    "type": "object",
                },
            },
        )
    )
    properties["next_request_template"] = {"$ref": "#/$defs/start_next_request_template"}
    if "next_request_template" not in required:
        required.append("next_request_template")
    compact_view = cast(dict[str, JsonValue], definitions["compact_view"])
    compact_properties = cast(dict[str, JsonValue], compact_view["properties"])
    compact_required = cast(list[JsonValue], compact_view["required"])
    compact_properties["open_obligation_count"] = {
        "oneOf": [
            {"$ref": "#/$defs/safe_count"},
            {"type": "null"},
        ]
    }
    legacy_unanswered = compact_properties.pop("unresolved_finding_count", None)
    if legacy_unanswered is not None:
        compact_properties["unanswered_finding_count"] = legacy_unanswered
    compact_properties["receipt_blocking_finding_count"] = {
        "oneOf": [
            {"$ref": "#/$defs/safe_count"},
            {"type": "null"},
        ]
    }
    if "unresolved_finding_count" in compact_required:
        compact_required[compact_required.index("unresolved_finding_count")] = (
            "unanswered_finding_count"
        )
    if "receipt_blocking_finding_count" not in compact_required:
        unanswered_index = compact_required.index("unanswered_finding_count")
        compact_required.insert(unanswered_index + 1, "receipt_blocking_finding_count")
    return document


def _plan_payload_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add the typed empty-scope declaration to the reviewed plan payload contract.

    The reviewed schemas carry identifier, collection, and conditional constraints that generic
    Pydantic introspection cannot reproduce. Preserve those constraints and make only the additive
    pre-release correction owned by the domain model.
    """

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document.setdefault("$defs", {}))
        properties = cast(dict[str, JsonValue], document["properties"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "plan_payload_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    definitions["no_obligations_reason"] = {
        "enum": [
            "no_material_change",
            "single_atomic_change",
            "exploratory_scope_unknown",
        ],
        "type": "string",
    }
    properties["no_obligations_reason"] = {"$ref": "#/$defs/no_obligations_reason"}
    return document


def _read_guidance_result_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Build the closed read-guidance result: document text or the shared public error."""

    from yoetz.protocol.models import REGISTERED_GUIDANCE_URIS

    raw: dict[str, object] = {
        "$defs": {
            "guidance_resource_uri": {
                "enum": list(REGISTERED_GUIDANCE_URIS),
                "type": "string",
            },
            "success": {
                "additionalProperties": False,
                "properties": {
                    "byte_count": {"maximum": 65536, "minimum": 0, "type": "integer"},
                    "media_type": {"const": "text/markdown", "type": "string"},
                    "ok": {"const": True, "type": "boolean"},
                    "text": {"maxLength": 65536, "minLength": 0, "type": "string"},
                    "uri": {"$ref": "#/$defs/guidance_resource_uri"},
                },
                "required": ["byte_count", "media_type", "ok", "text", "uri"],
                "type": "object",
            },
        },
        "oneOf": [
            {"$ref": "#/$defs/success"},
            {
                "$ref": (
                    f"{SCHEMA_NAMESPACE}common/operation-result-1.0.0.schema.json"
                    "#/$defs/failure_result"
                )
            },
        ],
    }
    return _normalize(raw, entry)


def _publish_work_result_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Preserve the reviewed publish result rather than replacing it with model introspection."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        return cast(dict[str, JsonValue], json.loads(source.read_bytes()))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "publish_work_result_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc


def _status_result_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed status result with model-owned completion and disposition leaves."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/operations/status-result-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        compact = cast(dict[str, JsonValue], definitions["compact_item"])
        compact_properties = cast(dict[str, JsonValue], compact["properties"])
        compact_required = cast(list[JsonValue], compact["required"])
        history = cast(dict[str, JsonValue], definitions["history_item"])
        history_properties = cast(dict[str, JsonValue], history["properties"])
        history_required = cast(list[JsonValue], history["required"])
        finding_item = cast(dict[str, JsonValue], definitions["finding_item"])
        finding_properties = cast(dict[str, JsonValue], finding_item["properties"])
        finding_rules_value = finding_item.setdefault("allOf", [])
        if not isinstance(finding_rules_value, list):
            raise TypeError("finding_item allOf must be an array")
        finding_rules = cast(list[JsonValue], finding_rules_value)
        readiness = cast(dict[str, JsonValue], definitions["closure_readiness"])
        readiness_properties = cast(dict[str, JsonValue], readiness["properties"])
        readiness_required = cast(list[JsonValue], readiness["required"])
        blocking = cast(dict[str, JsonValue], readiness_properties["blocking_conditions"])
        blocking_items = cast(dict[str, JsonValue], blocking["items"])
        blocker_values = cast(list[JsonValue], blocking_items["enum"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "status_result_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    definitions["no_obligations_reason"] = {
        "enum": [
            "no_material_change",
            "single_atomic_change",
            "exploratory_scope_unknown",
        ],
        "type": "string",
    }
    finding_properties["disposition"] = {
        "enum": [
            "acknowledged",
            "none",
            "provenance_disputed",
            "rejected",
            "waived",
        ],
        "type": "string",
    }
    if not any(
        isinstance(rule, dict)
        and isinstance(rule.get("if"), dict)
        and isinstance(cast(dict[str, JsonValue], rule["if"]).get("properties"), dict)
        and cast(
            dict[str, JsonValue],
            cast(dict[str, JsonValue], rule["if"])["properties"],
        ).get("disposition")
        == {"const": "provenance_disputed"}
        for rule in finding_rules
    ):
        finding_rules.append(
            {
                "if": {
                    "properties": {"disposition": {"const": "provenance_disputed"}},
                    "required": ["disposition"],
                },
                "then": {
                    "properties": {
                        "reason": {"not": {"type": "null"}},
                        "resolved": {"const": False},
                        "waiver_expiry": {"type": "null"},
                        "waiver_scope": {"type": "null"},
                    }
                },
            }
        )
    history_properties["occurred_at_consistency"] = {
        "description": (
            "Exact comparison of caller-asserted occurred_at with service accepted_at. Caller "
            "time through five seconds ahead is within_forward_skew_allowance; larger forward "
            "drift is ahead_of_forward_skew_allowance. This does not verify caller time or "
            "affect ingestion-sequence ordering."
        ),
        "enum": [
            "within_forward_skew_allowance",
            "ahead_of_forward_skew_allowance",
        ],
        "type": "string",
    }
    if "occurred_at_consistency" not in history_required:
        accepted_at_index = history_required.index("accepted_at")
        history_required.insert(accepted_at_index + 1, "occurred_at_consistency")
    nullable_count: dict[str, JsonValue] = {
        "oneOf": [
            {"$ref": "#/$defs/canonical_uint"},
            {"type": "null"},
        ]
    }
    nullable_reason: dict[str, JsonValue] = {
        "oneOf": [
            {"$ref": "#/$defs/no_obligations_reason"},
            {"type": "null"},
        ]
    }
    compact_properties["declared_obligation_count"] = nullable_count
    compact_properties["no_obligations_reason"] = nullable_reason
    compact_properties["open_obligation_count"] = nullable_count
    legacy_unanswered_count = compact_properties.pop("unresolved_finding_count", None)
    if legacy_unanswered_count is not None:
        compact_properties["unanswered_finding_count"] = legacy_unanswered_count
    compact_properties["receipt_blocking_finding_count"] = {"$ref": "#/$defs/canonical_uint"}
    legacy_unanswered_items = compact_properties.pop("unresolved_findings", None)
    if legacy_unanswered_items is not None:
        compact_properties["unanswered_findings"] = legacy_unanswered_items
    if "unresolved_finding_count" in compact_required:
        compact_required[compact_required.index("unresolved_finding_count")] = (
            "unanswered_finding_count"
        )
    if "receipt_blocking_finding_count" not in compact_required:
        unanswered_index = compact_required.index("unanswered_finding_count")
        compact_required.insert(unanswered_index + 1, "receipt_blocking_finding_count")
    if "unresolved_findings" in compact_required:
        compact_required[compact_required.index("unresolved_findings")] = "unanswered_findings"
    readiness_properties["declared_obligation_count"] = nullable_count
    readiness_properties["no_obligations_reason"] = nullable_reason
    legacy_readiness_count = readiness_properties.pop("unresolved_finding_count", None)
    if legacy_readiness_count is not None:
        readiness_properties["unanswered_finding_count"] = legacy_readiness_count
    readiness_properties["receipt_blocking_finding_count"] = nullable_count
    if "unresolved_finding_count" in readiness_required:
        readiness_required[readiness_required.index("unresolved_finding_count")] = (
            "unanswered_finding_count"
        )
    if "receipt_blocking_finding_count" not in readiness_required:
        unanswered_index = readiness_required.index("unanswered_finding_count")
        readiness_required.insert(unanswered_index + 1, "receipt_blocking_finding_count")
    for required, names in (
        (compact_required, ("declared_obligation_count", "no_obligations_reason")),
        (readiness_required, ("declared_obligation_count", "no_obligations_reason")),
    ):
        for name in names:
            if name not in required:
                required.append(name)
    if "findings_unresolved" in blocker_values:
        blocker_values.remove("findings_unresolved")
    for blocker in (
        "findings_unanswered",
        "receipt_findings_unresolved",
        "no_obligations_declared",
    ):
        if blocker not in blocker_values:
            blocker_values.append(blocker)
    if entry.schema_version == "1.1.0":
        _extend_status_result_v11(document)
        document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
        document["title"] = "Yoetz status result 1.1.0"
    return document


def _extend_status_result_v11(document: dict[str, JsonValue]) -> None:
    """Add actionable result and requested-item projections to the frozen v1.0 shape."""

    definitions = cast(dict[str, JsonValue], document["$defs"])
    obligation = cast(dict[str, JsonValue], definitions["obligation_item"])
    obligation_properties = cast(dict[str, JsonValue], obligation["properties"])
    success = cast(dict[str, JsonValue], definitions["success"])
    success_properties = cast(dict[str, JsonValue], success["properties"])
    success_view = cast(dict[str, JsonValue], success_properties["view"])
    success_pages = cast(dict[str, JsonValue], success_properties["page"])
    success_page_refs = cast(list[JsonValue], success_pages["anyOf"])
    success_view_rules = cast(list[JsonValue], success["allOf"])

    definitions["action_id"] = {
        "pattern": "^act_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    definitions["result_id"] = {
        "pattern": "^res_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    definitions["requested_item_text"] = {"maxLength": 1024, "type": "string"}
    definitions["status_requested_item"] = {
        "additionalProperties": False,
        "properties": {
            "item_kind": {
                "enum": ["url", "file", "command", "change", "source"],
                "type": "string",
            },
            "value": {
                "oneOf": [
                    {"$ref": "#/$defs/requested_item_text"},
                    {"$ref": "#/$defs/obligation_omission"},
                ]
            },
        },
        "required": ["item_kind", "value"],
        "type": "object",
    }
    requested_item_list: dict[str, JsonValue] = {
        "items": {"$ref": "#/$defs/status_requested_item"},
        "maxItems": 64,
        "type": "array",
    }
    obligation_properties["requested_items"] = requested_item_list
    obligation_properties["unattempted_items"] = requested_item_list
    definitions["result_item"] = {
        "additionalProperties": False,
        "properties": {
            "action_id": {"oneOf": [{"$ref": "#/$defs/action_id"}, {"type": "null"}]},
            "evidence_refs": {
                "items": {"$ref": "#/$defs/evidence_id"},
                "maxItems": 64,
                "type": "array",
                "uniqueItems": True,
            },
            "outcome": {
                "oneOf": [
                    {
                        "enum": ["success", "failure", "partial", "unknown"],
                        "type": "string",
                    },
                    {"type": "null"},
                ]
            },
            "payload_available": {"type": "boolean"},
            "result_id": {"$ref": "#/$defs/result_id"},
            "source_event_id": {"$ref": "#/$defs/event_id"},
        },
        "required": [
            "result_id",
            "source_event_id",
            "payload_available",
            "outcome",
            "action_id",
            "evidence_refs",
        ],
        "type": "object",
    }
    definitions["results_page"] = {
        "additionalProperties": False,
        "properties": {
            "items": {
                "items": {"$ref": "#/$defs/result_item"},
                "maxItems": 100,
                "type": "array",
            },
            "next_cursor": {"$ref": "#/$defs/nullable_cursor"},
        },
        "required": ["items", "next_cursor"],
        "type": "object",
    }
    definitions["view_results"] = {
        "if": {"properties": {"view": {"const": "results"}}, "required": ["view"]},
        "then": {"properties": {"page": {"$ref": "#/$defs/results_page"}}},
    }
    view_values = cast(list[JsonValue], success_view["enum"])
    view_values.insert(view_values.index("versions"), "results")
    success_page_refs.append({"$ref": "#/$defs/results_page"})
    success_view_rules.append({"$ref": "#/$defs/view_results"})


def _receipt_document_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed receipt document with the model-owned response disposition."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        response = cast(dict[str, JsonValue], definitions["receipt_response"])
        response_properties = cast(dict[str, JsonValue], response["properties"])
        disposition = cast(dict[str, JsonValue], response_properties["disposition"])
        rules = cast(list[JsonValue], response["allOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "receipt_document_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    disposition["enum"] = ["acknowledged", "provenance_disputed", "rejected", "waived"]
    has_provenance_rule = False
    for rule_value in rules:
        if not isinstance(rule_value, dict):
            continue
        condition = rule_value.get("if")
        if not isinstance(condition, dict):
            continue
        properties = condition.get("properties")
        if not isinstance(properties, dict):
            continue
        disposition_condition = properties.get("disposition")
        if isinstance(disposition_condition, dict) and disposition_condition.get("const") == (
            "provenance_disputed"
        ):
            has_provenance_rule = True
            break
    if not has_provenance_rule:
        rules.insert(
            1,
            {
                "if": {
                    "properties": {"disposition": {"const": "provenance_disputed"}},
                    "required": ["disposition"],
                },
                "then": {
                    "not": {
                        "anyOf": [
                            {"required": ["waiver_scope"]},
                            {"required": ["waiver_expiry"]},
                        ]
                    },
                    "required": ["reason"],
                },
            },
        )
    return document


def _response_recorded_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed response event with the model-owned response disposition."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        properties = cast(dict[str, JsonValue], document["properties"])
        disposition = cast(dict[str, JsonValue], properties["disposition"])
        rules = cast(list[JsonValue], document["oneOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "response_recorded_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    disposition["enum"] = ["acknowledged", "provenance_disputed", "rejected", "waived"]
    if not any(
        isinstance(rule, dict)
        and isinstance(rule.get("properties"), dict)
        and cast(dict[str, JsonValue], rule["properties"]).get("disposition")
        == {"const": "provenance_disputed"}
        for rule in rules
    ):
        rules.insert(
            1,
            {
                "not": {
                    "anyOf": [
                        {"required": ["waiver_scope"]},
                        {"required": ["waiver_expiry"]},
                    ]
                },
                "properties": {"disposition": {"const": "provenance_disputed"}},
                "required": ["reason"],
            },
        )
    return document


def _insert_provenance_dispute_condition(rules: list[JsonValue]) -> None:
    """Add the shared reason-required, waiver-forbidden branch once."""

    for rule_value in rules:
        if not isinstance(rule_value, dict):
            continue
        condition = rule_value.get("if")
        if not isinstance(condition, dict):
            continue
        properties = condition.get("properties")
        if not isinstance(properties, dict):
            continue
        disposition = properties.get("disposition")
        if isinstance(disposition, dict) and disposition.get("const") == "provenance_disputed":
            return
    rules.insert(
        1,
        {
            "if": {
                "properties": {"disposition": {"const": "provenance_disputed"}},
                "required": ["disposition"],
            },
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["waiver_scope"]},
                        {"required": ["waiver_expiry"]},
                    ]
                },
                "required": ["reason"],
            },
        },
    )


def _respond_request_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed respond request with the model-owned response disposition."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        properties = cast(dict[str, JsonValue], document["properties"])
        disposition = cast(dict[str, JsonValue], properties["disposition"])
        rules = cast(list[JsonValue], document["allOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "respond_request_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    disposition["enum"] = ["acknowledged", "provenance_disputed", "rejected", "waived"]
    _insert_provenance_dispute_condition(rules)
    return document


def _respond_result_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed respond result with the model-owned response disposition."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        response = cast(dict[str, JsonValue], definitions["response"])
        properties = cast(dict[str, JsonValue], response["properties"])
        disposition = cast(dict[str, JsonValue], properties["disposition"])
        rules = cast(list[JsonValue], response["allOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "respond_result_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    disposition["enum"] = ["acknowledged", "provenance_disputed", "rejected", "waived"]
    _insert_provenance_dispute_condition(rules)
    return document


def _status_request_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed status filter with the model-owned response disposition."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/operations/status-request-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        findings_filter = cast(dict[str, JsonValue], definitions["findings_filter"])
        properties = cast(dict[str, JsonValue], findings_filter["properties"])
        disposition = cast(dict[str, JsonValue], properties["disposition"])
        if not isinstance(disposition, dict):
            raise TypeError("disposition must be an object")
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "status_request_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    disposition["enum"] = [
        "acknowledged",
        "none",
        "provenance_disputed",
        "rejected",
        "waived",
    ]
    if entry.schema_version == "1.1.0":
        properties = cast(dict[str, JsonValue], document["properties"])
        view = cast(dict[str, JsonValue], properties["view"])
        view_values = cast(list[JsonValue], view["enum"])
        view_values.insert(view_values.index("versions"), "results")
        rules = cast(list[JsonValue], document["allOf"])
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            condition = rule.get("if")
            if not isinstance(condition, dict):
                continue
            condition_properties = condition.get("properties")
            if not isinstance(condition_properties, dict):
                continue
            view_condition = condition_properties.get("view")
            if not isinstance(view_condition, dict):
                continue
            no_filter_values = view_condition.get("enum")
            if isinstance(no_filter_values, list) and "compact" in no_filter_values:
                no_filter_values.append("results")
                break
        document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
        document["title"] = "Yoetz status request 1.1.0"
    return document


_REGISTRY: Final[tuple[_RegistryEntry, ...]] = (
    _RegistryEntry(
        "common/actor-assertion-1.0.0.schema.json",
        "actor-assertion",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["ActorAssertionModel"]
            ).ActorAssertionModel
        ),
    ),
    _RegistryEntry(
        "common/client-info-1.0.0.schema.json",
        "client-info",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.protocol.models", fromlist=["ClientInfoModel"]).ClientInfoModel,
    ),
    _RegistryEntry(
        "common/coverage-1.0.0.schema.json",
        "coverage",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.protocol.coverage", fromlist=["Coverage"]).Coverage,
    ),
    _RegistryEntry(
        "common/frontier-1.0.0.schema.json",
        "frontier",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.domain.values", fromlist=["Frontier"]).Frontier,
    ),
    _RegistryEntry(
        "common/operation-result-1.0.0.schema.json",
        "operation-result",
        "1.0.0",
        "request_result",
        "MCP output",
        _operation_result_schema,
    ),
    _RegistryEntry(
        "common/public-error-1.0.0.schema.json",
        "public-error",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: (
            __import__(
                "yoetz.protocol.errors", fromlist=["PublicOperationError"]
            ).PublicOperationError
        ),
    ),
    _RegistryEntry(
        "common/subject-state-ref-1.0.0.schema.json",
        "subject-state-ref",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.domain.values", fromlist=["SubjectStateRef"]).SubjectStateRef,
    ),
    _RegistryEntry(
        "config/yoetz-config-1.0.0.schema.json",
        "yoetz-config",
        "1.0.0",
        "config",
        "configuration",
        lambda: __import__("yoetz.config.models", fromlist=["YoetzConfig"]).YoetzConfig,
    ),
    _RegistryEntry(
        "consent/catalog-2.0.0.schema.json",
        "catalog",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/catalog-3.0.0.schema.json",
        "catalog",
        "3.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/catalog-4.0.0.schema.json",
        "catalog",
        "4.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentCatalogModel"]
            ).ConsentCatalogModel
        ),
    ),
    _RegistryEntry(
        "consent/chat-user-attestation-1.0.0.schema.json",
        "chat-user-attestation",
        "1.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.chat_user_authority", fromlist=["ChatUserAttestationModel"]
            ).ChatUserAttestationModel
        ),
    ),
    _RegistryEntry(
        "consent/pending-agent-2.0.0.schema.json",
        "pending-agent",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/pending-agent-3.0.0.schema.json",
        "pending-agent",
        "3.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/pending-agent-4.0.0.schema.json",
        "pending-agent",
        "4.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["AgentSafePendingModel"]
            ).AgentSafePendingModel
        ),
    ),
    _RegistryEntry(
        "consent/prepare-result-2.0.0.schema.json",
        "prepare-result",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/prepare-result-3.0.0.schema.json",
        "prepare-result",
        "3.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/prepare-result-4.0.0.schema.json",
        "prepare-result",
        "4.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentPrepareResultModel"]
            ).ConsentPrepareResultModel
        ),
    ),
    _RegistryEntry(
        "consent/review-result-2.0.0.schema.json",
        "review-result",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/review-result-3.0.0.schema.json",
        "review-result",
        "3.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/review-result-4.0.0.schema.json",
        "review-result",
        "4.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentReviewResultModel"]
            ).ConsentReviewResultModel
        ),
    ),
    _RegistryEntry(
        "consent/status-2.0.0.schema.json",
        "status",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/status-3.0.0.schema.json",
        "status",
        "3.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/status-4.0.0.schema.json",
        "status",
        "4.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__("yoetz.protocol.consent", fromlist=["ConsentStatusModel"]).ConsentStatusModel
        ),
    ),
    _RegistryEntry(
        "events/accepted-event-1.0.0.schema.json",
        "accepted-event",
        "1.0.0",
        "event",
        "persisted-envelope",
        lambda: __import__("yoetz.domain.events", fromlist=["AcceptedEvent"]).AcceptedEvent,
    ),
    _RegistryEntry(
        "events/action-recorded-1.0.0.schema.json",
        "action-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ActionRecordedPayload"]
            ).ActionRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/assignment-recorded-1.0.0.schema.json",
        "assignment-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["AssignmentRecordedPayload"]
            ).AssignmentRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/check-recorded-1.0.0.schema.json",
        "check-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["CheckRecordedPayload"]
            ).CheckRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/claim-recorded-1.0.0.schema.json",
        "claim-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ClaimRecordedPayload"]
            ).ClaimRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/claim-recorded-1.1.0.schema.json",
        "claim-recorded",
        "1.1.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ClaimRecordedPayloadV1_1"]
            ).ClaimRecordedPayloadV1_1
        ),
    ),
    _RegistryEntry(
        "events/decision-recorded-1.0.0.schema.json",
        "decision-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["DecisionRecordedPayload"]
            ).DecisionRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/event-draft-1.0.0.schema.json",
        "event-draft",
        "1.0.0",
        "event",
        "event-envelope",
        lambda: __import__("yoetz.domain.events", fromlist=["EventDraft"]).EventDraft,
    ),
    _RegistryEntry(
        "events/evidence-recorded-1.0.0.schema.json",
        "evidence-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["EvidenceRecordedPayload"]
            ).EvidenceRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/evidence-recorded-1.1.0.schema.json",
        "evidence-recorded",
        "1.1.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["EvidenceRecordedPayload"]
            ).EvidenceRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/finding-recorded-1.0.0.schema.json",
        "finding-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: __import__("yoetz.domain.findings", fromlist=["Finding"]).Finding,
    ),
    _RegistryEntry(
        "events/obligation-published-1.0.0.schema.json",
        "obligation-published",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ObligationPublishedPayload"]
            ).ObligationPublishedPayload
        ),
    ),
    _RegistryEntry(
        "events/opaque-unknown-event-draft-1.0.0.schema.json",
        "opaque-unknown-event-draft",
        "1.0.0",
        "event",
        "event-envelope",
        lambda: __import__("yoetz.domain.events", fromlist=["UnknownEvent"]).UnknownEvent,
    ),
    _RegistryEntry(
        "events/plan-published-1.0.0.schema.json",
        "plan-published",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["PlanPublishedPayload"]
            ).PlanPublishedPayload
        ),
    ),
    _RegistryEntry(
        "events/plan-revised-1.0.0.schema.json",
        "plan-revised",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__("yoetz.domain.events", fromlist=["PlanRevisedPayload"]).PlanRevisedPayload
        ),
    ),
    _RegistryEntry(
        "events/receipt-recorded-1.0.0.schema.json",
        "receipt-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ReceiptRecordedPayload"]
            ).ReceiptRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/redaction-recorded-1.0.0.schema.json",
        "redaction-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["RedactionRecordedPayload"]
            ).RedactionRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/response-recorded-1.0.0.schema.json",
        "response-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ResponseRecordedPayload"]
            ).ResponseRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/result-recorded-1.0.0.schema.json",
        "result-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ResultRecordedPayload"]
            ).ResultRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/session-opened-1.0.0.schema.json",
        "session-opened",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["SessionOpenedPayload"]
            ).SessionOpenedPayload
        ),
    ),
    _RegistryEntry(
        "events/session-resumed-1.0.0.schema.json",
        "session-resumed",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["SessionResumedPayload"]
            ).SessionResumedPayload
        ),
    ),
    _RegistryEntry(
        "findings/finding-1.0.0.schema.json",
        "finding",
        "1.0.0",
        "request_result",
        "finding",
        lambda: __import__("yoetz.domain.findings", fromlist=["Finding"]).Finding,
    ),
    _RegistryEntry(
        "findings/semantic-provenance-1.0.0.schema.json",
        "semantic-provenance",
        "1.0.0",
        "request_result",
        "semantic-provenance",
        lambda: (
            __import__("yoetz.domain.findings", fromlist=["SemanticProvenance"]).SemanticProvenance
        ),
    ),
    _RegistryEntry(
        "findings/provider-judgment-1.0.0.schema.json",
        "provider-judgment",
        "1.0.0",
        "request_result",
        "provider-judgment",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["ProviderJudgmentEnvelopeModel"]
            ).ProviderJudgmentEnvelopeModel
        ),
    ),
    _RegistryEntry(
        "operations/check-request-1.0.0.schema.json",
        "check-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["CheckRequestModel"]).CheckRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/check-result-1.0.0.schema.json",
        "check-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: __import__("yoetz.protocol.models", fromlist=["CheckResultModel"]).CheckResultModel,
    ),
    _RegistryEntry(
        "operations/publish-work-request-1.0.0.schema.json",
        "publish-work-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["PublishWorkRequestModel"]
            ).PublishWorkRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/publish-work-result-1.0.0.schema.json",
        "publish-work-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["PublishWorkResultModel"]
            ).PublishWorkResultModel
        ),
    ),
    _RegistryEntry(
        "operations/read-guidance-request-1.0.0.schema.json",
        "read-guidance-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["ReadGuidanceRequestModel"]
            ).ReadGuidanceRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/read-guidance-result-1.0.0.schema.json",
        "read-guidance-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["ReadGuidanceResultModel"]
            ).ReadGuidanceResultModel
        ),
    ),
    _RegistryEntry(
        "operations/receipt-request-1.0.0.schema.json",
        "receipt-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["ReceiptRequestModel"]
            ).ReceiptRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/receipt-result-1.0.0.schema.json",
        "receipt-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["ReceiptResultModel"]).ReceiptResultModel
        ),
    ),
    _RegistryEntry(
        "operations/respond-request-1.0.0.schema.json",
        "respond-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["RespondRequestModel"]
            ).RespondRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/respond-result-1.0.0.schema.json",
        "respond-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["RespondResultModel"]).RespondResultModel
        ),
    ),
    _RegistryEntry(
        "operations/start-request-1.0.0.schema.json",
        "start-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StartRequestModel"]).StartRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/start-result-1.0.0.schema.json",
        "start-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: __import__("yoetz.protocol.models", fromlist=["StartResultModel"]).StartResultModel,
    ),
    _RegistryEntry(
        "operations/status-request-1.0.0.schema.json",
        "status-request",
        "1.0.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StatusRequestModel"]).StatusRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/status-result-1.0.0.schema.json",
        "status-result",
        "1.0.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StatusResultModel"]).StatusResultModel
        ),
    ),
    _RegistryEntry(
        "operations/status-request-1.1.0.schema.json",
        "status-request",
        "1.1.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StatusRequestModel"]).StatusRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/status-result-1.1.0.schema.json",
        "status-result",
        "1.1.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StatusResultModel"]).StatusResultModel
        ),
    ),
    _RegistryEntry(
        "privacy/egress-receipt-1.0.0.schema.json",
        "egress-receipt",
        "1.0.0",
        "request_result",
        "privacy-audit",
        lambda: __import__("yoetz.domain.privacy", fromlist=["EgressReceipt"]).EgressReceipt,
    ),
    _RegistryEntry(
        "privacy/outbound-case-1.0.0.schema.json",
        "outbound-case",
        "1.0.0",
        "request_result",
        "outbound-case",
        lambda: (
            __import__(
                "yoetz.domain.privacy", fromlist=["ApprovedOutboundCase"]
            ).ApprovedOutboundCase
        ),
    ),
    _RegistryEntry(
        "privacy/privacy-policy-1.0.0.schema.json",
        "privacy-policy",
        "1.0.0",
        "request_result",
        "privacy-policy",
        lambda: __import__("yoetz.domain.privacy", fromlist=["PrivacyPolicy"]).PrivacyPolicy,
    ),
    _RegistryEntry(
        "privacy/setup-wizard-contract-1.0.0.schema.json",
        "setup-wizard-contract",
        "1.0.0",
        "request_result",
        "setup-contract",
        None,
    ),
    _RegistryEntry(
        "receipts/receipt-document-1.0.0.schema.json",
        "receipt-document",
        "1.0.0",
        "request_result",
        "receipt-document",
        lambda: __import__("yoetz.domain.receipts", fromlist=["ReceiptDocument"]).ReceiptDocument,
    ),
    _RegistryEntry(
        "service/control-hello-1.0.0.schema.json",
        "control-hello",
        "1.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-1.0.0.schema.json",
        "control-hello-result",
        "1.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-2.0.0.schema.json",
        "control-hello",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.0.0.schema.json",
        "control-hello-result",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-request-1.0.0.schema.json",
        "control-request",
        "1.0.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlRequest"]).ControlRequest,
    ),
    _RegistryEntry(
        "service/control-result-1.0.0.schema.json",
        "control-result",
        "1.0.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlResult"]).ControlResult,
    ),
    _RegistryEntry(
        "service/control-request-2.0.0.schema.json",
        "control-request",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-result-2.0.0.schema.json",
        "control-result",
        "2.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-2.1.0.schema.json",
        "control-hello",
        "2.1.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.1.0.schema.json",
        "control-hello-result",
        "2.1.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-request-2.1.0.schema.json",
        "control-request",
        "2.1.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-result-2.1.0.schema.json",
        "control-result",
        "2.1.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-2.2.0.schema.json",
        "control-hello",
        "2.2.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.2.0.schema.json",
        "control-hello-result",
        "2.2.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-request-2.2.0.schema.json",
        "control-request",
        "2.2.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-result-2.2.0.schema.json",
        "control-result",
        "2.2.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-2.3.0.schema.json",
        "control-hello",
        "2.3.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.3.0.schema.json",
        "control-hello-result",
        "2.3.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-request-2.3.0.schema.json",
        "control-request",
        "2.3.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-result-2.3.0.schema.json",
        "control-result",
        "2.3.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/service-status-1.0.0.schema.json",
        "service-status",
        "1.0.0",
        "request_result",
        "service-status",
        lambda: __import__("yoetz.ports.control", fromlist=["ServiceStatus"]).ServiceStatus,
    ),
    _RegistryEntry(
        "version/version-manifest-1.0.0.schema.json",
        "version-manifest",
        "1.0.0",
        "version_manifest",
        "version-report",
        None,
    ),
    _RegistryEntry(
        "version/version-manifest-2.0.0.schema.json",
        "version-manifest",
        "2.0.0",
        "version_manifest",
        "version-report",
        lambda: __import__("yoetz.version", fromlist=["VersionManifest"]).VersionManifest,
    ),
    _RegistryEntry(
        "version/version-manifest-2.1.0.schema.json",
        "version-manifest",
        "2.1.0",
        "version_manifest",
        "version-report",
        lambda: __import__("yoetz.version", fromlist=["VersionManifest"]).VersionManifest,
    ),
)


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def _rename_defs(raw: dict[str, object]) -> dict[str, object]:
    """Strip the framework ``Model``/``Payload`` suffix from generated ``$defs`` anchors."""

    defs = raw.get("$defs")
    if not isinstance(defs, dict):
        return raw

    rename: dict[str, str] = {}
    for key in cast(dict[str, object], defs):
        new_key = key
        for suffix in ("Model", "Payload"):
            if new_key.endswith(suffix) and len(new_key) > len(suffix):
                new_key = new_key[: -len(suffix)]
        rename[f"#/$defs/{key}"] = f"#/$defs/{new_key}"

    def _walk(node: object) -> object:
        if isinstance(node, dict):
            result: dict[str, object] = {}
            for key, value in cast(dict[str, object], node).items():
                if key == "$ref" and isinstance(value, str) and value in rename:
                    result[key] = rename[value]
                else:
                    result[key] = _walk(value)
            return result
        if isinstance(node, list):
            return [_walk(item) for item in cast(list[object], node)]
        return node

    renamed = cast(dict[str, object], _walk(raw))
    new_defs: dict[str, object] = {}
    for key, value in cast(dict[str, object], defs).items():
        target = rename[f"#/$defs/{key}"].removeprefix("#/$defs/")
        new_defs[target] = _walk(value)
    renamed["$defs"] = new_defs
    return renamed


def _sort_lists(node: object) -> object:
    """Sort ``required`` and ``enum`` value lists by ASCII byte order, recursively."""

    if isinstance(node, dict):
        result: dict[str, object] = {}
        mapping = cast(dict[str, object], node)
        for key, value in mapping.items():
            if key == "required" and isinstance(value, list):
                result[key] = sorted(cast(list[str], value), key=lambda item: item.encode("utf-8"))
            elif (
                key == "enum"
                and isinstance(value, list)
                and all(isinstance(item, str) for item in cast(list[object], value))
            ):
                result[key] = sorted(cast(list[str], value), key=lambda item: item.encode("utf-8"))
            else:
                result[key] = _sort_lists(cast(object, value))
        return result
    if isinstance(node, list):
        return [_sort_lists(item) for item in cast(list[object], node)]
    return node


def _enforce_closed_objects(node: object) -> object:
    """Set ``additionalProperties: false`` on every object schema that declares ``properties``."""

    if isinstance(node, dict):
        mapping = cast(dict[str, object], node)
        result = {key: _enforce_closed_objects(value) for key, value in mapping.items()}
        if (
            result.get("type") == "object"
            and "properties" in result
            and "additionalProperties" not in result
        ):
            result["additionalProperties"] = False
        return result
    if isinstance(node, list):
        return [_enforce_closed_objects(item) for item in cast(list[object], node)]
    return node


def _strip_framework_metadata(node: object) -> object:
    """Remove pydantic-internal keys that are not part of the frozen public contract."""

    forbidden = {"$comment"}
    if isinstance(node, dict):
        mapping = cast(dict[str, object], node)
        return {
            key: _strip_framework_metadata(value)
            for key, value in mapping.items()
            if key not in forbidden
        }
    if isinstance(node, list):
        return [_strip_framework_metadata(item) for item in cast(list[object], node)]
    return node


def _normalize(raw: dict[str, object], entry: _RegistryEntry) -> dict[str, JsonValue]:
    working = _rename_defs(raw)
    working = _strip_framework_metadata(working)
    working = _enforce_closed_objects(working)
    working = _sort_lists(working)

    document = cast(dict[str, object], working)
    document.pop("title", None)
    document["$schema"] = _DRAFT_2020_12
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    ordered: dict[str, object] = {"$id": document.pop("$id"), "$schema": document.pop("$schema")}
    ordered.update(document)
    ordered["title"] = entry.schema_name
    return cast(dict[str, JsonValue], ordered)


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------


def _load_disk_document(entry: _RegistryEntry, schema_root: Path) -> SchemaDocument:
    """Load one registry-owned schema document from reviewed on-disk bytes."""

    candidate = schema_root / entry.relative_path
    if candidate.is_symlink() or not candidate.is_file():
        raise SchemaGenerationError("schema_missing", entries=(entry.relative_path,))
    try:
        schema_bytes = candidate.read_bytes()
        parsed = cast(object, json.loads(schema_bytes.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError("schema_unreadable", entries=(entry.relative_path,)) from exc
    if not isinstance(parsed, dict):
        raise SchemaGenerationError("schema_invalid", entries=(entry.relative_path,))
    normalized = cast(dict[str, JsonValue], parsed)
    schema_id = cast(str, normalized.get("$id", ""))
    expected_id = SCHEMA_NAMESPACE + entry.relative_path
    if schema_id != expected_id:
        raise SchemaGenerationError("schema_id_mismatch", entries=(entry.relative_path,))
    if canonical_encode(cast(JsonValue, normalized)) != schema_bytes:
        raise SchemaGenerationError("schema_bytes_not_canonical", entries=(entry.relative_path,))
    return SchemaDocument(
        schema_kind=entry.schema_kind,
        artifact_role=entry.artifact_role,
        schema_name=entry.schema_name,
        schema_version=entry.schema_version,
        schema_id=schema_id,
        relative_path=entry.relative_path,
        canonical_digest=canonical_digest_hex(schema_bytes),
        schema_bytes=schema_bytes,
        json_schema=normalized,
    )


def build_schema_documents(
    *,
    schema_root: Path | None = None,
    entries: tuple[_RegistryEntry, ...] = _REGISTRY,
) -> tuple[SchemaDocument, ...]:
    """Build the ordered schema document set from the frozen registry.

    - ``schema_root`` set (``--check``): load every registry path from disk and validate. This covers
      hand-maintained ``loader=None`` entries and avoids false drift when the generator's Pydantic
      projection differs from already-reviewed committed bytes.
    - ``schema_root`` omitted (``--write``): introspect loader-backed types only; ``loader=None``
      entries fail closed with ``owning_type_not_yet_available`` (never fabricate them).
    """

    _install_type_shims()

    if schema_root is not None:
        seen_paths: set[str] = set()
        seen_ids: set[str] = set()
        documents: list[SchemaDocument] = []
        for entry in entries:
            if entry.relative_path in seen_paths:
                raise SchemaGenerationError("duplicate_path", entries=(entry.relative_path,))
            seen_paths.add(entry.relative_path)
            document = _load_disk_document(entry, schema_root)
            if document.schema_id in seen_ids:
                raise SchemaGenerationError("duplicate_schema_id", entries=(document.schema_id,))
            seen_ids.add(document.schema_id)
            documents.append(document)
        return tuple(sorted(documents, key=lambda doc: doc.relative_path.encode("utf-8")))

    pending = tuple(entry.relative_path for entry in entries if entry.loader is None)
    if pending:
        raise SchemaGenerationError("owning_type_not_yet_available", entries=pending)

    seen_paths = set()
    seen_ids = set()
    documents = []

    for entry in entries:
        if entry.relative_path in seen_paths:
            raise SchemaGenerationError("duplicate_path", entries=(entry.relative_path,))
        seen_paths.add(entry.relative_path)

        assert entry.loader is not None  # narrowed by the pending-check above
        if entry.relative_path in {
            "events/plan-published-1.0.0.schema.json",
            "events/plan-revised-1.0.0.schema.json",
        }:
            normalized = _plan_payload_schema(entry)
        elif entry.relative_path == "events/event-draft-1.0.0.schema.json":
            normalized = _event_draft_schema(entry)
        elif entry.relative_path == "events/evidence-recorded-1.1.0.schema.json":
            normalized = _evidence_payload_schema(entry)
        elif entry.relative_path == "events/claim-recorded-1.1.0.schema.json":
            normalized = _claim_payload_schema(entry)
        elif entry.relative_path == "events/response-recorded-1.0.0.schema.json":
            normalized = _response_recorded_schema(entry)
        elif entry.relative_path == "events/opaque-unknown-event-draft-1.0.0.schema.json":
            normalized = _opaque_unknown_event_draft_schema(entry)
        elif entry.relative_path == "operations/start-result-1.0.0.schema.json":
            normalized = _start_result_schema(entry)
        elif entry.relative_path == "operations/read-guidance-result-1.0.0.schema.json":
            normalized = _read_guidance_result_schema(entry)
        elif entry.relative_path == "operations/publish-work-result-1.0.0.schema.json":
            normalized = _publish_work_result_schema(entry)
        elif entry.relative_path == "operations/respond-request-1.0.0.schema.json":
            normalized = _respond_request_schema(entry)
        elif entry.relative_path == "operations/respond-result-1.0.0.schema.json":
            normalized = _respond_result_schema(entry)
        elif entry.relative_path in {
            "operations/status-request-1.0.0.schema.json",
            "operations/status-request-1.1.0.schema.json",
        }:
            normalized = _status_request_schema(entry)
        elif entry.relative_path in {
            "operations/status-result-1.0.0.schema.json",
            "operations/status-result-1.1.0.schema.json",
        }:
            normalized = _status_result_schema(entry)
        elif entry.relative_path == "receipts/receipt-document-1.0.0.schema.json":
            normalized = _receipt_document_schema(entry)
        elif entry.relative_path == "version/version-manifest-2.0.0.schema.json":
            normalized = _frozen_version_manifest_schema(entry)
        elif entry.relative_path == "version/version-manifest-2.1.0.schema.json":
            normalized = _version_manifest_schema(entry)
        else:
            try:
                python_type = entry.loader()
                raw_schema = TypeAdapter(python_type).json_schema()
            except Exception as exc:  # noqa: BLE001 - normalized into a bounded generator error
                raise SchemaGenerationError(
                    "model_introspection_failed", entries=(entry.relative_path,)
                ) from exc
            normalized = _normalize(cast(dict[str, object], raw_schema), entry)
        schema_id = cast(str, normalized["$id"])
        if schema_id in seen_ids:
            raise SchemaGenerationError("duplicate_schema_id", entries=(schema_id,))
        seen_ids.add(schema_id)

        rendered = render_schema(cast(Mapping[str, JsonValue], normalized))
        documents.append(
            SchemaDocument(
                schema_kind=entry.schema_kind,
                artifact_role=entry.artifact_role,
                schema_name=entry.schema_name,
                schema_version=entry.schema_version,
                schema_id=schema_id,
                relative_path=entry.relative_path,
                canonical_digest=canonical_digest_hex(rendered),
                schema_bytes=rendered,
                json_schema=cast(Mapping[str, JsonValue], normalized),
            )
        )

    return tuple(sorted(documents, key=lambda doc: doc.relative_path.encode("utf-8")))


def canonical_digest_hex(data: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def validate_schema_document(document: SchemaDocument) -> None:
    """Recheck a generated document's dialect and Yoetz policy invariants."""

    if document.json_schema.get("$schema") != _DRAFT_2020_12:
        raise SchemaGenerationError("schema_draft_unsupported", entries=(document.relative_path,))
    expected_id = SCHEMA_NAMESPACE + document.relative_path
    if document.schema_id != expected_id or document.json_schema.get("$id") != expected_id:
        raise SchemaGenerationError("schema_id_mismatch", entries=(document.relative_path,))
    if canonical_encode(cast(JsonValue, document.json_schema)) != document.schema_bytes:
        raise SchemaGenerationError("schema_bytes_not_canonical", entries=(document.relative_path,))

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(cast(dict[str, object], document.json_schema))
    except SchemaError as exc:
        raise SchemaGenerationError(
            "schema_metaschema_invalid", entries=(document.relative_path,)
        ) from exc


def render_schema(document: Mapping[str, JsonValue]) -> bytes:
    """Render a schema document to canonical compact UTF-8 bytes with no trailing newline."""

    return canonical_encode(cast(JsonValue, document))


def compare_tree(expected: tuple[SchemaDocument, ...], root: Path) -> SchemaDiff:
    """Compare generated documents against the on-disk reviewed schema tree, read-only."""

    expected_by_path = {document.relative_path: document for document in expected}
    missing: list[str] = []
    changed: list[str] = []

    for relative_path, document in sorted(expected_by_path.items()):
        candidate = root / relative_path
        if candidate.is_symlink() or not candidate.is_file():
            missing.append(relative_path)
            continue
        on_disk = candidate.read_bytes()
        if on_disk != document.schema_bytes:
            changed.append(relative_path)

    extra: list[str] = []
    if root.is_dir():
        for candidate in sorted(root.rglob("*.schema.json")):
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative not in expected_by_path:
                extra.append(relative)

    return SchemaDiff(missing=tuple(missing), extra=tuple(extra), changed=tuple(changed))


def write_tree(expected: tuple[SchemaDocument, ...], root: Path) -> None:
    """Atomically stage and replace only generator-owned schema files beneath ``root``."""

    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root.parent, prefix=".generate-schemas-") as staging:
        staging_root = Path(staging)
        for document in expected:
            destination = staging_root / document.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(document.schema_bytes)
            with open(destination, "rb") as handle:
                os.fsync(handle.fileno())

        for document in expected:
            source = staging_root / document.relative_path
            target = root / document.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)


def update_schema_manifest(documents: tuple[SchemaDocument, ...], root: Path) -> None:
    """Refresh reviewed byte identities for schemas regenerated by this tool."""

    manifest_path = root / "manifest.json"
    try:
        manifest = cast(dict[str, JsonValue], json.loads(manifest_path.read_bytes()))
        members = cast(list[JsonValue], manifest["members"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError("schema_manifest_unreadable") from exc
    by_path = {
        cast(str, cast(dict[str, JsonValue], member)["path"]): cast(dict[str, JsonValue], member)
        for member in members
    }
    registry_by_path = {entry.relative_path: entry for entry in _REGISTRY}
    for document in documents:
        member = by_path.get(document.relative_path)
        if member is None:
            entry = registry_by_path[document.relative_path]
            assert entry.loader is not None
            owning_type = entry.loader()
            member = {
                "$id": document.schema_id,
                "artifact_role": document.artifact_role,
                "byte_length": len(document.schema_bytes),
                "media_type": _SCHEMA_MEDIA_TYPE,
                "owning_model": getattr(owning_type, "__name__", type(owning_type).__name__),
                "path": document.relative_path,
                "schema_kind": document.schema_kind,
                "schema_version": document.schema_version,
                "sha256": "sha256:" + hashlib.sha256(document.schema_bytes).hexdigest(),
            }
            members.append(cast(JsonValue, member))
            by_path[document.relative_path] = member
        member["byte_length"] = len(document.schema_bytes)
        member["sha256"] = "sha256:" + hashlib.sha256(document.schema_bytes).hexdigest()
    members.sort(key=lambda item: cast(str, cast(dict[str, JsonValue], item)["path"]).encode())
    manifest_path.write_bytes(canonical_encode(cast(JsonValue, manifest)))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _default_schema_root() -> Path:
    return Path(__file__).resolve().parent.parent / "schemas"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_schemas.py",
        description="Generate and verify the reviewable public JSON Schema tree.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify schemas/ matches the registry.")
    mode.add_argument("--write", action="store_true", help="Regenerate schemas/ from the registry.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Test-only: an explicit temporary output root instead of repository schemas/.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="RELATIVE_PATH",
        help="With --write, regenerate only the named loader-backed registry path (repeatable).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    root = args.output_root.resolve() if args.output_root is not None else _default_schema_root()
    selected_entries = _REGISTRY
    if args.only:
        if args.check:
            parser.error("--only is supported only with --write")
        requested = frozenset(args.only)
        selected_entries = tuple(entry for entry in _REGISTRY if entry.relative_path in requested)
        missing = sorted(requested - {entry.relative_path for entry in selected_entries})
        if missing:
            parser.error(f"unknown schema registry path: {missing[0]}")

    try:
        # --check may load hand-maintained registry entries (loader=None) from disk.
        # --write still requires owning Python types and never fabricates those files.
        documents = build_schema_documents(
            schema_root=root if args.check else None,
            entries=selected_entries,
        )
        for document in documents:
            validate_schema_document(document)
    except SchemaGenerationError as exc:
        print(f"generate_schemas: FAIL ({exc.reason})", file=sys.stderr)
        for entry in exc.entries:
            print(f"  {entry}", file=sys.stderr)
        return 1

    if args.check:
        diff = compare_tree(documents, root)
        if diff.is_clean:
            print(f"generate_schemas: PASS ({len(documents)} schema(s) match)")
            return 0
        print("generate_schemas: FAIL (drift detected)", file=sys.stderr)
        for relative_path in diff.missing:
            print(f"  missing {relative_path}", file=sys.stderr)
        for relative_path in diff.extra:
            print(f"  extra {relative_path}", file=sys.stderr)
        for relative_path in diff.changed:
            print(f"  changed {relative_path}", file=sys.stderr)
        return 1

    write_tree(documents, root)
    try:
        update_schema_manifest(documents, root)
    except SchemaGenerationError as exc:
        print(f"generate_schemas: FAIL ({exc.reason})", file=sys.stderr)
        for entry in exc.entries:
            print(f"  {entry}", file=sys.stderr)
        return 1
    if args.only:
        for document in documents:
            if (root / document.relative_path).read_bytes() != document.schema_bytes:
                print("generate_schemas: FAIL (post-write verification drift)", file=sys.stderr)
                return 1
        print(f"generate_schemas: WROTE ({len(documents)} schema(s))")
        return 0
    diff = compare_tree(documents, root)
    if not diff.is_clean:
        print("generate_schemas: FAIL (post-write verification drift)", file=sys.stderr)
        return 1
    print(f"generate_schemas: WROTE ({len(documents)} schema(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
