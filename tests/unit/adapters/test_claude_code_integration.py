from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.claude_code_integration import (
    CLAUDE_CODE_HARNESS_PROFILE,
    CLAUDE_CODE_HOOK_EVENTS,
    ClaudeCodeCapabilityIdentity,
    ClaudeCodeCommandResult,
    ClaudeCodeIntegrationError,
    ClaudeCodeMcpSource,
    ClaudeCodePluginAction,
    ClaudeCodePluginArtifact,
    ClaudeCodePluginTarget,
    apply_claude_code_plugin,
    observe_claude_code_mcp,
    observe_claude_code_session_init,
    preview_claude_code_plugin,
    render_claude_code_plugin,
    status_claude_code_plugin,
)
from yoetz.domain.values import JsonObject, request_id
from yoetz.ports.plugin_artifacts import (
    ArtifactAuthority,
    McpOwnership,
    McpOwnershipState,
    PluginArtifactReason,
    PluginArtifactState,
    PluginFormatProfile,
    PluginOperationState,
    PluginProofFacet,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.version import read_verified_resource

_REQUEST = request_id("req_10000000-0000-4000-8000-000000000001")


class _Review:
    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume_setup_authority(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        assert authority.target_digest == preview_digest
        self.consumed.append(preview_digest)

    def consume_artifact_review(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        assert authority.target_digest == preview_digest
        self.consumed.append(preview_digest)


def _authority(digest: str) -> ArtifactAuthority:
    return ArtifactAuthority("review_only", digest, "a" * 64)


def _target(tmp_path: Path) -> ClaudeCodePluginTarget:
    project = tmp_path / "project"
    config = tmp_path / "config"
    marketplace = tmp_path / "marketplace"
    executable = tmp_path / "claude"
    project.mkdir()
    config.mkdir()
    executable.write_bytes(b"claude-test-executable")
    executable.chmod(0o700)
    executable_digest = f"sha256:{hashlib.sha256(executable.read_bytes()).hexdigest()}"
    return ClaudeCodePluginTarget(
        str(project),
        str(config),
        str(config / "plugins" / "cache"),
        str(marketplace),
        str(executable),
        ClaudeCodeCapabilityIdentity(
            "2.1.241",
            executable_digest,
            "darwin",
            "arm64",
        ),
    )


class _ClaudeFixture:
    def __init__(self, artifact: ClaudeCodePluginArtifact) -> None:
        self.artifact = artifact
        self.installed = False
        self.enabled = False
        self.calls: list[tuple[str, ...]] = []

    def settings(self, target: ClaudeCodePluginTarget, registered: bool) -> None:
        path = Path(target.project_root) / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        value: dict[str, object] = {"enabledPlugins": {"yoetz@yoetz-local": self.enabled}}
        if registered:
            value["extraKnownMarketplaces"] = {
                "yoetz-local": {"source": {"source": "directory", "path": target.marketplace_root}}
            }
        path.write_text(json.dumps(value), encoding="utf-8")
        known = Path(target.claude_config_root) / "plugins" / "known_marketplaces.json"
        known.parent.mkdir(parents=True, exist_ok=True)
        known.write_text(
            json.dumps(
                {
                    "yoetz-local": {
                        "installLocation": target.marketplace_root,
                        "source": {
                            "source": "directory",
                            "path": target.marketplace_root,
                        },
                    }
                }
                if registered
                else {}
            ),
            encoding="utf-8",
        )

    def _cache(self, target: ClaudeCodePluginTarget) -> Path:
        return Path(target.cache_root) / "yoetz-local" / "yoetz" / self.artifact.plan.version

    def run(
        self, target: ClaudeCodePluginTarget, arguments: Sequence[str]
    ) -> ClaudeCodeCommandResult:
        args = tuple(arguments)
        self.calls.append(args)
        if args == ("plugin", "list", "--json"):
            rows: list[dict[str, object]] = []
            if self.installed:
                rows.append(
                    {
                        "enabled": self.enabled,
                        "id": "yoetz@yoetz-local",
                        "installPath": str(self._cache(target)),
                        "projectPath": target.project_root,
                        "scope": "project",
                        "version": self.artifact.plan.version,
                    }
                )
            return ClaudeCodeCommandResult(0, canonical_encode(cast(JsonValue, rows)), b"")
        if args[:4] == ("plugin", "marketplace", "add", "--scope"):
            self.settings(target, True)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:2] == ("plugin", "install"):
            cache = self._cache(target)
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(target.marketplace_root) / "plugins" / "yoetz", cache)
            self.installed = True
            self.enabled = False
            self.settings(target, True)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:2] == ("plugin", "enable"):
            self.enabled = True
            self.settings(target, True)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:2] == ("plugin", "disable"):
            self.enabled = False
            self.settings(target, True)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:3] == ("plugin", "marketplace", "update"):
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:2] == ("plugin", "update"):
            cache = self._cache(target)
            if cache.exists():
                shutil.rmtree(cache)
            shutil.copytree(Path(target.marketplace_root) / "plugins" / "yoetz", cache)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:2] == ("plugin", "uninstall"):
            cache = self._cache(target)
            if cache.exists():
                shutil.rmtree(cache)
            self.installed = False
            self.enabled = False
            self.settings(target, True)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        if args[:3] == ("plugin", "marketplace", "remove"):
            self.settings(target, False)
            return ClaudeCodeCommandResult(0, b"ok", b"")
        return ClaudeCodeCommandResult(1, b"", b"unexpected")


