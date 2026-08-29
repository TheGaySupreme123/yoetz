from __future__ import annotations

import json
import shlex
import shutil
import subprocess
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
    discover_cursor_cli,
    discover_cursor_sdk,
    observe_cursor_mcp,
    preview_cursor_plugin,
    remove_cursor_plugin,
    render_cursor_plugin,
    status_cursor_plugin,
)
from yoetz.domain.values import JsonObject, request_id
from yoetz.ports.plugin_artifacts import (
    ArtifactAuthority,
    McpOwnership,
    McpOwnershipState,
    PluginArtifactAction,
    PluginArtifactReason,
    PluginArtifactState,
    PluginFormatProfile,
    PluginOperationState,
    PluginProofFacet,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.version import read_verified_resource

_REQUEST = request_id("req_10000000-0000-4000-8000-000000000001")
_REVIEW_ID = "a" * 64


class _AcceptingReview:
    """Stand in for one already-approved single-shot trusted review."""

    def __init__(self) -> None:
        self.artifact_reviews: list[tuple[ArtifactAuthority, str]] = []
        self.setup_authorities: list[tuple[ArtifactAuthority, str]] = []

    def consume_setup_authority(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        self.setup_authorities.append((authority, preview_digest))

    def consume_artifact_review(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        self.artifact_reviews.append((authority, preview_digest))


def _authority(preview_digest: str) -> ArtifactAuthority:
    return ArtifactAuthority("review_only", preview_digest, _REVIEW_ID)


def test_cursor_profile_exposes_only_supported_ide_and_cli_cells() -> None:
    assert CURSOR_HARNESS_PROFILE.harness_id.value == "cursor"
    assert CURSOR_HARNESS_PROFILE.capability_profile_ids == (
        "cursor-cli-2026.07.09-a3815c0",
        "cursor-ide-3.17.8",
    )
    assert CURSOR_HARNESS_PROFILE.supported_versions == (
        "2026.07.09-a3815c0",
        "3.17.8",
    )
    assert not any(
        profile_id.startswith("cursor-sdk-")
        for profile_id in CURSOR_HARNESS_PROFILE.capability_profile_ids
    )
    assert not {"1.0.23", "1.0.24"}.intersection(CURSOR_HARNESS_PROFILE.supported_versions)
    hooks = dict(CURSOR_HARNESS_PROFILE.hooks_by_capability_profile)
    assert hooks["cursor-cli-2026.07.09-a3815c0"] is None
    native_hooks = hooks["cursor-ide-3.17.8"]
    assert native_hooks is not None
    assert native_hooks.observation_events == CURSOR_HOOK_EVENTS
    assert native_hooks.evidence_case_ids == ("cursor-ide-native-3.17.8-macos-arm64",)
    for name in (
        "cursor-cli-portable-2026.07.09.case.json",
        "cursor-ide-native-3.17.8.case.json",
        "cursor-sdk-python-1.0.24.case.json",
        "cursor-sdk-typescript-1.0.23.case.json",
        "cursor-stale-shared-mcp-runtime.case.json",
    ):
        fixture = json.loads(read_verified_resource(f"fixtures/agent-plugins/{name}"))
        assert fixture["schema"].startswith("yoetz.cursor-")
        if name.startswith("cursor-sdk-"):
            assert fixture["proof_limits"] == ["metadata_only", "not_a_support_claim"]


def test_portable_and_native_reuse_exact_skill_bytes_but_keep_manifests_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "bin" / "yoetz"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    executable.chmod(0o755)

    def find_executable(command: str) -> str | None:
        return str(executable) if command == "yoetz" else None

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which", find_executable
    )
    portable = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    native = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)

    assert portable.members["skills/yoetz/SKILL.md"] == native.members["skills/yoetz/SKILL.md"]
    assert "plugin.json" in portable.members
    assert "hooks/hooks.json" not in portable.members
    assert ".cursor-plugin/plugin.json" not in portable.members
    assert ".cursor-plugin/plugin.json" in native.members
    assert "plugin.json" not in native.members
    assert "hooks/hooks.json" in native.members
    assert native.plan.host_extension_profile == "cursor-native-3.17"

    manifest = json.loads(native.members[".cursor-plugin/plugin.json"])
    hooks = json.loads(native.members["hooks/hooks.json"])
    assert manifest["hooks"] == "hooks/hooks.json"
    assert tuple(sorted(hooks["hooks"])) == CURSOR_HOOK_EVENTS
    assert "afterAgentThought" not in hooks["hooks"]
    resolved = str(executable.resolve())
    assert native.yoetz_launcher == (resolved,)
    assert all(
        definition[0]["command"].startswith(f"{shlex.quote(resolved)} hooks cursor-observe ")
        for definition in hooks["hooks"].values()
    )
    assert {event: definition[0]["timeout"] for event, definition in hooks["hooks"].items()} == {
        "afterFileEdit": 5,
        "afterMCPExecution": 5,
        "sessionEnd": 3,
        "sessionStart": 10,
        "stop": 10,
    }
    command = hooks["hooks"]["sessionStart"][0]["command"]
    completed = subprocess.run(
        shlex.split(command),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "hooks",
        "cursor-observe",
        "--workspace",
        ".",
        "--event",
        "sessionStart",
    ]


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
        "args": ["mcp", "serve", "--host", "cursor", "--semantic", "off"],
        "command": "yoetz",
        "type": "stdio",
    }

    policy = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        mcp_ownership=McpOwnership.PLUGIN_MANAGED,
        route_profile="policy",
    )
    assert json.loads(policy.members["mcp.json"])["mcpServers"]["yoetz"]["args"] == [
        "mcp",
        "serve",
        "--host",
        "cursor",
    ]


