from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from yoetz.adapters.integrations.cursor_integration import (
    CURSOR_HARNESS_PROFILE,
    CURSOR_HOOK_EVENTS,
    CursorIntegrationError,
    CursorMcpSource,
    CursorPluginTarget,
    CursorSdkBinding,
    apply_cursor_plugin,
    build_cursor_sdk_profile,
    discover_cursor_sdk,
    observe_cursor_mcp,
    preview_cursor_plugin,
    remove_cursor_plugin,
    render_cursor_plugin,
    status_cursor_plugin,
)
from yoetz.domain.values import JsonObject, request_id
from yoetz.ports.plugin_artifacts import (
    McpOwnership,
    McpOwnershipState,
    PluginArtifactAction,
    PluginArtifactReason,
    PluginArtifactState,
    PluginFormatProfile,
    PluginProofFacet,
)
from yoetz.version import read_verified_resource

_REQUEST = request_id("req_10000000-0000-4000-8000-000000000001")


def test_cursor_profile_and_all_four_cells_are_resource_registered() -> None:
    assert CURSOR_HARNESS_PROFILE.harness_id.value == "cursor"
    assert CURSOR_HARNESS_PROFILE.capability_profile_ids == (
        "cursor-cli-2026.07.09-a3815c0",
        "cursor-ide-3.17.8",
        "cursor-sdk-python-1.0.24",
        "cursor-sdk-typescript-1.0.23",
    )
    for name in (
        "cursor-cli-portable-2026.07.09.case.json",
        "cursor-ide-native-3.17.8.case.json",
        "cursor-sdk-python-1.0.24.case.json",
        "cursor-sdk-typescript-1.0.23.case.json",
    ):
        fixture = json.loads(read_verified_resource(f"fixtures/agent-plugins/{name}"))
        assert fixture["schema"].startswith("yoetz.cursor-")


def test_portable_and_native_reuse_exact_skill_bytes_but_keep_manifests_disjoint() -> None:
    portable = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    native = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)

    assert portable.members["skills/yoetz/SKILL.md"] == native.members["skills/yoetz/SKILL.md"]
    assert "plugin.json" in portable.members
    assert ".cursor-plugin/plugin.json" not in portable.members
    assert ".cursor-plugin/plugin.json" in native.members
    assert "plugin.json" not in native.members
    assert native.plan.host_extension_profile == "cursor-native-3.17"

    manifest = json.loads(native.members[".cursor-plugin/plugin.json"])
    hooks = json.loads(native.members["hooks/hooks.json"])
    assert manifest["hooks"] == "hooks/hooks.json"
    assert tuple(sorted(hooks["hooks"])) == CURSOR_HOOK_EVENTS
    assert "afterAgentThought" not in hooks["hooks"]
    assert all(
        definition[0]["command"].startswith("yoetz hooks cursor-observe ")
        for definition in hooks["hooks"].values()
    )


def test_plugin_managed_native_route_is_exact_and_external_omits_it() -> None:
    external = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    managed = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="strict",
    )

    assert "mcp.json" not in external.members
    route = json.loads(managed.members["mcp.json"])["mcpServers"]["yoetz"]
    assert route == {
        "args": ["mcp", "serve", "--semantic", "off"],
        "command": "yoetz",
        "type": "stdio",
    }


def test_safe_cursor_lifecycle_is_preview_bound_atomic_and_reversible(tmp_path: Path) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)

    preview = preview_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
    )
    assert preview.state_before is PluginArtifactState.ABSENT
    result = apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        explicitly_accepted=True,
    )
    assert result.state_after is PluginArtifactState.NATIVE_MANAGED

    status = status_cursor_plugin(target, artifact)
    assert status.state is PluginArtifactState.NATIVE_MANAGED
    assert status.marker_valid is True
    assert status.installed_digest == artifact.artifact_digest
    proof = {item.facet: item.status for item in status.proof}
    assert proof[PluginProofFacet.INSTALLED_BYTES] == "proven"
    assert proof[PluginProofFacet.HOST_ACTIVATION] == "not_observed"

    remove_preview = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000002"),
        target,
        PluginArtifactAction.REMOVE,
        artifact,
    )
    removed = remove_cursor_plugin(
        remove_preview.request_id,
        target,
        artifact,
        accepted_preview_digest=remove_preview.preview_digest,
        explicitly_accepted=True,
    )
    assert removed.state_after is PluginArtifactState.ABSENT
    assert not (tmp_path / ".cursor" / "plugins" / "local" / "yoetz").exists()
    assert status_cursor_plugin(target, artifact).operation_state.value == "not_started"