def test_native_projection_uses_shared_bytes_and_only_admitted_claude_components() -> None:
    external = render_claude_code_plugin()
    managed = render_claude_code_plugin(
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="strict",
    )

    assert CLAUDE_CODE_HARNESS_PROFILE.harness_id.value == "claude"
    assert CLAUDE_CODE_HARNESS_PROFILE.capability_profile_ids == (
        "claude-code-cli-local-project-2.1.241",
    )
    fixture = json.loads(
        read_verified_resource(
            "fixtures/agent-plugins/claude-code-cli-native-project-2.1.241.case.json"
        )
    )
    assert fixture["case_id"] == "claude-code-cli-native-project-2.1.241-macos-arm64"
    assert fixture["format_profile"] == "claude_code_plugin_native"
    hook = next(iter(CLAUDE_CODE_HARNESS_PROFILE.hooks_by_capability_profile.values()))
    assert hook is not None
    assert hook.observation_events == CLAUDE_CODE_HOOK_EVENTS
    assert managed.plan.format_profile is PluginFormatProfile.CLAUDE_CODE_PLUGIN_NATIVE
    assert external.members["skills/yoetz/SKILL.md"] == managed.members["skills/yoetz/SKILL.md"]
    assert ".mcp.json" not in external.members
    assert json.loads(managed.members[".mcp.json"])["mcpServers"]["yoetz"] == {
        "args": ["mcp", "serve", "--semantic", "off"],
        "command": "yoetz",
        "type": "stdio",
    }
    assert set(managed.members) == {
        ".claude-plugin/plugin.json",
        ".mcp.json",
        "hooks/hooks.json",
        "skills/yoetz/SKILL.md",
        *{
            f"skills/yoetz/references/{name}"
            for name in (
                "agent-instructions.md",
                "coverage-and-receipts.md",
                "publication-policy.md",
                "request-templates.md",
                "workflow.md",
            )
        },
    }
    manifest = json.loads(managed.members[".claude-plugin/plugin.json"])
    marketplace = json.loads(managed.marketplace_manifest)
    hooks = json.loads(managed.members["hooks/hooks.json"])["hooks"]
    assert manifest["defaultEnabled"] is False
    assert manifest["name"] == "yoetz"
    assert marketplace["plugins"] == [
        {"name": "yoetz", "source": "./plugins/yoetz", "strict": True}
    ]
    assert tuple(sorted(hooks)) == CLAUDE_CODE_HOOK_EVENTS
    assert "PermissionRequest" not in hooks
    assert hooks["PostToolUse"][0]["matcher"] == (
        "^mcp__plugin_yoetz_yoetz__(start|publish_work|check|respond|status|receipt)$"
    )
    assert all("CLAUDE_PROJECT_DIR" in row[0]["hooks"][0]["command"] for row in hooks.values())


