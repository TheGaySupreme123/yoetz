from __future__ import annotations

import hashlib
import importlib
import importlib.resources as resources
import importlib.util
from collections.abc import Iterator, Mapping
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urldefrag, urlparse

import pytest

from yoetz.protocol.canonical import canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT_SCHEMA_DIR = _REPO_ROOT / "schemas"
_PACKAGE_SCHEMA_DIR = resources.files("yoetz").joinpath("resources").joinpath("schemas")
_MANIFEST_PATH = "manifest.json"
_SCHEMA_NAMESPACE = "https://schemas.yoetz.dev/0.1/"
_EXPECTED_SCHEMA_MANIFEST_SCHEMA = "yoetz.schema-manifest/1.0.0"
_EXPECTED_SCHEMA_MANIFEST_VERSION = "1.0.0"
_EXPECTED_MEMBER_COUNT = 58
_EXPECTED_REQUEST_RESULT_VERSION_COUNT = 37
_EXPECTED_EVENT_VERSION_COUNT = 16


def _schema_module() -> Any:
    if importlib.util.find_spec("yoetz.protocol.schemas") is None:
        pytest.skip("yoetz.protocol.schemas not implemented yet")
    return importlib.import_module("yoetz.protocol.schemas")


def _collect_traversable_files(root: Traversable, prefix: str = "") -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for child in root.iterdir():
        rel = f"{prefix}{child.name}"
        if child.is_file():
            result[rel] = child.read_bytes()
        else:
            result.update(_collect_traversable_files(child, f"{rel}/"))
    return result


def _load_manifest_bytes(root: Path) -> bytes:
    return (root / _MANIFEST_PATH).read_bytes()


def _load_manifest(value: bytes) -> dict[str, Any]:
    parsed = strict_json_parse(value)
    if not isinstance(parsed, dict):
        raise AssertionError("manifest_root_not_object")
    return parsed


def _schema_files_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    members = manifest.get("members")
    if not isinstance(members, list):
        raise AssertionError("manifest_members_not_list")
    return cast(list[dict[str, Any]], members)