def test_remove_preserves_separately_registered_mcp_route(tmp_path: Path) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="strict",
    )
    preview = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        explicitly_accepted=True,
    )
    (tmp_path / ".cursor" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "yoetz": {
                        "args": ["mcp", "serve", "--semantic", "off"],
                        "command": "yoetz",
                        "type": "stdio",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    remove_preview = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000004"),
        target,
        PluginArtifactAction.REMOVE,
        artifact,
    )
    remove_cursor_plugin(
        remove_preview.request_id,
        target,
        artifact,
        accepted_preview_digest=remove_preview.preview_digest,
        explicitly_accepted=True,
    )

    assert (tmp_path / ".cursor" / "mcp.json").is_file()
    assert status_cursor_plugin(target, artifact).mcp_observation.ownership_state is (
        McpOwnershipState.EXTERNAL
    )


def test_post_commit_cleanup_failure_preserves_new_and_old_trees_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    portable = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    native = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    installed = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, portable)
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        portable,
        accepted_preview_digest=installed.preview_digest,
        explicitly_accepted=True,
    )
    replacement = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000005"),
        target,
        PluginArtifactAction.REPLACE,
        native,
    )
    real_rmtree = shutil.rmtree

    def fail_rollback_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name == ".yoetz-cursor-plugin-rollback":
            raise OSError("cleanup failed")
        assert not args and not kwargs
        real_rmtree(path)

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.rmtree",
        fail_rollback_cleanup,
    )
    with pytest.raises(CursorIntegrationError) as raised:
        apply_cursor_plugin(
            replacement.request_id,
            target,
            PluginArtifactAction.REPLACE,
            native,
            accepted_preview_digest=replacement.preview_digest,
            explicitly_accepted=True,
        )

    assert raised.value.reason is PluginArtifactReason.WRITE_FAILED
    destination = tmp_path / ".cursor" / "plugins" / "local" / "yoetz"
    assert (destination / ".cursor-plugin" / "plugin.json").is_file()
    assert (destination.parent / ".yoetz-cursor-plugin-rollback" / "plugin.json").is_file()
    assert status_cursor_plugin(target, native).state is PluginArtifactState.RECOVERY_REQUIRED


def test_modified_managed_copy_is_preserved_and_remove_refused(tmp_path: Path) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    preview = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        explicitly_accepted=True,
    )
    skill = tmp_path / ".cursor" / "plugins" / "local" / "yoetz" / "skills" / "yoetz" / "SKILL.md"
    skill.write_text("locally modified\n", encoding="utf-8")

    assert status_cursor_plugin(target, artifact).state is PluginArtifactState.MODIFIED
    with pytest.raises(CursorIntegrationError) as raised:
        preview_cursor_plugin(
            request_id("req_10000000-0000-4000-8000-000000000003"),
            target,
            PluginArtifactAction.REMOVE,
            artifact,
        )
    assert raised.value.reason is PluginArtifactReason.REMOVE_REFUSED
    assert skill.read_text("utf-8") == "locally modified\n"


def test_mcp_precedence_negative_controls_never_create_false_plugin_pass(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    user = tmp_path / "user"
    plugin.mkdir()
    (project / ".cursor").mkdir(parents=True)
    user.mkdir()
    exact = {
        "mcpServers": {"yoetz": {"args": ["mcp", "serve"], "command": "yoetz", "type": "stdio"}}
    }
    (plugin / "mcp.json").write_text(json.dumps(exact), encoding="utf-8")

    plugin_only = observe_cursor_mcp(
        plugin_root=plugin,
        project_root=project,
        user_config_root=user,
    )
    assert plugin_only.ownership_state is McpOwnershipState.PLUGIN
    assert plugin_only.winning_source is CursorMcpSource.PLUGIN

    inline = JsonObject(
        {"yoetz": JsonObject({"args": ["mcp", "serve"], "command": "yoetz", "type": "stdio"})}
    )
    dual = observe_cursor_mcp(
        plugin_root=plugin,
        project_root=project,
        user_config_root=user,
        inline_send=inline,
    )
    assert dual.ownership_state is McpOwnershipState.DUAL
    assert dual.winning_source is CursorMcpSource.INLINE_SEND

    foreign = {
        "mcpServers": {"yoetz": {"args": ["-c", "foreign"], "command": "sh", "type": "stdio"}}
    }
    (project / ".cursor" / "mcp.json").write_text(json.dumps(foreign), encoding="utf-8")
    observed = observe_cursor_mcp(
        plugin_root=plugin,
        project_root=project,
        user_config_root=user,
    )
    assert observed.ownership_state is McpOwnershipState.FOREIGN


def test_unreadable_mcp_configuration_is_ambiguous_not_absent(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    project = tmp_path / "project"
    user = tmp_path / "user"
    plugin.mkdir()
    project.mkdir()
    user.mkdir()
    (user / "mcp.json").write_text("not json", encoding="utf-8")

    observed = observe_cursor_mcp(
        plugin_root=plugin,
        project_root=project,
        user_config_root=user,
    )

    assert observed.ownership_state is McpOwnershipState.AMBIGUOUS
    assert observed.observed is False
    assert observed.winning_source is None
    assert observed.present_sources == (CursorMcpSource.USER,)


def test_sdk_profiles_require_explicit_sources_and_pin_bridge_protocol(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name":"@cursor/sdk","version":"1.0.23"}', encoding="utf-8")
    identity = discover_cursor_sdk(
        CursorSdkBinding.TYPESCRIPT,
        package_metadata=package_json,
    )
    profile = build_cursor_sdk_profile(
        identity,
        setting_sources=("plugins", "project"),
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        sandbox_enabled=True,
        approval_mode="allowlist",
    )
    assert profile.identity.package_version == "1.0.23"
    assert profile.identity.bridge_protocol == "sdk.v1"
    assert profile.mcp_precedence[0] is CursorMcpSource.INLINE_SEND

    with pytest.raises(ValueError, match="cursor_sdk_plugin_source_required"):
        build_cursor_sdk_profile(
            identity,
            setting_sources=("project",),
            mcp_ownership=McpOwnership.PLUGIN_MANAGED,
            sandbox_enabled=True,
            approval_mode="default",
        )
