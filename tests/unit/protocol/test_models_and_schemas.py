"""B0 schema-catalog checks; model parity joins this owner with models.py."""

from __future__ import annotations

import hashlib
import shutil
import socket
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError, fields
from importlib import resources
from pathlib import Path
from typing import cast

import pytest

import yoetz.protocol.schemas as schemas_module
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import (
    SCHEMA_MANIFEST_SCHEMA,
    SCHEMA_MANIFEST_VERSION,
    SCHEMA_MEMBER_COUNT,
    SCHEMA_NAMESPACE,
    SchemaArtifactRole,
    SchemaCatalog,
    SchemaDocument,
    SchemaKind,
    event_schema_versions,
    load_schema_catalog,
    request_result_schema_versions,
    schema_document_for,
    schema_path_for,
    schema_uri,
    validate_schema_document,
    validate_schema_instance,
)

_VALID_COVERAGE: dict[str, JsonValue] = {
    "publication_channels": ["cooperative_mcp"],
    "authorship_assurance": "self_asserted",
    "artifact_observation": "published_only",
    "evidence_immutability": "content_digest",
    "ledger_freshness": "current",
    "check_types": ["none"],
    "known_gaps": [],
}


def _assert_reason(exc_info: pytest.ExceptionInfo[ProtocolValueError], reason: str) -> None:
    assert exc_info.value.reason_code == reason
    assert exc_info.value.args == (reason,)


def _walk_frozen(value: object) -> None:
    assert not isinstance(value, dict | list | set)
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        for key, item in source.items():
            assert type(key) is str
            _walk_frozen(item)
    elif isinstance(value, tuple):
        for item in cast(tuple[object, ...], value):
            _walk_frozen(item)


def _count_refs(value: object) -> int:
    count = 0
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        for key, item in source.items():
            if key == "$ref":
                assert type(item) is str
                count += 1
            count += _count_refs(item)
    elif isinstance(value, tuple):
        count += sum(_count_refs(item) for item in cast(tuple[object, ...], value))
    return count


def _plain_object(data: bytes) -> dict[str, JsonValue]:
    value = strict_json_parse(data)
    assert type(value) is dict
    return cast(dict[str, JsonValue], value)


def _schema_tree(tmp_path: Path, case: str) -> Path:
    source = Path(__file__).resolve().parents[3] / "src" / "yoetz" / "resources" / "schemas"
    destination = tmp_path / case
    shutil.copytree(source, destination)
    return destination


def _write_canonical(path: Path, value: dict[str, JsonValue]) -> None:
    path.write_bytes(canonical_encode(value))


def _manifest_members(tree: Path) -> tuple[dict[str, JsonValue], list[JsonValue]]:
    manifest = _plain_object((tree / "manifest.json").read_bytes())
    members = manifest["members"]
    assert type(members) is list
    return manifest, cast(list[JsonValue], members)


def _rewrite_schema(
    tree: Path,
    relative_path: str,
    mutate: Callable[[dict[str, JsonValue]], None],
) -> None:
    schema_path = tree.joinpath(*relative_path.split("/"))
    schema = _plain_object(schema_path.read_bytes())
    mutate(schema)
    schema_bytes = canonical_encode(schema)
    schema_path.write_bytes(schema_bytes)

    manifest_path = tree / "manifest.json"
    manifest = _plain_object(manifest_path.read_bytes())
    members = manifest["members"]
    assert type(members) is list
    for raw_member in members:
        assert type(raw_member) is dict
        member = cast(dict[str, JsonValue], raw_member)
        if member["path"] == relative_path:
            member["byte_length"] = len(schema_bytes)
            member["sha256"] = f"sha256:{hashlib.sha256(schema_bytes).hexdigest()}"
            break
    else:
        raise AssertionError("schema_member_not_found")
    _write_canonical(manifest_path, manifest)


