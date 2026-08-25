"""Codex integration profile, resource, marker, and status tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from yoetz.adapters.integrations.codex_capability_cells import (
    CODEX_ROLLOUT_CAPABILITY_PROFILE_ID,
    CODEX_ROLLOUT_SUPPORTED_VERSIONS,
    skill_manifest_capability_fields,
)
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
    SkillPreviewCommand,
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
        **dict(skill_manifest_capability_fields()),
        "guidance_version": "0.1.0",
        "harness": "codex",
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
        "guidance/request-templates.md": b"# Request templates\n",
        "guidance/workflow.md": b"# Workflow\n",
    }
    installed_paths = {
        "SKILL.md": "skills/codex/yoetz/SKILL.md",
        "references/agent-instructions.md": "guidance/agent-instructions.md",
        "references/coverage-and-receipts.md": "guidance/coverage-and-receipts.md",
        "references/publication-policy.md": "guidance/publication-policy.md",
        "references/request-templates.md": "guidance/request-templates.md",
        "references/workflow.md": "guidance/workflow.md",
    }
    managed_members: list[JsonValue] = []
    for logical_name, package_path in installed_paths.items():
        data = files[package_path]
        managed_members.append(
            {
                "logical_name": logical_name,
                "origin": "harness_owned" if logical_name == "SKILL.md" else "shared_guidance",
                "role": "skill" if logical_name == "SKILL.md" else "guidance",
                "sha256": _digest(data),
                "size": len(data),
                **({} if logical_name == "SKILL.md" else {"source_logical_name": package_path}),
            }
        )
    managed_members.insert(
        1,
        {
            "identity_status": "self_excluded",
            "logical_name": "manifest.json",
            "origin": "harness_owned",
            "role": "compatibility_manifest",
        },
    )
    skill_manifest_body["managed_members"] = managed_members
    skill_manifest_body.pop("member_digest")
    skill_manifest_body["member_digest"] = canonical_digest(skill_manifest_body)
    files["skills/codex/yoetz/manifest.json"] = canonical_encode(skill_manifest_body) + b"\n"
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


def test_profile_publishes_exact_rollout_cell_with_absent_hooks() -> None:
    assert CODEX_HARNESS_PROFILE.harness_id is HarnessId.CODEX
    assert CODEX_HARNESS_PROFILE.skill_root == ".agents/skills/yoetz/"
    assert CODEX_HARNESS_PROFILE.capability_profile_ids == (CODEX_ROLLOUT_CAPABILITY_PROFILE_ID,)
    assert CODEX_HARNESS_PROFILE.supported_versions == CODEX_ROLLOUT_SUPPORTED_VERSIONS
    assert dict(CODEX_HARNESS_PROFILE.hooks_by_capability_profile) == {
        CODEX_ROLLOUT_CAPABILITY_PROFILE_ID: None
    }
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
    assert source.harness_tested_set == CODEX_ROLLOUT_SUPPORTED_VERSIONS
    assert tuple(file.relative_path for file in source.files) == (
        "SKILL.md",
        "manifest.json",
        "references/agent-instructions.md",
        "references/coverage-and-receipts.md",
        "references/publication-policy.md",
        "references/request-templates.md",
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
def test_status_separates_filesystem_state_from_capability_compatibility(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    adapter = CodexSkillIntegration(_resources())
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path))
    status = await adapter.status_skill(HarnessId.CODEX, SkillStatusCommand(target))
    assert status.state is IntegrationState.ABSENT
    assert status.compatibility == "supported"
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
    assert caught.value.reason is IntegrationReason.PREVIEW_STALE
    assert not (tmp_path / ".agents").exists()


@pytest.mark.anyio
async def test_explicit_allow_untested_installs_discoverable_project_skill(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    adapter = CodexSkillIntegration(_resources(), allow_untested=True)
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path))
    request = request_id("req_00000000-0000-4000-8000-000000000032")
    preview = await adapter.preview_skill(
        HarnessId.CODEX,
        SkillPreviewCommand(request, target, IntegrationAction.INSTALL, False),
    )

    result = await adapter.install_skill(
        HarnessId.CODEX,
        SkillApplyCommand(
            request,
            target,
            IntegrationAction.INSTALL,
            preview.preview_digest,
            True,
            False,
        ),
    )

    assert result.state_after is IntegrationState.INSTALLED_EXACT
    status = await adapter.status_skill(HarnessId.CODEX, SkillStatusCommand(target))
    assert status.state is IntegrationState.INSTALLED_EXACT
    assert status.compatibility == "supported"
    assert (tmp_path / ".agents/skills/yoetz/SKILL.md").is_file()


def test_package_import_has_no_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    __import__("yoetz.adapters.integrations")
    assert list(tmp_path.iterdir()) == []
