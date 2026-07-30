"""Deterministic public JSON Schema generator and parity checker for the Yoetz protocol.

Generates and verifies the reviewable JSON Schema files for the public six-operation
request/result models, shared common values, durable event payloads, configuration, findings,
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
    for document in documents:
        member = by_path.get(document.relative_path)
        if member is None:
            raise SchemaGenerationError(
                "schema_manifest_member_missing",
                entries=(document.relative_path,),
            )
        member["byte_length"] = len(document.schema_bytes)
        member["sha256"] = "sha256:" + hashlib.sha256(document.schema_bytes).hexdigest()
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
