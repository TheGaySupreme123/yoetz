"""Codex integration profile, resource, marker, and status tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from yoetz.adapters.integrations.codex_skill import (
    CODEX_HARNESS_PROFILE,
    CodexSkillIntegration,
    build_managed_marker,
    load_packaged_skill_source,
)
from yoetz.domain.values import request_id
from yoetz.ports.integrations import (
    HarnessId,
    HarnessProfile,
    IntegrationAction,
    IntegrationError,
    IntegrationReason,
    IntegrationScope,
    IntegrationState,
    IntegrationTarget,
    SkillApplyCommand,
    SkillStatusCommand,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Resources:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read_bytes(self, package_path: str) -> bytes:
        try:
            return self.files[package_path]
        except KeyError as exc:
            raise FileNotFoundError(package_path) from exc


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _resources() -> _Resources:
    skill = (
        b"---\nname: yoetz\ndescription: Durable evidence guidance.\n"
        b"metadata:\n  short-description: Yoetz guidance\n---\n\n# Yoetz\n"
    )
    skill_manifest_body: dict[str, JsonValue] = {
        "capability_profile_ids": [],
        "codex_version_bounds": {"tested": []},
        "guidance_version": "0.1.0",
        "harness": "codex",
        "hooks_by_capability_profile": {},
        "managed_members": [],
        "protocol_version": "0.1",
        "schema": "yoetz.codex-skill-manifest/1",
        "skill": "yoetz",
        "skill_version": "0.1.0",
    }
    skill_manifest_body["member_digest"] = canonical_digest(skill_manifest_body)
    files = {
        "skills/codex/yoetz/SKILL.md": skill,
        "skills/codex/yoetz/manifest.json": canonical_encode(skill_manifest_body) + b"\n",
        "guidance/agent-instructions.md": b"# Agent instructions\n",
        "guidance/coverage-and-receipts.md": b"# Coverage and receipts\n",
        "guidance/publication-policy.md": b"# Publication policy\n",
        "guidance/workflow.md": b"# Workflow\n",
    }
    entries: list[JsonValue] = []
    for package_path, data in sorted(files.items(), key=lambda item: item[0].encode("ascii")):
        entries.append(
            {
                "kind": "skill" if package_path.endswith("SKILL.md") else "skill_reference",
                "logical_name": package_path,
                "media_type": "text/markdown"
                if package_path.endswith(".md")
                else "application/json",
                "package_path": package_path,
                "sha256": _digest(data),
                "size": len(data),
                "source_path": package_path,
            }
        )
    manifest_body: dict[str, JsonValue] = {
        "entries": entries,
        "package": "yoetz",
        "resource_set_version": "0.1.0",
        "schema": "yoetz.resource-manifest/1",
    }
    manifest_body["resource_set_digest"] = canonical_digest(manifest_body)
    files["manifest.json"] = canonical_encode(manifest_body) + b"\n"
    return _Resources(files)


def test_profile_is_explicitly_unprofiled_until_e002_evidence_exists() -> None:
    assert CODEX_HARNESS_PROFILE.harness_id is HarnessId.CODEX
    assert CODEX_HARNESS_PROFILE.skill_root == ".agents/skills/yoetz/"
    assert CODEX_HARNESS_PROFILE.capability_profile_ids == ()
    assert CODEX_HARNESS_PROFILE.supported_versions == ()
    assert dict(CODEX_HARNESS_PROFILE.hooks_by_capability_profile) == {}
    with pytest.raises(ProtocolValueError):
        HarnessProfile(
            HarnessId.CODEX,
            ".agents/skills/yoetz/",
            "codex_skill_frontmatter_v1",
            (),
            ("0.139.0",),
            {},
        )


def test_injected_source_is_manifest_verified_and_marker_is_path_free() -> None:
    source = load_packaged_skill_source(_resources())
    assert source.harness_id is HarnessId.CODEX
    assert source.harness_tested_set == ()
    assert tuple(file.relative_path for file in source.files) == (
        "SKILL.md",
        "manifest.json",
        "references/agent-instructions.md",
        "references/coverage-and-receipts.md",
        "references/publication-policy.md",
        "references/workflow.md",
    )
    marker = build_managed_marker(source, IntegrationScope.TRUSTED_PROJECT)
    assert b"project_root" not in marker
    assert b"/Users/" not in marker


def test_source_mutation_fails_closed_without_checkout_fallback() -> None:
    resources = _resources()
    resources.files["guidance/workflow.md"] = b"changed\n"
    with pytest.raises(IntegrationError) as caught:
        load_packaged_skill_source(resources)
    assert caught.value.reason is IntegrationReason.SOURCE_INVALID


@pytest.mark.anyio
async def test_status_is_read_only_and_incompatible_while_unprofiled(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    adapter = CodexSkillIntegration(_resources())
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path))
    status = await adapter.status_skill(HarnessId.CODEX, SkillStatusCommand(target))
    assert status.state is IntegrationState.INCOMPATIBLE
    assert status.compatibility == "unsupported"
    assert not (tmp_path / ".agents").exists()

    command = SkillApplyCommand(
        request_id("req_00000000-0000-4000-8000-000000000031"),
        target,
        IntegrationAction.INSTALL,
        "sha256:" + "d" * 64,
        True,
        False,
    )
    with pytest.raises(IntegrationError) as caught:
        await adapter.install_skill(HarnessId.CODEX, command)
    assert caught.value.reason is IntegrationReason.VERSION_INCOMPATIBLE
    assert not (tmp_path / ".agents").exists()


def test_package_import_has_no_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    __import__("yoetz.adapters.integrations")
    assert list(tmp_path.iterdir()) == []
