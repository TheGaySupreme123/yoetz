"""First-party local Cursor plugin, MCP, SDK, and capability integration.

The adapter deliberately keeps portable/native format, installed bytes, activation,
MCP ownership, SDK source precedence, and observation proof as separate facts.  It
never reads Cursor caches and every mutation is bound to an explicit user-scoped
configuration root and an exact preview digest.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Literal, cast

from yoetz import __version__
from yoetz.adapters.integrations.portable_plugin import (
    PackagedPortableResources,
    RenderedPortablePlugin,
    build_portable_plugin_plan,
)
from yoetz.domain.values import JsonObject, RequestId
from yoetz.domain.values import request_id as validate_request_id
from yoetz.ports.integrations import HarnessHookProfile, HarnessId, HarnessProfile
from yoetz.ports.plugin_artifacts import (
    ManagedPluginFile,
    McpOwnership,
    McpOwnershipState,
    PluginArtifactAction,
    PluginArtifactReason,
    PluginArtifactState,
    PluginFormatProfile,
    PluginOperationState,
    PluginProofFacet,
    PluginProofStatus,
    PortablePluginPlan,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "CURSOR_HOOK_EVENTS",
    "CURSOR_HARNESS_PROFILE",
    "CURSOR_NATIVE_PROFILE_ID",
    "CURSOR_PLUGIN_RELATIVE_ROOT",
    "CursorArtifactIdentity",
    "CursorCapabilityIdentity",
    "CursorIntegrationError",
    "CursorMcpObservation",
    "CursorMcpSource",
    "CursorPluginArtifact",
    "CursorPluginPreview",
    "CursorPluginResult",
    "CursorPluginStatus",
    "CursorPluginTarget",
    "CursorSdkBinding",
    "CursorSdkProfile",
    "apply_cursor_plugin",
    "build_cursor_sdk_profile",
    "discover_cursor_cli",
    "discover_cursor_ide",
    "discover_cursor_sdk",
    "observe_cursor_mcp",
    "preview_cursor_plugin",
    "remove_cursor_plugin",
    "render_cursor_plugin",
    "status_cursor_plugin",
]

CURSOR_PLUGIN_RELATIVE_ROOT: Final = "plugins/local/yoetz"
CURSOR_NATIVE_PROFILE_ID: Final = "cursor-native-3.17"
CURSOR_HOOK_MAPPING_VERSION: Final = "cursor-hooks-3.17-v1"
CURSOR_SDK_BRIDGE_PROTOCOL: Final = "sdk.v1"
CURSOR_HOOK_EVENTS: Final = (
    "afterFileEdit",
    "afterMCPExecution",
    "sessionEnd",
    "sessionStart",
    "stop",
)
_CURSOR_CAPABILITY_PROFILE_IDS: Final = (
    "cursor-cli-2026.07.09-a3815c0",
    "cursor-ide-3.17.8",
    "cursor-sdk-python-1.0.24",
    "cursor-sdk-typescript-1.0.23",
)
_CURSOR_HOOK_PROFILE: Final = HarnessHookProfile(
    trigger_event="sessionStart",
    trigger_payload_profile_id="cursor-hooks-common-3.17-v1",
    evidence_case_ids=(
        "cursor-cli-portable-2026.07.09-a3815c0-macos-arm64",
        "cursor-ide-native-3.17.8-macos-arm64",
    ),
    observation_events=CURSOR_HOOK_EVENTS,
)
CURSOR_HARNESS_PROFILE: Final = HarnessProfile(
    harness_id=HarnessId.CURSOR,
    skill_root="plugins/local/yoetz/skills/",
    frontmatter_profile="agent-skills-1",
    capability_profile_ids=_CURSOR_CAPABILITY_PROFILE_IDS,
    supported_versions=("1.0.23", "1.0.24", "2026.07.09-a3815c0", "3.17.8"),
    hooks_by_capability_profile={
        "cursor-cli-2026.07.09-a3815c0": _CURSOR_HOOK_PROFILE,
        "cursor-ide-3.17.8": _CURSOR_HOOK_PROFILE,
        "cursor-sdk-python-1.0.24": _CURSOR_HOOK_PROFILE,
        "cursor-sdk-typescript-1.0.23": _CURSOR_HOOK_PROFILE,
    },
)

_MARKER_NAME: Final = ".yoetz-cursor-plugin-install.json"
_MARKER_SCHEMA: Final = "yoetz.cursor-plugin-install/1"
_RENDERER_VERSION: Final = "cursor-plugin/0.1.0"
_ROLLBACK_NAME: Final = ".yoetz-cursor-plugin-rollback"
_STAGE_PREFIX: Final = ".yoetz-cursor-plugin-stage-"
_MAX_FILE_BYTES: Final = 262_144
_MAX_FILES: Final = 64
_MAX_PATH: Final = 4_096
_MAX_CURSOR_IDENTITY_BYTES: Final = 4_096
_GUIDANCE_NAMES: Final = (
    "agent-instructions.md",
    "coverage-and-receipts.md",
    "publication-policy.md",
    "request-templates.md",
    "workflow.md",
)
_DESCRIPTION: Final = (
    "Records material work in a local Yoetz ledger and checks completion claims "
    "against that record."
)


class CursorSdkBinding(str, Enum):  # noqa: UP042 - exact public token
    TYPESCRIPT = "typescript"
    PYTHON = "python"


class CursorMcpSource(str, Enum):  # noqa: UP042 - exact public token
    INLINE_SEND = "inline_send"
    INLINE_CREATE = "inline_create"
    PLUGIN = "plugin"
    PROJECT = "project"
    USER = "user"


@dataclass(frozen=True, slots=True, repr=False)
class CursorCapabilityIdentity:
    surface: Literal["cursor_ide", "cursor_cli"]
    version: str
    build: str
    artifact_digest: str
    os_name: str
    architecture: str

    def __post_init__(self) -> None:
        if self.surface not in {"cursor_ide", "cursor_cli"}:
            raise ValueError("cursor_surface_invalid")
        for value in (self.version, self.build, self.os_name, self.architecture):
            if type(value) is not str or not value or len(value) > 128:
                raise ValueError("cursor_identity_invalid")
        _validate_digest(self.artifact_digest)

    def __repr__(self) -> str:
        return (
            "CursorCapabilityIdentity("
            f"surface={self.surface!r}, version={self.version!r}, build={self.build!r}, "
            f"artifact_digest={self.artifact_digest!r}, os_name={self.os_name!r}, "
            f"architecture={self.architecture!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CursorArtifactIdentity:
    binding: CursorSdkBinding
    package_version: str
    package_digest: str
    bridge_protocol: Literal["sdk.v1"] = CURSOR_SDK_BRIDGE_PROTOCOL
    bridge_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.binding) is not CursorSdkBinding:
            raise ValueError("cursor_sdk_binding_invalid")
        if type(self.package_version) is not str or not self.package_version:
            raise ValueError("cursor_sdk_version_invalid")
        _validate_digest(self.package_digest)
        if self.bridge_protocol != CURSOR_SDK_BRIDGE_PROTOCOL:
            raise ValueError("cursor_sdk_bridge_invalid")
        if self.bridge_digest is not None:
            _validate_digest(self.bridge_digest)

    def __repr__(self) -> str:
        return (
            "CursorArtifactIdentity("
            f"binding={self.binding.value!r}, package_version={self.package_version!r}, "
            f"package_digest={self.package_digest!r}, bridge_protocol={self.bridge_protocol!r}, "
            f"bridge_digest={self.bridge_digest!r})"
        )


@dataclass(frozen=True, slots=True)
class CursorSdkProfile:
    identity: CursorArtifactIdentity
    setting_sources: tuple[Literal["plugins", "project", "user"], ...]
    mcp_precedence: tuple[CursorMcpSource, ...]
    mcp_ownership: McpOwnership
    sandbox_enabled: bool
    approval_mode: Literal["default", "allowlist", "full"]

    def __post_init__(self) -> None:
        if type(self.identity) is not CursorArtifactIdentity:
            raise ValueError("cursor_sdk_identity_invalid")
        allowed = {"plugins", "project", "user"}
        if (
            type(self.setting_sources) is not tuple
            or len(set(self.setting_sources)) != len(self.setting_sources)
            or any(item not in allowed for item in self.setting_sources)
        ):
            raise ValueError("cursor_sdk_setting_sources_invalid")
        expected = (
            CursorMcpSource.INLINE_SEND,
            CursorMcpSource.INLINE_CREATE,
            CursorMcpSource.PLUGIN,
            CursorMcpSource.PROJECT,
            CursorMcpSource.USER,
        )
        if self.mcp_precedence != expected:
            raise ValueError("cursor_sdk_precedence_invalid")
        if type(self.mcp_ownership) is not McpOwnership or type(self.sandbox_enabled) is not bool:
            raise ValueError("cursor_sdk_profile_invalid")
        if self.approval_mode not in {"default", "allowlist", "full"}:
            raise ValueError("cursor_sdk_profile_invalid")
        if (
            self.mcp_ownership is McpOwnership.PLUGIN_MANAGED
            and "plugins" not in self.setting_sources
        ):
            raise ValueError("cursor_sdk_plugin_source_required")
        if self.mcp_ownership is McpOwnership.EXTERNAL_REGISTRATION and not {
            "project",
            "user",
        }.intersection(self.setting_sources):
            raise ValueError("cursor_sdk_external_source_required")


@dataclass(frozen=True, slots=True, repr=False)
class CursorPluginTarget:
    cursor_config_root: str
    scope: Literal["user"] = "user"

    def __post_init__(self) -> None:
        if (
            type(self.cursor_config_root) is not str
            or not 1 <= len(self.cursor_config_root) <= _MAX_PATH
            or not Path(self.cursor_config_root).is_absolute()
            or any(ord(char) < 32 or ord(char) == 127 for char in self.cursor_config_root)
            or self.scope != "user"
        ):
            raise ValueError("cursor_target_invalid")

    def __repr__(self) -> str:
        return "CursorPluginTarget(cursor_config_root=<redacted>, scope='user')"


@dataclass(frozen=True, slots=True)
class CursorPluginArtifact:
    plan: PortablePluginPlan
    members: Mapping[str, bytes]
    artifact_digest: str

    def __post_init__(self) -> None:
        if type(self.plan) is not PortablePluginPlan:
            raise ValueError("cursor_artifact_invalid")
        if self.plan.format_profile not in {
            PluginFormatProfile.AGENT_PLUGINS_1,
            PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        }:
            raise ValueError("cursor_artifact_invalid")
        _validate_digest(self.artifact_digest)
        expected = tuple(item.relative_path for item in self.plan.inventory)
        if tuple(sorted(self.members, key=str.encode)) != expected:
            raise ValueError("cursor_artifact_inventory_invalid")


@dataclass(frozen=True, slots=True)
class CursorMcpObservation:
    ownership_state: McpOwnershipState
    winning_source: CursorMcpSource | None
    route_profile: Literal["strict", "policy"] | None
    present_sources: tuple[CursorMcpSource, ...]
    observed: bool

    def __post_init__(self) -> None:
        if type(self.ownership_state) is not McpOwnershipState:
            raise ValueError("cursor_mcp_observation_invalid")
        if self.winning_source is not None and type(self.winning_source) is not CursorMcpSource:
            raise ValueError("cursor_mcp_observation_invalid")
        if self.route_profile not in {None, "strict", "policy"}:
            raise ValueError("cursor_mcp_observation_invalid")
        if type(self.observed) is not bool:
            raise ValueError("cursor_mcp_observation_invalid")
        if type(self.present_sources) is not tuple or any(
            type(item) is not CursorMcpSource for item in self.present_sources
        ):
            raise ValueError("cursor_mcp_observation_invalid")


@dataclass(frozen=True, slots=True)
class CursorPluginPreview:
    request_id: RequestId
    action: PluginArtifactAction
    state_before: PluginArtifactState
    format_profile: PluginFormatProfile
    target_identity: str
    current_state_digest: str
    artifact_digest: str
    preview_digest: str
    mcp_ownership: McpOwnership
    mcp_ownership_state: McpOwnershipState
    mcp_route_profile: Literal["strict", "policy"] | None
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_request_id(self.request_id))
        if type(self.action) is not PluginArtifactAction:
            raise ValueError("cursor_preview_invalid")
        if type(self.state_before) is not PluginArtifactState:
            raise ValueError("cursor_preview_invalid")
        if type(self.format_profile) is not PluginFormatProfile:
            raise ValueError("cursor_preview_invalid")
        for value in (
            self.target_identity,
            self.current_state_digest,
            self.artifact_digest,
            self.preview_digest,
        ):
            _validate_digest(value)


@dataclass(frozen=True, slots=True)
class CursorPluginStatus:
    state: PluginArtifactState
    operation_state: PluginOperationState
    format_profile: PluginFormatProfile | None
    artifact_digest: str
    installed_digest: str | None
    marker_valid: bool
    rollback_available: bool
    mcp_observation: CursorMcpObservation
    proof: tuple[PluginProofStatus, ...]

    def __post_init__(self) -> None:
        if (
            type(self.state) is not PluginArtifactState
            or type(self.operation_state) is not PluginOperationState
        ):
            raise ValueError("cursor_status_invalid")
        if self.format_profile is not None and type(self.format_profile) is not PluginFormatProfile:
            raise ValueError("cursor_status_invalid")
        _validate_digest(self.artifact_digest)
        if self.installed_digest is not None:
            _validate_digest(self.installed_digest)
        if type(self.marker_valid) is not bool or type(self.rollback_available) is not bool:
            raise ValueError("cursor_status_invalid")
        if type(self.mcp_observation) is not CursorMcpObservation or type(self.proof) is not tuple:
            raise ValueError("cursor_status_invalid")
        if any(type(item) is not PluginProofStatus for item in self.proof):
            raise ValueError("cursor_status_invalid")


@dataclass(frozen=True, slots=True)
class CursorPluginResult:
    request_id: RequestId
    action: PluginArtifactAction
    operation_state: PluginOperationState
    state_before: PluginArtifactState
    state_after: PluginArtifactState
    format_profile: PluginFormatProfile
    preview_digest: str
    artifact_digest: str
    installed_digest: str | None
    changed_files: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_request_id(self.request_id))
        if (
            type(self.action) is not PluginArtifactAction
            or type(self.operation_state) is not PluginOperationState
            or type(self.state_before) is not PluginArtifactState
            or type(self.state_after) is not PluginArtifactState
            or type(self.format_profile) is not PluginFormatProfile
        ):
            raise ValueError("cursor_result_invalid")
        _validate_digest(self.preview_digest)
        _validate_digest(self.artifact_digest)
        if self.installed_digest is not None:
            _validate_digest(self.installed_digest)
        if type(self.changed_files) is not tuple or any(
            type(path) is not str or not path or len(path) > _MAX_PATH
            for path in self.changed_files
        ):
            raise ValueError("cursor_result_invalid")


@dataclass(frozen=True, slots=True)
class CursorIntegrationError(Exception):
    reason: PluginArtifactReason
    safe_details: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if type(self.reason) is not PluginArtifactReason:
            raise ValueError("cursor_error_invalid")
        try:
            details = JsonObject(self.safe_details)
        except ProtocolValueError as exc:
            raise ValueError("cursor_error_invalid") from exc
        if (
            len(details) > 16
            or len(canonical_encode(details)) > 4_096
            or any(key not in {"mcp_ownership_state"} for key in details)
        ):
            raise ValueError("cursor_error_invalid")
        object.__setattr__(self, "safe_details", details)
        Exception.__init__(self, self.reason.value)


@dataclass(frozen=True, slots=True, repr=False)
class _Inspection:
    root: Path
    destination: Path
    state: PluginArtifactState
    format_profile: PluginFormatProfile | None
    target_identity: str
    current_state_digest: str
    installed_digest: str | None
    marker_valid: bool
    rollback_available: bool


def _error(
    reason: PluginArtifactReason, details: Mapping[str, JsonValue] | None = None
) -> CursorIntegrationError:
    return CursorIntegrationError(reason, {} if details is None else details)


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _validate_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ValueError("cursor_digest_invalid")
    return value


def _inventory(members: Mapping[str, bytes]) -> tuple[ManagedPluginFile, ...]:
    return tuple(
        ManagedPluginFile(path, len(data), _sha(data))
        for path, data in sorted(members.items(), key=lambda item: item[0].encode("ascii"))
    )


def _mcp_json(route_profile: Literal["strict", "policy"]) -> bytes:
    args = ["mcp", "serve", "--host", "cursor"]
    if route_profile == "strict":
        args.extend(("--semantic", "off"))
    return canonical_encode(
        cast(
            JsonValue,
            {"mcpServers": {"yoetz": {"args": args, "command": "yoetz", "type": "stdio"}}},
        )
    )


def _native_members(
    *,
    source: PackagedPortableResources,
    mcp_ownership: McpOwnership,
    route_profile: Literal["strict", "policy"] | None,
) -> dict[str, bytes]:
    manifest: dict[str, JsonValue] = {
        "author": {"name": "Yoetz contributors"},
        "description": _DESCRIPTION,
        "hooks": "hooks/hooks.json",
        "name": "yoetz",
        "skills": "skills",
        "version": __version__,
    }
    if mcp_ownership is McpOwnership.PLUGIN_MANAGED:
        if route_profile not in {"strict", "policy"}:
            raise ValueError("cursor_mcp_route_required")
        manifest["mcpServers"] = "mcp.json"
    elif route_profile is not None:
        raise ValueError("cursor_mcp_route_forbidden")
    hook_command = "yoetz hooks cursor-observe --workspace ."
    hooks = {
        event: [{"command": f"{hook_command} --event {event}", "timeout": 3}]
        for event in CURSOR_HOOK_EVENTS
    }
    members: dict[str, bytes] = {
        ".cursor-plugin/plugin.json": canonical_encode(manifest),
        "hooks/hooks.json": canonical_encode(cast(JsonValue, {"hooks": hooks, "version": 1})),
        "skills/yoetz/SKILL.md": source.read_bytes("skills/portable/yoetz/SKILL.md"),
    }
    for name in _GUIDANCE_NAMES:
        members[f"skills/yoetz/references/{name}"] = source.read_bytes(f"guidance/{name}")
    if mcp_ownership is McpOwnership.PLUGIN_MANAGED:
        assert route_profile is not None
        members["mcp.json"] = _mcp_json(route_profile)
    return members


def render_cursor_plugin(
    format_profile: PluginFormatProfile,
    *,
    mcp_ownership: McpOwnership = McpOwnership.EXTERNAL_REGISTRATION,
    route_profile: Literal["strict", "policy"] | None = None,
    source: PackagedPortableResources | None = None,
) -> CursorPluginArtifact:
    """Render one Cursor artifact from canonical packaged guidance bytes."""

    if type(format_profile) is not PluginFormatProfile or format_profile not in {
        PluginFormatProfile.AGENT_PLUGINS_1,
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
    }:
        raise ValueError("cursor_format_invalid")
    if type(mcp_ownership) is not McpOwnership:
        raise ValueError("cursor_mcp_ownership_invalid")
    resources = PackagedPortableResources() if source is None else source
    if format_profile is PluginFormatProfile.AGENT_PLUGINS_1:
        rendered: RenderedPortablePlugin = build_portable_plugin_plan(
            mcp_ownership=mcp_ownership,
            mcp_route_profile=route_profile,
            resource_source=resources,
        )
        return CursorPluginArtifact(rendered.plan, dict(rendered.members), rendered.artifact_digest)
    members = _native_members(
        source=resources,
        mcp_ownership=mcp_ownership,
        route_profile=route_profile,
    )
    plan = PortablePluginPlan(
        name="yoetz",
        version=__version__,
        description=_DESCRIPTION,
        format_profile=PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
        mcp_ownership=mcp_ownership,
        mcp_route_profile=route_profile,
        host_extension_profile=CURSOR_NATIVE_PROFILE_ID,
        specification_version="1.0.0",
        renderer_version=_RENDERER_VERSION,
        source_refs=tuple(
            sorted(
                {
                    "guidance/agent-instructions.md",
                    "guidance/coverage-and-receipts.md",
                    "guidance/publication-policy.md",
                    "guidance/request-templates.md",
                    "guidance/workflow.md",
                    "skills/portable/yoetz/SKILL.md",
                },
                key=str.encode,
            )
        ),
        inventory=_inventory(members),
    )
    digest = canonical_digest(
        {
            "files": [
                {"relative_path": item.relative_path, "sha256": item.sha256, "size": item.size}
                for item in plan.inventory
            ],
            "format_profile": format_profile.value,
            "renderer_version": _RENDERER_VERSION,
        }
    )
    return CursorPluginArtifact(plan, members, digest)


def _safe_existing_ancestor(path: Path) -> Path:
    current = path
    missing: list[Path] = []
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    if hasattr(os, "geteuid") and current.stat().st_uid != os.geteuid():
        raise _error(PluginArtifactReason.TARGET_UNTRUSTED)
    if current.stat().st_mode & 0o022:
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    for candidate in reversed(missing):
        if candidate.name in {"", ".", ".."}:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
    return current


def _target_path(target: CursorPluginTarget) -> tuple[Path, str]:
    if type(target) is not CursorPluginTarget:
        raise _error(PluginArtifactReason.TARGET_UNTRUSTED)
    root = Path(target.cursor_config_root)
    _safe_existing_ancestor(root)
    for candidate in (root, *root.parents):
        if candidate.exists() and candidate.is_symlink():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        if candidate.parent == candidate:
            break
    identity = canonical_digest({"cursor_config_root": str(root), "scope": target.scope})
    return root, identity


def _ensure_private_path(path: Path) -> None:
    existing = _safe_existing_ancestor(path)
    relative = path.relative_to(existing)
    current = existing
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        facts = current.stat()
        if (hasattr(os, "geteuid") and facts.st_uid != os.geteuid()) or facts.st_mode & 0o022:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)


def _safe_tree(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix().encode("utf-8")):
        if path.is_symlink():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        if path.is_dir():
            continue
        if (
            not path.is_file()
            or path.stat().st_nlink != 1
            or path.stat().st_size > _MAX_FILE_BYTES
            or len(result) >= _MAX_FILES
        ):
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _tree_digest(files: Mapping[str, bytes]) -> str:
    return canonical_digest(
        {
            "files": [
                {"relative_path": path, "sha256": _sha(data), "size": len(data)}
                for path, data in sorted(files.items(), key=lambda item: item[0].encode("ascii"))
            ]
        }
    )


def _load_object(data: bytes) -> Mapping[str, JsonValue] | None:
    try:
        value = strict_json_parse(data)
    except ProtocolValueError, UnicodeError:
        return None
    return cast(Mapping[str, JsonValue], value) if isinstance(value, Mapping) else None


def _marker(artifact: CursorPluginArtifact) -> bytes:
    body: dict[str, JsonValue] = {
        "artifact_digest": artifact.artifact_digest,
        "format_profile": artifact.plan.format_profile.value,
        "hook_mapping_version": (
            CURSOR_HOOK_MAPPING_VERSION
            if artifact.plan.format_profile is PluginFormatProfile.CURSOR_PLUGIN_NATIVE
            else None
        ),
        "managed_files": [
            {"relative_path": item.relative_path, "sha256": item.sha256, "size": item.size}
            for item in artifact.plan.inventory
        ],
        "mcp_ownership": artifact.plan.mcp_ownership.value,
        "mcp_route_profile": artifact.plan.mcp_route_profile,
        "renderer_version": artifact.plan.renderer_version,
        "schema": _MARKER_SCHEMA,
        "yoetz_version": artifact.plan.version,
    }
    return canonical_encode({**body, "marker_digest": canonical_digest(body)})


def _valid_marker(
    files: Mapping[str, bytes],
) -> tuple[bool, PluginFormatProfile | None, str | None]:
    raw = files.get(_MARKER_NAME)
    marker = None if raw is None else _load_object(raw)
    if marker is None or marker.get("schema") != _MARKER_SCHEMA:
        return False, None, None
    digest = marker.get("marker_digest")
    body = {key: value for key, value in marker.items() if key != "marker_digest"}
    if type(digest) is not str or digest != canonical_digest(body):
        return False, None, None
    try:
        format_profile = PluginFormatProfile(cast(str, marker.get("format_profile")))
    except TypeError, ValueError:
        return False, None, None
    if format_profile not in {
        PluginFormatProfile.AGENT_PLUGINS_1,
        PluginFormatProfile.CURSOR_PLUGIN_NATIVE,
    }:
        return False, None, None
    rows = marker.get("managed_files")
    if type(rows) is not list:
        return False, None, None
    expected: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return False, None, None
        path = row.get("relative_path")
        size = row.get("size")
        sha = row.get("sha256")
        if (
            type(path) is not str
            or type(size) is not int
            or type(sha) is not str
            or path in expected
        ):
            return False, None, None
        expected[path] = (size, sha)
    content = {path: data for path, data in files.items() if path != _MARKER_NAME}
    if set(content) != set(expected) or any(
        expected[path] != (len(data), _sha(data)) for path, data in content.items()
    ):
        return False, format_profile, cast(str | None, marker.get("artifact_digest"))
    artifact_digest = marker.get("artifact_digest")
    return True, format_profile, artifact_digest if type(artifact_digest) is str else None


def _inspect(target: CursorPluginTarget, artifact: CursorPluginArtifact) -> _Inspection:
    root, target_identity = _target_path(target)
    destination = root / CURSOR_PLUGIN_RELATIVE_ROOT
    rollback = destination.parent / _ROLLBACK_NAME
    rollback_available = rollback.exists() and rollback.is_dir() and not rollback.is_symlink()
    if rollback.exists() or rollback.is_symlink():
        return _Inspection(
            root,
            destination,
            PluginArtifactState.RECOVERY_REQUIRED,
            None,
            target_identity,
            canonical_digest({"state": "rollback_present"}),
            None,
            False,
            rollback_available,
        )
    parent = destination.parent
    if parent.exists() and any(item.name.startswith(_STAGE_PREFIX) for item in parent.iterdir()):
        return _Inspection(
            root,
            destination,
            PluginArtifactState.RECOVERY_REQUIRED,
            None,
            target_identity,
            canonical_digest({"state": "stage_present"}),
            None,
            False,
            False,
        )
    if not destination.exists():
        return _Inspection(
            root,
            destination,
            PluginArtifactState.ABSENT,
            None,
            target_identity,
            canonical_digest({"state": "absent"}),
            None,
            False,
            False,
        )
    if destination.is_symlink() or not destination.is_dir():
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    files = _safe_tree(destination)
    current_digest = _tree_digest(files)
    valid, format_profile, installed_digest = _valid_marker(files)
    if not valid:
        state = (
            PluginArtifactState.MODIFIED
            if format_profile is not None
            else PluginArtifactState.UNMANAGED
        )
        return _Inspection(
            root,
            destination,
            state,
            format_profile,
            target_identity,
            current_digest,
            installed_digest,
            False,
            False,
        )
    expected = {**artifact.members, _MARKER_NAME: _marker(artifact)}
    state = (
        PluginArtifactState.PORTABLE_EXACT
        if format_profile is PluginFormatProfile.AGENT_PLUGINS_1 and files == expected
        else PluginArtifactState.PORTABLE_MANAGED
        if format_profile is PluginFormatProfile.AGENT_PLUGINS_1
        else PluginArtifactState.NATIVE_MANAGED
    )
    return _Inspection(
        root,
        destination,
        state,
        format_profile,
        target_identity,
        current_digest,
        installed_digest,
        True,
        False,
    )


def _preview_from_inspection(
    request: RequestId,
    action: PluginArtifactAction,
    inspection: _Inspection,
    artifact: CursorPluginArtifact,
    mcp_observation: CursorMcpObservation,
) -> CursorPluginPreview:
    if inspection.state is PluginArtifactState.RECOVERY_REQUIRED:
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    if (
        action is PluginArtifactAction.INSTALL
        and inspection.state is not PluginArtifactState.ABSENT
    ):
        raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    if action is PluginArtifactAction.REPLACE and inspection.state not in {
        PluginArtifactState.PORTABLE_EXACT,
        PluginArtifactState.PORTABLE_MANAGED,
        PluginArtifactState.NATIVE_MANAGED,
    }:
        raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    if action is PluginArtifactAction.REMOVE and (
        inspection.state
        not in {
            PluginArtifactState.PORTABLE_EXACT,
            PluginArtifactState.PORTABLE_MANAGED,
            PluginArtifactState.NATIVE_MANAGED,
        }
        or not inspection.marker_valid
    ):
        raise _error(PluginArtifactReason.REMOVE_REFUSED)
    if action is not PluginArtifactAction.REMOVE:
        allowed = (
            {McpOwnershipState.EXTERNAL}
            if artifact.plan.mcp_ownership is McpOwnership.EXTERNAL_REGISTRATION
            else {McpOwnershipState.ABSENT, McpOwnershipState.PLUGIN}
        )
        # Replacement may be the operation that installs plugin-managed MCP.  The
        # currently installed exact external artifact legitimately observes absent.
        if artifact.plan.mcp_ownership is McpOwnership.EXTERNAL_REGISTRATION:
            allowed.add(McpOwnershipState.ABSENT)
        if mcp_observation.ownership_state not in allowed:
            raise _error(
                PluginArtifactReason.MCP_OWNERSHIP_CONFLICT,
                {"mcp_ownership_state": mcp_observation.ownership_state.value},
            )
    exact = (
        inspection.marker_valid
        and inspection.format_profile is artifact.plan.format_profile
        and inspection.installed_digest == artifact.artifact_digest
    )
    effective = (
        PluginArtifactAction.NOOP
        if exact and action in {PluginArtifactAction.INSTALL, PluginArtifactAction.REPLACE}
        else action
    )
    warnings = tuple(
        sorted(
            {
                "activation_not_inferred_from_installation",
                "cursor_cloud_not_supported",
                "mcp_handshake_does_not_prove_model_use",
                "observation_requires_separate_consent",
                "sdk_setting_sources_must_be_explicit",
            },
            key=str.encode,
        )
    )
    preview_digest = canonical_digest(
        {
            "action": effective.value,
            "artifact_digest": artifact.artifact_digest,
            "current_state_digest": inspection.current_state_digest,
            "format_profile": artifact.plan.format_profile.value,
            "mcp_ownership": artifact.plan.mcp_ownership.value,
            "mcp_ownership_state": mcp_observation.ownership_state.value,
            "mcp_route_profile": artifact.plan.mcp_route_profile,
            "request_id": request,
            "target_identity": inspection.target_identity,
        }
    )
    return CursorPluginPreview(
        request,
        effective,
        inspection.state,
        artifact.plan.format_profile,
        inspection.target_identity,
        inspection.current_state_digest,
        artifact.artifact_digest,
        preview_digest,
        artifact.plan.mcp_ownership,
        mcp_observation.ownership_state,
        artifact.plan.mcp_route_profile,
        warnings,
    )


def preview_cursor_plugin(
    request: RequestId,
    target: CursorPluginTarget,
    action: PluginArtifactAction,
    artifact: CursorPluginArtifact,
    *,
    project_root: Path | None = None,
    inline_create: Mapping[str, JsonValue] | None = None,
    inline_send: Mapping[str, JsonValue] | None = None,
) -> CursorPluginPreview:
    request = validate_request_id(request)
    if type(action) is not PluginArtifactAction or action is PluginArtifactAction.NOOP:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    inspection = _inspect(target, artifact)
    observation = observe_cursor_mcp(
        plugin_root=inspection.destination,
        project_root=project_root,
        user_config_root=inspection.root,
        inline_create=inline_create,
        inline_send=inline_send,
    )
    return _preview_from_inspection(request, action, inspection, artifact, observation)


def _write_file(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short_write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage(parent: Path, artifact: CursorPluginArtifact, request: RequestId) -> Path:
    stage = parent / f"{_STAGE_PREFIX}{request.removeprefix('req_')}"
    if stage.exists() or stage.is_symlink():
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    stage.mkdir(mode=0o700)
    try:
        members = {**artifact.members, _MARKER_NAME: _marker(artifact)}
        for relative, data in sorted(members.items(), key=lambda item: item[0].encode("ascii")):
            destination = stage / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.parent.is_symlink():
                raise _error(PluginArtifactReason.TARGET_UNSAFE)
            _write_file(destination, data)
        for directory in sorted(
            (path for path in stage.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            _fsync_dir(directory)
        _fsync_dir(stage)
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def apply_cursor_plugin(
    request: RequestId,
    target: CursorPluginTarget,
    action: PluginArtifactAction,
    artifact: CursorPluginArtifact,
    *,
    accepted_preview_digest: str,
    explicitly_accepted: bool,
    project_root: Path | None = None,
) -> CursorPluginResult:
    if not explicitly_accepted:
        raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
    _validate_digest(accepted_preview_digest)
    preview = preview_cursor_plugin(
        request,
        target,
        action,
        artifact,
        project_root=project_root,
    )
    if preview.preview_digest != accepted_preview_digest:
        raise _error(PluginArtifactReason.PREVIEW_STALE)
    if preview.action is PluginArtifactAction.NOOP:
        return CursorPluginResult(
            preview.request_id,
            preview.action,
            PluginOperationState.COMPLETED,
            preview.state_before,
            preview.state_before,
            preview.format_profile,
            preview.preview_digest,
            preview.artifact_digest,
            preview.artifact_digest,
            (),
        )
    root, _identity = _target_path(target)
    _ensure_private_path(root / "plugins" / "local")
    destination = root / CURSOR_PLUGIN_RELATIVE_ROOT
    parent = destination.parent
    rollback = parent / _ROLLBACK_NAME
    stage = _stage(parent, artifact, preview.request_id)
    moved_existing = False
    try:
        if destination.exists():
            os.replace(destination, rollback)
            moved_existing = True
            _fsync_dir(parent)
        os.replace(stage, destination)
        _fsync_dir(parent)
        installed = _safe_tree(destination)
        if installed != {**artifact.members, _MARKER_NAME: _marker(artifact)}:
            raise OSError("installed_bytes_mismatch")
    except BaseException as exc:
        with contextlib.suppress(OSError):
            if destination.exists():
                failed = parent / f"{_STAGE_PREFIX}failed"
                os.replace(destination, failed)
            if moved_existing and rollback.exists() and not destination.exists():
                os.replace(rollback, destination)
                _fsync_dir(parent)
        raise _error(PluginArtifactReason.WRITE_FAILED) from exc
    # The new bytes are committed once the exact post-swap verification passes.
    # Cleanup failure must never enter the pre-commit rollback branch: the old
    # tree may already be partially removed.  Preserve both trees and report a
    # recovery-required state instead of guessing which outcome won.
    if moved_existing:
        try:
            shutil.rmtree(rollback)
            _fsync_dir(parent)
        except OSError as exc:
            raise _error(PluginArtifactReason.WRITE_FAILED) from exc
    after = (
        PluginArtifactState.PORTABLE_EXACT
        if artifact.plan.format_profile is PluginFormatProfile.AGENT_PLUGINS_1
        else PluginArtifactState.NATIVE_MANAGED
    )
    return CursorPluginResult(
        preview.request_id,
        preview.action,
        PluginOperationState.COMPLETED,
        preview.state_before,
        after,
        preview.format_profile,
        preview.preview_digest,
        preview.artifact_digest,
        preview.artifact_digest,
        tuple(sorted({*artifact.members, _MARKER_NAME}, key=str.encode)),
    )


def remove_cursor_plugin(
    request: RequestId,
    target: CursorPluginTarget,
    artifact: CursorPluginArtifact,
    *,
    accepted_preview_digest: str,
    explicitly_accepted: bool,
    project_root: Path | None = None,
) -> CursorPluginResult:
    if not explicitly_accepted:
        raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
    preview = preview_cursor_plugin(
        request,
        target,
        PluginArtifactAction.REMOVE,
        artifact,
        project_root=project_root,
    )
    if preview.preview_digest != accepted_preview_digest:
        raise _error(PluginArtifactReason.PREVIEW_STALE)
    root, _identity = _target_path(target)
    destination = root / CURSOR_PLUGIN_RELATIVE_ROOT
    rollback = destination.parent / _ROLLBACK_NAME
    files = tuple(sorted(_safe_tree(destination), key=str.encode))
    try:
        os.replace(destination, rollback)
        _fsync_dir(destination.parent)
        shutil.rmtree(rollback)
        _fsync_dir(destination.parent)
    except OSError as exc:
        raise _error(PluginArtifactReason.WRITE_FAILED) from exc
    return CursorPluginResult(
        preview.request_id,
        PluginArtifactAction.REMOVE,
        PluginOperationState.COMPLETED,
        preview.state_before,
        PluginArtifactState.ABSENT,
        preview.format_profile,
        preview.preview_digest,
        preview.artifact_digest,
        None,
        files,
    )


def _proof(state: PluginArtifactState) -> tuple[PluginProofStatus, ...]:
    installed = state in {
        PluginArtifactState.PORTABLE_EXACT,
        PluginArtifactState.PORTABLE_MANAGED,
        PluginArtifactState.NATIVE_MANAGED,
    }
    return tuple(
        PluginProofStatus(
            facet,
            (
                "proven"
                if facet in {PluginProofFacet.SOURCE, PluginProofFacet.RENDERED_ARTIFACT}
                or (installed and facet is PluginProofFacet.INSTALLED_BYTES)
                else "not_observed"
            ),
        )
        for facet in PluginProofFacet
    )


def status_cursor_plugin(
    target: CursorPluginTarget,
    artifact: CursorPluginArtifact,
    *,
    project_root: Path | None = None,
) -> CursorPluginStatus:
    inspection = _inspect(target, artifact)
    observation = observe_cursor_mcp(
        plugin_root=inspection.destination,
        project_root=project_root,
        user_config_root=inspection.root,
    )
    return CursorPluginStatus(
        inspection.state,
        (
            PluginOperationState.IN_PROGRESS
            if inspection.state is PluginArtifactState.RECOVERY_REQUIRED
            else PluginOperationState.NOT_STARTED
            if inspection.state is PluginArtifactState.ABSENT
            else PluginOperationState.COMPLETED
        ),
        inspection.format_profile,
        artifact.artifact_digest,
        inspection.installed_digest,
        inspection.marker_valid,
        inspection.rollback_available,
        observation,
        _proof(inspection.state),
    )


def _config_entry(path: Path) -> tuple[Mapping[str, JsonValue] | None, bool]:
    if not path.exists() and not path.is_symlink():
        return None, True
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return None, False
        parsed = strict_json_parse(path.read_bytes())
    except OSError, ProtocolValueError, UnicodeError:
        return None, False
    if not isinstance(parsed, Mapping):
        return None, False
    servers = parsed.get("mcpServers")
    if not isinstance(servers, Mapping):
        return None, "mcpServers" not in parsed
    entry = servers.get("yoetz")
    if entry is None:
        return None, True
    if not isinstance(entry, Mapping):
        return None, False
    return cast(Mapping[str, JsonValue], entry), True


def _route_profile(entry: Mapping[str, JsonValue] | None) -> Literal["strict", "policy"] | None:
    if entry is None:
        return None
    expected_base = {"command": "yoetz", "type": "stdio"}
    if any(entry.get(key) != value for key, value in expected_base.items()):
        return None
    args = entry.get("args")
    if args in (
        ["mcp", "serve"],
        ("mcp", "serve"),
        ["mcp", "serve", "--host", "cursor"],
        ("mcp", "serve", "--host", "cursor"),
    ):
        return "policy"
    if args in (
        ["mcp", "serve", "--semantic", "off"],
        ("mcp", "serve", "--semantic", "off"),
        ["mcp", "serve", "--host", "cursor", "--semantic", "off"],
        ("mcp", "serve", "--host", "cursor", "--semantic", "off"),
    ):
        return "strict"
    return None


def observe_cursor_mcp(
    *,
    plugin_root: Path,
    project_root: Path | None,
    user_config_root: Path,
    inline_create: Mapping[str, JsonValue] | None = None,
    inline_send: Mapping[str, JsonValue] | None = None,
) -> CursorMcpObservation:
    """Classify exact same-name sources using Cursor SDK precedence.

    A successful route-shaped entry is not silently attributed to the plugin.
    Duplicate exact sources remain ambiguous (or dual for plugin+external), and
    any same-name foreign entry remains foreign.
    """

    candidates: list[tuple[CursorMcpSource, Mapping[str, JsonValue] | None]] = []
    uncertain: list[CursorMcpSource] = []
    for source, raw in (
        (CursorMcpSource.INLINE_SEND, inline_send),
        (CursorMcpSource.INLINE_CREATE, inline_create),
    ):
        if raw is not None:
            entry = raw.get("yoetz") if isinstance(raw.get("yoetz"), Mapping) else raw
            candidates.append((source, cast(Mapping[str, JsonValue], entry)))
    plugin_entry, plugin_observed = _config_entry(plugin_root / "mcp.json")
    if not plugin_observed:
        uncertain.append(CursorMcpSource.PLUGIN)
    if plugin_entry is not None:
        candidates.append((CursorMcpSource.PLUGIN, plugin_entry))
    if project_root is not None:
        project_entry, project_observed = _config_entry(project_root / ".cursor" / "mcp.json")
        if not project_observed:
            uncertain.append(CursorMcpSource.PROJECT)
        if project_entry is not None:
            candidates.append((CursorMcpSource.PROJECT, project_entry))
    user_entry, user_observed = _config_entry(user_config_root / "mcp.json")
    if not user_observed:
        uncertain.append(CursorMcpSource.USER)
    if user_entry is not None:
        candidates.append((CursorMcpSource.USER, user_entry))
    if uncertain:
        present = tuple(source for source, _entry in candidates)
        return CursorMcpObservation(
            McpOwnershipState.AMBIGUOUS,
            None,
            None,
            tuple(dict.fromkeys((*present, *uncertain))),
            False,
        )
    if not candidates:
        return CursorMcpObservation(McpOwnershipState.ABSENT, None, None, (), True)
    profiles = [(source, _route_profile(entry)) for source, entry in candidates]
    present = tuple(source for source, _profile in profiles)
    if any(profile is None for _source, profile in profiles):
        foreign_source = next(source for source, profile in profiles if profile is None)
        return CursorMcpObservation(McpOwnershipState.FOREIGN, foreign_source, None, present, True)
    plugin_profiles = [profile for source, profile in profiles if source is CursorMcpSource.PLUGIN]
    external_profiles = [
        profile for source, profile in profiles if source is not CursorMcpSource.PLUGIN
    ]
    if plugin_profiles and external_profiles:
        return CursorMcpObservation(McpOwnershipState.DUAL, present[0], None, present, True)
    if len(profiles) > 1:
        return CursorMcpObservation(McpOwnershipState.AMBIGUOUS, present[0], None, present, True)
    source, profile = profiles[0]
    assert profile is not None
    return CursorMcpObservation(
        McpOwnershipState.PLUGIN
        if source is CursorMcpSource.PLUGIN
        else McpOwnershipState.EXTERNAL,
        source,
        cast(Literal["strict", "policy"], profile),
        present,
        True,
    )


def _digest_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("cursor_artifact_unavailable")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def discover_cursor_ide(app_path: Path) -> CursorCapabilityIdentity:
    info_path = app_path / "Contents" / "Info.plist"
    executable_root = app_path / "Contents" / "MacOS"
    if app_path.is_symlink() or not info_path.is_file() or info_path.is_symlink():
        raise ValueError("cursor_ide_unavailable")
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("cursor_ide_identity_invalid") from exc
    executable_name = info.get("CFBundleExecutable")
    version = info.get("CFBundleShortVersionString")
    build = info.get("CFBundleVersion")
    if not all(type(item) is str and item for item in (executable_name, version, build)):
        raise ValueError("cursor_ide_identity_invalid")
    executable = executable_root / cast(str, executable_name)
    return CursorCapabilityIdentity(
        "cursor_ide",
        cast(str, version),
        cast(str, build),
        _digest_file(executable),
        platform.system().lower(),
        platform.machine().lower(),
    )


def discover_cursor_cli(executable: Path) -> CursorCapabilityIdentity:
    if executable.is_symlink():
        resolved = executable.resolve(strict=True)
    else:
        resolved = executable
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("cursor_cli_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="yoetz-cursor-discovery-") as temporary:
            environment = dict(os.environ)
            environment.update(
                {
                    "CURSOR_CONFIG_DIR": str(Path(temporary) / "config"),
                    "CURSOR_DATA_DIR": str(Path(temporary) / "data"),
                    "HOME": temporary,
                    "XDG_CACHE_HOME": str(Path(temporary) / "cache"),
                    "XDG_CONFIG_HOME": str(Path(temporary) / "xdg-config"),
                    "XDG_DATA_HOME": str(Path(temporary) / "xdg-data"),
                }
            )
            completed = subprocess.run(
                (str(executable), "--version"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                shell=False,
                timeout=5,
                env=environment,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("cursor_cli_unavailable") from exc
    if completed.returncode != 0:
        raise ValueError("cursor_cli_unavailable")
    if len(completed.stdout) > _MAX_CURSOR_IDENTITY_BYTES:
        raise ValueError("cursor_cli_identity_invalid")
    try:
        output = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("cursor_cli_identity_invalid") from exc
    version = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if not version or len(version) > 128:
        raise ValueError("cursor_cli_identity_invalid")
    return CursorCapabilityIdentity(
        "cursor_cli",
        version,
        version,
        _digest_file(resolved),
        platform.system().lower(),
        platform.machine().lower(),
    )


def discover_cursor_sdk(
    binding: CursorSdkBinding,
    *,
    package_metadata: Path,
    bridge_path: Path | None = None,
) -> CursorArtifactIdentity:
    if type(binding) is not CursorSdkBinding:
        raise ValueError("cursor_sdk_binding_invalid")
    if package_metadata.is_symlink() or not package_metadata.is_file():
        raise ValueError("cursor_sdk_unavailable")
    package_digest = _digest_file(package_metadata)
    try:
        metadata = strict_json_parse(package_metadata.read_bytes())
    except ProtocolValueError, UnicodeError:
        metadata = None
    version: object = None
    if isinstance(metadata, Mapping):
        version = metadata.get("version")
    if version is None and binding is CursorSdkBinding.PYTHON:
        # A wheel METADATA file is deliberately parsed without importing the package.
        for line in package_metadata.read_text("utf-8").splitlines():
            if line.startswith("Version: "):
                version = line.removeprefix("Version: ").strip()
                break
    if type(version) is not str or not version or len(version) > 128:
        raise ValueError("cursor_sdk_version_invalid")
    bridge_digest = None if bridge_path is None else _digest_file(bridge_path)
    return CursorArtifactIdentity(binding, version, package_digest, bridge_digest=bridge_digest)


def build_cursor_sdk_profile(
    identity: CursorArtifactIdentity,
    *,
    setting_sources: Sequence[Literal["plugins", "project", "user"]],
    mcp_ownership: McpOwnership,
    sandbox_enabled: bool,
    approval_mode: Literal["default", "allowlist", "full"],
) -> CursorSdkProfile:
    return CursorSdkProfile(
        identity,
        tuple(setting_sources),
        (
            CursorMcpSource.INLINE_SEND,
            CursorMcpSource.INLINE_CREATE,
            CursorMcpSource.PLUGIN,
            CursorMcpSource.PROJECT,
            CursorMcpSource.USER,
        ),
        mcp_ownership,
        sandbox_enabled,
        approval_mode,
    )