def test_native_render_fails_closed_when_yoetz_console_is_not_discoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def find_executable(_name: str) -> str | None:
        return None

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which", find_executable
    )
    with pytest.raises(ValueError, match="^yoetz_executable_unavailable$"):
        render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)


def test_native_render_prefers_explicit_invoking_executable_over_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoking = tmp_path / "invoking" / "yoetz"
    invoking.parent.mkdir()
    invoking.write_text("#!/bin/sh\n", encoding="utf-8")
    invoking.chmod(0o755)
    ambient = tmp_path / "ambient" / "yoetz"
    ambient.parent.mkdir()
    ambient.write_text("#!/bin/sh\n", encoding="utf-8")
    ambient.chmod(0o755)

    def find_ambient(_name: str) -> str | None:
        return str(ambient)

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which",
        find_ambient,
    )

    artifact = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        yoetz_launcher=invoking,
    )

    resolved = str(invoking.resolve())
    assert artifact.yoetz_launcher == (resolved,)
    hooks = json.loads(artifact.members["hooks/hooks.json"])
    assert all(
        definition[0]["command"].startswith(f"{shlex.quote(resolved)} hooks cursor-observe ")
        for definition in hooks["hooks"].values()
    )


def test_native_render_preserves_module_entrypoint_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)

    def refuse_path_lookup(_name: str) -> str | None:
        raise AssertionError("module entrypoint launcher must not consult PATH")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which",
        refuse_path_lookup,
    )

    artifact = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        yoetz_launcher=(str(interpreter), "-m", "yoetz"),
    )

    resolved = str(interpreter.resolve())
    assert artifact.yoetz_launcher == (resolved, "-m", "yoetz")
    hooks = json.loads(artifact.members["hooks/hooks.json"])
    prefix = f"{shlex.quote(resolved)} -m yoetz hooks cursor-observe "
    assert all(
        definition[0]["command"].startswith(prefix) for definition in hooks["hooks"].values()
    )


@pytest.mark.parametrize(
    ("candidate", "relative_target"),
    [
        ("./yoetz", "work/yoetz"),
        ("bin/yoetz", "work/bin/yoetz"),
        ("../bin/yoetz", "bin/yoetz"),
        (Path("yoetz"), "work/yoetz"),
    ],
)
def test_native_render_preserves_explicit_relative_executable_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: str | Path,
    relative_target: str,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    invoking = tmp_path / relative_target
    invoking.parent.mkdir(parents=True, exist_ok=True)
    invoking.write_text("#!/bin/sh\n", encoding="utf-8")
    invoking.chmod(0o755)
    monkeypatch.chdir(work)

    def refuse_path_lookup(_name: str) -> str | None:
        raise AssertionError("explicit executable path must not consult PATH")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which",
        refuse_path_lookup,
    )

    artifact = render_cursor_plugin(
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        yoetz_launcher=candidate,
    )

    assert artifact.yoetz_launcher == (str(invoking.resolve()),)


