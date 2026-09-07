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
    properties["policy_versions"] = {"const": list(manifest.policy_versions)}
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


def _frozen_schema(
    entry: _RegistryEntry, *, error_reason: str = "frozen_schema_template_invalid"
) -> dict[str, JsonValue]:
    """Load a released schema byte model without deriving a replacement shape."""

    source = Path(__file__).resolve().parent.parent / "schemas" / entry.relative_path
    try:
        return cast(dict[str, JsonValue], json.loads(source.read_bytes()))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(error_reason, entries=(entry.relative_path,)) from exc


def _privacy_policy_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Append fallback authorization without changing the released v1.0 contract."""

    document = _simple_versioned_schema(entry, "privacy/privacy-policy-1.0.0.schema.json", {})
    properties = cast(dict[str, JsonValue], document["properties"])
    properties["schema_version"] = {"const": "1.1.0"}
    definitions = cast(dict[str, dict[str, JsonValue]], document["$defs"])
    base = definitions["channel_policy_base"]
    cast(dict[str, JsonValue], base["properties"])["fallback_provider_binding"] = {
        "$ref": "#/$defs/provider_binding"
    }
    fallback_rule: JsonValue = {
        "if": {"required": ["fallback_provider_binding"]},
        "then": {
            "required": ["provider_binding"],
            "properties": {
                "provider_binding": {"properties": {"transport": {"const": "external"}}},
                "fallback_provider_binding": {"properties": {"transport": {"const": "external"}}},
            },
        },
    }
    cast(list[JsonValue], base["allOf"]).append(fallback_rule)
    disabled = cast(dict[str, JsonValue], cast(list[JsonValue], base["allOf"])[0])
    then = cast(dict[str, JsonValue], disabled["then"])
    then["not"] = {
        "anyOf": [{"required": ["provider_binding"]}, {"required": ["fallback_provider_binding"]}]
    }
    for name in ("crash_diagnostics", "update_checks", "capability_testing", "product_telemetry"):
        arm = cast(
            dict[str, JsonValue], cast(list[JsonValue], definitions[name + "_policy"]["allOf"])[1]
        )
        arm["not"] = {
            "anyOf": [
                {"required": ["provider_binding"]},
                {"required": ["fallback_provider_binding"]},
            ]
        }
    return document


def _frozen_version_manifest_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Preserve the released v2.0 version report while newer reports append."""

    return _frozen_schema(entry, error_reason="version_schema_template_invalid")