def _assert_manifest_member_shape(member: dict[str, Any]) -> None:
    expected_keys = {
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
    if set(member) != expected_keys:
        raise AssertionError(f"unexpected_manifest_member_keys:{sorted(member)}")
    if type(member["path"]) is not str or not member["path"]:
        raise AssertionError("schema_path_invalid")
    if type(member["schema_version"]) is not str or not member["schema_version"]:
        raise AssertionError("schema_version_invalid")
    if type(member["$id"]) is not str or not member["$id"].startswith(_SCHEMA_NAMESPACE):
        raise AssertionError("schema_id_invalid")
    if type(member["media_type"]) is not str or member["media_type"] != "application/schema+json":
        raise AssertionError("schema_media_type_invalid")
    if type(member["byte_length"]) is not int or member["byte_length"] < 0:
        raise AssertionError("schema_byte_length_invalid")
    if type(member["sha256"]) is not str or not member["sha256"].startswith("sha256:"):
        raise AssertionError("schema_digest_invalid")


def _schema_name_version(member: dict[str, Any]) -> tuple[str, str]:
    path = PurePosixPath(member["path"])
    stem = path.name.removesuffix(".schema.json")
    schema_version = member["schema_version"]
    if not stem.endswith(f"-{schema_version}"):
        raise AssertionError("schema_version_suffix_mismatch")
    schema_name = stem[: -(len(schema_version) + 1)]
    return schema_name, schema_version


def _walk_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in cast(dict[Any, Any], node).items():
            if key == "$ref" and type(value) is str:
                yield value
            yield from _walk_refs(value)
    elif isinstance(node, list | tuple):
        for item in cast(list[Any] | tuple[Any, ...], node):
            yield from _walk_refs(item)


def _assert_reference_closed(schema_text: bytes, member_ids: set[str]) -> None:
    parsed = strict_json_parse(schema_text)
    if not isinstance(parsed, dict):
        raise AssertionError("schema_document_not_object")
    for ref in _walk_refs(parsed):
        parsed_ref = urlparse(ref)
        if (
            parsed_ref.fragment
            and not parsed_ref.scheme
            and not parsed_ref.netloc
            and parsed_ref.path == ""
        ):
            continue
        if parsed_ref.query:
            raise AssertionError("schema_reference_query_forbidden")
        if parsed_ref.scheme != "https" or parsed_ref.netloc != "schemas.yoetz.dev":
            raise AssertionError("schema_reference_not_local")
        if not parsed_ref.path.startswith("/0.1/"):
            raise AssertionError("schema_reference_not_namespaced")
        if urldefrag(ref)[0] not in member_ids:
            raise AssertionError("schema_reference_unresolved")


def _assert_public_json_frozen(value: Any) -> None:
    if isinstance(value, MappingProxyType):
        for item in cast(Mapping[Any, Any], value).values():
            _assert_public_json_frozen(item)
        return
    if isinstance(value, tuple):
        for item in cast(tuple[Any, ...], value):
            _assert_public_json_frozen(item)
        return
    if value is None or type(value) in {bool, int, str}:
        return
    raise AssertionError(f"mutable_schema_value:{type(value).__name__}")


def test_schema_registry_is_complete() -> None:
    manifest_bytes = _load_manifest_bytes(_ROOT_SCHEMA_DIR)
    manifest = _load_manifest(manifest_bytes)
    members = _schema_files_from_manifest(manifest)

    assert manifest["manifest_schema"] == _EXPECTED_SCHEMA_MANIFEST_SCHEMA
    assert manifest["manifest_version"] == _EXPECTED_SCHEMA_MANIFEST_VERSION
    assert len(members) == _EXPECTED_MEMBER_COUNT

    paths = [member["path"] for member in members]
    assert paths == sorted(paths, key=lambda item: item.encode("ascii"))
    assert len(paths) == len(set(paths))
    assert len({member["$id"] for member in members}) == len(members)
    for member in members:
        expected = "2.0.0" if member["path"].startswith("consent/") else "1.0.0"
        assert member["schema_version"] == expected
    for member in members:
        _assert_manifest_member_shape(member)
    assert canonical_encode(manifest) == manifest_bytes


def test_schema_root_and_package_resources_are_byte_identical() -> None:
    root_files: dict[str, bytes] = {}
    for child in _ROOT_SCHEMA_DIR.iterdir():
        if child.is_file():
            root_files[child.name] = child.read_bytes()
        elif child.is_dir():
            for grandchild in child.rglob("*"):
                if grandchild.is_file():
                    root_files[str(grandchild.relative_to(_ROOT_SCHEMA_DIR)).replace("\\", "/")] = (
                        grandchild.read_bytes()
                    )

    package_files = _collect_traversable_files(_PACKAGE_SCHEMA_DIR)

    assert set(root_files) == set(package_files)
    for rel_path, root_bytes in root_files.items():
        package_bytes = package_files[rel_path]
        assert root_bytes == package_bytes


def test_schema_documents_are_reference_closed() -> None:
    manifest = _load_manifest(_load_manifest_bytes(_ROOT_SCHEMA_DIR))
    member_paths = {member["path"] for member in _schema_files_from_manifest(manifest)}
    member_ids = {member["$id"] for member in _schema_files_from_manifest(manifest)}

    for rel_path in member_paths:
        _assert_reference_closed((_ROOT_SCHEMA_DIR / rel_path).read_bytes(), member_ids)


def test_schema_documents_are_frozen_when_catalog_available() -> None:
    schemas = _schema_module()
    catalog = schemas.load_schema_catalog()
    manifest = _load_manifest(_load_manifest_bytes(_ROOT_SCHEMA_DIR))
    members = _schema_files_from_manifest(manifest)

    assert len(catalog.documents) == _EXPECTED_MEMBER_COUNT
    assert catalog.manifest_version == _EXPECTED_SCHEMA_MANIFEST_VERSION
    assert (
        catalog.manifest_digest
        == f"sha256:{hashlib.sha256(_load_manifest_bytes(_ROOT_SCHEMA_DIR)).hexdigest()}"
    )
    assert len(catalog.request_result_versions) == _EXPECTED_REQUEST_RESULT_VERSION_COUNT
    assert len(catalog.event_schema_versions) == _EXPECTED_EVENT_VERSION_COUNT

    by_path = dict(catalog.by_path)
    by_id = dict(catalog.by_id)
    by_name_version = dict(catalog.by_name_version)

    for member in members:
        schema_name, schema_version = _schema_name_version(member)
        doc = by_path[member["path"]]
        assert by_id[member["$id"]] is doc
        assert by_name_version[(schema_name, schema_version)] is doc
        assert doc.schema_id == member["$id"]
        assert doc.schema_version == schema_version
        assert doc.relative_path == member["path"]
        assert doc.schema_bytes == (_ROOT_SCHEMA_DIR / member["path"]).read_bytes()
        assert canonical_encode(doc.json_schema) == doc.schema_bytes
        _assert_public_json_frozen(doc.json_schema)

    assert isinstance(catalog.by_path, MappingProxyType)
    assert isinstance(catalog.by_id, MappingProxyType)
    assert isinstance(catalog.by_name_version, MappingProxyType)


def test_schema_uris_and_versions_are_stable() -> None:
    schemas = _schema_module()
    catalog = schemas.load_schema_catalog()
    manifest = _load_manifest(_load_manifest_bytes(_ROOT_SCHEMA_DIR))

    for member in _schema_files_from_manifest(manifest):
        schema_name, schema_version = _schema_name_version(member)
        assert schemas.schema_path_for(schema_name, schema_version) == member["path"]
        assert schemas.schema_uri(schema_name, schema_version) == member["$id"]
        assert schemas.schema_document_for(schema_name, schema_version).schema_id == member["$id"]

    assert schemas.request_result_schema_versions(catalog) == catalog.request_result_versions
    assert schemas.event_schema_versions(catalog) == catalog.event_schema_versions


def test_schema_instance_validation_stays_local_when_catalog_available() -> None:
    schemas = _schema_module()
    try:
        schemas.validate_schema_instance("public-error", "1.0.0", {})
    except ProtocolValueError as exc:
        assert exc.reason_code == "schema_instance_invalid"
    else:
        raise AssertionError("schema_instance_invalid_not_raised")