def test_portable_render_never_resolves_a_host_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_path_lookup(_name: str) -> str | None:
        raise AssertionError("portable rendering must stay host-resolver-free")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.shutil.which",
        refuse_path_lookup,
    )

    artifact = render_cursor_plugin(
        PluginFormatProfile.AGENT_PLUGINS_1,
        yoetz_launcher="./yoetz",
    )

    assert artifact.yoetz_launcher is None


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
        authority=_authority(preview.preview_digest),
        review=_AcceptingReview(),
    )
    assert result.state_after is PluginArtifactState.NATIVE_MANAGED
    marker = json.loads(
        (
            tmp_path
            / ".cursor"
            / "plugins"
            / "local"
            / "yoetz"
            / ".yoetz-cursor-plugin-install.json"
        ).read_text(encoding="utf-8")
    )
    assert artifact.yoetz_launcher is not None
    assert marker["yoetz_launcher"] == list(artifact.yoetz_launcher)
    assert marker["schema"] == "yoetz.cursor-plugin-install/2"
    assert marker["renderer_version"] == "cursor-plugin/0.2.0"

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
        authority=_authority(remove_preview.preview_digest),
        review=_AcceptingReview(),
    )
    assert removed.state_after is PluginArtifactState.ABSENT
    assert not (tmp_path / ".cursor" / "plugins" / "local" / "yoetz").exists()
    assert status_cursor_plugin(target, artifact).operation_state.value == "not_started"


def _rewrite_native_marker_as_legacy_v1(destination: Path) -> None:
    marker_path = destination / ".yoetz-cursor-plugin-install.json"
    marker = json.loads(marker_path.read_bytes())
    marker.pop("yoetz_launcher")
    marker["schema"] = "yoetz.cursor-plugin-install/1"
    body = {key: value for key, value in marker.items() if key != "marker_digest"}
    marker["marker_digest"] = canonical_digest(body)
    marker_path.write_bytes(canonical_encode(marker))


def test_legacy_native_v1_marker_has_a_safe_replace_path(tmp_path: Path) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    installed = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=installed.preview_digest,
        authority=_authority(installed.preview_digest),
        review=_AcceptingReview(),
    )
    destination = tmp_path / ".cursor" / "plugins" / "local" / "yoetz"
    _rewrite_native_marker_as_legacy_v1(destination)

    status = status_cursor_plugin(target, artifact)
    assert status.state is PluginArtifactState.MODIFIED
    assert status.marker_valid is True
    replacement = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000025"),
        target,
        PluginArtifactAction.REPLACE,
        artifact,
    )
    replaced = apply_cursor_plugin(
        replacement.request_id,
        target,
        PluginArtifactAction.REPLACE,
        artifact,
        accepted_preview_digest=replacement.preview_digest,
        authority=_authority(replacement.preview_digest),
        review=_AcceptingReview(),
    )
    assert replaced.state_after is PluginArtifactState.NATIVE_MANAGED
    marker = json.loads((destination / ".yoetz-cursor-plugin-install.json").read_bytes())
    assert marker["schema"] == "yoetz.cursor-plugin-install/2"


def test_portable_v1_marker_remains_exact_and_removable(tmp_path: Path) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    installed = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=installed.preview_digest,
        authority=_authority(installed.preview_digest),
        review=_AcceptingReview(),
    )
    destination = tmp_path / ".cursor" / "plugins" / "local" / "yoetz"
    marker = json.loads((destination / ".yoetz-cursor-plugin-install.json").read_bytes())
    assert marker["schema"] == "yoetz.cursor-plugin-install/1"
    status = status_cursor_plugin(target, artifact)
    assert status.state is PluginArtifactState.PORTABLE_EXACT
    assert status.marker_valid is True
    preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000026"),
        target,
        PluginArtifactAction.REMOVE,
        artifact,
    )


