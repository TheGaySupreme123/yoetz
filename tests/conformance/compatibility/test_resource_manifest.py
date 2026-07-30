"""Compatibility conformance: the packaged resource manifest matches the reviewed source set.

Grounded entirely in the real, installed ``yoetz.version`` module -- the sole runtime authority for
resource parity (``src/yoetz/version.py``). The positive checks call only
its public surface (``build_version_manifest``, ``verify_resource_manifest``,
``read_verified_resource``). The closed-validation negative cases drive the same private
``_load_resource_manifest`` seam ``yoetz.version`` itself uses at startup, reached only through
``getattr`` (matching the established pattern in
``tests/integration/storage/test_build_and_pragma_gate.py``) and monkeypatched at the
``_package_bytes`` boundary so real validation logic runs against synthetic bytes -- never against a
file this suite writes into the installed package tree.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import yoetz.version as version_module
from yoetz.ports.diagnostics import StartupCheckOutcome
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.version import (
    ResourceIntegrityError,
    build_version_manifest,
    read_verified_resource,
    verify_resource_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

_EXPECTED_RESOURCE_COUNTS = {
    "canonical_vectors": "9",
    "guidance_resources": "4",
    "migrations": "6",
    "runtime_support_resources": "1",
    "schema_resources": "59",
    "skill_resources": "2",
    "total": "81",
}


def _package_bytes_fn() -> Callable[[str], bytes]:
    return cast(Callable[[str], bytes], getattr(version_module, "_package_bytes"))


def _load_resource_manifest_fn() -> Callable[[], Any]:
    return cast(Callable[[], Any], getattr(version_module, "_load_resource_manifest"))


def test_root_resource_bytes_match_manifest() -> None:
    """Every installed resource is byte-identical to both its checked-in root source and manifest."""

    manifest = build_version_manifest()
    assert dict(manifest.resource_counts) == _EXPECTED_RESOURCE_COUNTS
    assert len(manifest.resources) == 81

    for resource in manifest.resources:
        installed = read_verified_resource(resource.name)
        assert len(installed) == resource.size_bytes, resource.name
        digest = f"sha256:{hashlib.sha256(installed).hexdigest()}"
        assert digest == resource.sha256_digest, resource.name

        root_path = _REPO_ROOT / resource.name
        assert root_path.is_file(), resource.name
        assert root_path.read_bytes() == installed, resource.name

    checks = verify_resource_manifest(manifest)
    assert len(checks) == 1
    assert checks[0].outcome is StartupCheckOutcome.OK
    assert checks[0].reason_code is None

    support_bytes = read_verified_resource("support/runtime-support.json")
    support = cast(dict[str, JsonValue], strict_json_parse(support_bytes))
    assert support["resource_set_digest"] == manifest.resource_manifest_digest

    # Equal installed bytes and runtime identities always produce a byte-identical manifest.
    second = build_version_manifest()
    assert second.resources == manifest.resources
    assert second.resource_manifest_digest == manifest.resource_manifest_digest


def _baseline_entries() -> tuple[list[JsonValue], dict[str, JsonValue]]:
    raw = _package_bytes_fn()("manifest.json")
    doc = cast(dict[str, JsonValue], strict_json_parse(raw))
    entries = cast(list[JsonValue], doc["entries"])
    return list(entries), doc


def _install_synthetic_manifest(
    monkeypatch: pytest.MonkeyPatch, doc: dict[str, JsonValue], entries: list[JsonValue]
) -> None:
    mutated: dict[str, JsonValue] = dict(doc)
    mutated["entries"] = entries
    payload = canonical_encode(mutated) + b"\n"
    original = _package_bytes_fn()

    def fake(package_path: str) -> bytes:
        if package_path == "manifest.json":
            return payload
        return original(package_path)

    monkeypatch.setattr(version_module, "_package_bytes", fake)


def test_missing_extra_duplicate_and_traversal_cases_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest validation is closed: wrong count, duplicates, and traversal names all fail."""

    load_resource_manifest = _load_resource_manifest_fn()
    entries, doc = _baseline_entries()
    assert len(entries) == 81

    # missing -- dropping the last entry breaks the exact inventory count.
    _install_synthetic_manifest(monkeypatch, doc, entries[:-1])
    with pytest.raises(ResourceIntegrityError):
        load_resource_manifest()

    # extra -- appending one more, uniquely named entry also breaks the exact count.
    extra_entry: dict[str, JsonValue] = dict(cast(dict[str, JsonValue], entries[0]))
    extra_entry["logical_name"] = "guidance/extra-not-real.md"
    extra_entry["package_path"] = "guidance/extra-not-real.md"
    extra_entry["source_path"] = "guidance/extra-not-real.md"
    extra_entry["kind"] = "guidance"
    extra_entry["media_type"] = "text/markdown"
    extra_entry["sha256"] = "sha256:" + "0" * 64
    _install_synthetic_manifest(monkeypatch, doc, [*entries, extra_entry])
    with pytest.raises(ResourceIntegrityError):
        load_resource_manifest()

    # Duplicate: preserve the exact count while displacing one real logical name.
    duplicate_entries = [*entries[:-1], dict(cast(dict[str, JsonValue], entries[0]))]
    _install_synthetic_manifest(monkeypatch, doc, cast(list[JsonValue], duplicate_entries))
    with pytest.raises(ResourceIntegrityError):
        load_resource_manifest()

    # traversal -- a path-traversal source_path is rejected before the count check is even reached.
    traversal_entries = [dict(cast(dict[str, JsonValue], item)) for item in entries]
    traversal_entries[0]["source_path"] = "../evil.txt"
    _install_synthetic_manifest(monkeypatch, doc, cast(list[JsonValue], traversal_entries))
    with pytest.raises(ResourceIntegrityError):
        load_resource_manifest()

    # The real, unmodified manifest still loads cleanly once the monkeypatch is undone.
    monkeypatch.undo()
    load_resource_manifest()


def test_public_resource_list_matches_release_artifact() -> None:
    """The public resource set is complete, stable, and matches the release support artifact."""

    load_resource_manifest = _load_resource_manifest_fn()
    manifest = load_resource_manifest()
    entries = cast(tuple[Any, ...], manifest.entries)

    kinds = [cast(str, entry.kind) for entry in entries]
    assert kinds.count("json_schema") == 59
    assert kinds.count("canonical_vector") == 9
    assert kinds.count("migration") == 6
    assert kinds.count("guidance") == 4
    assert kinds.count("runtime_support") == 1
    assert kinds.count("skill") == 1
    assert kinds.count("compatibility_manifest") == 1

    schema_paths = {
        cast(str, entry.logical_name) for entry in entries if entry.kind == "json_schema"
    }
    assert "schemas/manifest.json" in schema_paths
    # 58 JSON Schema documents plus the one schema inventory manifest.
    assert len(schema_paths) - 1 == 58

    names = [cast(str, entry.logical_name) for entry in entries]
    assert len(names) == 81
    assert names == sorted(set(names), key=lambda item: item.encode("ascii"))

    version_manifest = build_version_manifest()
    assert [resource.name for resource in version_manifest.resources] == names

    resource_set_digest = cast(str, manifest.resource_set_digest)
    assert resource_set_digest == version_manifest.resource_manifest_digest

    support_bytes = read_verified_resource("support/runtime-support.json")
    support = cast(dict[str, JsonValue], strict_json_parse(support_bytes))
    assert support["resource_set_digest"] == resource_set_digest
