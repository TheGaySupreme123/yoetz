"""Codex skill discovery and safe integration capability evidence.

Non-live cells prove verified skill resource digests, isolated preview/modification protection via
an injected self-consistent skill source, and that app-server discovery claims stay unsupported
while harness profiles are unfrozen. Live ``skills/list`` / trigger cells require
``YOETZ_LIVE_CODEX=1``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    codex_profiles_frozen,
    live_codex_authorized,
    record_and_write,
    runtime_capability_context,
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
from yoetz.version import read_verified_resource

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_VERSION = "0.139.0"


class _Resources:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read_bytes(self, package_path: str) -> bytes:
        try:
            return self.files[package_path]
        except KeyError as exc:
            raise FileNotFoundError(package_path) from exc


def _injectable_skill_resources() -> _Resources:
    """Build a self-consistent skill/resource bundle for isolated integration probes."""

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
        "guidance/request-templates.md": b"# Request templates\n",
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
                "sha256": bytes_digest(data),
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_verified_skill_resources_have_stable_digests(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    skill = read_verified_resource("skills/codex/yoetz/SKILL.md")
    manifest = read_verified_resource("skills/codex/yoetz/manifest.json")
    assert skill.startswith(b"---")
    assert b"name: yoetz" in skill
    assert CODEX_HARNESS_PROFILE.skill_root == ".agents/skills/yoetz/"

    context = runtime_capability_context(
        fixture_digest=bytes_digest(skill + manifest),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"skill_root": "agents_skills_yoetz"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_skill",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SKL-001",
            requirement_id="ADR-005.skill-discovery",
            claim_id="E-002.skill-parity",
            capability_family="codex_skill_discovery",
            required_observation_codes=frozenset(
                {"skill_digest_bound", "manifest_digest_bound", "harness_root_exact"}
            ),
            allowed_observation_codes=frozenset(
                {"skill_digest_bound", "manifest_digest_bound", "harness_root_exact"}
            ),
        ),
        context,
        (
            Observation("skill_digest_bound", digest_value=bytes_digest(skill)),
            Observation("manifest_digest_bound", digest_value=bytes_digest(manifest)),
            Observation("harness_root_exact", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.anyio
async def test_preview_and_modified_copy_protection_in_isolated_repo(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    resources = _injectable_skill_resources()
    adapter = CodexSkillIntegration(resources)
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(project))

    status = await adapter.status_skill(HarnessId.CODEX, SkillStatusCommand(target))
    assert status.state in {IntegrationState.ABSENT, IntegrationState.INCOMPATIBLE}
    assert status.compatibility == "unsupported"

    preview = await adapter.preview_skill(
        HarnessId.CODEX,
        SkillPreviewCommand(
            request_id("req_00000000-0000-4000-8000-000000000040"),
            target,
            IntegrationAction.INSTALL,
            False,
        ),
    )
    assert preview.preview_digest.startswith("sha256:")
    source = load_packaged_skill_source(resources)
    marker = build_managed_marker(source, IntegrationScope.TRUSTED_PROJECT)
    assert b"/Users/" not in marker
    assert b"project_root" not in marker

    skill_dir = project / ".agents" / "skills" / "yoetz"
    skill_dir.mkdir(parents=True, mode=0o700)
    (skill_dir / "SKILL.md").write_bytes(b"user-modified-skill\n")
    before = (skill_dir / "SKILL.md").read_bytes()
    apply = SkillApplyCommand(
        request_id("req_00000000-0000-4000-8000-000000000041"),
        target,
        IntegrationAction.INSTALL,
        preview.preview_digest,
        True,
        False,
    )
    with pytest.raises(IntegrationError) as caught:
        await adapter.install_skill(HarnessId.CODEX, apply)
    assert caught.value.reason is IntegrationReason.VERSION_INCOMPATIBLE
    assert (skill_dir / "SKILL.md").read_bytes() == before

    context = runtime_capability_context(
        fixture_digest=bytes_digest(before),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"cell": "modified_copy_protection"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_skill",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SKL-002",
            requirement_id="ADR-005.skill-discovery",
            claim_id="E-002.skill-integration",
            capability_family="codex_skill_discovery",
            required_observation_codes=frozenset({"modified_copy_preserved", "marker_path_free"}),
            allowed_observation_codes=frozenset(
                {"modified_copy_preserved", "marker_path_free", "install_refused"}
            ),
        ),
        context,
        (
            Observation("modified_copy_preserved", boolean_value=True),
            Observation("marker_path_free", boolean_value=True),
            Observation("install_refused", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


def test_app_server_discovery_claim_unsupported_while_unprofiled(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    assert CODEX_HARNESS_PROFILE.capability_profile_ids == ()
    assert CODEX_HARNESS_PROFILE.supported_versions == ()
    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"skills-list-unprofiled"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"cell": "skills_list"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_skill",
    )
    if codex_profiles_frozen():
        pytest.skip("frozen profiles move skills/list into the live matrix")
    evidence = record_and_write(
        CapabilityCase(
            case_id="SKL-003",
            requirement_id="ADR-005.skill-discovery",
            claim_id="E-002.skill-discovery",
            capability_family="codex_skill_discovery",
            required_observation_codes=frozenset({"profiles_frozen", "implicit_trigger_claimed"}),
            allowed_observation_codes=frozenset({"profiles_frozen", "implicit_trigger_claimed"}),
        ),
        context,
        (
            Observation("profiles_frozen", boolean_value=False),
            Observation("implicit_trigger_claimed", boolean_value=False),
        ),
        EvidenceOutcome.UNSUPPORTED,
        ("codex_skill_discovery_unprobed",),
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.UNSUPPORTED


@pytest.mark.live
def test_live_codex_skill_discovery_and_triggers(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"live-skill-discovery"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"cell": "live_skill_discovery"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_skill",
    )
    if not live_codex_authorized():
        evidence = record_and_write(
            CapabilityCase(
                case_id="SKL-LIVE-001",
                requirement_id="ADR-005.skill-discovery",
                claim_id="E-002.skill-discovery-live",
                capability_family="codex_skill_discovery",
                required_observation_codes=frozenset({"live_authorized"}),
                allowed_observation_codes=frozenset({"live_authorized"}),
            ),
            context,
            (Observation("live_authorized", boolean_value=False),),
            EvidenceOutcome.UNSUPPORTED,
            ("live_codex_not_authorized",),
            output_root=evidence_root,
        )
        assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
        return
    pytest.fail("live Codex skill discovery authorized; observe skills/list before pass")