def test_native_executable_drift_is_modified_and_replaceable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first" / "yoetz"
    second = tmp_path / "second" / "yoetz"
    for executable in (first, second):
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)

    def find_first(_command: str) -> str | None:
        return str(first)

    monkeypatch.setattr("yoetz.adapters.integrations.cursor_integration.shutil.which", find_first)
    installed_artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    installed = preview_cursor_plugin(
        _REQUEST, target, PluginArtifactAction.INSTALL, installed_artifact
    )
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        installed_artifact,
        accepted_preview_digest=installed.preview_digest,
        authority=_authority(installed.preview_digest),
        review=_AcceptingReview(),
    )

    def find_second(_command: str) -> str | None:
        return str(second)

    monkeypatch.setattr("yoetz.adapters.integrations.cursor_integration.shutil.which", find_second)
    desired = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    status = status_cursor_plugin(target, desired)
    assert status.state is PluginArtifactState.MODIFIED
    assert status.marker_valid is True
    replacement = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000027"),
        target,
        PluginArtifactAction.REPLACE,
        desired,
    )
    assert replacement.state_before is PluginArtifactState.MODIFIED
    replaced = apply_cursor_plugin(
        replacement.request_id,
        target,
        PluginArtifactAction.REPLACE,
        desired,
        accepted_preview_digest=replacement.preview_digest,
        authority=_authority(replacement.preview_digest),
        review=_AcceptingReview(),
    )
    assert replaced.state_after is PluginArtifactState.NATIVE_MANAGED
    marker_path = (
        tmp_path / ".cursor" / "plugins" / "local" / "yoetz" / ".yoetz-cursor-plugin-install.json"
    )
    assert json.loads(marker_path.read_bytes())["yoetz_launcher"] == [str(second.resolve())]


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
        authority=_authority(preview.preview_digest),
        review=_AcceptingReview(),
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
        authority=_authority(remove_preview.preview_digest),
        review=_AcceptingReview(),
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
        authority=_authority(installed.preview_digest),
        review=_AcceptingReview(),
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
            authority=_authority(replacement.preview_digest),
            review=_AcceptingReview(),
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
        authority=_authority(preview.preview_digest),
        review=_AcceptingReview(),
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

    cursor_exact = {
        "mcpServers": {
            "yoetz": {
                "args": ["mcp", "serve", "--host", "cursor"],
                "command": "yoetz",
                "type": "stdio",
            }
        }
    }
    (plugin / "mcp.json").write_text(json.dumps(cursor_exact), encoding="utf-8")
    cursor_plugin_only = observe_cursor_mcp(
        plugin_root=plugin,
        project_root=project,
        user_config_root=user,
    )
    assert cursor_plugin_only.ownership_state is McpOwnershipState.PLUGIN
    assert cursor_plugin_only.route_profile == "policy"

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
    assert observed.winning_source is CursorMcpSource.PROJECT


def test_cursor_cli_discovery_uses_first_nonempty_complete_utf8_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "cursor-agent"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    output = b"\n\n2026.07.09-a3815c0\n"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess((str(executable), "--version"), 0, output, b"")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.subprocess.run",
        fake_run,
    )

    identity = discover_cursor_cli(executable)

    assert identity.version == "2026.07.09-a3815c0"


@pytest.mark.parametrize("stdout", [b"", b"\xff", b"version\n" + b"x" * 4_096])
def test_cursor_cli_discovery_normalizes_empty_and_invalid_utf8_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
) -> None:
    executable = tmp_path / "cursor-agent"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess((str(executable), "--version"), 0, stdout, b"")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.cursor_integration.subprocess.run",
        fake_run,
    )

    with pytest.raises(ValueError, match="^cursor_cli_identity_invalid$"):
        discover_cursor_cli(executable)


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
    assert profile.identity.proof_limits == ("metadata_only", "not_a_support_claim")
    assert profile.proof_limits == ("metadata_only", "not_a_support_claim")
    assert profile.metadata_only is True
    assert profile.support_claim is False
    assert profile.mcp_precedence[0] is CursorMcpSource.INLINE_SEND

    with pytest.raises(ValueError, match="cursor_sdk_plugin_source_required"):
        build_cursor_sdk_profile(
            identity,
            setting_sources=("project",),
            mcp_ownership=McpOwnership.PLUGIN_MANAGED,
            sandbox_enabled=True,
            approval_mode="default",
        )


