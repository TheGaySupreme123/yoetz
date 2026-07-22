"""Codex plugin renderer and fail-closed installer unit tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from yoetz.adapters.integrations import codex_plugin as plugin_mod
from yoetz.adapters.integrations.codex_plugin import (
    PluginHookPresence,
    inspect_plugin,
    install_plugin,
    render_plugin_tree,
)
from yoetz.adapters.integrations.codex_skill import SkillResourceSource, load_packaged_skill_source
from yoetz.ports.integrations import (
    HarnessId,
    IntegrationError,
    IntegrationFile,
    IntegrationReason,
    IntegrationScope,
    IntegrationTarget,
    SkillSource,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode


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
                "media_type": (
                    "text/markdown" if package_path.endswith(".md") else "application/json"
                ),
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


def test_render_plugin_tree_wires_three_hooks() -> None:
    tree = render_plugin_tree(resource_source=_resources())
    assert ".codex-plugin/plugin.json" in tree
    assert "hooks/hooks.json" in tree
    assert ".mcp.json" in tree
    assert "skills/yoetz/SKILL.md" in tree
    hooks = tree["hooks/hooks.json"].decode("utf-8")
    assert "yoetz hooks user-prompt-submit" in hooks
    assert "yoetz hooks post-tool-use" in hooks
    assert "yoetz hooks session-start" in hooks
    assert "mcp__yoetz__start" in hooks
    assert "resume|compact" in hooks


def test_install_refuses_when_tested_set_empty(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source = load_packaged_skill_source(_resources())
    assert source.harness_tested_set == ()
    with pytest.raises(IntegrationError) as caught:
        install_plugin(
            IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path)),
            resource_source=_resources(),
        )
    assert caught.value.reason is IntegrationReason.VERSION_INCOMPATIBLE
    assert not (tmp_path / ".agents").exists()


def test_install_refuses_locally_modified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    resources = _resources()
    fake_source = SkillSource(
        HarnessId.CODEX,
        "0.1.0",
        "0.1",
        ("0.146.0",),
        "sha256:" + "a" * 64,
        (
            IntegrationFile(
                "SKILL.md",
                len(resources.files["skills/codex/yoetz/SKILL.md"]),
                "sha256:" + "b" * 64,
                "text/markdown",
            ),
        ),
    )

    def _fake_load(_resource_source: SkillResourceSource | None = None) -> SkillSource:
        return fake_source

    monkeypatch.setattr(plugin_mod, "load_packaged_skill_source", _fake_load)
    target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path))
    first = install_plugin(target, resource_source=resources)
    assert first.presence is PluginHookPresence.INSTALLED
    assert first.trust_observable is False

    hooks_path = tmp_path / ".agents/plugins/yoetz/hooks/hooks.json"
    hooks_path.write_bytes(hooks_path.read_bytes() + b"# modified\n")
    with pytest.raises(IntegrationError) as caught:
        install_plugin(target, resource_source=resources)
    assert caught.value.reason is IntegrationReason.MODIFIED_COPY


def test_inspect_absent_and_trust_not_observable(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    inspection = inspect_plugin(
        IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, str(tmp_path)),
        resource_source=_resources(),
    )
    assert inspection.presence is PluginHookPresence.ABSENT
    assert inspection.trust_observable is False
    assert "codex_hook_trust_not_observable_from_installation_state" in inspection.notes