def _evidence_payload_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Derive additive evidence contracts from frozen v1.0 bytes."""

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
                "enum": [
                    "approved_check",
                    "caller_asserted",
                    "import_observed",
                    *(["observation_captured"] if entry.schema_version == "1.2.0" else []),
                ],
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
    """Derive v1.1 from the frozen v1.0 draft and add post-v1.0 claim and evidence pairs."""

    source = Path(__file__).resolve().parent.parent / "schemas/events/event-draft-1.0.0.schema.json"
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        branches = cast(list[JsonValue], document["oneOf"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = "Yoetz event draft 1.1.0"
    opaque_branch = next(
        (
            item
            for item in branches
            if isinstance(item, dict)
            and item.get("$ref")
            == SCHEMA_NAMESPACE + "events/opaque-unknown-event-draft-1.0.0.schema.json"
        ),
        None,
    )
    if opaque_branch is None:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        )
    opaque_branch["$ref"] = SCHEMA_NAMESPACE + "events/opaque-unknown-event-draft-1.1.0.schema.json"
    branches[:] = [
        item
        for item in branches
        if not (
            isinstance(item, dict)
            and any(
                marker in json.dumps(item)
                for marker in (
                    "claim-recorded-1.1.0.schema.json",
                    "evidence-recorded-1.1.0.schema.json",
                    "evidence-recorded-1.2.0.schema.json",
                )
            )
        )
    ]
    evidence_legacy_index = next(
        (
            index
            for index, item in enumerate(branches)
            if isinstance(item, dict) and "evidence-recorded-1.0.0.schema.json" in json.dumps(item)
        ),
        None,
    )
    if evidence_legacy_index is None:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        )
    for offset, version in enumerate(("1.1.0", "1.2.0"), start=1):
        suffix = "_".join(version.split(".")[:2])
        definitions[f"schema_identity_evidence_recorded_{suffix}"] = {
            "additionalProperties": False,
            "properties": {
                "name": {"const": "evidence_recorded"},
                "version": {"const": version},
            },
            "required": ["name", "version"],
            "type": "object",
        }
        definitions[f"evidence_recorded_{suffix}_schema"] = {
            "$ref": f"#/$defs/schema_identity_evidence_recorded_{suffix}"
        }
        branches.insert(
            evidence_legacy_index + offset,
            {
                "properties": {
                    "payload": {
                        "$ref": (
                            "https://schemas.yoetz.dev/0.1/events/"
                            f"evidence-recorded-{version}.schema.json"
                        )
                    },
                    "schema": {"$ref": f"#/$defs/evidence_recorded_{suffix}_schema"},
                },
                "required": ["schema", "payload"],
            },
        )
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
    """Exclude every exact-known pair, including additive claim and evidence versions."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/events/opaque-unknown-event-draft-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        unknown = cast(dict[str, JsonValue], definitions["unknown_event_schema"])
        current_not = cast(dict[str, JsonValue], unknown["not"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "opaque_unknown_event_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = "Yoetz opaque unknown event draft 1.1.0"
    legacy = current_not
    if "anyOf" in current_not:
        values = cast(list[JsonValue], current_not["anyOf"])
        legacy = cast(dict[str, JsonValue], values[0])
    unknown["not"] = {
        "anyOf": [
            legacy,
            *(
                {
                    "additionalProperties": False,
                    "properties": {
                        "name": {"const": "evidence_recorded"},
                        "version": {"const": version},
                    },
                    "required": ["name", "version"],
                    "type": "object",
                }
                for version in ("1.1.0", "1.2.0")
            ),
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


def _load_versioned_template(
    entry: _RegistryEntry,
    source_relative_path: str,
    *,
    replacements: Mapping[str, str] = {},
) -> dict[str, JsonValue]:
    """Copy one reviewed predecessor into a new append-only schema version."""

    source = Path(__file__).resolve().parent.parent / "schemas" / source_relative_path
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "versioned_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc

    def rewrite(value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            result = value
            for before, after in replacements.items():
                result = result.replace(before, after)
            return result
        if isinstance(value, list):
            return cast(JsonValue, [rewrite(item) for item in value])
        if isinstance(value, dict):
            return cast(JsonValue, {key: rewrite(item) for key, item in value.items()})
        return value

    copied = cast(dict[str, JsonValue], rewrite(document))
    copied["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    title = copied.get("title")
    if isinstance(title, str):
        copied["title"] = title.rsplit(" ", 1)[0] + f" {entry.schema_version}"
    return copied


def _runtime_attempt_evidence_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    digest = {
        "maxLength": 71,
        "minLength": 71,
        "pattern": "^sha256:[0-9a-f]{64}$",
        "type": "string",
    }
    correlation = {
        "maxLength": 256,
        "minLength": 1,
        "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        "type": "string",
    }
    required = [
        "app_server_schema_sha256",
        "capability_cell_sha256",
        "capability_evidence_expires_at",
        "capability_profile",
        "case_disclosed",
        "credential_authority",
        "disclosed_case_sha256",
        "executable_sha256",
        "instruction_sha256",
        "isolated_config_sha256",
        "launcher_sha256",
        "output_schema_sha256",
        "process_cleanup",
        "reasoning_effort",
        "runtime_source_identity",
        "runtime_version",
        "selection_sha256",
        "turn_acknowledged",
        "upstream_body_observability",
    ]
    properties: dict[str, JsonValue] = {
        name: dict(digest)
        for name in (
            "app_server_schema_sha256",
            "capability_cell_sha256",
            "disclosed_case_sha256",
            "executable_sha256",
            "final_output_sha256",
            "instruction_sha256",
            "isolated_config_sha256",
            "launcher_sha256",
            "output_schema_sha256",
            "selection_sha256",
        )
    }
    properties.update(
        {
            "auth_mode": {"const": "chatgpt", "type": "string"},
            "capability_profile": {
                "maxLength": 256,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$",
                "type": "string",
            },
            "capability_evidence_expires_at": {
                "maxLength": 20,
                "minLength": 20,
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
                "type": "string",
            },
            "case_disclosed": {"type": "boolean"},
            "credential_authority": {
                "const": "external_runtime_oauth",
                "type": "string",
            },
            "failure_stage": {
                "enum": sorted(
                    __import__(
                        "yoetz.domain.findings", fromlist=["RUNTIME_FAILURE_STAGES"]
                    ).RUNTIME_FAILURE_STAGES
                ),
                "type": "string",
            },
            "plan_type": {
                "maxLength": 64,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
                "type": "string",
            },
            "process_cleanup": {
                "enum": ["failed", "killed", "not_started", "terminated"],
                "type": "string",
            },
            "reasoning_effort": {
                "enum": ["high", "low", "max", "medium", "ultra", "xhigh"],
                "type": "string",
            },
            "runtime_source_identity": {
                "maxLength": 256,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$",
                "type": "string",
            },
            "runtime_version": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[0-9A-Za-z][0-9A-Za-z._+-]*$",
                "type": "string",
            },
            "thread_id": dict(correlation),
            "turn_acknowledged": {"type": "boolean"},
            "turn_id": dict(correlation),
            "upstream_body_observability": {
                "const": "unavailable",
                "type": "string",
            },
        }
    )
    return {
        "$id": SCHEMA_NAMESPACE + entry.relative_path,
        "$schema": _DRAFT_2020_12,
        "additionalProperties": False,
        "allOf": [
            {
                "if": {
                    "properties": {"turn_acknowledged": {"const": True}},
                    "required": ["turn_acknowledged"],
                },
                "then": {
                    "properties": {"case_disclosed": {"const": True}},
                    "required": ["thread_id", "turn_id"],
                },
            }
        ],
        "properties": properties,
        "required": required,
        "title": f"Yoetz runtime attempt evidence {entry.schema_version}",
        "type": "object",
    }


def _semantic_provenance_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _load_versioned_template(
        entry,
        "findings/semantic-provenance-1.0.0.schema.json",
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    unavailable = cast(dict[str, JsonValue], definitions["status_unavailable"])
    unavailable_reason = cast(
        dict[str, JsonValue], cast(dict[str, JsonValue], unavailable["properties"])["reason"]
    )
    reasons = cast(list[JsonValue], unavailable_reason["enum"])
    if "outcome_unknown" not in reasons:
        reasons.append("outcome_unknown")
        reasons.sort(key=cast(Callable[[JsonValue], bytes], lambda item: str(item).encode()))
    semantic_reason = cast(dict[str, JsonValue], definitions["semantic_reason"])
    all_reasons = cast(list[JsonValue], semantic_reason["enum"])
    if "outcome_unknown" not in all_reasons:
        all_reasons.append("outcome_unknown")
        all_reasons.sort(key=cast(Callable[[JsonValue], bytes], lambda item: str(item).encode()))
    properties = cast(dict[str, JsonValue], document["properties"])
    dispatch = cast(dict[str, JsonValue], properties["dispatch_kind"])
    dispatch["enum"] = ["external", "external_runtime_oauth", "local_model"]
    properties["runtime_evidence"] = {
        "$ref": (f"{SCHEMA_NAMESPACE}findings/runtime-attempt-evidence-1.0.0.schema.json")
    }
    # Issue #582: present exactly when the declared fallback endpoint served this attempt. The
    # top-level provider/model/endpoint then name the fallback; this names the primary and the
    # closed reason it could not serve (a fallback-licensing class, or pre-dispatch
    # credential_unavailable). Optional and additive: single-endpoint provenance is unchanged.
    properties["fallback_from"] = {
        "additionalProperties": False,
        "properties": {
            # Decimal string like every other integer leaf on the wire (uint53_decimal shape).
            "attempted_count": {"maxLength": 1, "pattern": "^[0-8]$", "type": "string"},
            "endpoint_profile_id": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[a-z0-9][a-z0-9._-]*$",
                "type": "string",
            },
            "endpoint_profile_version": {
                "maxLength": 128,
                "minLength": 5,
                "type": "string",
            },
            "model": {
                "maxLength": 256,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
                "type": "string",
            },
            "provider": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[a-z0-9][a-z0-9._-]*$",
                "type": "string",
            },
            "reason": {
                "enum": [
                    "credential_unavailable",
                    "provider_quota_exhausted",
                    "provider_rate_limited",
                    "provider_timeout",
                    "transport_unavailable",
                ],
                "type": "string",
            },
        },
        "required": [
            "attempted_count",
            "endpoint_profile_id",
            "endpoint_profile_version",
            "model",
            "provider",
            "reason",
        ],
        "type": "object",
    }
    rules = cast(list[JsonValue], document["allOf"])
    rules[0] = {
        "oneOf": [
            {
                "not": {
                    "anyOf": [
                        {"required": ["local_disclosure_reservation_id"]},
                        {"required": ["runtime_evidence"]},
                    ]
                },
                "properties": {"dispatch_kind": {"const": "external"}},
                "required": [
                    "dispatch_kind",
                    "egress_authorization_id",
                    "request_commitment",
                ],
            },
            {
                "not": {"required": ["local_disclosure_reservation_id"]},
                "properties": {"dispatch_kind": {"const": "external_runtime_oauth"}},
                "required": [
                    "dispatch_kind",
                    "egress_authorization_id",
                    "request_commitment",
                    "runtime_evidence",
                ],
            },
            {
                "not": {
                    "anyOf": [
                        {"required": ["egress_authorization_id"]},
                        {"required": ["request_commitment"]},
                        {"required": ["runtime_evidence"]},
                    ]
                },
                "properties": {"dispatch_kind": {"const": "local_model"}},
                "required": ["dispatch_kind", "local_disclosure_reservation_id"],
            },
        ]
    }
    return document


def _check_result_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _load_versioned_template(
        entry,
        "operations/check-result-1.0.0.schema.json",
        replacements={"semantic-provenance-1.0.0": "semantic-provenance-1.1.0"},
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    binding = cast(dict[str, JsonValue], definitions["semantic_binding"])
    branches = cast(list[JsonValue], binding["oneOf"])
    template = next(
        cast(dict[str, JsonValue], item)
        for item in branches
        if isinstance(item, dict) and '"transport_unavailable"' in json.dumps(item)
    )
    unknown = cast(dict[str, JsonValue], json.loads(json.dumps(template)))
    props = cast(dict[str, JsonValue], unknown["properties"])
    cast(dict[str, JsonValue], props["semantic_reason"])["const"] = "outcome_unknown"
    provenance = cast(dict[str, JsonValue], props["semantic_provenance"])
    constraint = cast(dict[str, JsonValue], cast(list[JsonValue], provenance["allOf"])[1])
    constraint_props = cast(dict[str, JsonValue], constraint["properties"])
    cast(dict[str, JsonValue], constraint_props["reason"])["const"] = "outcome_unknown"
    branches.append(unknown)
    success = cast(dict[str, JsonValue], definitions["success"])
    success_props = cast(dict[str, JsonValue], success["properties"])
    reason_enum = cast(
        list[JsonValue], cast(dict[str, JsonValue], success_props["semantic_reason"])["enum"]
    )
    reason_enum.append("outcome_unknown")
    reason_enum.sort(key=cast(Callable[[JsonValue], bytes], lambda item: str(item).encode()))
    return document


def _check_recorded_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _load_versioned_template(
        entry,
        "events/check-recorded-1.0.0.schema.json",
        replacements={"semantic-provenance-1.0.0": "semantic-provenance-1.1.0"},
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    binding = cast(dict[str, JsonValue], definitions["semantic_binding"])
    branches = cast(list[JsonValue], binding["oneOf"])
    template = next(
        cast(dict[str, JsonValue], item)
        for item in branches
        if isinstance(item, dict) and '"transport_unavailable"' in json.dumps(item)
    )
    unknown = cast(dict[str, JsonValue], json.loads(json.dumps(template)))
    props = cast(dict[str, JsonValue], unknown["properties"])
    cast(dict[str, JsonValue], props["semantic_reason"])["const"] = "outcome_unknown"
    provenance = cast(dict[str, JsonValue], props["semantic_provenance"])
    constraint = cast(dict[str, JsonValue], cast(list[JsonValue], provenance["allOf"])[1])
    constraint_props = cast(dict[str, JsonValue], constraint["properties"])
    cast(dict[str, JsonValue], constraint_props["reason"])["const"] = "outcome_unknown"
    branches.append(unknown)
    properties = cast(dict[str, JsonValue], document["properties"])
    reason_enum = cast(
        list[JsonValue], cast(dict[str, JsonValue], properties["semantic_reason"])["enum"]
    )
    reason_enum.append("outcome_unknown")
    reason_enum.sort(key=cast(Callable[[JsonValue], bytes], lambda item: str(item).encode()))
    return document


def _simple_versioned_schema(
    entry: _RegistryEntry, source: str, replacements: Mapping[str, str]
) -> dict[str, JsonValue]:
    return _load_versioned_template(entry, source, replacements=replacements)


def _finding_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add the coordination finding and policy identity to the current finding reader."""

    document = _simple_versioned_schema(
        entry,
        "findings/finding-1.1.0.schema.json",
        {"semantic-provenance-1.1.0": "semantic-provenance-1.1.0"},
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    finding_kind = cast(dict[str, JsonValue], definitions["finding_kind"])
    enum_values = cast(list[JsonValue], finding_kind["enum"])
    if "coordination_overlap" not in enum_values:
        enum_values.append("coordination_overlap")
        enum_values.sort(key=lambda item: str(item).encode("ascii"))
    priority = {
        "properties": {
            "kind": {"const": "coordination_overlap"},
            "priority": {"const": 2},
        },
        "required": ["kind", "priority"],
    }
    branches = cast(list[JsonValue], cast(list[JsonValue], document["allOf"])[1]["oneOf"])
    if priority not in branches:
        branches.append(priority)
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz finding {entry.schema_version}"
    return document


def _check_request_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Allow the dedicated coordination deterministic policy in the current check request."""

    document = _load_versioned_template(entry, "operations/check-request-1.0.0.schema.json")
    properties = cast(dict[str, JsonValue], document["properties"])
    policy_packs = cast(dict[str, JsonValue], properties["policy_packs"])
    items = cast(dict[str, JsonValue], policy_packs["items"])
    enum_values = cast(list[JsonValue], items["enum"])
    if "coordination/0.1.0" not in enum_values:
        enum_values.append("coordination/0.1.0")
        enum_values.sort(key=lambda item: str(item).encode("ascii"))
    policy_packs["maxItems"] = 3
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz check request {entry.schema_version}"
    return document


_LINEAGE_ID_PATTERNS: Final[Mapping[str, str]] = {
    "task_id": r"^tsk_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "event_id": r"^evt_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "receipt_id": r"^rcp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "finding_id": r"^fnd_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "project_id": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
}
_LINEAGE_UINT_PATTERN: Final = r"^(?:0|[1-9][0-9]{0,17}|[1-8][0-9]{18}|9[0-1][0-9]{17}|92[0-1][0-9]{16}|922[0-2][0-9]{15}|9223[0-2][0-9]{14}|92233[0-6][0-9]{13}|922337[0-1][0-9]{12}|92233720[0-2][0-9]{10}|922337203[0-5][0-9]{9}|9223372036[0-7][0-9]{8}|92233720368[0-4][0-9]{7}|922337203685[0-3][0-9]{6}|9223372036854[0-6][0-9]{5}|92233720368547[0-6][0-9]{4}|922337203685477[0-4][0-9]{3}|9223372036854775[0-7][0-9]{2}|922337203685477580[0-6]|9223372036854775807)$"
_LINEAGE_POSITIVE_UINT_PATTERN: Final = _LINEAGE_UINT_PATTERN.replace(r"(?:0|", r"(?:")


def _lineage_id_schema(kind: str) -> dict[str, JsonValue]:
    return {"pattern": _LINEAGE_ID_PATTERNS[kind], "type": "string"}


def _lineage_frontier_schema() -> dict[str, JsonValue]:
    return {"$ref": SCHEMA_NAMESPACE + "common/frontier-1.0.0.schema.json"}


def _lineage_enum_schema(values: Sequence[str]) -> dict[str, JsonValue]:
    return {"enum": list(values), "type": "string"}


def _lineage_vocabulary_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Build one shared closed lineage vocabulary schema."""

    values: Mapping[str, tuple[str, ...]] = {
        "work-state": ("open", "closed", "cancelled", "abandoned", "written_off"),
        "session-health": ("active", "contact_lost", "ended"),
        "lineage-origin": ("parent_minted", "self_registered", "host_observed"),
        "lineage-acceptance": ("pending", "accepted", "rejected"),
    }
    try:
        enum_values = values[entry.schema_name]
    except KeyError as exc:
        raise SchemaGenerationError(
            "lineage_vocabulary_unknown", entries=(entry.relative_path,)
        ) from exc
    return _normalize({"enum": list(enum_values), "type": "string"}, entry)


def _lineage_child_finding_schema() -> dict[str, JsonValue]:
    traits = {
        "completion_with_open_obligations": (1, True),
        "requested_item_never_attempted": (2, True),
        "failed_work_omitted": (1, True),
        "claim_without_admissible_evidence": (1, True),
        "result_without_action": (2, True),
        "action_without_result": (3, True),
        "stale_evidence_for_changed_state": (2, True),
        "contradictory_claims_unresolved": (1, True),
        "ledger_stale_or_incomplete": (3, False),
        "weak_or_stale_response": (2, True),
        "evidence_does_not_support_claim": (1, True),
        "diff_does_not_match_account": (1, True),
        "material_limitation_omitted": (1, True),
        "questionable_finding_rejection": (2, True),
        "coordination_overlap": (2, True),
    }
    return {
        "additionalProperties": False,
        "allOf": [
            *(
                {
                    "if": {
                        "properties": {"kind": {"const": kind}},
                        "required": ["kind"],
                    },
                    "then": {
                        "properties": {
                            "actionable": {"const": actionable},
                            "priority": {"const": priority},
                        }
                    },
                }
                for kind, (priority, actionable) in traits.items()
            ),
            {
                "if": {"properties": {"resolved": {"const": True}}, "required": ["resolved"]},
                "then": {
                    "properties": {"resolution_event_id": _lineage_id_schema("event_id")},
                    "required": ["resolution_event_id"],
                },
            },
            {
                "if": {"required": ["resolution_event_id"]},
                "then": {"properties": {"resolved": {"const": True}}},
            },
        ],
        "properties": {
            "actionable": {"type": "boolean"},
            "finding_id": _lineage_id_schema("finding_id"),
            "kind": _lineage_enum_schema(
                [
                    "action_without_result",
                    "claim_without_admissible_evidence",
                    "coordination_overlap",
                    "completion_with_open_obligations",
                    "contradictory_claims_unresolved",
                    "diff_does_not_match_account",
                    "evidence_does_not_support_claim",
                    "failed_work_omitted",
                    "ledger_stale_or_incomplete",
                    "material_limitation_omitted",
                    "questionable_finding_rejection",
                    "requested_item_never_attempted",
                    "result_without_action",
                    "stale_evidence_for_changed_state",
                    "weak_or_stale_response",
                ]
            ),
            "origin": _lineage_enum_schema(["deterministic", "semantic_model_derived"]),
            "priority": {"maximum": 3, "minimum": 1, "type": "integer"},
            "resolution_event_id": {"oneOf": [_lineage_id_schema("event_id"), {"type": "null"}]},
            "resolved": {"type": "boolean"},
        },
        "required": [
            "actionable",
            "finding_id",
            "kind",
            "origin",
            "priority",
            "resolved",
        ],
        "type": "object",
    }


def _lineage_child_snapshot_schema() -> dict[str, JsonValue]:
    gap_schema = _lineage_enum_schema(
        ["missing", "not_authorized", "quarantined", "revoked", "unknown", "unreadable"]
    )
    restriction_schema = _lineage_enum_schema(
        [
            "authorization_missing",
            "category_restricted",
            "minimization",
            "never_send",
            "task_scope",
        ]
    )
    frontier_or_null: dict[str, JsonValue] = {
        "oneOf": [_lineage_frontier_schema(), {"type": "null"}]
    }
    properties: dict[str, JsonValue] = {
        "acceptance": _lineage_enum_schema(["accepted", "pending", "rejected"]),
        "child_check_id": {"oneOf": [_lineage_id_schema("event_id"), {"type": "null"}]},
        "child_check_subject_frontier": frontier_or_null,
        "child_frontier": frontier_or_null,
        "child_receipt_id": {"oneOf": [_lineage_id_schema("receipt_id"), {"type": "null"}]},
        "child_task_id": _lineage_id_schema("task_id"),
        "coverage": {"$ref": SCHEMA_NAMESPACE + "common/coverage-1.0.0.schema.json"},
        "findings": {
            "items": {"$ref": "#/$defs/child_finding"},
            "maxItems": 100,
            "type": "array",
            "uniqueItems": True,
        },
        "lineage_authority_revision": {
            "maxLength": 19,
            "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
            "type": "string",
        },
        "membership_generation": {
            "oneOf": [
                {"maxLength": 19, "pattern": _LINEAGE_POSITIVE_UINT_PATTERN, "type": "string"},
                {"type": "null"},
            ]
        },
        "origin": _lineage_enum_schema(["host_observed", "parent_minted", "self_registered"]),
        "provenance_restrictions": {
            "items": restriction_schema,
            "maxItems": 5,
            "type": "array",
            "uniqueItems": True,
        },
        "read_gap_reasons": {
            "items": gap_schema,
            "maxItems": 6,
            "type": "array",
            "uniqueItems": True,
        },
        "session_health": _lineage_enum_schema(["active", "contact_lost", "ended"]),
        "work_state": _lineage_enum_schema(
            ["abandoned", "cancelled", "closed", "open", "written_off"]
        ),
    }
    return {
        "additionalProperties": False,
        "oneOf": [
            {
                "not": {
                    "properties": {"read_gap_reasons": {"minItems": 1}},
                    "required": ["read_gap_reasons"],
                },
                "properties": {"child_frontier": {"type": "object"}},
                "required": ["child_frontier"],
            },
            {
                "properties": {
                    "child_frontier": {"type": "null"},
                    "read_gap_reasons": {"minItems": 1},
                },
                "required": ["read_gap_reasons"],
            },
        ],
        "allOf": [
            {
                "if": {"required": ["child_check_id"]},
                "then": {"required": ["child_check_subject_frontier"]},
            },
            {
                "if": {"required": ["child_check_subject_frontier"]},
                "then": {"required": ["child_check_id"]},
            },
        ],
        "properties": properties,
        "required": [
            "acceptance",
            "child_task_id",
            "coverage",
            "findings",
            "lineage_authority_revision",
            "origin",
            "session_health",
            "work_state",
        ],
        "type": "object",
    }


def _lineage_event_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Build closed event payload schemas for delegation, lifecycle, and frozen manifests."""

    name = entry.schema_name
    if name == "child-dependencies-recorded":
        raw: dict[str, object] = {
            "$defs": {
                "child_finding": _lineage_child_finding_schema(),
                "child_snapshot": _lineage_child_snapshot_schema(),
            },
            "additionalProperties": False,
            "properties": {
                "children": {
                    "items": {"$ref": "#/$defs/child_snapshot"},
                    "maxItems": 100,
                    "type": "array",
                    "uniqueItems": True,
                }
            },
            "required": ["children"],
            "type": "object",
        }
        return _normalize(raw, entry)

    child_id = _lineage_id_schema("task_id")
    reason = {
        "maxLength": 256,
        "minLength": 1,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
        "type": "string",
    }
    properties: dict[str, object] = {}
    required: list[str] = []
    if name == "delegation-declared":
        properties = {
            "child_task_id": child_id,
            "depth": {"maximum": 64, "minimum": 1, "type": "integer"},
            "handle_digest": {
                "maxLength": 71,
                "minLength": 71,
                "pattern": r"^sha256:[0-9a-f]{64}$",
                "type": "string",
            },
            "membership_generation": {
                "maxLength": 19,
                "minLength": 1,
                "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
                "type": "string",
            },
            "project_id": _lineage_id_schema("project_id"),
        }
        required = ["child_task_id", "depth", "handle_digest"]
    elif name in {"delegation-cancelled", "child-rejected", "child-written-off"}:
        properties = {"child_task_id": child_id, "reason_code": reason}
        required = ["child_task_id"]
    elif name == "child-accepted":
        properties = {"child_task_id": child_id}
        required = ["child_task_id"]
    elif name in {"work-closed", "work-cancelled", "work-written-off"}:
        properties = {"reason_code": reason}
    elif name == "work-abandoned":
        properties = {"reason_code": reason, "service_stamped": {"const": True, "type": "boolean"}}
        required = ["service_stamped"]
    else:
        raise SchemaGenerationError("lineage_event_schema_unknown", entries=(entry.relative_path,))
    raw = {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }
    if name == "delegation-declared":
        raw["allOf"] = [
            {
                "if": {
                    "anyOf": [
                        {"required": ["membership_generation"]},
                        {"required": ["project_id"]},
                    ]
                },
                "then": {"required": ["membership_generation", "project_id"]},
            }
        ]
    return _normalize(raw, entry)


def _coordination_event_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Build closed structural payloads for service context and typed dispositions."""

    event_id = _lineage_id_schema("event_id")
    task_id = _lineage_id_schema("task_id")
    project_id = {
        "pattern": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    obligation_id = {
        "pattern": r"^obl_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    finding_id = _lineage_id_schema("finding_id")
    sha256 = {"pattern": r"^sha256:[0-9a-f]{64}$", "type": "string"}
    commitment = {"pattern": r"^hmac-sha256:[0-9a-f]{64}$", "type": "string"}
    positive = {
        "maxLength": 19,
        "minLength": 1,
        "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
        "type": "string",
    }
    canonical = {
        "maxLength": 19,
        "minLength": 1,
        "pattern": _LINEAGE_UINT_PATTERN,
        "type": "string",
    }
    text_ref = {
        "additionalProperties": False,
        "properties": {
            "content_digest": sha256,
            "envelope_digest": sha256,
            "object_id": {
                "pattern": r"^obj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                "type": "string",
            },
            "owner_task_id": task_id,
            "plaintext_size": {"maximum": 4194304, "minimum": 0, "type": "integer"},
            "route_generation": positive,
        },
        "required": [
            "content_digest",
            "object_id",
            "owner_task_id",
            "plaintext_size",
            "route_generation",
        ],
        "type": "object",
    }
    if entry.schema_name == "coordination-context-recorded":
        raw = {
            "additionalProperties": False,
            "properties": {
                "context_digest": sha256,
                "counterpart_task_id": task_id,
                "detection_id": event_id,
                "detail_ref": text_ref,
                "gap_codes": {
                    "items": {
                        "enum": [
                            "details_truncated",
                            "not_observable",
                            "revoked",
                            "source_unavailable",
                        ],
                        "type": "string",
                    },
                    "maxItems": 4,
                    "type": "array",
                    "uniqueItems": True,
                },
                "left_task_id": task_id,
                "membership_generation": positive,
                "overlap_kind": {"enum": ["integration", "physical", "plan"], "type": "string"},
                "project_id": project_id,
                "recorded_authority_revision": sha256,
                "recipient_task_id": task_id,
                "resource_count": canonical,
                "resource_identities": {
                    "items": sha256,
                    "maxItems": 64,
                    "type": "array",
                    "uniqueItems": True,
                },
                "right_task_id": task_id,
                "source_attributable_paths": {"type": "boolean"},
                "source_repository_commitment": commitment,
                "source_route_generation": positive,
                "source_task_id": task_id,
                "source_workspace_commitment": commitment,
            },
            "required": [
                "context_digest",
                "counterpart_task_id",
                "detection_id",
                "left_task_id",
                "membership_generation",
                "overlap_kind",
                "project_id",
                "recipient_task_id",
                "resource_count",
                "resource_identities",
                "right_task_id",
                "source_attributable_paths",
                "source_repository_commitment",
                "source_route_generation",
                "source_task_id",
                "source_workspace_commitment",
            ],
            "type": "object",
        }
    elif entry.schema_name == "coordination-obligation-declared":
        raw = {
            "additionalProperties": False,
            "properties": {
                "detection_id": event_id,
                "membership_generation": positive,
                "obligation_id": obligation_id,
                "project_id": project_id,
                "recipient_task_id": task_id,
            },
            "required": [
                "detection_id",
                "membership_generation",
                "obligation_id",
                "project_id",
                "recipient_task_id",
            ],
            "type": "object",
        }
    elif entry.schema_name == "coordination-disposition-recorded":
        raw = {
            "additionalProperties": False,
            "properties": {
                "context_digest": sha256,
                "detection_id": event_id,
                "disposition": {
                    "enum": ["scope_revision", "sequencing", "shared_work"],
                    "type": "string",
                },
                "evidence_refs": {
                    "items": {
                        "pattern": r"^(?:evd|res)_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                        "type": "string",
                    },
                    "maxItems": 64,
                    "minItems": 1,
                    "type": "array",
                    "uniqueItems": True,
                },
                "finding_id": finding_id,
                "membership_generation": positive,
                "obligation_id": obligation_id,
                "project_id": project_id,
                "recipient_task_id": task_id,
            },
            "required": [
                "detection_id",
                "disposition",
                "evidence_refs",
                "membership_generation",
                "obligation_id",
                "project_id",
                "recipient_task_id",
            ],
            "type": "object",
        }
    else:
        raise SchemaGenerationError(
            "coordination_event_schema_unknown", entries=(entry.relative_path,)
        )
    return _normalize(raw, entry)


def _lineage_session_opened_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _load_versioned_template(
        entry,
        "events/session-opened-1.1.0.schema.json",
    )
    properties = cast(dict[str, JsonValue], document["properties"])
    properties.update(
        {
            "depth": {"maximum": 64, "minimum": 1, "type": "integer"},
            "origin": _lineage_enum_schema(["host_observed", "parent_minted", "self_registered"]),
            "parent_task_id": _lineage_id_schema("task_id"),
            "membership_generation": {
                "maxLength": 19,
                "minLength": 1,
                "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
                "type": "string",
            },
            "project_id": _lineage_id_schema("project_id"),
        }
    )
    all_of = cast(list[JsonValue], document.setdefault("allOf", []))
    all_of.append(
        {
            "if": {
                "anyOf": [
                    {"required": ["depth"]},
                    {"required": ["origin"]},
                    {"required": ["parent_task_id"]},
                ]
            },
            "then": {"required": ["depth", "origin", "parent_task_id"]},
        }
    )
    all_of.append(
        {
            "if": {
                "anyOf": [
                    {"required": ["membership_generation"]},
                    {"required": ["project_id"]},
                ]
            },
            "then": {"required": ["membership_generation", "project_id"]},
        }
    )
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz session opened {entry.schema_version}"
    return cast(dict[str, JsonValue], document)


def _start_request_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add delegation, self-registration, and host correlation to ``start``."""

    document = _load_versioned_template(entry, "operations/start-request-1.0.0.schema.json")
    definitions = cast(dict[str, JsonValue], document.setdefault("$defs", {}))
    definitions.update(
        {
            "attach_handle": {
                "additionalProperties": False,
                "properties": {
                    "child_task_id": _lineage_id_schema("task_id"),
                    "expires_at": {
                        "format": "date-time",
                        "pattern": r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$",
                        "type": "string",
                    },
                    "handle": {
                        "maxLength": 256,
                        "minLength": 32,
                        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
                        "type": "string",
                    },
                },
                "required": ["child_task_id", "expires_at", "handle"],
                "type": "object",
            },
            "host_correlation": {
                "maxLength": 256,
                "minLength": 1,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
                "type": "string",
            },
            "task_id": _lineage_id_schema("task_id"),
        }
    )
    properties = cast(dict[str, JsonValue], document["properties"])
    properties.update(
        {
            "attach_handle": {"$ref": "#/$defs/attach_handle"},
            "correlation_id": {"$ref": "#/$defs/host_correlation"},
            "mode": {"enum": ["attach", "create", "create_or_attach", "delegate"]},
            "parent_session_id": {"$ref": "#/$defs/session_id"},
            "parent_tool_call_id": {"$ref": "#/$defs/host_correlation"},
            "session_id": {"$ref": "#/$defs/session_id"},
            "subagent_id": {"$ref": "#/$defs/host_correlation"},
        }
    )
    rules = cast(list[JsonValue], document.setdefault("allOf", []))
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        condition = rule.get("if")
        then = rule.get("then")
        if not isinstance(condition, dict) or not isinstance(then, dict):
            continue
        condition_properties = condition.get("properties")
        if not isinstance(condition_properties, dict):
            continue
        mode = condition_properties.get("mode")
        if isinstance(mode, dict) and mode.get("const") == "attach":
            any_of = cast(dict[str, JsonValue], then).get("anyOf")
            if isinstance(any_of, list):
                any_of.append({"required": ["attach_handle"]})
            break
    rules.extend(
        [
            {
                "if": {"properties": {"mode": {"const": "delegate"}}, "required": ["mode"]},
                "then": {
                    "required": ["session_id"],
                    "not": {
                        "anyOf": [
                            {"required": ["parent_session_id"]},
                            {"required": ["attach_handle"]},
                        ]
                    },
                },
            },
            {
                "if": {"required": ["attach_handle"]},
                "then": {"properties": {"mode": {"const": "attach"}}},
            },
            {
                "if": {"required": ["parent_session_id"]},
                "then": {"properties": {"mode": {"enum": ["create", "create_or_attach"]}}},
            },
        ]
    )
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz start request {entry.schema_version}"
    return cast(dict[str, JsonValue], document)


def _start_result_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Extend the reviewed start result with the delegated-child return contract."""

    predecessor = _RegistryEntry(
        "operations/start-result-1.0.0.schema.json",
        "start-result",
        "1.0.0",
        "request_result",
        "MCP output",
        entry.loader,
    )
    document = _start_result_schema(predecessor)
    definitions = cast(dict[str, JsonValue], document["$defs"])
    success = cast(dict[str, JsonValue], definitions["success"])
    properties = cast(dict[str, JsonValue], success["properties"])
    properties.update(
        {
            "acceptance": {"enum": ["accepted", "pending", "rejected"]},
            "attach_handle": {"$ref": "#/$defs/attach_handle"},
            "depth": {
                "oneOf": [
                    {
                        "maxLength": 19,
                        "minLength": 1,
                        "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
                        "type": "string",
                    },
                    {"type": "null"},
                ]
            },
            "origin": {"enum": ["host_observed", "parent_minted", "self_registered"]},
            "parent_task_id": {"oneOf": [_lineage_id_schema("task_id"), {"type": "null"}]},
        }
    )
    outcome = cast(dict[str, JsonValue], properties["outcome"])
    outcome["enum"] = ["attached", "created", "delegated", "replayed"]
    # The additive fields are omitted for ordinary create/attach results and therefore remain
    # optional at the top level.  Their delegated branch is made complete below.
    success.setdefault("allOf", [])
    cast(list[JsonValue], success["allOf"]).extend(
        [
            {
                "if": {
                    "anyOf": [
                        {"required": ["acceptance"]},
                        {"required": ["depth"]},
                        {"required": ["origin"]},
                        {"required": ["parent_task_id"]},
                    ]
                },
                "then": {"required": ["acceptance", "depth", "origin", "parent_task_id"]},
            },
            {
                "if": {"properties": {"outcome": {"const": "delegated"}}, "required": ["outcome"]},
                "then": {
                    "properties": {
                        "acceptance": {"const": "accepted"},
                        "origin": {"const": "parent_minted"},
                    },
                    "required": [
                        "acceptance",
                        "attach_handle",
                        "depth",
                        "origin",
                        "parent_task_id",
                    ],
                },
            },
            {
                "if": {"required": ["attach_handle"]},
                "then": {"properties": {"outcome": {"const": "delegated"}}},
            },
        ]
    )
    definitions["attach_handle"] = {
        "additionalProperties": False,
        "properties": {
            "child_task_id": _lineage_id_schema("task_id"),
            "expires_at": {
                "format": "date-time",
                "pattern": r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$",
                "type": "string",
            },
            "handle": {
                "maxLength": 256,
                "minLength": 32,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
                "type": "string",
            },
        },
        "required": ["child_task_id", "expires_at", "handle"],
        "type": "object",
    }
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz start result {entry.schema_version}"
    return document


def _check_result_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add the frozen child preview and advisory coordination branches to ``check``."""

    document = _load_versioned_template(
        entry,
        "operations/check-result-1.1.0.schema.json",
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    binding = cast(dict[str, JsonValue], definitions["semantic_binding"])
    for branch in cast(list[JsonValue], binding["oneOf"]):
        if not isinstance(branch, dict):
            continue
        branch_properties = branch.get("properties")
        branch_required = branch.get("required")
        if not isinstance(branch_properties, dict) or not isinstance(branch_required, list):
            continue
        provenance_shape = branch_properties.get("semantic_provenance")
        # The current result serializer omits nullable provenance for outcomes where no semantic
        # evidence is required.  Keep the required field on provenance-bearing branches, while
        # allowing omission on null-only and failed/coordinator branches.
        if isinstance(provenance_shape, dict):
            if provenance_shape.get("type") == "null" or "oneOf" in provenance_shape:
                branch_required[:] = [
                    item for item in branch_required if item != "semantic_provenance"
                ]
    definitions["code"] = {
        "maxLength": 128,
        "pattern": r"^[a-z][a-z0-9_]{0,127}$",
        "type": "string",
    }
    frontier_ref = SCHEMA_NAMESPACE + "common/frontier-1.0.0.schema.json"
    definitions["child_preview_item"] = {
        "additionalProperties": False,
        "properties": {
            "acceptance": {
                "enum": ["accepted", "pending", "rejected"],
                "type": "string",
            },
            "blocking_conditions": {
                "items": {"$ref": "#/$defs/code"},
                "maxItems": 64,
                "type": "array",
                "uniqueItems": True,
            },
            "child_task_id": {"$ref": "#/$defs/task_id"},
            "origin": {
                "enum": ["host_observed", "parent_minted", "self_registered"],
                "type": "string",
            },
            "rollup_state": {
                "enum": [
                    "annotation",
                    "blocked",
                    "clean",
                    "incomplete",
                    "open_gap",
                    "unavailable",
                ],
                "type": "string",
            },
            "session_health": {
                "enum": ["active", "contact_lost", "ended"],
                "type": "string",
            },
            "work_state": {
                "enum": ["abandoned", "cancelled", "closed", "open", "written_off"],
                "type": "string",
            },
        },
        "required": [
            "acceptance",
            "blocking_conditions",
            "child_task_id",
            "origin",
            "rollup_state",
            "session_health",
            "work_state",
        ],
        "type": "object",
    }
    definitions["children_preview"] = {
        "additionalProperties": False,
        "properties": {
            "items": {
                "items": {"$ref": "#/$defs/child_preview_item"},
                "maxItems": 64,
                "type": "array",
                "uniqueItems": True,
            },
            "label": {"enum": ["preview", "recorded"], "type": "string"},
            "tested_manifest_frontier": {"oneOf": [{"$ref": frontier_ref}, {"type": "null"}]},
        },
        "required": ["items", "label", "tested_manifest_frontier"],
        "type": "object",
    }
    definitions["advisory_note"] = {
        "additionalProperties": False,
        "properties": {
            "count": {
                "maxLength": 19,
                "minLength": 1,
                "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
                "type": "string",
            },
            "kind": {
                "enum": ["duplicate_finding", "live_member_present"],
                "type": "string",
            },
            "project_id": {
                "pattern": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                "type": "string",
            },
            "task_ids": {
                "items": {"$ref": "#/$defs/task_id"},
                "maxItems": 64,
                "type": "array",
                "uniqueItems": True,
            },
        },
        "required": ["count", "kind", "project_id", "task_ids"],
        "type": "object",
    }
    success = cast(dict[str, JsonValue], definitions["success"])
    properties = cast(dict[str, JsonValue], success["properties"])
    # Current public serialization omits nullable semantic provenance when no semantic attempt was
    # requested.  The frozen 1.1 predecessor required an explicit null marker; this successor
    # carries the additive omission contract used by CheckSuccessModel.optional_non_null_fields.
    required = cast(list[JsonValue], success["required"])
    if "semantic_provenance" in required:
        required.remove("semantic_provenance")
    properties["children"] = {"oneOf": [{"$ref": "#/$defs/children_preview"}, {"type": "null"}]}
    properties["advisory_notes"] = {
        "items": {"$ref": "#/$defs/advisory_note"},
        "maxItems": 64,
        "type": "array",
        "uniqueItems": True,
    }
    policy_execution = cast(dict[str, JsonValue], definitions["policy_execution"])
    policy_execution_properties = cast(dict[str, JsonValue], policy_execution["properties"])
    policy_id = cast(dict[str, JsonValue], policy_execution_properties["policy_id"])
    policy_id_values = cast(list[JsonValue], policy_id["enum"])
    if "coordination" not in policy_id_values:
        policy_id_values.append("coordination")
        policy_id_values.sort(key=lambda item: str(item).encode("ascii"))
    policy_executions = cast(dict[str, JsonValue], properties["policy_executions"])
    policy_executions["maxItems"] = 3
    projected_finding = cast(dict[str, JsonValue], definitions["projected_finding"])
    projected_properties = cast(dict[str, JsonValue], projected_finding["properties"])
    projected_kind = cast(dict[str, JsonValue], projected_properties["kind"])
    projected_kind_values = cast(list[JsonValue], projected_kind["enum"])
    if "coordination_overlap" not in projected_kind_values:
        projected_kind_values.append("coordination_overlap")
        projected_kind_values.sort(key=lambda item: str(item).encode("ascii"))
    projected_policy = cast(dict[str, JsonValue], projected_properties["policy_id"])
    projected_policy_values = cast(list[JsonValue], projected_policy["enum"])
    if "coordination" not in projected_policy_values:
        projected_policy_values.append("coordination")
        projected_policy_values.sort(key=lambda item: str(item).encode("ascii"))
    version_slice = cast(dict[str, JsonValue], definitions["version_slice"])
    version_properties = cast(dict[str, JsonValue], version_slice["properties"])
    version_packs = cast(dict[str, JsonValue], version_properties["policy_packs"])
    version_items = cast(dict[str, JsonValue], version_packs["items"])
    version_values = cast(list[JsonValue], version_items["enum"])
    if "coordination/0.1.0" not in version_values:
        version_values.append("coordination/0.1.0")
        version_values.sort(key=lambda item: str(item).encode("ascii"))
    version_packs["maxItems"] = 3
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz check result {entry.schema_version}"
    return document


def _status_request_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add project, task, and host-correlation selectors to the status request."""

    document = _load_versioned_template(
        entry,
        "operations/status-request-1.1.0.schema.json",
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    definitions["project_id"] = {
        "pattern": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    definitions["task_id"] = _lineage_id_schema("task_id")
    definitions["host_correlation"] = {
        "maxLength": 256,
        "minLength": 1,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
        "type": "string",
    }
    properties = cast(dict[str, JsonValue], document["properties"])
    properties.update(
        {
            "correlation_id": {"$ref": "#/$defs/host_correlation"},
            "project_id": {"$ref": "#/$defs/project_id"},
            "task_id": {"$ref": "#/$defs/task_id"},
        }
    )
    view = cast(dict[str, JsonValue], properties["view"])
    values = cast(list[JsonValue], view["enum"])
    for value in ("lineage", "project"):
        if value not in values:
            values.append(value)
    rules = cast(list[JsonValue], document["allOf"])
    # The no-filter branch in v1.1 also contains the previously-added results view.  Keep the
    # selector views explicitly filter-free in this successor.
    rules.append(
        {
            "if": {
                "properties": {"view": {"enum": ["lineage", "project"]}},
                "required": ["view"],
            },
            "then": {"not": {"required": ["filter"]}},
        }
    )
    # Selectors are pairwise exclusive.  Separate rules keep each conflict addressable by the
    # protocol validator rather than hiding all three cases in one opaque branch.
    for left, right in (
        ("project_id", "task_id"),
        ("project_id", "correlation_id"),
        ("task_id", "correlation_id"),
    ):
        rules.append({"not": {"required": [left, right]}})
    rules.extend(
        [
            {
                "if": {"required": ["project_id"]},
                "then": {"properties": {"view": {"const": "project"}}},
            },
            {
                "if": {"required": ["correlation_id"]},
                "then": {"properties": {"view": {"const": "lineage"}}},
            },
        ]
    )
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz status request {entry.schema_version}"
    return document


def _status_result_v1_3_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add lineage and project pages while preserving every earlier status view."""

    document = _load_versioned_template(
        entry,
        "operations/status-result-1.2.0.schema.json",
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    history_item = cast(dict[str, JsonValue], definitions["history_item"])
    history_properties = cast(dict[str, JsonValue], history_item["properties"])
    history_summary = cast(dict[str, JsonValue], history_properties["summary_code"])
    history_codes = cast(list[JsonValue], history_summary["enum"])
    history_codes.extend(
        code
        for code in (
            "child_accepted",
            "child_dependencies_recorded",
            "child_rejected",
            "child_written_off",
            "coordination_context_recorded",
            "coordination_disposition_recorded",
            "coordination_obligation_declared",
            "delegation_cancelled",
            "delegation_declared",
            "work_abandoned",
            "work_cancelled",
            "work_closed",
            "work_written_off",
        )
        if code not in history_codes
    )
    history_codes.sort(key=lambda item: str(item).encode("ascii"))
    finding_kind = cast(dict[str, JsonValue], definitions["finding_kind"])
    finding_kind_values = cast(list[JsonValue], finding_kind["enum"])
    if "coordination_overlap" not in finding_kind_values:
        finding_kind_values.append("coordination_overlap")
        finding_kind_values.sort(key=lambda item: str(item).encode("ascii"))
    for definition_name in ("candidate_finding_item", "finding_item"):
        finding_definition = cast(dict[str, JsonValue], definitions[definition_name])
        finding_properties = cast(dict[str, JsonValue], finding_definition["properties"])
        policy_id = cast(dict[str, JsonValue], finding_properties["policy_id"])
        policy_values = cast(list[JsonValue], policy_id["enum"])
        if "coordination" not in policy_values:
            policy_values.append("coordination")
            policy_values.sort(key=lambda item: str(item).encode("ascii"))
    frontier_ref = SCHEMA_NAMESPACE + "common/frontier-1.0.0.schema.json"
    definitions["project_id"] = {
        "pattern": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "type": "string",
    }
    definitions["host_correlation"] = {
        "maxLength": 256,
        "minLength": 1,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$",
        "type": "string",
    }
    definitions["positive_uint"] = {
        "maxLength": 19,
        "minLength": 1,
        "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
        "type": "string",
    }
    definitions["lineage_child"] = {
        "additionalProperties": False,
        "properties": {
            "acceptance": {"enum": ["accepted", "pending", "rejected"], "type": "string"},
            "blocking_conditions": {
                "items": {"$ref": "#/$defs/code"},
                "maxItems": 64,
                "type": "array",
                "uniqueItems": True,
            },
            "depth": {"$ref": "#/$defs/positive_uint"},
            "origin": {
                "enum": ["host_observed", "parent_minted", "self_registered"],
                "type": "string",
            },
            "parent_task_id": {"$ref": "#/$defs/task_id"},
            "rollup_state": {
                "enum": [
                    "annotation",
                    "blocked",
                    "clean",
                    "incomplete",
                    "open_gap",
                    "unavailable",
                ],
                "type": "string",
            },
            "session_health": {
                "enum": ["active", "contact_lost", "ended"],
                "type": "string",
            },
            "task_id": {"$ref": "#/$defs/task_id"},
            "work_state": {
                "enum": ["abandoned", "cancelled", "closed", "open", "written_off"],
                "type": "string",
            },
        },
        "required": [
            "acceptance",
            "blocking_conditions",
            "depth",
            "origin",
            "parent_task_id",
            "rollup_state",
            "session_health",
            "task_id",
            "work_state",
        ],
        "type": "object",
    }
    definitions["lineage_annotation"] = {
        "additionalProperties": False,
        "anyOf": [
            {
                "properties": {"subagent_id": {"$ref": "#/$defs/host_correlation"}},
                "required": ["subagent_id"],
            },
            {
                "properties": {"parent_tool_call_id": {"$ref": "#/$defs/host_correlation"}},
                "required": ["parent_tool_call_id"],
            },
        ],
        "properties": {
            "acceptance": {"const": "pending", "type": "string"},
            "correlation_id": {"$ref": "#/$defs/host_correlation"},
            "origin": {"const": "host_observed", "type": "string"},
            "parent_tool_call_id": {
                "oneOf": [{"$ref": "#/$defs/host_correlation"}, {"type": "null"}]
            },
            "subagent_id": {"oneOf": [{"$ref": "#/$defs/host_correlation"}, {"type": "null"}]},
        },
        "required": ["acceptance", "correlation_id", "origin"],
        "type": "object",
    }
    definitions["lineage_page"] = {
        "additionalProperties": False,
        "properties": {
            "annotations": {
                "items": {"$ref": "#/$defs/lineage_annotation"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "children": {
                "items": {"$ref": "#/$defs/lineage_child"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "next_cursor": {"$ref": "#/$defs/nullable_cursor"},
            "parent_task_id": {"oneOf": [{"$ref": "#/$defs/task_id"}, {"type": "null"}]},
        },
        "required": ["annotations", "children", "next_cursor"],
        "type": "object",
    }
    definitions["project_text_ref"] = {
        "additionalProperties": False,
        "properties": {
            "content_digest": {"$ref": "#/$defs/digest"},
            "envelope_digest": {"oneOf": [{"$ref": "#/$defs/digest"}, {"type": "null"}]},
            "object_id": {"$ref": "#/$defs/object_id"},
            "owner_task_id": {"$ref": "#/$defs/task_id"},
            "plaintext_size": {"maximum": 4194304, "minimum": 0, "type": "integer"},
            "route_generation": {"$ref": "#/$defs/positive_uint"},
        },
        "required": [
            "content_digest",
            "object_id",
            "owner_task_id",
            "plaintext_size",
            "route_generation",
        ],
        "type": "object",
    }
    definitions["project_member"] = {
        "additionalProperties": False,
        "properties": {
            "actor_id": {"oneOf": [{"$ref": "#/$defs/actor_id"}, {"type": "null"}]},
            "parent_task_id": {"oneOf": [{"$ref": "#/$defs/task_id"}, {"type": "null"}]},
            "session_health": {
                "enum": ["active", "contact_lost", "ended"],
                "type": "string",
            },
            "task_id": {"$ref": "#/$defs/task_id"},
            "work_state": {
                "enum": ["abandoned", "cancelled", "closed", "open", "written_off"],
                "type": "string",
            },
        },
        "required": ["actor_id", "session_health", "task_id", "work_state"],
        "type": "object",
    }
    definitions["project_detection"] = {
        "additionalProperties": False,
        "properties": {
            "detection_id": {"$ref": "#/$defs/event_id"},
            "open": {"type": "boolean"},
            "resource_count": {"$ref": "#/$defs/canonical_uint"},
            "resource_paths": {
                "oneOf": [
                    {
                        "items": {
                            "maxLength": 4096,
                            "minLength": 1,
                            "type": "string",
                        },
                        "maxItems": 256,
                        "type": "array",
                        "uniqueItems": True,
                    },
                    {"$ref": "#/$defs/resource_omission"},
                ]
            },
            "task_ids": {
                "items": {"$ref": "#/$defs/task_id"},
                "maxItems": 64,
                "minItems": 2,
                "type": "array",
                "uniqueItems": True,
            },
        },
        "required": ["detection_id", "open", "resource_count", "task_ids"],
        "type": "object",
    }
    definitions["resource_omission"] = {
        "allOf": [
            {
                "$ref": (
                    SCHEMA_NAMESPACE
                    + "common/operation-result-1.0.0.schema.json#/$defs/omitted_content"
                )
            },
            {
                "properties": {"category": {"const": "repository_excerpt"}},
                "required": ["category"],
            },
        ]
    }
    advice_item = cast(dict[str, JsonValue], definitions["advice_item"])
    advice_properties = cast(dict[str, JsonValue], advice_item["properties"])
    advice_properties.update(
        {
            "coordination_counterpart_task_id": {"$ref": "#/$defs/task_id"},
            "coordination_detection_id": {"$ref": "#/$defs/event_id"},
            "coordination_membership_generation": {"$ref": "#/$defs/positive_uint"},
            "coordination_project_id": {"$ref": "#/$defs/project_id"},
            "coordination_resource_paths": {
                "oneOf": [
                    {
                        "items": {
                            "maxLength": 4096,
                            "minLength": 1,
                            "type": "string",
                        },
                        "maxItems": 256,
                        "type": "array",
                        "uniqueItems": True,
                    },
                    {"$ref": "#/$defs/resource_omission"},
                ]
            },
        }
    )
    advice_item["allOf"] = [
        {
            "if": {
                "anyOf": [
                    {"required": ["coordination_counterpart_task_id"]},
                    {"required": ["coordination_detection_id"]},
                    {"required": ["coordination_membership_generation"]},
                    {"required": ["coordination_project_id"]},
                    {"required": ["coordination_resource_paths"]},
                ]
            },
            "then": {
                "required": [
                    "coordination_counterpart_task_id",
                    "coordination_detection_id",
                    "coordination_membership_generation",
                    "coordination_project_id",
                    "coordination_resource_paths",
                ]
            },
        }
    ]
    definitions["project_coverage"] = {
        "additionalProperties": False,
        "properties": {
            "coverage": {"const": "unobservable", "type": "string"},
            "coverage_id": {"$ref": "#/$defs/event_id"},
            "gap_code": {"const": "not_observable", "type": "string"},
            "membership_generation": {"$ref": "#/$defs/positive_uint"},
            "project_id": {"$ref": "#/$defs/project_id"},
            "task_id": {"$ref": "#/$defs/task_id"},
        },
        "required": [
            "coverage",
            "coverage_id",
            "gap_code",
            "membership_generation",
            "project_id",
            "task_id",
        ],
        "type": "object",
    }
    definitions["project_receipt"] = {
        "additionalProperties": False,
        "properties": {
            "conclusion": {
                "enum": [
                    "insufficient_coverage",
                    "no_unresolved_deterministic_findings",
                    "unresolved_findings_remain",
                ],
                "type": "string",
            },
            "frontier": {"$ref": frontier_ref},
            "receipt_id": {
                "pattern": r"^rcp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                "type": "string",
            },
            "task_id": {"$ref": "#/$defs/task_id"},
        },
        "required": ["conclusion", "frontier", "receipt_id", "task_id"],
        "type": "object",
    }
    definitions["project_page"] = {
        "additionalProperties": False,
        "properties": {
            "description": {
                "oneOf": [
                    {"$ref": "#/$defs/content_text"},
                    {"$ref": "#/$defs/task_omission"},
                ]
            },
            "description_ref": {"oneOf": [{"$ref": "#/$defs/project_text_ref"}, {"type": "null"}]},
            "coverage": {
                "items": {"$ref": "#/$defs/project_coverage"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "detections": {
                "items": {"$ref": "#/$defs/project_detection"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "grant_state": {
                "enum": ["active", "revoked", None],
            },
            "kind": {"enum": ["general", "repository"], "type": "string"},
            "lineage": {"$ref": "#/$defs/lineage_page"},
            "members": {
                "items": {"$ref": "#/$defs/project_member"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "membership_generation": {"$ref": "#/$defs/positive_uint"},
            "next_cursor": {"$ref": "#/$defs/nullable_cursor"},
            "project_id": {"$ref": "#/$defs/project_id"},
            "receipts": {
                "items": {"$ref": "#/$defs/project_receipt"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "title": {
                "oneOf": [
                    {"$ref": "#/$defs/content_text"},
                    {"$ref": "#/$defs/task_omission"},
                ]
            },
            "title_ref": {"oneOf": [{"$ref": "#/$defs/project_text_ref"}, {"type": "null"}]},
        },
        "required": [
            "detections",
            "grant_state",
            "kind",
            "lineage",
            "members",
            "membership_generation",
            "next_cursor",
            "project_id",
            "receipts",
        ],
        "type": "object",
    }
    success = cast(dict[str, JsonValue], definitions["success"])
    properties = cast(dict[str, JsonValue], success["properties"])
    view = cast(dict[str, JsonValue], properties["view"])
    view_values = cast(list[JsonValue], view["enum"])
    for value in ("lineage", "project"):
        if value not in view_values:
            view_values.append(value)
    page = cast(dict[str, JsonValue], properties["page"])
    page_values = cast(list[JsonValue], page["anyOf"])
    page_values.extend([{"$ref": "#/$defs/lineage_page"}, {"$ref": "#/$defs/project_page"}])
    rules = cast(list[JsonValue], success.setdefault("allOf", []))
    rules.extend(
        [
            {
                "if": {"properties": {"view": {"const": "lineage"}}, "required": ["view"]},
                "then": {"properties": {"page": {"$ref": "#/$defs/lineage_page"}}},
            },
            {
                "if": {"properties": {"view": {"const": "project"}}, "required": ["view"]},
                "then": {"properties": {"page": {"$ref": "#/$defs/project_page"}}},
            },
        ]
    )
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz status result {entry.schema_version}"
    return document


def _receipt_document_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add the recorded child outcome section to the immutable receipt document."""

    document = _load_versioned_template(
        entry,
        "receipts/receipt-document-1.1.0.schema.json",
    )
    definitions = cast(dict[str, JsonValue], document["$defs"])
    definitions["child_finding"] = _lineage_child_finding_schema()
    definitions["receipt_child"] = {
        "additionalProperties": False,
        "allOf": [
            {
                "if": {
                    "properties": {"outcome": {"const": "unavailable"}},
                    "required": ["outcome"],
                },
                "then": {"properties": {"tested_manifest_ref": {"type": "null"}}},
            }
        ],
        "properties": {
            "child_task_id": _lineage_id_schema("task_id"),
            "findings": {
                "items": {"$ref": "#/$defs/child_finding"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "freshness": {"enum": ["known", "unknown"], "type": "string"},
            "outcome": {
                "enum": ["annotated", "clean", "incomplete", "open_gap", "unavailable"],
                "type": "string",
            },
            "tested_manifest_ref": {"oneOf": [_lineage_id_schema("event_id"), {"type": "null"}]},
        },
        "required": [
            "child_task_id",
            "findings",
            "freshness",
            "outcome",
            "tested_manifest_ref",
        ],
        "type": "object",
    }
    # ``later_manifest_ref`` is nullable and optional in the domain model.
    cast(dict[str, JsonValue], definitions["receipt_child"])["properties"]["later_manifest_ref"] = {
        "oneOf": [_lineage_id_schema("event_id"), {"type": "null"}]
    }
    definitions["receipt_children"] = {
        "additionalProperties": False,
        "properties": {
            "children": {
                "items": {"$ref": "#/$defs/receipt_child"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            }
        },
        "required": ["children"],
        "type": "object",
    }
    properties = cast(dict[str, JsonValue], document["properties"])
    findings = cast(dict[str, JsonValue], properties["findings"])
    findings["items"] = {"$ref": SCHEMA_NAMESPACE + "findings/finding-1.2.0.schema.json"}
    properties["children"] = {"$ref": "#/$defs/receipt_children"}
    required = cast(list[JsonValue], document["required"])
    if "children" not in required:
        required.append("children")
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz receipt document {entry.schema_version}"
    return document


def _receipt_result_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    return _simple_versioned_schema(
        entry,
        "operations/receipt-result-1.1.0.schema.json",
        {"receipt-document-1.1.0": "receipt-document-1.2.0"},
    )


def _publish_work_request_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    return _simple_versioned_schema(
        entry,
        "operations/publish-work-request-1.1.0.schema.json",
        {"event-draft-1.1.0": "event-draft-1.2.0"},
    )


def _event_draft_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _event_draft_schema(entry)
    definitions = cast(dict[str, JsonValue], document["$defs"])
    branches = cast(list[JsonValue], document["oneOf"])
    for family in (
        "check_recorded",
        "finding_recorded",
        "session_opened",
        "session_resumed",
    ):
        definition_name = f"schema_identity_{family}_1_1"
        alias_name = f"{family}_1_1_schema"
        definitions[definition_name] = {
            "additionalProperties": False,
            "properties": {"name": {"const": family}, "version": {"const": "1.1.0"}},
            "required": ["name", "version"],
            "type": "object",
        }
        definitions[alias_name] = {"$ref": f"#/$defs/{definition_name}"}
        legacy_path = f"events/{family.replace('_', '-')}-1.0.0.schema.json"
        new_path = f"events/{family.replace('_', '-')}-1.1.0.schema.json"
        legacy_index = next(
            index
            for index, item in enumerate(branches)
            if isinstance(item, dict) and legacy_path in json.dumps(item)
        )
        branches.insert(
            legacy_index + 1,
            {
                "properties": {
                    "payload": {"$ref": SCHEMA_NAMESPACE + new_path},
                    "schema": {"$ref": f"#/$defs/{alias_name}"},
                },
                "required": ["schema", "payload"],
            },
        )
    return document


def _event_draft_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add lineage payload families while retaining every previously admitted event pair."""

    document = _event_draft_v1_1_schema(entry)
    definitions = cast(dict[str, JsonValue], document["$defs"])
    branches = cast(list[JsonValue], document["oneOf"])
    # ``_event_draft_v1_1_schema`` is intentionally derived from the frozen v1.0 template and
    # therefore points its opaque fallback at the v1.1 opaque schema.  The v1.2 draft must carry
    # that fallback forward as well; otherwise a known lineage event matches both its new branch
    # and the stale opaque branch, making the advertised publish request oneOf ambiguous.
    opaque_v11 = SCHEMA_NAMESPACE + "events/opaque-unknown-event-draft-1.1.0.schema.json"
    opaque_v12 = SCHEMA_NAMESPACE + "events/opaque-unknown-event-draft-1.2.0.schema.json"
    opaque_branch = next(
        (item for item in branches if isinstance(item, dict) and item.get("$ref") == opaque_v11),
        None,
    )
    if opaque_branch is None:
        raise SchemaGenerationError(
            "event_draft_schema_template_invalid", entries=(entry.relative_path,)
        )
    opaque_branch["$ref"] = opaque_v12

    def add_branch(family: str, version: str) -> None:
        suffix = "_".join(version.split(".")[:2])
        identity_name = f"schema_identity_{family}_{suffix}"
        alias_name = f"{family}_{suffix}_schema"
        definitions[identity_name] = {
            "additionalProperties": False,
            "properties": {"name": {"const": family}, "version": {"const": version}},
            "required": ["name", "version"],
            "type": "object",
        }
        definitions[alias_name] = {"$ref": f"#/$defs/{identity_name}"}
        payload_path = f"events/{family.replace('_', '-')}-{version}.schema.json"
        branches.append(
            {
                "properties": {
                    "payload": {"$ref": SCHEMA_NAMESPACE + payload_path},
                    "schema": {"$ref": f"#/$defs/{alias_name}"},
                },
                "required": ["schema", "payload"],
            }
        )

    add_branch("session_opened", "1.2.0")
    add_branch("finding_recorded", "1.2.0")
    for family in (
        "child_accepted",
        "child_dependencies_recorded",
        "child_rejected",
        "child_written_off",
        "delegation_cancelled",
        "delegation_declared",
        "work_abandoned",
        "work_cancelled",
        "work_closed",
        "work_written_off",
        "coordination_context_recorded",
        "coordination_obligation_declared",
        "coordination_disposition_recorded",
    ):
        add_branch(family, "1.0.0")
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz event draft {entry.schema_version}"
    return document


def _opaque_unknown_event_v1_1_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    document = _opaque_unknown_event_draft_schema(entry)
    definitions = cast(dict[str, JsonValue], document["$defs"])
    unknown = cast(dict[str, JsonValue], definitions["unknown_event_schema"])
    exclusion = cast(dict[str, JsonValue], unknown["not"])
    values = cast(list[JsonValue], exclusion["anyOf"])
    for family in (
        "check_recorded",
        "finding_recorded",
        "session_opened",
        "session_resumed",
    ):
        values.append(
            {
                "additionalProperties": False,
                "properties": {"name": {"const": family}, "version": {"const": "1.1.0"}},
                "required": ["name", "version"],
                "type": "object",
            }
        )
    return document


def _opaque_unknown_event_v1_2_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Exclude the lineage event pairs from the opaque-event fallback."""

    document = _opaque_unknown_event_v1_1_schema(entry)
    definitions = cast(dict[str, JsonValue], document["$defs"])
    unknown = cast(dict[str, JsonValue], definitions["unknown_event_schema"])
    exclusion = cast(dict[str, JsonValue], unknown["not"])
    values = cast(list[JsonValue], exclusion["anyOf"])
    values.append(
        {
            "additionalProperties": False,
            "properties": {"name": {"const": "session_opened"}, "version": {"const": "1.2.0"}},
            "required": ["name", "version"],
            "type": "object",
        }
    )
    values.append(
        {
            "additionalProperties": False,
            "properties": {"name": {"const": "finding_recorded"}, "version": {"const": "1.2.0"}},
            "required": ["name", "version"],
            "type": "object",
        }
    )
    for family in (
        "child_accepted",
        "child_dependencies_recorded",
        "child_rejected",
        "child_written_off",
        "delegation_cancelled",
        "delegation_declared",
        "work_abandoned",
        "work_cancelled",
        "work_closed",
        "work_written_off",
        "coordination_context_recorded",
        "coordination_obligation_declared",
        "coordination_disposition_recorded",
    ):
        values.append(
            {
                "additionalProperties": False,
                "properties": {"name": {"const": family}, "version": {"const": "1.0.0"}},
                "required": ["name", "version"],
                "type": "object",
            }
        )
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz opaque unknown event draft {entry.schema_version}"
    return document


def _publish_work_request_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Derive v1.1 authoring from frozen v1.0 and select the v1.1 draft union."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas/operations/publish-work-request-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        properties = cast(dict[str, JsonValue], document["properties"])
        event_drafts = cast(dict[str, JsonValue], properties["event_drafts"])
        items = cast(dict[str, JsonValue], event_drafts["items"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "publish_work_request_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = "Yoetz publish-work request 1.1.0"
    items["$ref"] = SCHEMA_NAMESPACE + "events/event-draft-1.1.0.schema.json"
    return document


def _outbound_case_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Add observation provenance to outbound-case v1.1 without rewriting v1.0."""

    source = (
        Path(__file__).resolve().parent.parent / "schemas/privacy/outbound-case-1.0.0.schema.json"
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
        definitions = cast(dict[str, JsonValue], document["$defs"])
        excerpt = cast(dict[str, JsonValue], definitions["targeted_excerpt"])
        excerpt_properties = cast(dict[str, JsonValue], excerpt["properties"])
        provenance = cast(dict[str, JsonValue], excerpt_properties["digest_provenance"])
        provenance_properties = cast(dict[str, JsonValue], provenance["properties"])
        provenance_enum = cast(dict[str, JsonValue], provenance_properties["provenance"])
        properties = cast(dict[str, JsonValue], document["properties"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "outbound_case_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path
    document["title"] = f"Yoetz outbound case {entry.schema_version}"
    properties["schema_version"] = {"const": entry.schema_version}
    provenance_enum["enum"] = [
        "approved_check",
        "caller_asserted",
        "import_observed",
        "observation_captured",
    ]
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


_CONTROL_ID_PATTERNS: Final[Mapping[str, str]] = {
    "event_id": r"^evt_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "object_id": r"^obj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "project_id": r"^prj_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "receipt_id": r"^rcp_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "task_id": r"^tsk_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
}
_CONTROL_DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
_CONTROL_COMMITMENT_PATTERN: Final = r"^hmac-sha256:[0-9a-f]{64}$"
_CONTROL_TIMESTAMP_PATTERN: Final = (
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$"
)


def _control_id_schema(kind: str) -> dict[str, JsonValue]:
    return {"pattern": _CONTROL_ID_PATTERNS[kind], "type": "string"}


def _control_string_schema(*, minimum: int = 1, maximum: int = 128) -> dict[str, JsonValue]:
    return {"maxLength": maximum, "minLength": minimum, "type": "string"}


def _control_nullable(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"oneOf": [schema, {"type": "null"}]}


def _control_positive_integer_schema() -> dict[str, JsonValue]:
    return {
        "maximum": 2**53 - 1,
        "minimum": 1,
        "type": "integer",
    }


def _control_project_request_schema() -> dict[str, JsonValue]:
    """Return the closed CLI-only project command body used by control 2.5."""

    text = _control_string_schema(maximum=65_536)
    task = _control_id_schema("task_id")
    project = _control_id_schema("project_id")
    commitment = {"pattern": _CONTROL_COMMITMENT_PATTERN, "type": "string"}
    member = {
        "oneOf": [
            _control_id_schema("task_id"),
            commitment,
        ]
    }
    event = _control_id_schema("event_id")
    positive = _control_positive_integer_schema()
    properties: dict[str, JsonValue] = {
        "audit_record_id": _control_nullable(event),
        "auto_grouping": _control_nullable({"type": "boolean"}),
        "description": _control_nullable(text),
        "expected_generation": _control_nullable(positive),
        "member_commitment_or_id": _control_nullable(member),
        "member_kind": _control_nullable(
            {"enum": ["repository", "task", "workspace"], "type": "string"}
        ),
        "member_repository_commitment": _control_nullable(commitment),
        "membership_generation": _control_nullable(positive),
        "operation": {
            "enum": [
                "amend",
                "create",
                "dissolve",
                "grant",
                "link",
                "opt_in",
                "opt_out",
                "revoke",
                "status",
                "unlink",
            ],
            "type": "string",
        },
        "owner_route_generation": _control_nullable(positive),
        "owner_task_id": _control_nullable(task),
        "project_id": _control_nullable(project),
        "repository_commitment": _control_nullable(commitment),
        "requester_task_id": _control_nullable(task),
        "schema_version": {"const": "1.0.0"},
        "selected_task_id": _control_nullable(task),
        "source_workspace_commitment": _control_nullable(commitment),
        "title": _control_nullable(text),
    }

    def operation(
        name: str,
        required: tuple[str, ...],
        overrides: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return {
            "if": {
                "properties": {"operation": {"const": name}},
                "required": ["operation"],
            },
            "then": {
                "properties": dict(overrides),
                "required": list(required),
            },
        }

    conditions: list[JsonValue] = [
        operation(
            "create",
            ("title", "owner_task_id"),
            {"owner_task_id": task, "title": text},
        ),
        operation(
            "link",
            ("project_id", "member_kind", "member_commitment_or_id"),
            {
                "member_commitment_or_id": member,
                "member_kind": {"enum": ["repository", "task", "workspace"], "type": "string"},
                "project_id": project,
            },
        ),
        operation(
            "unlink",
            ("project_id", "member_kind", "member_commitment_or_id"),
            {
                "member_commitment_or_id": member,
                "member_kind": {"enum": ["repository", "task", "workspace"], "type": "string"},
                "project_id": project,
            },
        ),
        {
            "if": {
                "properties": {"operation": {"const": "amend"}},
                "required": ["operation"],
            },
            "then": {
                "anyOf": [
                    {"properties": {"title": text}, "required": ["title"]},
                    {"properties": {"description": text}, "required": ["description"]},
                ],
                "properties": {"owner_task_id": task, "project_id": project},
                "required": ["owner_task_id", "project_id"],
            },
        },
        operation("dissolve", ("project_id",), {"project_id": project}),
        operation(
            "opt_out",
            ("repository_commitment",),
            {"repository_commitment": commitment},
        ),
        operation(
            "opt_in",
            ("repository_commitment",),
            {"repository_commitment": commitment},
        ),
        operation(
            "grant",
            ("project_id", "membership_generation"),
            {"membership_generation": positive, "project_id": project},
        ),
        operation(
            "revoke",
            ("project_id", "membership_generation"),
            {"membership_generation": positive, "project_id": project},
        ),
        operation(
            "status",
            ("requester_task_id", "project_id"),
            {"project_id": project, "requester_task_id": task},
        ),
    ]
    return {
        "additionalProperties": False,
        "allOf": conditions,
        "properties": properties,
        "required": ["operation", "schema_version"],
        "type": "object",
    }


def _control_project_result_schema() -> dict[str, JsonValue]:
    """Return the structural project result union used by CLI control responses."""

    digest = {"pattern": _CONTROL_DIGEST_PATTERN, "type": "string"}
    commitment = {"pattern": _CONTROL_COMMITMENT_PATTERN, "type": "string"}
    timestamp = {"format": "date-time", "pattern": _CONTROL_TIMESTAMP_PATTERN, "type": "string"}
    project = _control_id_schema("project_id")
    task = _control_id_schema("task_id")
    event = _control_id_schema("event_id")
    object_id = _control_id_schema("object_id")
    positive = {
        "maxLength": 19,
        "pattern": _LINEAGE_POSITIVE_UINT_PATTERN,
        "type": "string",
    }
    text_ref: dict[str, JsonValue] = {
        "additionalProperties": False,
        "properties": {
            "content_digest": digest,
            "envelope_digest": _control_nullable(digest),
            "object_id": object_id,
            "owner_task_id": task,
            "plaintext_size": {"maximum": 4_194_304, "minimum": 0, "type": "integer"},
            "route_generation": positive,
        },
        "required": [
            "content_digest",
            "object_id",
            "owner_task_id",
            "plaintext_size",
            "route_generation",
        ],
        "type": "object",
    }
    descriptor: dict[str, JsonValue] = {
        "additionalProperties": False,
        "properties": {
            "auto_grouping": {"type": "boolean"},
            "created_at": timestamp,
            "description_ref": text_ref,
            "dissolved_at": timestamp,
            "kind": {"enum": ["general", "repository"], "type": "string"},
            "membership_generation": positive,
            "project_id": project,
            "repository_commitment": commitment,
            "title_ref": text_ref,
        },
        "required": [
            "auto_grouping",
            "created_at",
            "kind",
            "membership_generation",
            "project_id",
        ],
        "type": "object",
    }
    membership: dict[str, JsonValue] = {
        "additionalProperties": False,
        "properties": {
            "bound_at": timestamp,
            "member_commitment_or_id": {
                "oneOf": [task, commitment],
            },
            "member_kind": {"enum": ["repository", "task", "workspace"], "type": "string"},
            "membership_generation": positive,
            "project_id": project,
            "unbound_at": timestamp,
        },
        "required": [
            "bound_at",
            "member_commitment_or_id",
            "member_kind",
            "membership_generation",
            "project_id",
        ],
        "type": "object",
    }
    membership_view = {
        "allOf": [
            {"$ref": "#/$defs/project_membership"},
            {
                "properties": {
                    "actor_id": {"pattern": r"^[A-Za-z0-9._:-]{1,128}$", "type": "string"},
                    "parent_task_id": task,
                    "session_health": {
                        "enum": ["active", "contact_lost", "ended"],
                        "type": "string",
                    },
                    "task_id": task,
                    "work_state": {
                        "enum": ["abandoned", "cancelled", "closed", "open", "written_off"],
                        "type": "string",
                    },
                },
                "type": "object",
            },
        ],
        "unevaluatedProperties": False,
    }
    grant: dict[str, JsonValue] = {
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"state": {"const": "active"}}, "required": ["state"]},
                "then": {"not": {"required": ["revoked_at"]}},
            },
            {
                "if": {"properties": {"state": {"const": "revoked"}}, "required": ["state"]},
                "then": {"required": ["revoked_at"]},
            },
        ],
        "properties": {
            "audit_record_id": event,
            "granted_at": timestamp,
            "membership_generation": positive,
            "project_id": project,
            "revoked_at": timestamp,
            "state": {"enum": ["active", "revoked"], "type": "string"},
        },
        "required": [
            "audit_record_id",
            "granted_at",
            "membership_generation",
            "project_id",
            "state",
        ],
        "type": "object",
    }
    detection: dict[str, JsonValue] = {
        "additionalProperties": False,
        "properties": {
            "detection_id": event,
            "open": {"type": "boolean"},
            "resource_count": {"maximum": 2**53 - 1, "minimum": 0, "type": "integer"},
            "task_ids": {
                "items": task,
                "maxItems": 64,
                "minItems": 2,
                "type": "array",
                "uniqueItems": True,
            },
        },
        "required": ["detection_id", "open", "resource_count", "task_ids"],
        "type": "object",
    }
    status: dict[str, JsonValue] = {
        "additionalProperties": False,
        "properties": {
            "authorized_task_id": task,
            "detections": {
                "items": detection,
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "grant": grant,
            "memberships": {
                "items": {"$ref": "#/$defs/project_membership_view"},
                "maxItems": 100,
                "type": "array",
                "uniqueItems": True,
            },
            "project": {"$ref": "#/$defs/project_descriptor"},
            "schema_version": {"const": "1.0.0"},
        },
        "required": ["detections", "memberships", "project", "schema_version"],
        "type": "object",
    }
    opt_result = {
        "additionalProperties": False,
        "properties": {
            "auto_grouping": {"type": "boolean"},
            "project_id": {"type": "null"},
            "repository_commitment": commitment,
            "schema_version": {"const": "1.0.0"},
        },
        "required": ["auto_grouping", "project_id", "repository_commitment", "schema_version"],
        "type": "object",
    }
    return {
        "$defs": {
            "project_descriptor": descriptor,
            "project_detection": detection,
            "project_grant": grant,
            "project_membership": membership,
            "project_membership_view": membership_view,
            "project_opt_result": opt_result,
            "project_status": status,
            "project_text_ref": text_ref,
        },
        "oneOf": [
            {"$ref": "#/$defs/project_descriptor"},
            {"$ref": "#/$defs/project_membership"},
            {"$ref": "#/$defs/project_grant"},
            {"$ref": "#/$defs/project_opt_result"},
            {"$ref": "#/$defs/project_status"},
        ],
    }


def _control_v2_5_schema(entry: _RegistryEntry) -> dict[str, JsonValue]:
    """Derive CLI project-control support from the frozen 2.4 envelope shapes."""

    source = (
        Path(__file__).resolve().parent.parent
        / "schemas"
        / entry.relative_path.replace("2.5.0", "2.4.0")
    )
    try:
        document = cast(dict[str, JsonValue], json.loads(source.read_bytes()))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaGenerationError(
            "control_schema_template_invalid", entries=(entry.relative_path,)
        ) from exc
    document["$id"] = SCHEMA_NAMESPACE + entry.relative_path

    operation_ref_replacements = {
        SCHEMA_NAMESPACE + "operations/start-request-1.0.0.schema.json": SCHEMA_NAMESPACE
        + "operations/start-request-1.1.0.schema.json",
        SCHEMA_NAMESPACE + "operations/start-result-1.0.0.schema.json": SCHEMA_NAMESPACE
        + "operations/start-result-1.1.0.schema.json",
        SCHEMA_NAMESPACE + "operations/publish-work-request-1.1.0.schema.json": SCHEMA_NAMESPACE
        + "operations/publish-work-request-1.2.0.schema.json",
        SCHEMA_NAMESPACE + "operations/check-request-1.0.0.schema.json": SCHEMA_NAMESPACE
        + "operations/check-request-1.1.0.schema.json",
        SCHEMA_NAMESPACE + "operations/check-result-1.1.0.schema.json": SCHEMA_NAMESPACE
        + "operations/check-result-1.2.0.schema.json",
        SCHEMA_NAMESPACE + "operations/status-request-1.1.0.schema.json": SCHEMA_NAMESPACE
        + "operations/status-request-1.2.0.schema.json",
        SCHEMA_NAMESPACE + "operations/status-result-1.2.0.schema.json": SCHEMA_NAMESPACE
        + "operations/status-result-1.3.0.schema.json",
        SCHEMA_NAMESPACE + "operations/receipt-result-1.1.0.schema.json": SCHEMA_NAMESPACE
        + "operations/receipt-result-1.2.0.schema.json",
    }

    def retarget(node: JsonValue) -> None:
        if isinstance(node, dict):
            for key, value in tuple(node.items()):
                if type(value) is str:
                    node[key] = operation_ref_replacements.get(value, value)
                else:
                    retarget(value)
        elif isinstance(node, list):
            for value in node:
                retarget(value)

    retarget(document)
    if entry.schema_name == "control-hello":
        return document
    if entry.schema_name == "control-hello-result":
        properties = cast(dict[str, JsonValue], document["properties"])
        allowed = cast(dict[str, JsonValue], properties["allowed_methods"])
        enum_values = cast(list[JsonValue], allowed["enum"])
        item_values = cast(dict[str, JsonValue], allowed["items"])
        item_enum = cast(list[JsonValue], item_values["enum"])
        for values in (enum_values[1], item_enum):
            methods = cast(list[JsonValue], values)
            if "project" not in methods:
                methods.append("project")
            methods.sort(key=lambda value: str(value).encode("ascii"))
        allowed["maxItems"] = 32
        return document
    definitions = cast(dict[str, JsonValue], document["$defs"])
    branches = cast(list[JsonValue], document["oneOf"])
    if entry.schema_name == "control-request":
        # Native Claude/Cursor ingress adds the reviewed pairing contract to the structural
        # observation payload.  These fields are intentionally current 2.5-only: the 2.4
        # document is frozen, while cloning it here must still track every field the domain
        # serializer can emit (including Cursor's generation identity).
        observation_envelope = cast(dict[str, JsonValue], definitions["observation_envelope"])
        envelope_properties = cast(dict[str, JsonValue], observation_envelope["properties"])
        structural_payload = cast(
            dict[str, JsonValue], envelope_properties["structural_payload"]
        )
        structural_properties = cast(
            dict[str, JsonValue], structural_payload["properties"]
        )
        structural_properties.update(
            {
                "correlation_kind": {
                    "enum": ["generation_id", "none", "tool_call_id"],
                    "type": "string",
                },
                "generation_id": {
                    "maxLength": 128,
                    "minLength": 1,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
                    "type": "string",
                },
                "pairing_mode": {
                    "enum": ["paired", "post_only"],
                    "type": "string",
                },
            }
        )
    if entry.schema_name == "control-result":
        # Project lifecycle refusals are bounded application reasons, not transport failures. 2.5
        # is the first control envelope that carries the CLI-only project method, so extend only
        # this unreleased schema's generic error branch; every older control-result artifact stays
        # frozen. Keep the finite vocabulary explicit here so a caller cannot smuggle arbitrary
        # project text into a wire error code.
        coordination_reasons = (
            "coordination_invalid",
            "project_not_found",
            "project_dissolved",
            "implicit_project_requires_opt_out",
            "general_project_membership_conflict",
            "project_member_not_found",
            "selector_conflict",
            "coordination_consent_required",
            "coordination_grant_required",
            "coordination_generation_revoked",
            "coordination_generation_mismatch",
            "cross_repository_lineage_requires_grant",
            "project_member_already_unbound",
        )
        error_body = definitions.get("error_body")
        if not isinstance(error_body, dict):
            raise SchemaGenerationError(
                "control_error_schema_template_invalid", entries=(entry.relative_path,)
            )
        error_branches = error_body.get("oneOf")
        if not isinstance(error_branches, list):
            raise SchemaGenerationError(
                "control_error_schema_template_invalid", entries=(entry.relative_path,)
            )
        for error_branch in error_branches:
            if not isinstance(error_branch, dict):
                continue
            properties = error_branch.get("properties")
            if not isinstance(properties, dict):
                continue
            code_schema = properties.get("code")
            if not isinstance(code_schema, dict):
                continue
            enum_values = code_schema.get("enum")
            if not isinstance(enum_values, list):
                continue
            enum_values.extend(
                reason for reason in coordination_reasons if reason not in enum_values
            )
            enum_values.sort(key=lambda value: str(value).encode("ascii"))
            break
        else:
            raise SchemaGenerationError(
                "control_error_schema_template_invalid", entries=(entry.relative_path,)
            )
    if entry.schema_name == "control-request":
        definitions["project_body"] = _control_project_request_schema()
        template = next(
            cast(dict[str, JsonValue], branch)
            for branch in branches
            if isinstance(branch, dict)
            and cast(dict[str, JsonValue], branch.get("properties", {})).get("method")
            == {"const": "review"}
        )
        project_branch = cast(dict[str, JsonValue], json.loads(json.dumps(template)))
        project_properties = cast(dict[str, JsonValue], project_branch["properties"])
        project_properties["body"] = {"$ref": "#/$defs/project_body"}
        project_properties["method"] = {"const": "project"}
        branches.append(project_branch)
        branches.sort(
            key=lambda branch: str(
                cast(dict[str, JsonValue], cast(dict[str, JsonValue], branch).get("properties", {}))
                .get("method", {})
                .get("const", "")
            ).encode("ascii")
        )
        return document
    definitions.update(_control_project_result_schema()["$defs"])
    result_defs = _control_project_result_schema()
    # The result helper's top-level oneOf is referenced from the project success branch below;
    # its local definitions are merged into the existing envelope definitions.
    template_error = next(
        cast(dict[str, JsonValue], branch)
        for branch in branches
        if isinstance(branch, dict)
        and cast(dict[str, JsonValue], branch.get("properties", {})).get("method")
        == {"const": "review"}
        and cast(dict[str, JsonValue], branch.get("properties", {})).get("outcome")
        == {"const": "error"}
    )
    template_ok = next(
        cast(dict[str, JsonValue], branch)
        for branch in branches
        if isinstance(branch, dict)
        and cast(dict[str, JsonValue], branch.get("properties", {})).get("method")
        == {"const": "review"}
        and cast(dict[str, JsonValue], branch.get("properties", {})).get("outcome")
        == {"const": "ok"}
    )
    error_branch = cast(dict[str, JsonValue], json.loads(json.dumps(template_error)))
    ok_branch = cast(dict[str, JsonValue], json.loads(json.dumps(template_ok)))
    cast(dict[str, JsonValue], error_branch["properties"])["method"] = {"const": "project"}
    cast(dict[str, JsonValue], ok_branch["properties"])["method"] = {"const": "project"}
    cast(dict[str, JsonValue], ok_branch["properties"])["body"] = {"oneOf": result_defs["oneOf"]}
    branches.extend((error_branch, ok_branch))
    branches.sort(
        key=lambda branch: (
            str(
                cast(dict[str, JsonValue], cast(dict[str, JsonValue], branch).get("properties", {}))
                .get("method", {})
                .get("const", "")
            ).encode("ascii"),
            str(
                cast(dict[str, JsonValue], cast(dict[str, JsonValue], branch).get("properties", {}))
                .get("outcome", {})
                .get("const", "")
            ).encode("ascii"),
        )
    )
    return document


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
        "common/lineage-acceptance-1.0.0.schema.json",
        "lineage-acceptance",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: (
            __import__(
                "yoetz.domain.coordination", fromlist=["LineageAcceptance"]
            ).LineageAcceptance
        ),
    ),
    _RegistryEntry(
        "common/lineage-origin-1.0.0.schema.json",
        "lineage-origin",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.domain.coordination", fromlist=["LineageOrigin"]).LineageOrigin,
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
        "common/session-health-1.0.0.schema.json",
        "session-health",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.domain.coordination", fromlist=["SessionHealth"]).SessionHealth,
    ),
    _RegistryEntry(
        "common/work-state-1.0.0.schema.json",
        "work-state",
        "1.0.0",
        "request_result",
        "common-value",
        lambda: __import__("yoetz.domain.coordination", fromlist=["WorkState"]).WorkState,
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
        "config/yoetz-config-1.1.0.schema.json",
        "yoetz-config",
        "1.1.0",
        "config",
        "configuration",
        lambda: __import__("yoetz.config.models", fromlist=["YoetzConfig"]).YoetzConfig,
    ),
    _RegistryEntry(
        "config/yoetz-config-1.2.0.schema.json",
        "yoetz-config",
        "1.2.0",
        "config",
        "configuration",
        lambda: __import__("yoetz.config.models", fromlist=["YoetzConfig"]).YoetzConfig,
    ),
    _RegistryEntry(
        "config/yoetz-config-1.3.0.schema.json",
        "yoetz-config",
        "1.3.0",
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
        None,
    ),
    _RegistryEntry(
        "consent/catalog-5.0.0.schema.json",
        "catalog",
        "5.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/catalog-6.0.0.schema.json",
        "catalog",
        "6.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentCatalogModel"]
            ).ConsentCatalogModel
        ),
    ),
    _RegistryEntry(
        "consent/catalog-7.0.0.schema.json",
        "catalog",
        "7.0.0",
        "request_result",
        "local-control",
        None,
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
        None,
    ),
    _RegistryEntry(
        "consent/pending-agent-5.0.0.schema.json",
        "pending-agent",
        "5.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/pending-agent-6.0.0.schema.json",
        "pending-agent",
        "6.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["AgentSafePendingModel"]
            ).AgentSafePendingModel
        ),
    ),
    _RegistryEntry(
        "consent/pending-agent-7.0.0.schema.json",
        "pending-agent",
        "7.0.0",
        "request_result",
        "local-control",
        None,
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
        None,
    ),
    _RegistryEntry(
        "consent/prepare-result-5.0.0.schema.json",
        "prepare-result",
        "5.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/prepare-result-6.0.0.schema.json",
        "prepare-result",
        "6.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentPrepareResultModel"]
            ).ConsentPrepareResultModel
        ),
    ),
    _RegistryEntry(
        "consent/prepare-result-7.0.0.schema.json",
        "prepare-result",
        "7.0.0",
        "request_result",
        "local-control",
        None,
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
        None,
    ),
    _RegistryEntry(
        "consent/review-result-5.0.0.schema.json",
        "review-result",
        "5.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/review-result-6.0.0.schema.json",
        "review-result",
        "6.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__(
                "yoetz.protocol.consent", fromlist=["ConsentReviewResultModel"]
            ).ConsentReviewResultModel
        ),
    ),
    _RegistryEntry(
        "consent/review-result-7.0.0.schema.json",
        "review-result",
        "7.0.0",
        "request_result",
        "local-control",
        None,
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
        None,
    ),
    _RegistryEntry(
        "consent/status-5.0.0.schema.json",
        "status",
        "5.0.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "consent/status-6.0.0.schema.json",
        "status",
        "6.0.0",
        "request_result",
        "local-control",
        lambda: (
            __import__("yoetz.protocol.consent", fromlist=["ConsentStatusModel"]).ConsentStatusModel
        ),
    ),
    _RegistryEntry(
        "consent/status-7.0.0.schema.json",
        "status",
        "7.0.0",
        "request_result",
        "local-control",
        None,
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
        "events/child-accepted-1.0.0.schema.json",
        "child-accepted",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ChildAcceptedPayload"]
            ).ChildAcceptedPayload
        ),
    ),
    _RegistryEntry(
        "events/child-dependencies-recorded-1.0.0.schema.json",
        "child-dependencies-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ChildDependenciesRecordedPayload"]
            ).ChildDependenciesRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/coordination-context-recorded-1.0.0.schema.json",
        "coordination-context-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["CoordinationContextRecordedPayload"]
            ).CoordinationContextRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/coordination-disposition-recorded-1.0.0.schema.json",
        "coordination-disposition-recorded",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["CoordinationDispositionRecordedPayload"]
            ).CoordinationDispositionRecordedPayload
        ),
    ),
    _RegistryEntry(
        "events/coordination-obligation-declared-1.0.0.schema.json",
        "coordination-obligation-declared",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["CoordinationObligationDeclaredPayload"]
            ).CoordinationObligationDeclaredPayload
        ),
    ),
    _RegistryEntry(
        "events/child-rejected-1.0.0.schema.json",
        "child-rejected",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ChildRejectedPayload"]
            ).ChildRejectedPayload
        ),
    ),
    _RegistryEntry(
        "events/child-written-off-1.0.0.schema.json",
        "child-written-off",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["ChildWrittenOffPayload"]
            ).ChildWrittenOffPayload
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
        "events/check-recorded-1.1.0.schema.json",
        "check-recorded",
        "1.1.0",
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
        "events/delegation-cancelled-1.0.0.schema.json",
        "delegation-cancelled",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["DelegationCancelledPayload"]
            ).DelegationCancelledPayload
        ),
    ),
    _RegistryEntry(
        "events/delegation-declared-1.0.0.schema.json",
        "delegation-declared",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["DelegationDeclaredPayload"]
            ).DelegationDeclaredPayload
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
        "events/event-draft-1.1.0.schema.json",
        "event-draft",
        "1.1.0",
        "event",
        "event-envelope",
        lambda: __import__("yoetz.domain.events", fromlist=["EventDraft"]).EventDraft,
    ),
    _RegistryEntry(
        "events/event-draft-1.2.0.schema.json",
        "event-draft",
        "1.2.0",
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
        "events/evidence-recorded-1.2.0.schema.json",
        "evidence-recorded",
        "1.2.0",
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
        "events/finding-recorded-1.1.0.schema.json",
        "finding-recorded",
        "1.1.0",
        "event",
        "event-payload",
        lambda: __import__("yoetz.domain.findings", fromlist=["Finding"]).Finding,
    ),
    _RegistryEntry(
        "events/finding-recorded-1.2.0.schema.json",
        "finding-recorded",
        "1.2.0",
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
        "events/opaque-unknown-event-draft-1.1.0.schema.json",
        "opaque-unknown-event-draft",
        "1.1.0",
        "event",
        "event-envelope",
        lambda: __import__("yoetz.domain.events", fromlist=["UnknownEvent"]).UnknownEvent,
    ),
    _RegistryEntry(
        "events/opaque-unknown-event-draft-1.2.0.schema.json",
        "opaque-unknown-event-draft",
        "1.2.0",
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
        "events/session-opened-1.1.0.schema.json",
        "session-opened",
        "1.1.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["SessionOpenedPayload"]
            ).SessionOpenedPayload
        ),
    ),
    _RegistryEntry(
        "events/session-opened-1.2.0.schema.json",
        "session-opened",
        "1.2.0",
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
        "events/session-resumed-1.1.0.schema.json",
        "session-resumed",
        "1.1.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["SessionResumedPayload"]
            ).SessionResumedPayload
        ),
    ),
    _RegistryEntry(
        "events/work-abandoned-1.0.0.schema.json",
        "work-abandoned",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["WorkAbandonedPayload"]
            ).WorkAbandonedPayload
        ),
    ),
    _RegistryEntry(
        "events/work-cancelled-1.0.0.schema.json",
        "work-cancelled",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["WorkCancelledPayload"]
            ).WorkCancelledPayload
        ),
    ),
    _RegistryEntry(
        "events/work-closed-1.0.0.schema.json",
        "work-closed",
        "1.0.0",
        "event",
        "event-payload",
        lambda: __import__("yoetz.domain.events", fromlist=["WorkClosedPayload"]).WorkClosedPayload,
    ),
    _RegistryEntry(
        "events/work-written-off-1.0.0.schema.json",
        "work-written-off",
        "1.0.0",
        "event",
        "event-payload",
        lambda: (
            __import__(
                "yoetz.domain.events", fromlist=["WorkWrittenOffPayload"]
            ).WorkWrittenOffPayload
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
        "findings/finding-1.1.0.schema.json",
        "finding",
        "1.1.0",
        "request_result",
        "finding",
        lambda: __import__("yoetz.domain.findings", fromlist=["Finding"]).Finding,
    ),
    _RegistryEntry(
        "findings/finding-1.2.0.schema.json",
        "finding",
        "1.2.0",
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
        "findings/semantic-provenance-1.1.0.schema.json",
        "semantic-provenance",
        "1.1.0",
        "request_result",
        "semantic-provenance",
        lambda: (
            __import__("yoetz.domain.findings", fromlist=["SemanticProvenance"]).SemanticProvenance
        ),
    ),
    _RegistryEntry(
        "findings/runtime-attempt-evidence-1.0.0.schema.json",
        "runtime-attempt-evidence",
        "1.0.0",
        "request_result",
        "semantic-provenance",
        lambda: (
            __import__(
                "yoetz.domain.findings", fromlist=["RuntimeAttemptEvidence"]
            ).RuntimeAttemptEvidence
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
        "operations/check-request-1.1.0.schema.json",
        "check-request",
        "1.1.0",
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
        "operations/check-result-1.1.0.schema.json",
        "check-result",
        "1.1.0",
        "request_result",
        "MCP output",
        lambda: __import__("yoetz.protocol.models", fromlist=["CheckResultModel"]).CheckResultModel,
    ),
    _RegistryEntry(
        "operations/check-result-1.2.0.schema.json",
        "check-result",
        "1.2.0",
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
        "operations/publish-work-request-1.1.0.schema.json",
        "publish-work-request",
        "1.1.0",
        "request_result",
        "MCP input",
        lambda: (
            __import__(
                "yoetz.protocol.models", fromlist=["PublishWorkRequestModel"]
            ).PublishWorkRequestModel
        ),
    ),
    _RegistryEntry(
        "operations/publish-work-request-1.2.0.schema.json",
        "publish-work-request",
        "1.2.0",
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
        "operations/receipt-result-1.1.0.schema.json",
        "receipt-result",
        "1.1.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["ReceiptResultModel"]).ReceiptResultModel
        ),
    ),
    _RegistryEntry(
        "operations/receipt-result-1.2.0.schema.json",
        "receipt-result",
        "1.2.0",
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
        "operations/start-request-1.1.0.schema.json",
        "start-request",
        "1.1.0",
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
        "operations/start-result-1.1.0.schema.json",
        "start-result",
        "1.1.0",
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
        "operations/status-request-1.2.0.schema.json",
        "status-request",
        "1.2.0",
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
        "operations/status-result-1.2.0.schema.json",
        "status-result",
        "1.2.0",
        "request_result",
        "MCP output",
        lambda: (
            __import__("yoetz.protocol.models", fromlist=["StatusResultModel"]).StatusResultModel
        ),
    ),
    _RegistryEntry(
        "operations/status-result-1.3.0.schema.json",
        "status-result",
        "1.3.0",
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
        "privacy/outbound-case-1.1.0.schema.json",
        "outbound-case",
        "1.1.0",
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
        "privacy/privacy-policy-1.1.0.schema.json",
        "privacy-policy",
        "1.1.0",
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
        "receipts/receipt-document-1.1.0.schema.json",
        "receipt-document",
        "1.1.0",
        "request_result",
        "receipt-document",
        lambda: __import__("yoetz.domain.receipts", fromlist=["ReceiptDocument"]).ReceiptDocument,
    ),
    _RegistryEntry(
        "receipts/receipt-document-1.2.0.schema.json",
        "receipt-document",
        "1.2.0",
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
        "service/control-hello-2.4.0.schema.json",
        "control-hello",
        "2.4.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.4.0.schema.json",
        "control-hello-result",
        "2.4.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-request-2.4.0.schema.json",
        "control-request",
        "2.4.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-result-2.4.0.schema.json",
        "control-result",
        "2.4.0",
        "request_result",
        "local-control",
        None,
    ),
    _RegistryEntry(
        "service/control-hello-2.5.0.schema.json",
        "control-hello",
        "2.5.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlRequest"]).ControlRequest,
    ),
    _RegistryEntry(
        "service/control-hello-result-2.5.0.schema.json",
        "control-hello-result",
        "2.5.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlResult"]).ControlResult,
    ),
    _RegistryEntry(
        "service/control-request-2.5.0.schema.json",
        "control-request",
        "2.5.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlRequest"]).ControlRequest,
    ),
    _RegistryEntry(
        "service/control-result-2.5.0.schema.json",
        "control-result",
        "2.5.0",
        "request_result",
        "local-control",
        lambda: __import__("yoetz.ports.control", fromlist=["ControlResult"]).ControlResult,
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
    _RegistryEntry(
        "version/version-manifest-2.2.0.schema.json",
        "version-manifest",
        "2.2.0",
        "version_manifest",
        "version-report",
        lambda: __import__("yoetz.version", fromlist=["VersionManifest"]).VersionManifest,
    ),
)