def test_cursor_mutation_requires_a_consumed_review_and_accept_alone_is_not_authority(
    tmp_path: Path,
) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    preview = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    destination = tmp_path / ".cursor" / "plugins" / "local" / "yoetz"

    with pytest.raises(CursorIntegrationError) as no_authority:
        apply_cursor_plugin(
            _REQUEST,
            target,
            PluginArtifactAction.INSTALL,
            artifact,
            accepted_preview_digest=preview.preview_digest,
            authority=None,
        )
    assert no_authority.value.reason is PluginArtifactReason.AUTHORITY_REQUIRED
    assert not destination.exists()

    # An unwired caller cannot self-authorize by constructing the discriminator itself.
    with pytest.raises(CursorIntegrationError) as unwired:
        apply_cursor_plugin(
            _REQUEST,
            target,
            PluginArtifactAction.INSTALL,
            artifact,
            accepted_preview_digest=preview.preview_digest,
            authority=_authority(preview.preview_digest),
        )
    assert unwired.value.reason is PluginArtifactReason.HUMAN_AUTHORITY_UNAVAILABLE
    assert not destination.exists()

    # An authority bound to a different digest never authorizes this preview.
    other = preview_cursor_plugin(
        request_id("req_10000000-0000-4000-8000-000000000021"),
        target,
        PluginArtifactAction.INSTALL,
        artifact,
    )
    with pytest.raises(CursorIntegrationError) as mismatched:
        apply_cursor_plugin(
            _REQUEST,
            target,
            PluginArtifactAction.INSTALL,
            artifact,
            accepted_preview_digest=preview.preview_digest,
            authority=_authority(other.preview_digest),
            review=_AcceptingReview(),
        )
    assert mismatched.value.reason is PluginArtifactReason.AUTHORITY_REQUIRED
    assert not destination.exists()

    review = _AcceptingReview()
    result = apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        authority=_authority(preview.preview_digest),
        review=review,
    )
    assert result.state_after is PluginArtifactState.NATIVE_MANAGED
    assert [digest for _authority_value, digest in review.artifact_reviews] == [
        preview.preview_digest
    ]
    assert review.setup_authorities == []


def test_wedged_install_replay_reconciles_without_conflict_or_a_second_review(
    tmp_path: Path,
) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    preview = preview_cursor_plugin(_REQUEST, target, PluginArtifactAction.INSTALL, artifact)
    committed = apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        authority=_authority(preview.preview_digest),
        review=_AcceptingReview(),
    )
    assert committed.operation_state is PluginOperationState.COMPLETED

    # The commit succeeded but its result was lost. The same request and digest must reconcile at
    # the selected state, and must not need a second single-shot review to do it.
    replay = apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=preview.preview_digest,
        authority=None,
    )
    assert replay.action is PluginArtifactAction.NOOP
    assert replay.operation_state is PluginOperationState.COMPLETED
    assert replay.state_before is PluginArtifactState.NATIVE_MANAGED
    assert replay.state_after is PluginArtifactState.NATIVE_MANAGED
    assert replay.installed_digest == artifact.artifact_digest
    assert replay.changed_files == ()

    # A foreign request replaying somebody else's accepted digest still fails closed, because
    # the request ID is part of the digest it would have to match.
    with pytest.raises(CursorIntegrationError) as borrowed_digest:
        apply_cursor_plugin(
            request_id("req_10000000-0000-4000-8000-000000000022"),
            target,
            PluginArtifactAction.INSTALL,
            artifact,
            accepted_preview_digest=preview.preview_digest,
            authority=None,
        )
    assert borrowed_digest.value.reason is PluginArtifactReason.DESTINATION_CONFLICT

    # Recognition is stateless, not request-bound, and this pins that honestly: the preview
    # digest is a pure function of its inputs, so a request that never committed anything can
    # recompute one and reach the same reconciled result. That is a read-only equivalent of
    # ``status`` -- no bytes move, no review is consumed -- so it is admitted, not a bypass.
    from yoetz.adapters.integrations.cursor_integration import (
        _ABSENT_STATE_DIGEST,  # pyright: ignore[reportPrivateUsage]
        _admissible_owner_states,  # pyright: ignore[reportPrivateUsage]
        _preview_digest,  # pyright: ignore[reportPrivateUsage]
    )

    never_committed = request_id("req_10000000-0000-4000-8000-000000000024")
    recomputed = _preview_digest(
        never_committed,
        PluginArtifactAction.INSTALL,
        artifact,
        current_state_digest=_ABSENT_STATE_DIGEST,
        mcp_ownership_state=preview.mcp_ownership_state,
        target_identity=preview.target_identity,
    )
    assert preview.mcp_ownership_state in _admissible_owner_states(artifact)
    installed_bytes = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (tmp_path / ".cursor").rglob("*")
        if path.is_file()
    }
    review = _AcceptingReview()
    foreign = apply_cursor_plugin(
        never_committed,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=recomputed,
        authority=None,
        review=review,
    )
    assert foreign.action is PluginArtifactAction.NOOP
    assert foreign.operation_state is PluginOperationState.COMPLETED
    assert foreign.changed_files == ()
    assert review.artifact_reviews == []
    assert review.setup_authorities == []
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (tmp_path / ".cursor").rglob("*")
        if path.is_file()
    } == installed_bytes

    # What the reconcile cannot do is claim a state that is not on disk.
    portable = render_cursor_plugin(PluginFormatProfile.AGENT_PLUGINS_1)
    with pytest.raises(CursorIntegrationError) as other_artifact:
        apply_cursor_plugin(
            _REQUEST,
            target,
            PluginArtifactAction.INSTALL,
            portable,
            accepted_preview_digest=preview.preview_digest,
            authority=None,
        )
    assert other_artifact.value.reason is PluginArtifactReason.DESTINATION_CONFLICT