def test_project_marketplace_install_enable_disable_and_remove_are_separate_states(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    artifact = render_claude_code_plugin(
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="strict",
    )
    commands = _ClaudeFixture(artifact)
    review = _Review()

    before = status_claude_code_plugin(target, artifact, commands=commands)
    assert before.state is PluginArtifactState.ABSENT
    install_preview = preview_claude_code_plugin(
        _REQUEST, target, ClaudeCodePluginAction.INSTALL, artifact, commands=commands
    )
    installed = apply_claude_code_plugin(
        _REQUEST,
        target,
        ClaudeCodePluginAction.INSTALL,
        artifact,
        accepted_preview_digest=install_preview.preview_digest,
        authority=_authority(install_preview.preview_digest),
        review=review,
        commands=commands,
    )
    assert installed.operation_state is PluginOperationState.COMPLETED
    assert installed.state_after is PluginArtifactState.NATIVE_MANAGED
    assert installed.enabled is False
    status = status_claude_code_plugin(target, artifact, commands=commands)
    assert status.marketplace_registered is True
    assert status.discovered is True
    assert status.enabled is False
    assert status.loaded_root_digest is None
    assert status.mcp_observation.ownership_state is McpOwnershipState.PLUGIN
    assert status.mcp_observation.winning_source is ClaudeCodeMcpSource.PLUGIN
    proof = {item.facet: item.status for item in status.proof}
    assert proof[PluginProofFacet.INSTALLED_BYTES] == "proven"
    assert proof[PluginProofFacet.HOST_DISCOVERY] == "proven"
    assert proof[PluginProofFacet.HOST_ACTIVATION] == "not_observed"
    assert proof[PluginProofFacet.MODEL_USE] == "not_observed"

    plugin_root = Path(target.marketplace_root) / "plugins" / "yoetz"
    session = observe_claude_code_session_init(
        JsonObject(
            {
                "claude_code_version": "2.1.241",
                "cwd": target.project_root,
                "mcp_servers": [{"name": "plugin:yoetz:yoetz", "status": "connected"}],
                "plugins": [
                    {
                        "name": "yoetz",
                        "path": str(plugin_root),
                        "source": "yoetz@yoetz-local",
                        "version": artifact.plan.version,
                    }
                ],
                "session_id": "session-activation-1",
                "skills": ["yoetz:yoetz"],
                "subtype": "init",
                "tools": [
                    f"mcp__plugin_yoetz_yoetz__{name}"
                    for name in (
                        "check",
                        "publish_work",
                        "read_guidance",
                        "receipt",
                        "respond",
                        "start",
                        "status",
                    )
                ],
                "type": "system",
            }
        ),
        target=target,
        artifact=artifact,
    )
    activated = status_claude_code_plugin(
        target, artifact, commands=commands, session_observation=session
    )
    activated_proof = {item.facet: item.status for item in activated.proof}
    assert activated.loaded_root_digest == artifact.artifact_digest
    assert activated_proof[PluginProofFacet.HOST_ACTIVATION] == "proven"
    assert activated_proof[PluginProofFacet.SKILL_DELIVERY] == "proven"
    assert activated_proof[PluginProofFacet.MCP_BINDING] == "proven"
    assert activated_proof[PluginProofFacet.MCP_RUNTIME] == "proven"
    assert activated_proof[PluginProofFacet.MODEL_USE] == "not_observed"

    enable_request = request_id("req_10000000-0000-4000-8000-000000000002")
    enable_preview = preview_claude_code_plugin(
        enable_request,
        target,
        ClaudeCodePluginAction.ENABLE,
        artifact,
        commands=commands,
    )
    enabled = apply_claude_code_plugin(
        enable_request,
        target,
        ClaudeCodePluginAction.ENABLE,
        artifact,
        accepted_preview_digest=enable_preview.preview_digest,
        authority=_authority(enable_preview.preview_digest),
        review=review,
        commands=commands,
    )
    assert enabled.enabled is True
    assert enabled.operation_state is PluginOperationState.COMPLETED

    disable_request = request_id("req_10000000-0000-4000-8000-000000000003")
    disable_preview = preview_claude_code_plugin(
        disable_request,
        target,
        ClaudeCodePluginAction.DISABLE,
        artifact,
        commands=commands,
    )
    disabled = apply_claude_code_plugin(
        disable_request,
        target,
        ClaudeCodePluginAction.DISABLE,
        artifact,
        accepted_preview_digest=disable_preview.preview_digest,
        authority=_authority(disable_preview.preview_digest),
        review=review,
        commands=commands,
    )
    assert disabled.enabled is False

    remove_request = request_id("req_10000000-0000-4000-8000-000000000004")
    remove_preview = preview_claude_code_plugin(
        remove_request,
        target,
        ClaudeCodePluginAction.REMOVE,
        artifact,
        commands=commands,
    )
    removed = apply_claude_code_plugin(
        remove_request,
        target,
        ClaudeCodePluginAction.REMOVE,
        artifact,
        accepted_preview_digest=remove_preview.preview_digest,
        authority=_authority(remove_preview.preview_digest),
        review=review,
        commands=commands,
    )
    assert removed.operation_state is PluginOperationState.COMPLETED
    assert removed.state_after is PluginArtifactState.ABSENT
    assert not Path(target.marketplace_root).exists()
    assert len(review.consumed) == 4


def test_mutation_requires_exact_review_and_rejects_legacy_claude(tmp_path: Path) -> None:
    target = _target(tmp_path)
    artifact = render_claude_code_plugin()
    commands = _ClaudeFixture(artifact)
    preview = preview_claude_code_plugin(
        _REQUEST, target, ClaudeCodePluginAction.INSTALL, artifact, commands=commands
    )

    with pytest.raises(ClaudeCodeIntegrationError) as no_authority:
        apply_claude_code_plugin(
            _REQUEST,
            target,
            ClaudeCodePluginAction.INSTALL,
            artifact,
            accepted_preview_digest=preview.preview_digest,
            authority=None,
            commands=commands,
        )
    assert no_authority.value.reason is PluginArtifactReason.AUTHORITY_REQUIRED
    assert not Path(target.marketplace_root).exists()

    legacy = ClaudeCodePluginTarget(
        target.project_root,
        target.claude_config_root,
        target.cache_root,
        target.marketplace_root,
        target.executable,
        ClaudeCodeCapabilityIdentity(
            "2.1.211", target.identity.executable_digest, "darwin", "arm64"
        ),
    )
    with pytest.raises(ClaudeCodeIntegrationError) as unsupported:
        preview_claude_code_plugin(
            _REQUEST,
            legacy,
            ClaudeCodePluginAction.INSTALL,
            artifact,
            commands=commands,
        )
    assert unsupported.value.reason is PluginArtifactReason.FORMAT_UNSUPPORTED


def test_update_replaces_only_marker_valid_source_and_rechecks_same_version_cache(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    strict = render_claude_code_plugin(
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="strict",
    )
    commands = _ClaudeFixture(strict)
    review = _Review()
    install = preview_claude_code_plugin(
        _REQUEST, target, ClaudeCodePluginAction.INSTALL, strict, commands=commands
    )
    apply_claude_code_plugin(
        _REQUEST,
        target,
        ClaudeCodePluginAction.INSTALL,
        strict,
        accepted_preview_digest=install.preview_digest,
        authority=_authority(install.preview_digest),
        review=review,
        commands=commands,
    )

    policy = render_claude_code_plugin(
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="policy",
    )
    commands.artifact = policy
    update_request = request_id("req_10000000-0000-4000-8000-000000000030")
    update = preview_claude_code_plugin(
        update_request,
        target,
        ClaudeCodePluginAction.UPDATE,
        policy,
        commands=commands,
    )
    result = apply_claude_code_plugin(
        update_request,
        target,
        ClaudeCodePluginAction.UPDATE,
        policy,
        accepted_preview_digest=update.preview_digest,
        authority=_authority(update.preview_digest),
        review=review,
        commands=commands,
    )
    assert result.operation_state is PluginOperationState.COMPLETED
    assert result.installed_digest == policy.artifact_digest
    assert status_claude_code_plugin(
        target, policy, commands=commands
    ).mcp_observation.route_profile == ("policy")

    source_skill = (
        Path(target.marketplace_root) / "plugins" / "yoetz" / "skills" / "yoetz" / "SKILL.md"
    )
    source_skill.write_text("modified\n", encoding="utf-8")
    with pytest.raises(ClaudeCodeIntegrationError) as modified:
        preview_claude_code_plugin(
            request_id("req_10000000-0000-4000-8000-000000000031"),
            target,
            ClaudeCodePluginAction.UPDATE,
            policy,
            commands=commands,
        )
    assert modified.value.reason is PluginArtifactReason.DESTINATION_CONFLICT
    assert source_skill.read_text("utf-8") == "modified\n"


def test_stale_enable_preview_and_lost_cli_outcome_fail_closed(tmp_path: Path) -> None:
    target = _target(tmp_path)
    artifact = render_claude_code_plugin()
    commands = _ClaudeFixture(artifact)
    review = _Review()
    install = preview_claude_code_plugin(
        _REQUEST, target, ClaudeCodePluginAction.INSTALL, artifact, commands=commands
    )
    apply_claude_code_plugin(
        _REQUEST,
        target,
        ClaudeCodePluginAction.INSTALL,
        artifact,
        accepted_preview_digest=install.preview_digest,
        authority=_authority(install.preview_digest),
        review=review,
        commands=commands,
    )
    enable_request = request_id("req_10000000-0000-4000-8000-000000000032")
    enable = preview_claude_code_plugin(
        enable_request,
        target,
        ClaudeCodePluginAction.ENABLE,
        artifact,
        commands=commands,
    )
    commands.enabled = True
    commands.settings(target, True)
    with pytest.raises(ClaudeCodeIntegrationError) as stale:
        apply_claude_code_plugin(
            enable_request,
            target,
            ClaudeCodePluginAction.ENABLE,
            artifact,
            accepted_preview_digest=enable.preview_digest,
            authority=_authority(enable.preview_digest),
            review=review,
            commands=commands,
        )
    assert stale.value.reason is PluginArtifactReason.PREVIEW_STALE

    commands.enabled = False
    commands.settings(target, True)
    retry_request = request_id("req_10000000-0000-4000-8000-000000000033")
    retry = preview_claude_code_plugin(
        retry_request,
        target,
        ClaudeCodePluginAction.ENABLE,
        artifact,
        commands=commands,
    )
    original_run = commands.run

    def fail_enable(
        target_value: ClaudeCodePluginTarget, arguments: Sequence[str]
    ) -> ClaudeCodeCommandResult:
        if tuple(arguments)[:2] == ("plugin", "enable"):
            return ClaudeCodeCommandResult(1, b"", b"lost")
        return original_run(target_value, arguments)

    commands.run = fail_enable  # type: ignore[method-assign]
    outcome = apply_claude_code_plugin(
        retry_request,
        target,
        ClaudeCodePluginAction.ENABLE,
        artifact,
        accepted_preview_digest=retry.preview_digest,
        authority=_authority(retry.preview_digest),
        review=review,
        commands=commands,
    )
    assert outcome.operation_state is PluginOperationState.OUTCOME_UNKNOWN
    assert outcome.enabled is False


def test_mcp_sources_preserve_precedence_and_report_dual_foreign_and_ambiguous(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    config = tmp_path / "config"
    plugin.mkdir()
    project.mkdir()
    config.mkdir()
    exact = {
        "mcpServers": {
            "yoetz": {
                "args": ["mcp", "serve"],
                "command": "yoetz",
                "type": "stdio",
            }
        }
    }
    (plugin / ".mcp.json").write_text(json.dumps(exact), encoding="utf-8")
    plugin_only = observe_claude_code_mcp(
        plugin_root=plugin,
        project_root=project,
        claude_config_root=config,
    )
    assert plugin_only.ownership_state is McpOwnershipState.PLUGIN

    (project / ".mcp.json").write_text(json.dumps(exact), encoding="utf-8")
    dual = observe_claude_code_mcp(
        plugin_root=plugin,
        project_root=project,
        claude_config_root=config,
    )
    assert dual.ownership_state is McpOwnershipState.DUAL
    assert dual.winning_source is ClaudeCodeMcpSource.PROJECT
    assert dual.route_profile is None

    foreign = JsonObject({"args": ["-c", "foreign"], "command": "sh", "type": "stdio"})
    observed = observe_claude_code_mcp(
        plugin_root=plugin,
        project_root=project,
        claude_config_root=config,
        connector_entry=foreign,
    )
    assert observed.ownership_state is McpOwnershipState.FOREIGN

    (config / ".claude.json").write_text("not json", encoding="utf-8")
    ambiguous = observe_claude_code_mcp(
        plugin_root=plugin,
        project_root=project,
        claude_config_root=config,
    )
    assert ambiguous.ownership_state is McpOwnershipState.AMBIGUOUS
    assert ambiguous.observed is False


def test_mcp_ownership_detects_an_exact_yoetz_route_under_an_alias(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    config = tmp_path / "config"
    plugin.mkdir()
    project.mkdir()
    config.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "team-ledger": {
                        "args": ["mcp", "serve", "--semantic", "off"],
                        "command": "yoetz",
                        "type": "stdio",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    observed = observe_claude_code_mcp(
        plugin_root=plugin,
        project_root=project,
        claude_config_root=config,
    )

    assert observed.ownership_state is McpOwnershipState.EXTERNAL
    assert observed.winning_source is ClaudeCodeMcpSource.PROJECT
    assert observed.route_profile == "strict"