# These paths were introduced in the current protocol wave and are derived by this generator
# from the owning models/templates.  The other legacy ``loader=None`` entries are deliberately
# checked from their reviewed bytes only: their source model is not available to this tool and
# regenerating them would silently rewrite frozen history.
_BUILDER_OWNED_SCHEMA_PATHS: Final[frozenset[str]] = frozenset(
    {
        "common/lineage-acceptance-1.0.0.schema.json",
        "common/lineage-origin-1.0.0.schema.json",
        "common/session-health-1.0.0.schema.json",
        "common/work-state-1.0.0.schema.json",
        "events/child-accepted-1.0.0.schema.json",
        "events/child-dependencies-recorded-1.0.0.schema.json",
        "events/child-rejected-1.0.0.schema.json",
        "events/child-written-off-1.0.0.schema.json",
        "events/coordination-context-recorded-1.0.0.schema.json",
        "events/coordination-disposition-recorded-1.0.0.schema.json",
        "events/coordination-obligation-declared-1.0.0.schema.json",
        "events/delegation-cancelled-1.0.0.schema.json",
        "events/delegation-declared-1.0.0.schema.json",
        "events/event-draft-1.2.0.schema.json",
        "events/finding-recorded-1.2.0.schema.json",
        "events/opaque-unknown-event-draft-1.2.0.schema.json",
        "events/session-opened-1.2.0.schema.json",
        "events/work-abandoned-1.0.0.schema.json",
        "events/work-cancelled-1.0.0.schema.json",
        "events/work-closed-1.0.0.schema.json",
        "events/work-written-off-1.0.0.schema.json",
        "operations/check-result-1.2.0.schema.json",
        "config/yoetz-config-1.3.0.schema.json",
        "operations/check-request-1.1.0.schema.json",
        "operations/publish-work-request-1.2.0.schema.json",
        "operations/receipt-result-1.2.0.schema.json",
        "operations/start-request-1.1.0.schema.json",
        "operations/start-result-1.1.0.schema.json",
        "operations/status-request-1.2.0.schema.json",
        "operations/status-result-1.3.0.schema.json",
        "receipts/receipt-document-1.2.0.schema.json",
        "findings/finding-1.2.0.schema.json",
        "service/control-hello-2.5.0.schema.json",
        "service/control-hello-result-2.5.0.schema.json",
        "service/control-request-2.5.0.schema.json",
        "service/control-result-2.5.0.schema.json",
    }
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
            "common/lineage-acceptance-1.0.0.schema.json",
            "common/lineage-origin-1.0.0.schema.json",
            "common/session-health-1.0.0.schema.json",
            "common/work-state-1.0.0.schema.json",
        }:
            normalized = _lineage_vocabulary_schema(entry)
        elif entry.relative_path in {
            "privacy/privacy-policy-1.0.0.schema.json",
            "config/yoetz-config-1.0.0.schema.json",
            "config/yoetz-config-1.1.0.schema.json",
            "config/yoetz-config-1.2.0.schema.json",
            "events/check-recorded-1.0.0.schema.json",
            "events/finding-recorded-1.0.0.schema.json",
            "findings/finding-1.0.0.schema.json",
            "findings/semantic-provenance-1.0.0.schema.json",
            "operations/check-result-1.0.0.schema.json",
            "operations/receipt-result-1.0.0.schema.json",
        }:
            normalized = _frozen_schema(entry)
        elif entry.relative_path in {
            "events/plan-published-1.0.0.schema.json",
            "events/plan-revised-1.0.0.schema.json",
        }:
            normalized = _plan_payload_schema(entry)
        elif entry.relative_path == "events/event-draft-1.0.0.schema.json":
            normalized = _frozen_schema(entry)
        elif entry.relative_path == "events/event-draft-1.1.0.schema.json":
            normalized = _event_draft_v1_1_schema(entry)
        elif entry.relative_path == "events/event-draft-1.2.0.schema.json":
            normalized = _event_draft_v1_2_schema(entry)
        elif entry.relative_path == "events/check-recorded-1.1.0.schema.json":
            normalized = _check_recorded_v1_1_schema(entry)
        elif entry.relative_path == "events/finding-recorded-1.1.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "events/finding-recorded-1.0.0.schema.json",
                {"finding-1.0.0": "finding-1.1.0"},
            )
        elif entry.relative_path == "events/finding-recorded-1.2.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "events/finding-recorded-1.1.0.schema.json",
                {"finding-1.1.0": "finding-1.2.0"},
            )
        elif entry.relative_path in {
            "events/evidence-recorded-1.1.0.schema.json",
            "events/evidence-recorded-1.2.0.schema.json",
        }:
            normalized = _evidence_payload_schema(entry)
        elif entry.relative_path == "events/claim-recorded-1.1.0.schema.json":
            normalized = _claim_payload_schema(entry)
        elif entry.relative_path == "events/response-recorded-1.0.0.schema.json":
            normalized = _response_recorded_schema(entry)
        elif entry.relative_path in {
            "events/child-accepted-1.0.0.schema.json",
            "events/child-dependencies-recorded-1.0.0.schema.json",
            "events/child-rejected-1.0.0.schema.json",
            "events/child-written-off-1.0.0.schema.json",
            "events/delegation-cancelled-1.0.0.schema.json",
            "events/delegation-declared-1.0.0.schema.json",
            "events/work-abandoned-1.0.0.schema.json",
            "events/work-cancelled-1.0.0.schema.json",
            "events/work-closed-1.0.0.schema.json",
            "events/work-written-off-1.0.0.schema.json",
        }:
            normalized = _lineage_event_schema(entry)
        elif entry.relative_path in {
            "events/coordination-context-recorded-1.0.0.schema.json",
            "events/coordination-obligation-declared-1.0.0.schema.json",
            "events/coordination-disposition-recorded-1.0.0.schema.json",
        }:
            normalized = _coordination_event_schema(entry)
        elif entry.relative_path == "events/session-opened-1.2.0.schema.json":
            normalized = _lineage_session_opened_schema(entry)
        elif entry.relative_path == "events/opaque-unknown-event-draft-1.0.0.schema.json":
            normalized = _frozen_schema(entry)
        elif entry.relative_path == "events/opaque-unknown-event-draft-1.1.0.schema.json":
            normalized = _opaque_unknown_event_v1_1_schema(entry)
        elif entry.relative_path == "events/opaque-unknown-event-draft-1.2.0.schema.json":
            normalized = _opaque_unknown_event_v1_2_schema(entry)
        elif entry.relative_path in {
            "service/control-hello-2.5.0.schema.json",
            "service/control-hello-result-2.5.0.schema.json",
            "service/control-request-2.5.0.schema.json",
            "service/control-result-2.5.0.schema.json",
        }:
            normalized = _control_v2_5_schema(entry)
        elif entry.relative_path == "operations/publish-work-request-1.1.0.schema.json":
            normalized = _publish_work_request_schema(entry)
        elif entry.relative_path == "operations/publish-work-request-1.2.0.schema.json":
            normalized = _publish_work_request_v1_2_schema(entry)
        elif entry.relative_path == "findings/runtime-attempt-evidence-1.0.0.schema.json":
            normalized = _runtime_attempt_evidence_schema(entry)
        elif entry.relative_path == "findings/semantic-provenance-1.1.0.schema.json":
            normalized = _semantic_provenance_v1_1_schema(entry)
        elif entry.relative_path == "findings/finding-1.1.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "findings/finding-1.0.0.schema.json",
                {"semantic-provenance-1.0.0": "semantic-provenance-1.1.0"},
            )
        elif entry.relative_path == "findings/finding-1.2.0.schema.json":
            normalized = _finding_v1_2_schema(entry)
        elif entry.relative_path == "operations/check-request-1.1.0.schema.json":
            normalized = _check_request_v1_1_schema(entry)
        elif entry.relative_path == "operations/check-result-1.1.0.schema.json":
            normalized = _check_result_v1_1_schema(entry)
        elif entry.relative_path == "operations/check-result-1.2.0.schema.json":
            normalized = _check_result_v1_2_schema(entry)
        elif entry.relative_path == "operations/receipt-result-1.1.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "operations/receipt-result-1.0.0.schema.json",
                {"receipt-document-1.0.0": "receipt-document-1.1.0"},
            )
        elif entry.relative_path == "operations/receipt-result-1.2.0.schema.json":
            normalized = _receipt_result_v1_2_schema(entry)
        elif entry.relative_path == "operations/start-result-1.0.0.schema.json":
            normalized = _start_result_schema(entry)
        elif entry.relative_path == "operations/start-result-1.1.0.schema.json":
            normalized = _start_result_v1_1_schema(entry)
        elif entry.relative_path == "operations/start-request-1.1.0.schema.json":
            normalized = _start_request_v1_1_schema(entry)
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
        elif entry.relative_path == "operations/status-request-1.2.0.schema.json":
            normalized = _status_request_v1_2_schema(entry)
        elif entry.relative_path in {
            "operations/status-result-1.0.0.schema.json",
            "operations/status-result-1.1.0.schema.json",
        }:
            normalized = _status_result_schema(entry)
        elif entry.relative_path == "operations/status-result-1.2.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "operations/status-result-1.1.0.schema.json",
                {"semantic-provenance-1.0.0": "semantic-provenance-1.1.0"},
            )
        elif entry.relative_path == "operations/status-result-1.3.0.schema.json":
            normalized = _status_result_v1_3_schema(entry)
        elif entry.relative_path == "receipts/receipt-document-1.0.0.schema.json":
            normalized = _receipt_document_schema(entry)
        elif entry.relative_path == "receipts/receipt-document-1.1.0.schema.json":
            normalized = _simple_versioned_schema(
                entry,
                "receipts/receipt-document-1.0.0.schema.json",
                {
                    "finding-1.0.0": "finding-1.1.0",
                    "semantic-provenance-1.0.0": "semantic-provenance-1.1.0",
                },
            )
        elif entry.relative_path == "receipts/receipt-document-1.2.0.schema.json":
            normalized = _receipt_document_v1_2_schema(entry)
        elif entry.relative_path in {
            "version/version-manifest-2.0.0.schema.json",
            "version/version-manifest-2.1.0.schema.json",
        }:
            normalized = _frozen_version_manifest_schema(entry)
        elif entry.relative_path == "version/version-manifest-2.2.0.schema.json":
            normalized = _version_manifest_schema(entry)
        elif entry.relative_path == "privacy/privacy-policy-1.1.0.schema.json":
            normalized = _privacy_policy_v1_1_schema(entry)
        elif entry.relative_path == "privacy/outbound-case-1.1.0.schema.json":
            normalized = _outbound_case_schema(entry)
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
    parser.add_argument(
        "--include-builder-owned",
        action="store_true",
        help="With --write, include the current builder-owned wave without rewriting frozen schemas.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    root = args.output_root.resolve() if args.output_root is not None else _default_schema_root()
    selected_entries = _REGISTRY
    if args.only or args.include_builder_owned:
        if args.check:
            parser.error("schema selection is supported only with --write")
        requested = frozenset(args.only) | (
            _BUILDER_OWNED_SCHEMA_PATHS if args.include_builder_owned else frozenset()
        )
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
        # ``schema_root`` mode intentionally treats legacy loader-less entries as reviewed
        # documents.  Rebuild only the current wave's owned paths as an additional guard so a
        # changed derivation cannot leave a stale source file while the disk-only check remains
        # green.  This subset is read-only and never rewrites manifests or schema files.
        builder_entries = tuple(
            entry
            for entry in selected_entries
            if entry.relative_path in _BUILDER_OWNED_SCHEMA_PATHS
        )
        if builder_entries:
            try:
                generated = build_schema_documents(entries=builder_entries)
                for document in generated:
                    validate_schema_document(document)
            except SchemaGenerationError as exc:
                print(f"generate_schemas: FAIL ({exc.reason})", file=sys.stderr)
                for entry in exc.entries:
                    print(f"  {entry}", file=sys.stderr)
                return 1
            generated_diff = compare_tree(generated, root)
            # ``compare_tree`` also reports every schema outside this deliberate subset as
            # ``extra``.  Those files are expected here because the subset is only the current
            # builder-owned wave, so only missing or changed owned paths are drift.
            if generated_diff.missing or generated_diff.changed:
                print("generate_schemas: FAIL (builder-owned drift detected)", file=sys.stderr)
                for relative_path in generated_diff.missing:
                    print(f"  missing {relative_path}", file=sys.stderr)
                for relative_path in generated_diff.changed:
                    print(f"  changed {relative_path}", file=sys.stderr)
                return 1
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
    if args.only or args.include_builder_owned:
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
