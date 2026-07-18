"""Frozen packaged JSON Schema catalog with closed local reference resolution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Final, Never, Protocol, cast
from urllib.parse import urldefrag, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012, Schema, SchemaRegistry

from yoetz.protocol.canonical import (
    JsonValue,
    canonical_encode,
    ensure_canonical_value,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "SCHEMA_MANIFEST_SCHEMA",
    "SCHEMA_MANIFEST_VERSION",
    "SCHEMA_MEMBER_COUNT",
    "SCHEMA_NAMESPACE",
    "SchemaArtifactRole",
    "SchemaCatalog",
    "SchemaDocument",
    "SchemaKind",
    "event_schema_versions",
    "load_schema_catalog",
    "request_result_schema_versions",
    "schema_document_for",
    "schema_path_for",
    "schema_uri",
    "validate_schema_document",
    "validate_schema_instance",
]

SCHEMA_NAMESPACE: Final = "https://schemas.yoetz.dev/0.1/"
SCHEMA_MANIFEST_SCHEMA: Final = "yoetz.schema-manifest/1.0.0"
SCHEMA_MANIFEST_VERSION: Final = "1.0.0"
SCHEMA_MEMBER_COUNT: Final = 52

_DRAFT_2020_12: Final = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_MEDIA_TYPE: Final = "application/schema+json"
_MANIFEST_FIELDS: Final = frozenset({"manifest_schema", "manifest_version", "members"})
_MEMBER_FIELDS: Final = frozenset(
    {
        "$id",
        "artifact_role",
        "byte_length",
        "media_type",
        "owning_model",
        "path",
        "schema_kind",
        "schema_version",
        "sha256",
    }
)
_SCHEMA_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", re.ASCII)
_SCHEMA_VERSION_PATTERN: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$", re.ASCII
)
_SHA256_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_RFC3339_DATE_TIME_PATTERN: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$",
    re.ASCII,
)


class SchemaKind(str, Enum):  # noqa: UP042 - the wire contract fixes these bases
    REQUEST_RESULT = "request_result"
    EVENT = "event"
    CONFIG = "config"
    VERSION_MANIFEST = "version_manifest"


class SchemaArtifactRole(str, Enum):  # noqa: UP042 - the wire contract fixes these bases
    COMMON_VALUE = "common-value"
    MCP_INPUT = "MCP input"
    MCP_OUTPUT = "MCP output"
    PERSISTED_ENVELOPE = "persisted-envelope"
    EVENT_ENVELOPE = "event-envelope"
    EVENT_PAYLOAD = "event-payload"
    CONFIGURATION = "configuration"
    FINDING = "finding"
    SEMANTIC_PROVENANCE = "semantic-provenance"
    RECEIPT_DOCUMENT = "receipt-document"
    PRIVACY_POLICY = "privacy-policy"
    OUTBOUND_CASE = "outbound-case"
    PRIVACY_AUDIT = "privacy-audit"
    SETUP_CONTRACT = "setup-contract"
    LOCAL_CONTROL = "local-control"
    SERVICE_STATUS = "service-status"
    VERSION_REPORT = "version-report"


@dataclass(frozen=True, slots=True)
class SchemaDocument:
    schema_kind: SchemaKind
    artifact_role: SchemaArtifactRole
    schema_name: str
    schema_version: str
    schema_id: str
    relative_path: str
    canonical_digest: str
    schema_bytes: bytes
    json_schema: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SchemaCatalog:
    documents: tuple[SchemaDocument, ...]
    by_path: Mapping[str, SchemaDocument]
    by_id: Mapping[str, SchemaDocument]
    by_name_version: Mapping[tuple[str, str], SchemaDocument]
    request_result_versions: Mapping[str, str]
    event_schema_versions: Mapping[str, str]
    manifest_version: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class _ManifestMember:
    path: str
    schema_id: str
    schema_version: str
    schema_kind: SchemaKind
    artifact_role: SchemaArtifactRole
    byte_length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _CatalogState:
    catalog: SchemaCatalog
    plain_by_id: Mapping[str, dict[str, JsonValue]]
    registry: SchemaRegistry


class _ValidatorProtocol(Protocol):
    def validate(self, instance: object) -> None: ...


_FORMAT_CHECKER: Final = FormatChecker(formats=())


def _is_rfc3339_date_time(value: object) -> bool:
    if type(value) is not str:
        return True
    if _RFC3339_DATE_TIME_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


_FORMAT_CHECKER.checks("date-time")(_is_rfc3339_date_time)


def _deny_retrieve(uri: str) -> Never:
    raise NoSuchResource(uri)


def _protocol_error(reason: str) -> Never:
    raise ProtocolValueError(reason)


def _actual_mapping(value: object) -> bool:
    try:
        return issubclass(type(value), Mapping)
    except BaseException:
        return False


def _as_plain_object(value: JsonValue, reason: str) -> dict[str, JsonValue]:
    if type(value) is not dict:
        _protocol_error(reason)
    return cast(dict[str, JsonValue], value)


def _read_resource(resource: Traversable, reason: str) -> bytes:
    try:
        if not resource.is_file():
            _protocol_error(reason)
        return resource.read_bytes()
    except ProtocolValueError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ProtocolValueError(reason) from exc


def _schema_root() -> Traversable:
    return resources.files("yoetz").joinpath("resources", "schemas")


def _resource_at(root: Traversable, relative_path: str) -> Traversable:
    resource = root
    for part in relative_path.split("/"):
        resource = resource.joinpath(part)
    return resource


def _validate_relative_schema_path(value: object) -> str:
    if type(value) is not str:
        _protocol_error("schema_path_unsafe")
    path = value
    try:
        encoded = path.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise ProtocolValueError("schema_path_unsafe") from exc
    if (
        not encoded
        or path.startswith("/")
        or "\\" in path
        or "%" in path
        or "//" in path
        or not path.endswith(".schema.json")
    ):
        _protocol_error("schema_path_unsafe")
    parts = path.split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        _protocol_error("schema_path_unsafe")
    return path


def _collect_files(root: Traversable) -> frozenset[str]:
    collected: set[str] = set()

    def _walk(node: Traversable, prefix: str) -> None:
        try:
            children = tuple(node.iterdir())
        except (OSError, ValueError, TypeError) as exc:
            raise ProtocolValueError("schema_manifest_member_mismatch") from exc
        for child in children:
            name = child.name
            relative = f"{prefix}{name}"
            try:
                if child.is_file():
                    collected.add(relative)
                elif child.is_dir():
                    _walk(child, f"{relative}/")
            except (OSError, ValueError, TypeError) as exc:
                raise ProtocolValueError("schema_manifest_member_mismatch") from exc

    _walk(root, "")
    return frozenset(collected)


def _parse_manifest(data: bytes) -> tuple[_ManifestMember, ...]:
    try:
        parsed = strict_json_parse(data)
        if canonical_encode(parsed) != data:
            _protocol_error("schema_manifest_invalid")
        manifest = _as_plain_object(parsed, "schema_manifest_invalid")
    except ProtocolValueError as exc:
        if exc.reason_code == "schema_manifest_invalid":
            raise
        raise ProtocolValueError("schema_manifest_invalid") from None

    if frozenset(manifest) != _MANIFEST_FIELDS:
        _protocol_error("schema_manifest_invalid")
    if (
        manifest["manifest_schema"] != SCHEMA_MANIFEST_SCHEMA
        or manifest["manifest_version"] != SCHEMA_MANIFEST_VERSION
    ):
        _protocol_error("schema_manifest_invalid")
    raw_members = manifest["members"]
    if type(raw_members) is not list or len(raw_members) != SCHEMA_MEMBER_COUNT:
        _protocol_error("schema_manifest_member_mismatch")

    members: list[_ManifestMember] = []
    seen_paths: set[str] = set()
    declared_paths: list[str] = []
    for raw_member in raw_members:
        if type(raw_member) is not dict:
            _protocol_error("schema_manifest_member_mismatch")
        member = cast(dict[str, JsonValue], raw_member)
        if frozenset(member) != _MEMBER_FIELDS:
            _protocol_error("schema_manifest_member_mismatch")

        path = _validate_relative_schema_path(member["path"])
        if path in seen_paths:
            _protocol_error("schema_manifest_duplicate_path")
        seen_paths.add(path)
        declared_paths.append(path)

        schema_id = member["$id"]
        schema_version = member["schema_version"]
        media_type = member["media_type"]
        owning_model = member["owning_model"]
        byte_length = member["byte_length"]
        digest = member["sha256"]
        if (
            type(schema_id) is not str
            or type(schema_version) is not str
            or type(media_type) is not str
            or media_type != _SCHEMA_MEDIA_TYPE
            or type(owning_model) is not str
            or not owning_model
            or type(byte_length) is not int
            or byte_length < 0
            or type(digest) is not str
            or _SHA256_PATTERN.fullmatch(digest) is None
        ):
            _protocol_error("schema_manifest_member_mismatch")

        raw_kind = member["schema_kind"]
        if type(raw_kind) is not str:
            _protocol_error("schema_kind_mismatch")
        try:
            kind = SchemaKind(raw_kind)
        except ValueError:
            _protocol_error("schema_kind_mismatch")

        raw_role = member["artifact_role"]
        if type(raw_role) is not str:
            _protocol_error("schema_artifact_role_invalid")
        try:
            role = SchemaArtifactRole(raw_role)
        except ValueError:
            _protocol_error("schema_artifact_role_invalid")

        members.append(
            _ManifestMember(
                path=path,
                schema_id=schema_id,
                schema_version=schema_version,
                schema_kind=kind,
                artifact_role=role,
                byte_length=byte_length,
                sha256=digest,
            )
        )

    if declared_paths != sorted(declared_paths, key=str.encode):
        _protocol_error("schema_manifest_invalid")
    return tuple(members)


def _derive_identity(path: str, version: str) -> tuple[str, str]:
    if _SCHEMA_VERSION_PATTERN.fullmatch(version) is None:
        _protocol_error("schema_version_mismatch")
    filename = path.rsplit("/", 1)[1]
    suffix = f"-{version}.schema.json"
    if not filename.endswith(suffix):
        _protocol_error("schema_version_mismatch")
    name = filename[: -len(suffix)]
    if _SCHEMA_NAME_PATTERN.fullmatch(name) is None:
        _protocol_error("schema_version_mismatch")
    return name, version


def _derive_kind(path: str) -> SchemaKind:
    prefix = path.split("/", 1)[0]
    if prefix == "events":
        return SchemaKind.EVENT
    if prefix == "config":
        return SchemaKind.CONFIG
    if prefix == "version":
        return SchemaKind.VERSION_MANIFEST
    if prefix in {"common", "operations", "findings", "receipts", "privacy", "service"}:
        return SchemaKind.REQUEST_RESULT
    _protocol_error("schema_kind_mismatch")


def _derive_role(path: str) -> SchemaArtifactRole:
    directory, filename = path.split("/", 1)
    if directory == "common":
        if filename.startswith("operation-result-"):
            return SchemaArtifactRole.MCP_OUTPUT
        return SchemaArtifactRole.COMMON_VALUE
    if directory == "operations":
        if "-request-" in filename:
            return SchemaArtifactRole.MCP_INPUT
        if "-result-" in filename:
            return SchemaArtifactRole.MCP_OUTPUT
        _protocol_error("schema_artifact_role_mismatch")
    if directory == "events":
        if filename.startswith("accepted-event-"):
            return SchemaArtifactRole.PERSISTED_ENVELOPE
        if filename.startswith(("event-draft-", "opaque-unknown-event-draft-")):
            return SchemaArtifactRole.EVENT_ENVELOPE
        return SchemaArtifactRole.EVENT_PAYLOAD
    if directory == "findings":
        if filename.startswith("finding-"):
            return SchemaArtifactRole.FINDING
        if filename.startswith("semantic-provenance-"):
            return SchemaArtifactRole.SEMANTIC_PROVENANCE
        _protocol_error("schema_artifact_role_mismatch")
    if directory == "config":
        return SchemaArtifactRole.CONFIGURATION
    if directory == "receipts":
        return SchemaArtifactRole.RECEIPT_DOCUMENT
    if directory == "version":
        return SchemaArtifactRole.VERSION_REPORT
    if directory == "privacy":
        if filename.startswith("privacy-policy-"):
            return SchemaArtifactRole.PRIVACY_POLICY
        if filename.startswith("outbound-case-"):
            return SchemaArtifactRole.OUTBOUND_CASE
        if filename.startswith("egress-receipt-"):
            return SchemaArtifactRole.PRIVACY_AUDIT
        if filename.startswith("setup-wizard-contract-"):
            return SchemaArtifactRole.SETUP_CONTRACT
        _protocol_error("schema_artifact_role_mismatch")
    if directory == "service":
        if filename.startswith("service-status-"):
            return SchemaArtifactRole.SERVICE_STATUS
        if filename.startswith(
            ("control-hello-", "control-hello-result-", "control-request-", "control-result-")
        ):
            return SchemaArtifactRole.LOCAL_CONTROL
        _protocol_error("schema_artifact_role_mismatch")
    _protocol_error("schema_artifact_role_mismatch")


def _freeze_json(value: JsonValue) -> JsonValue:
    if type(value) is dict:
        source = cast(dict[str, JsonValue], value)
        frozen = {
            key: _freeze_json(source[key])
            for key in sorted(source, key=lambda item: item.encode("utf-16-be"))
        }
        return MappingProxyType(frozen)
    if type(value) is list:
        return tuple(_freeze_json(item) for item in cast(list[JsonValue], value))
    return value


def _walk_refs(value: JsonValue) -> Iterator[str]:
    if type(value) is dict:
        source = cast(dict[str, JsonValue], value)
        for key, item in source.items():
            if key == "$ref":
                if type(item) is not str:
                    _protocol_error("schema_reference_unresolved")
                yield item
            yield from _walk_refs(item)
    elif type(value) is list:
        for item in cast(list[JsonValue], value):
            yield from _walk_refs(item)


def _build_registry(plain_by_id: Mapping[str, dict[str, JsonValue]]) -> SchemaRegistry:
    registry: Registry[Schema] = Registry(retrieve=_deny_retrieve)
    for schema_id in sorted(plain_by_id, key=str.encode):
        registry = registry.with_resource(
            schema_id, DRAFT202012.create_resource(plain_by_id[schema_id])
        )
    return registry


def _validate_references(
    plain_by_id: Mapping[str, dict[str, JsonValue]], registry: SchemaRegistry
) -> None:
    known_ids = frozenset(plain_by_id)
    for schema_id in sorted(plain_by_id, key=str.encode):
        schema = plain_by_id[schema_id]
        resolver = registry.resolver(schema_id)
        for ref in _walk_refs(schema):
            split = urlsplit(ref)
            if split.query:
                _protocol_error("schema_reference_unresolved")
            if ref.startswith("#"):
                admissible = split.scheme == split.netloc == split.path == ""
            else:
                base, _ = urldefrag(ref)
                admissible = (
                    bool(split.scheme)
                    and bool(split.netloc)
                    and base in known_ids
                    and split.scheme == "https"
                )
            if not admissible:
                _protocol_error("schema_reference_unresolved")
            try:
                resolver.lookup(ref)
            except BaseException:
                raise ProtocolValueError("schema_reference_unresolved") from None


def _plain_schema(data: bytes) -> dict[str, JsonValue]:
    try:
        parsed = strict_json_parse(data)
        if canonical_encode(parsed) != data:
            _protocol_error("schema_bytes_invalid")
        schema = _as_plain_object(parsed, "schema_bytes_invalid")
    except ProtocolValueError as exc:
        if exc.reason_code == "schema_bytes_invalid":
            raise
        raise ProtocolValueError("schema_bytes_invalid") from None
    dialect = schema.get("$schema")
    if dialect != _DRAFT_2020_12:
        _protocol_error("schema_draft_unsupported")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ProtocolValueError("schema_bytes_invalid") from None
    return schema


@lru_cache(maxsize=1)
def _load_catalog_state() -> _CatalogState:
    root = _schema_root()
    manifest_resource = root.joinpath("manifest.json")
    manifest_bytes = _read_resource(manifest_resource, "schema_manifest_missing")
    members = _parse_manifest(manifest_bytes)
    expected_files = frozenset({"manifest.json", *(member.path for member in members)})
    if _collect_files(root) != expected_files:
        _protocol_error("schema_manifest_member_mismatch")

    documents: list[SchemaDocument] = []
    plain_by_id: dict[str, dict[str, JsonValue]] = {}
    seen_identities: set[tuple[str, str]] = set()

    for member in members:
        resource = _resource_at(root, member.path)
        data = _read_resource(resource, "schema_manifest_member_mismatch")
        if len(data) != member.byte_length:
            _protocol_error("schema_manifest_member_mismatch")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if digest != member.sha256:
            _protocol_error("schema_digest_mismatch")
        plain = _plain_schema(data)
        name, version = _derive_identity(member.path, member.schema_version)
        expected_id = SCHEMA_NAMESPACE + member.path
        document_id = plain.get("$id")
        if member.schema_id != expected_id or document_id != expected_id:
            _protocol_error("schema_id_mismatch")
        if member.schema_kind is not _derive_kind(member.path):
            _protocol_error("schema_kind_mismatch")
        if member.artifact_role is not _derive_role(member.path):
            _protocol_error("schema_artifact_role_mismatch")
        identity = (name, version)
        if expected_id in plain_by_id or identity in seen_identities:
            _protocol_error("schema_duplicate_identity")
        plain_by_id[expected_id] = plain
        seen_identities.add(identity)
        frozen = _freeze_json(plain)
        if not _actual_mapping(frozen):
            _protocol_error("schema_bytes_invalid")
        documents.append(
            SchemaDocument(
                schema_kind=member.schema_kind,
                artifact_role=member.artifact_role,
                schema_name=name,
                schema_version=version,
                schema_id=expected_id,
                relative_path=member.path,
                canonical_digest=digest,
                schema_bytes=data,
                json_schema=cast(Mapping[str, JsonValue], frozen),
            )
        )

    registry = _build_registry(plain_by_id)
    _validate_references(plain_by_id, registry)

    ordered_documents = tuple(documents)
    by_path_dict = {document.relative_path: document for document in ordered_documents}
    by_id_dict = {
        document.schema_id: document
        for document in sorted(ordered_documents, key=lambda item: item.schema_id.encode("ascii"))
    }
    by_name_version_dict = {
        (document.schema_name, document.schema_version): document
        for document in sorted(
            ordered_documents,
            key=lambda item: (
                item.schema_name.encode("ascii"),
                item.schema_version.encode("ascii"),
            ),
        )
    }
    request_versions_dict = {
        document.schema_name: document.schema_version
        for document in sorted(
            (item for item in ordered_documents if item.schema_kind is SchemaKind.REQUEST_RESULT),
            key=lambda item: item.schema_name.encode("ascii"),
        )
    }
    event_versions_dict = {
        document.schema_name.replace("-", "_"): document.schema_version
        for document in sorted(
            (
                item
                for item in ordered_documents
                if item.artifact_role is SchemaArtifactRole.EVENT_PAYLOAD
            ),
            key=lambda item: item.schema_name.replace("-", "_").encode("ascii"),
        )
    }
    if len(request_versions_dict) != 31 or len(event_versions_dict) != 16:
        _protocol_error("schema_catalog_incomplete")

    catalog = SchemaCatalog(
        documents=ordered_documents,
        by_path=MappingProxyType(by_path_dict),
        by_id=MappingProxyType(by_id_dict),
        by_name_version=MappingProxyType(by_name_version_dict),
        request_result_versions=MappingProxyType(request_versions_dict),
        event_schema_versions=MappingProxyType(event_versions_dict),
        manifest_version=SCHEMA_MANIFEST_VERSION,
        manifest_digest=f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
    )
    return _CatalogState(
        catalog=catalog,
        plain_by_id=MappingProxyType(plain_by_id),
        registry=registry,
    )


def load_schema_catalog() -> SchemaCatalog:
    """Load and validate the immutable packaged schema catalog."""

    return _load_catalog_state().catalog


def _validate_lookup_identity(name: object, version: object) -> tuple[str, str]:
    if (
        type(name) is not str
        or type(version) is not str
        or _SCHEMA_NAME_PATTERN.fullmatch(name) is None
        or _SCHEMA_VERSION_PATTERN.fullmatch(version) is None
    ):
        _protocol_error("schema_name_invalid")
    return name, version


def schema_path_for(name: str, version: str) -> str:
    """Return the exact packaged path for one canonical schema identity."""

    identity = _validate_lookup_identity(name, version)
    document = load_schema_catalog().by_name_version.get(identity)
    if document is None:
        _protocol_error("schema_not_found")
    return document.relative_path


def schema_uri(name: str, version: str) -> str:
    """Return the static-host URI for one canonical schema identity."""

    return SCHEMA_NAMESPACE + schema_path_for(name, version)


def schema_document_for(name: str, version: str) -> SchemaDocument:
    """Return one validated packaged schema document."""

    identity = _validate_lookup_identity(name, version)
    document = load_schema_catalog().by_name_version.get(identity)
    if document is None:
        _protocol_error("schema_not_found")
    return document


def validate_schema_document(document: SchemaDocument) -> None:
    """Recheck the bounded public invariants of one schema document."""

    if type(document) is not SchemaDocument:
        _protocol_error("schema_bytes_invalid")
    if hashlib.sha256(document.schema_bytes).hexdigest() != document.canonical_digest.removeprefix(
        "sha256:"
    ):
        _protocol_error("schema_digest_mismatch")
    plain = _plain_schema(document.schema_bytes)
    expected_id = SCHEMA_NAMESPACE + document.relative_path
    if plain.get("$id") != expected_id or document.schema_id != expected_id:
        _protocol_error("schema_id_mismatch")
    name, version = _derive_identity(document.relative_path, document.schema_version)
    if name != document.schema_name or version != document.schema_version:
        _protocol_error("schema_version_mismatch")
    if document.schema_kind is not _derive_kind(document.relative_path):
        _protocol_error("schema_kind_mismatch")
    if document.artifact_role is not _derive_role(document.relative_path):
        _protocol_error("schema_artifact_role_mismatch")
    try:
        if canonical_encode(document.json_schema) != document.schema_bytes:
            _protocol_error("schema_bytes_invalid")
    except ProtocolValueError:
        raise ProtocolValueError("schema_bytes_invalid") from None


def request_result_schema_versions(catalog: SchemaCatalog) -> Mapping[str, str]:
    """Return the catalog's immutable request/result version map."""

    if type(catalog) is not SchemaCatalog:
        raise TypeError("schema_catalog_wrong_type")
    return catalog.request_result_versions


def event_schema_versions(catalog: SchemaCatalog) -> Mapping[str, str]:
    """Return the catalog's immutable event-payload version map."""

    if type(catalog) is not SchemaCatalog:
        raise TypeError("schema_catalog_wrong_type")
    return catalog.event_schema_versions


def validate_schema_instance(name: str, version: str, value: JsonValue) -> None:
    """Validate a canonical JSON value against one exact local schema."""

    document = schema_document_for(name, version)
    ensure_canonical_value(value)
    state = _load_catalog_state()
    plain = state.plain_by_id[document.schema_id]
    validator = Draft202012Validator(
        plain,
        registry=state.registry,
        format_checker=_FORMAT_CHECKER,
    )
    try:
        validator_api = cast(_ValidatorProtocol, cast(object, validator))
        validator_api.validate(value)
    except ValidationError:
        raise ProtocolValueError("schema_instance_invalid") from None
    except BaseException:
        raise ProtocolValueError("schema_instance_invalid") from None
