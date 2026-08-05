from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from yoetz.ports.diagnostics import StartupCheckOutcome
from yoetz.version import (
    ResourceIntegrityError,
    build_version_manifest,
    read_verified_resource,
    verify_resource_manifest,
    version_manifest_json,
)


def test_development_manifest_is_truthful_and_complete() -> None:
    manifest = build_version_manifest()

    assert manifest.support_status == "development_unverified"
    assert manifest.mcp_protocol_supported == ()
    assert manifest.codex_capability_profiles == ()
    assert manifest.service_capabilities == ()
    assert dict(manifest.subject_state_capabilities) == {"status": "absent"}
    assert manifest.limitations == (
        "development_unverified",
        "mcp_capability_unverified",
    )
    assert len(manifest.request_result_schema_versions) == 37
    assert len(manifest.event_schema_versions) == 16
    assert len(manifest.resources) == 83
    assert dict(manifest.resource_counts) == {
        "canonical_vectors": "9",
        "guidance_resources": "5",
        "migrations": "7",
        "runtime_support_resources": "1",
        "schema_resources": "59",
        "skill_resources": "2",
        "total": "83",
    }


def test_version_json_allows_installed_sdk_with_empty_tested_protocol_set() -> None:
    rendered = version_manifest_json(build_version_manifest())
    document = json.loads(rendered)
    schema = json.loads(Path("schemas/version/version-manifest-1.0.0.schema.json").read_text())

    assert document["mcp_sdk_version"]["status"] == "present"
    assert document["mcp_protocol_supported"] == []
    assert "mcp_capability_unverified" in document["limitations"]
    Draft202012Validator(schema).validate(document)  # pyright: ignore[reportUnknownMemberType]


def test_resource_manifest_verifies_every_installed_member() -> None:
    result = verify_resource_manifest(build_version_manifest())

    assert len(result) == 1
    assert result[0].outcome is StartupCheckOutcome.OK
    assert result[0].safe_details["resource_count"] == 83


def test_guidance_and_skill_source_package_bytes_are_identical() -> None:
    paths = (
        "guidance/agent-instructions.md",
        "guidance/coverage-and-receipts.md",
        "guidance/publication-policy.md",
        "guidance/workflow.md",
        "skills/codex/yoetz/SKILL.md",
        "skills/codex/yoetz/manifest.json",
        "support/runtime-support.json",
    )

    for logical_name in paths:
        assert read_verified_resource(logical_name) == Path(logical_name).read_bytes()


def test_skill_advertises_no_untested_codex_profile_or_hook() -> None:
    document = json.loads(read_verified_resource("skills/codex/yoetz/manifest.json"))

    assert document["codex_version_bounds"] == {
        "denied": [],
        "supported": [],
        "tested": [],
    }
    assert document["capability_profile_ids"] == []
    assert document["hooks_by_capability_profile"] == {}


def test_resource_reader_is_a_closed_logical_name_lookup() -> None:
    for name in ("../pyproject.toml", "/etc/passwd", "guidance/missing.md"):
        try:
            read_verified_resource(name)
        except ResourceIntegrityError:
            pass
        else:
            raise AssertionError(f"unexpected resource read: {name}")