def test_wedged_remove_replay_reconciles_at_absent_instead_of_remove_refused(
    tmp_path: Path,
) -> None:
    target = CursorPluginTarget(str(tmp_path / ".cursor"))
    artifact = render_cursor_plugin(PluginFormatProfile.CURSOR_PLUGIN_NATIVE)
    install_preview = preview_cursor_plugin(
        _REQUEST, target, PluginArtifactAction.INSTALL, artifact
    )
    apply_cursor_plugin(
        _REQUEST,
        target,
        PluginArtifactAction.INSTALL,
        artifact,
        accepted_preview_digest=install_preview.preview_digest,
        authority=_authority(install_preview.preview_digest),
        review=_AcceptingReview(),
    )
    remove_request = request_id("req_10000000-0000-4000-8000-000000000023")
    remove_preview = preview_cursor_plugin(
        remove_request, target, PluginArtifactAction.REMOVE, artifact
    )
    removed = remove_cursor_plugin(
        remove_request,
        target,
        artifact,
        accepted_preview_digest=remove_preview.preview_digest,
        authority=_authority(remove_preview.preview_digest),
        review=_AcceptingReview(),
    )
    assert removed.state_after is PluginArtifactState.ABSENT

    replay = remove_cursor_plugin(
        remove_request,
        target,
        artifact,
        accepted_preview_digest=remove_preview.preview_digest,
        authority=None,
    )
    assert replay.action is PluginArtifactAction.NOOP
    assert replay.operation_state is PluginOperationState.COMPLETED
    assert replay.state_before is PluginArtifactState.ABSENT
    assert replay.state_after is PluginArtifactState.ABSENT
    assert replay.installed_digest is None
    assert replay.changed_files == ()


@pytest.mark.parametrize("extra_key", ["cwd", "env"])
def test_same_name_entry_with_any_extra_key_is_foreign_not_a_recognized_route(
    tmp_path: Path, extra_key: str
) -> None:
    plugin = tmp_path / "plugin"
    user = tmp_path / "user"
    plugin.mkdir()
    user.mkdir()
    entry: dict[str, object] = {
        "args": ["mcp", "serve", "--host", "cursor"],
        "command": "yoetz",
        "type": "stdio",
        extra_key: {"YOETZ_TOKEN": "leak"} if extra_key == "env" else "/tmp",
    }
    (user / "mcp.json").write_text(json.dumps({"mcpServers": {"yoetz": entry}}), encoding="utf-8")

    observation = observe_cursor_mcp(plugin_root=plugin, project_root=None, user_config_root=user)

    assert observation.ownership_state is McpOwnershipState.FOREIGN
    assert observation.route_profile is None
    assert observation.winning_source is CursorMcpSource.USER