def _load_tree_with_reason(
    tree: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    monkeypatch.setattr(schemas_module, "_schema_root", lambda: tree)
    schemas_module._load_catalog_state.cache_clear()  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(ProtocolValueError) as exc_info:
            schemas_module.load_schema_catalog()
        _assert_reason(exc_info, reason)
    finally:
        schemas_module._load_catalog_state.cache_clear()  # pyright: ignore[reportPrivateUsage]


def test_schema_catalog_reports_complete_registry() -> None:
    catalog = load_schema_catalog()
    assert SCHEMA_NAMESPACE == "https://schemas.yoetz.dev/0.1/"
    assert SCHEMA_MANIFEST_SCHEMA == "yoetz.schema-manifest/1.0.0"
    assert SCHEMA_MANIFEST_VERSION == "1.0.0"
    assert SCHEMA_MEMBER_COUNT == 52
    assert len(catalog.documents) == SCHEMA_MEMBER_COUNT

    paths = tuple(document.relative_path for document in catalog.documents)
    assert paths == tuple(sorted(paths, key=str.encode))
    assert tuple(catalog.by_path) == paths
    assert len(catalog.by_id) == len(catalog.by_name_version) == SCHEMA_MEMBER_COUNT
    assert set(SchemaKind) == {
        SchemaKind.REQUEST_RESULT,
        SchemaKind.EVENT,
        SchemaKind.CONFIG,
        SchemaKind.VERSION_MANIFEST,
    }
    assert len(SchemaArtifactRole) == 17


def test_schema_uri_and_path_resolution_are_stable() -> None:
    assert schema_path_for("action-recorded", "1.0.0") == (
        "events/action-recorded-1.0.0.schema.json"
    )
    assert schema_uri("action-recorded", "1.0.0") == (
        "https://schemas.yoetz.dev/0.1/events/action-recorded-1.0.0.schema.json"
    )
    document = schema_document_for("action-recorded", "1.0.0")
    assert document.schema_name == "action-recorded"
    assert document.schema_id == schema_uri("action-recorded", "1.0.0")

    for name, version, reason in (
        ("events/action-recorded", "1.0.0", "schema_name_invalid"),
        ("action_recorded", "1.0.0", "schema_name_invalid"),
        ("../action-recorded", "1.0.0", "schema_name_invalid"),
        ("action%2drecorded", "1.0.0", "schema_name_invalid"),
        ("é", "1.0.0", "schema_name_invalid"),
        ("action-recorded", "01.0.0", "schema_name_invalid"),
        ("action-recorded", "1.0", "schema_name_invalid"),
        ("absent-schema", "1.0.0", "schema_not_found"),
    ):
        with pytest.raises(ProtocolValueError) as exc_info:
            schema_path_for(name, version)
        _assert_reason(exc_info, reason)


def test_schema_catalog_record_shape_and_indexes_are_exact() -> None:
    assert tuple(field.name for field in fields(SchemaDocument)) == (
        "schema_kind",
        "artifact_role",
        "schema_name",
        "schema_version",
        "schema_id",
        "relative_path",
        "canonical_digest",
        "schema_bytes",
        "json_schema",
    )
    assert tuple(field.name for field in fields(SchemaCatalog)) == (
        "documents",
        "by_path",
        "by_id",
        "by_name_version",
        "request_result_versions",
        "event_schema_versions",
        "manifest_version",
        "manifest_digest",
    )

    catalog = load_schema_catalog()
    for document in catalog.documents:
        assert catalog.by_path[document.relative_path] is document
        assert catalog.by_id[document.schema_id] is document
        assert catalog.by_name_version[(document.schema_name, document.schema_version)] is document
        assert canonical_encode(document.json_schema) == document.schema_bytes
        _walk_frozen(document.json_schema)
        validate_schema_document(document)

    with pytest.raises(TypeError):
        catalog.by_path["x"] = catalog.documents[0]  # type: ignore[index]
    with pytest.raises(TypeError):
        catalog.documents[0].json_schema["x"] = None  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        catalog.documents[0].schema_name = "changed"  # type: ignore[misc]

    root = resources.files("yoetz").joinpath("resources", "schemas")
    manifest_bytes = root.joinpath("manifest.json").read_bytes()
    assert catalog.manifest_digest == f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    assert sum(_count_refs(document.json_schema) for document in catalog.documents) == 1_251


def test_schema_name_derivation_and_version_maps_are_exact() -> None:
    catalog = load_schema_catalog()
    request_versions = request_result_schema_versions(catalog)
    event_versions = event_schema_versions(catalog)
    assert request_versions is catalog.request_result_versions
    assert event_versions is catalog.event_schema_versions
    assert len(request_versions) == 31
    assert len(event_versions) == 16
    assert tuple(request_versions) == tuple(sorted(request_versions, key=str.encode))
    assert tuple(event_versions) == tuple(sorted(event_versions, key=str.encode))
    assert set(request_versions.values()) == {"1.0.0"}
    assert set(event_versions.values()) == {"1.0.0"}
    assert event_versions["action_recorded"] == "1.0.0"
    assert "accepted_event" not in event_versions
    assert "event_draft" not in event_versions
    assert "opaque_unknown_event_draft" not in event_versions


def test_schema_manifest_failure_reason_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _schema_tree(tmp_path, "missing")
    (missing / "manifest.json").unlink()
    _load_tree_with_reason(missing, monkeypatch, "schema_manifest_missing")

    noncanonical = _schema_tree(tmp_path, "noncanonical")
    manifest_path = noncanonical / "manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    _load_tree_with_reason(noncanonical, monkeypatch, "schema_manifest_invalid")

    duplicate = _schema_tree(tmp_path, "duplicate")
    manifest, members = _manifest_members(duplicate)
    assert type(members[0]) is dict
    members[1] = dict(cast(dict[str, JsonValue], members[0]))
    _write_canonical(duplicate / "manifest.json", manifest)
    _load_tree_with_reason(duplicate, monkeypatch, "schema_manifest_duplicate_path")

    extra = _schema_tree(tmp_path, "extra")
    (extra / "unlisted.txt").write_text("unlisted", encoding="utf-8")
    _load_tree_with_reason(extra, monkeypatch, "schema_manifest_member_mismatch")

    unsafe = _schema_tree(tmp_path, "unsafe")
    manifest, members = _manifest_members(unsafe)
    assert type(members[0]) is dict
    cast(dict[str, JsonValue], members[0])["path"] = "../escape.schema.json"
    _write_canonical(unsafe / "manifest.json", manifest)
    _load_tree_with_reason(unsafe, monkeypatch, "schema_path_unsafe")

    digest = _schema_tree(tmp_path, "digest")
    schema_path = digest / "common" / "actor-assertion-1.0.0.schema.json"
    schema_bytes = schema_path.read_bytes()
    schema_path.write_bytes(schema_bytes[:-1] + b"]")
    _load_tree_with_reason(digest, monkeypatch, "schema_digest_mismatch")

    invalid_role = _schema_tree(tmp_path, "invalid-role")
    manifest, members = _manifest_members(invalid_role)
    assert type(members[0]) is dict
    cast(dict[str, JsonValue], members[0])["artifact_role"] = "unknown-role"
    _write_canonical(invalid_role / "manifest.json", manifest)
    _load_tree_with_reason(invalid_role, monkeypatch, "schema_artifact_role_invalid")

    wrong_role = _schema_tree(tmp_path, "wrong-role")
    manifest, members = _manifest_members(wrong_role)
    assert type(members[0]) is dict
    cast(dict[str, JsonValue], members[0])["artifact_role"] = "MCP input"
    _write_canonical(wrong_role / "manifest.json", manifest)
    _load_tree_with_reason(wrong_role, monkeypatch, "schema_artifact_role_mismatch")

    wrong_kind = _schema_tree(tmp_path, "wrong-kind")
    manifest, members = _manifest_members(wrong_kind)
    assert type(members[0]) is dict
    cast(dict[str, JsonValue], members[0])["schema_kind"] = "event"
    _write_canonical(wrong_kind / "manifest.json", manifest)
    _load_tree_with_reason(wrong_kind, monkeypatch, "schema_kind_mismatch")

    wrong_draft = _schema_tree(tmp_path, "wrong-draft")

    def change_draft(schema: dict[str, JsonValue]) -> None:
        schema["$schema"] = "https://json-schema.org/draft/2019-09/schema"

    _rewrite_schema(
        wrong_draft,
        "common/actor-assertion-1.0.0.schema.json",
        change_draft,
    )
    _load_tree_with_reason(wrong_draft, monkeypatch, "schema_draft_unsupported")

    wrong_id = _schema_tree(tmp_path, "wrong-id")

    def change_id(schema: dict[str, JsonValue]) -> None:
        schema["$id"] = "https://schemas.yoetz.dev/0.1/common/not-the-route.schema.json"

    _rewrite_schema(wrong_id, "common/actor-assertion-1.0.0.schema.json", change_id)
    _load_tree_with_reason(wrong_id, monkeypatch, "schema_id_mismatch")

    invalid_schema = _schema_tree(tmp_path, "invalid-schema")

    def break_metaschema(schema: dict[str, JsonValue]) -> None:
        schema["type"] = 7

    _rewrite_schema(
        invalid_schema,
        "common/actor-assertion-1.0.0.schema.json",
        break_metaschema,
    )
    _load_tree_with_reason(invalid_schema, monkeypatch, "schema_bytes_invalid")

    unresolved = _schema_tree(tmp_path, "unresolved")

    def add_unresolved_ref(schema: dict[str, JsonValue]) -> None:
        schema["x-unresolved"] = {
            "$ref": "https://schemas.yoetz.dev/0.1/common/missing-1.0.0.schema.json"
        }

    _rewrite_schema(
        unresolved,
        "common/actor-assertion-1.0.0.schema.json",
        add_unresolved_ref,
    )
    _load_tree_with_reason(unresolved, monkeypatch, "schema_reference_unresolved")


def test_schema_instance_validation_is_closed_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", deny_network)
    schemas_module._load_catalog_state.cache_clear()  # pyright: ignore[reportPrivateUsage]
    try:
        validate_schema_instance("coverage", "1.0.0", _VALID_COVERAGE)

        invalid_cases: tuple[JsonValue, ...] = (
            {**_VALID_COVERAGE, "extra": True},
            {key: value for key, value in _VALID_COVERAGE.items() if key != "known_gaps"},
            {**_VALID_COVERAGE, "publication_channels": []},
        )
        for invalid in invalid_cases:
            with pytest.raises(ProtocolValueError) as exc_info:
                validate_schema_instance("coverage", "1.0.0", invalid)
            _assert_reason(exc_info, "schema_instance_invalid")

        with pytest.raises(ProtocolValueError) as exc_info:
            validate_schema_instance("coverage", "1.0.0", cast(JsonValue, {"value": 1.0}))
        _assert_reason(exc_info, "float_forbidden")
    finally:
        schemas_module._load_catalog_state.cache_clear()  # pyright: ignore[reportPrivateUsage]
