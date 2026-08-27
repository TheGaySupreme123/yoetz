"""Claude Code native project-plugin integration (issue #154).

Claude Code does not consume the Agent Plugins carrier.  This adapter renders a
Claude-native projection from the same packaged skill bytes, exposes one exact
CLI/local/project/private-marketplace capability cell, and keeps source,
marketplace registration, cache installation, enablement, loaded-session
activation, MCP ownership, hooks, observation, model use, and receipts separate.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from yoetz import __version__
from yoetz.adapters.integrations.portable_plugin import PackagedPortableResources
from yoetz.domain.values import JsonObject, RequestId
from yoetz.domain.values import request_id as validate_request_id
from yoetz.ports.integrations import HarnessHookProfile, HarnessId, HarnessProfile
from yoetz.ports.plugin_artifacts import (
    ArtifactAuthority,
    ManagedPluginFile,
    McpOwnership,
    McpOwnershipState,
    PluginArtifactReason,
    PluginArtifactState,
    PluginFormatProfile,
    PluginMutationReviewPort,
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
    "CLAUDE_CODE_HARNESS_PROFILE",
    "CLAUDE_CODE_HOOK_EVENTS",
    "CLAUDE_CODE_MINIMUM_VERSION",
    "CLAUDE_CODE_NATIVE_PROFILE_ID",
    "ClaudeCodeCapabilityIdentity",
    "ClaudeCodeCommandPort",
    "ClaudeCodeCommandResult",
    "ClaudeCodeIntegrationError",
    "ClaudeCodeMcpObservation",
    "ClaudeCodeMcpSource",
    "ClaudeCodePluginAction",
    "ClaudeCodePluginArtifact",
    "ClaudeCodePluginPreview",
    "ClaudeCodePluginResult",
    "ClaudeCodeSessionObservation",
    "ClaudeCodePluginStatus",
    "ClaudeCodePluginTarget",
    "SubprocessClaudeCodeCommands",
    "apply_claude_code_plugin",
    "discover_claude_code",
    "observe_claude_code_mcp",
    "observe_claude_code_session_init",
    "preview_claude_code_plugin",
    "render_claude_code_plugin",
    "status_claude_code_plugin",
]

CLAUDE_CODE_MINIMUM_VERSION: Final = "2.1.233"
CLAUDE_CODE_NATIVE_PROFILE_ID: Final = "claude-code-cli-local-project-2.1.241"
CLAUDE_CODE_HOOK_MAPPING_VERSION: Final = "claude-code-hooks-2.1.241-v1"
CLAUDE_CODE_HOOK_EVENTS: Final = (
    "PostToolUse",
    "PostToolUseFailure",
    "SessionEnd",
    "SessionStart",
    "Stop",
)
_CLAUDE_HOOK_PROFILE: Final = HarnessHookProfile(
    trigger_event="SessionStart",
    trigger_payload_profile_id=CLAUDE_CODE_HOOK_MAPPING_VERSION,
    evidence_case_ids=("claude-code-cli-native-project-2.1.241-macos-arm64",),
    observation_events=CLAUDE_CODE_HOOK_EVENTS,
)
CLAUDE_CODE_HARNESS_PROFILE: Final = HarnessProfile(
    harness_id=HarnessId.CLAUDE,
    skill_root="skills/",
    frontmatter_profile="agent-skills-1",
    capability_profile_ids=(CLAUDE_CODE_NATIVE_PROFILE_ID,),
    supported_versions=("2.1.241",),
    hooks_by_capability_profile={CLAUDE_CODE_NATIVE_PROFILE_ID: _CLAUDE_HOOK_PROFILE},
)

_MARKETPLACE_NAME: Final = "yoetz-local"
_PLUGIN_ID: Final = f"yoetz@{_MARKETPLACE_NAME}"
_MARKER_NAME: Final = ".yoetz-claude-marketplace-install.json"
_MARKER_SCHEMA: Final = "yoetz.claude-code-marketplace-install/1"
_RENDERER_VERSION: Final = "claude-code-plugin/0.1.0"
_STAGE_PREFIX: Final = ".yoetz-claude-marketplace-stage-"
_ROLLBACK_NAME: Final = ".yoetz-claude-marketplace-rollback"
_MAX_FILE_BYTES: Final = 262_144
_MAX_FILES: Final = 64
_MAX_PATH: Final = 4_096
_MAX_COMMAND_OUTPUT: Final = 1_048_576
_DESCRIPTION: Final = (
    "Records material work in a local Yoetz ledger and checks completion claims "
    "against that record."
)
_GUIDANCE_NAMES: Final = (
    "agent-instructions.md",
    "coverage-and-receipts.md",
    "publication-policy.md",
    "request-templates.md",
    "workflow.md",
)
_YOETZ_SCOPED_TOOL_MATCHER: Final = (
    "^mcp__plugin_yoetz_yoetz__(start|publish_work|check|respond|status|receipt)$"
)
_VERSION_RE: Final = re.compile(r"^(\d+)\.(\d+)\.(\d+)$", re.ASCII)


class ClaudeCodePluginAction(str, Enum):  # noqa: UP042 - exact public token
    INSTALL = "install"
    UPDATE = "update"
    ENABLE = "enable"
    DISABLE = "disable"
    REMOVE = "remove"
    NOOP = "noop"


class ClaudeCodeMcpSource(str, Enum):  # noqa: UP042 - exact public token
    LOCAL = "local"
    PROJECT = "project"
    USER = "user"
    PLUGIN = "plugin"
    CLAUDE_AI_CONNECTOR = "claude_ai_connector"


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeCodeCapabilityIdentity:
    version: str
    executable_digest: str
    os_name: str
    architecture: str

    def __post_init__(self) -> None:
        _version_tuple(self.version)
        _validate_digest(self.executable_digest)
        for value in (self.os_name, self.architecture):
            if type(value) is not str or not value or len(value) > 128:
                raise ValueError("claude_code_identity_invalid")

    def __repr__(self) -> str:
        return (
            "ClaudeCodeCapabilityIdentity("
            f"version={self.version!r}, executable_digest={self.executable_digest!r}, "
            f"os_name={self.os_name!r}, architecture={self.architecture!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeCodePluginTarget:
    project_root: str
    claude_config_root: str
    cache_root: str
    marketplace_root: str
    executable: str
    identity: ClaudeCodeCapabilityIdentity
    scope: Literal["project"] = "project"
    marketplace_name: Literal["yoetz-local"] = _MARKETPLACE_NAME

    def __post_init__(self) -> None:
        for value in (
            self.project_root,
            self.claude_config_root,
            self.cache_root,
            self.marketplace_root,
            self.executable,
        ):
            if (
                type(value) is not str
                or not 1 <= len(value) <= _MAX_PATH
                or not Path(value).is_absolute()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError("claude_code_target_invalid")
        if type(self.identity) is not ClaudeCodeCapabilityIdentity:
            raise ValueError("claude_code_target_invalid")
        if self.scope != "project" or self.marketplace_name != _MARKETPLACE_NAME:
            raise ValueError("claude_code_target_invalid")
        if Path(self.cache_root) != Path(self.claude_config_root) / "plugins" / "cache":
            raise ValueError("claude_code_cache_root_invalid")

    def __repr__(self) -> str:
        return (
            "ClaudeCodePluginTarget(project_root=<redacted>, claude_config_root=<redacted>, "
            "cache_root=<redacted>, marketplace_root=<redacted>, executable=<redacted>, "
            f"identity={self.identity!r}, scope={self.scope!r}, "
            f"marketplace_name={self.marketplace_name!r})"
        )


@dataclass(frozen=True, slots=True)
class ClaudeCodePluginArtifact:
    plan: PortablePluginPlan
    members: Mapping[str, bytes]
    marketplace_manifest: bytes
    artifact_digest: str
    marketplace_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not PortablePluginPlan
            or self.plan.format_profile is not PluginFormatProfile.CLAUDE_CODE_PLUGIN_NATIVE
        ):
            raise ValueError("claude_code_artifact_invalid")
        _validate_digest(self.artifact_digest)
        _validate_digest(self.marketplace_digest)
        expected = tuple(item.relative_path for item in self.plan.inventory)
        if tuple(sorted(self.members, key=str.encode)) != expected:
            raise ValueError("claude_code_artifact_inventory_invalid")
        for item in self.plan.inventory:
            data = self.members[item.relative_path]
            if type(data) is not bytes or len(data) != item.size or _sha(data) != item.sha256:
                raise ValueError("claude_code_artifact_inventory_invalid")
        if (
            type(self.marketplace_manifest) is not bytes
            or _sha(self.marketplace_manifest) != self.marketplace_digest
        ):
            raise ValueError("claude_code_artifact_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodeMcpObservation:
    ownership_state: McpOwnershipState
    winning_source: ClaudeCodeMcpSource | None
    route_profile: Literal["strict", "policy"] | None
    present_sources: tuple[ClaudeCodeMcpSource, ...]
    observed: bool

    def __post_init__(self) -> None:
        if type(self.ownership_state) is not McpOwnershipState:
            raise ValueError("claude_code_mcp_observation_invalid")
        if self.winning_source is not None and type(self.winning_source) is not ClaudeCodeMcpSource:
            raise ValueError("claude_code_mcp_observation_invalid")
        if self.route_profile not in {None, "strict", "policy"} or type(self.observed) is not bool:
            raise ValueError("claude_code_mcp_observation_invalid")
        if type(self.present_sources) is not tuple or any(
            type(item) is not ClaudeCodeMcpSource for item in self.present_sources
        ):
            raise ValueError("claude_code_mcp_observation_invalid")
        order = {source: index for index, source in enumerate(ClaudeCodeMcpSource)}
        if (
            tuple(sorted(set(self.present_sources), key=lambda item: order[item]))
            != self.present_sources
        ):
            raise ValueError("claude_code_mcp_observation_invalid")
        if (
            self.ownership_state not in {McpOwnershipState.EXTERNAL, McpOwnershipState.PLUGIN}
            and self.route_profile is not None
        ):
            raise ValueError("claude_code_mcp_observation_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodePluginStatus:
    state: PluginArtifactState
    operation_state: PluginOperationState
    artifact_digest: str
    marketplace_digest: str
    host_state_digest: str
    source_digest: str | None
    installed_digest: str | None
    installed_version: str | None
    marketplace_registered: bool | None
    discovered: bool
    enabled: bool | None
    loaded_root_digest: str | None
    marker_valid: bool
    mcp_observation: ClaudeCodeMcpObservation
    proof: tuple[PluginProofStatus, ...]
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.state) is not PluginArtifactState
            or type(self.operation_state) is not PluginOperationState
        ):
            raise ValueError("claude_code_status_invalid")
        for value in (self.artifact_digest, self.marketplace_digest, self.host_state_digest):
            _validate_digest(value)
        for value in (self.source_digest, self.installed_digest, self.loaded_root_digest):
            if value is not None:
                _validate_digest(value)
        if self.installed_version is not None:
            _version_tuple(self.installed_version)
        if (
            self.marketplace_registered is not None
            and type(self.marketplace_registered) is not bool
        ):
            raise ValueError("claude_code_status_invalid")
        if type(self.discovered) is not bool or (
            self.enabled is not None and type(self.enabled) is not bool
        ):
            raise ValueError("claude_code_status_invalid")
        if (
            type(self.marker_valid) is not bool
            or type(self.mcp_observation) is not ClaudeCodeMcpObservation
        ):
            raise ValueError("claude_code_status_invalid")
        if type(self.proof) is not tuple or tuple(item.facet for item in self.proof) != tuple(
            PluginProofFacet
        ):
            raise ValueError("claude_code_status_invalid")
        if self.notes != tuple(sorted(set(self.notes), key=str.encode)):
            raise ValueError("claude_code_status_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodePluginPreview:
    request_id: RequestId
    action: ClaudeCodePluginAction
    state_before: PluginArtifactState
    target_identity: str
    current_state_digest: str
    artifact_digest: str
    marketplace_digest: str
    preview_digest: str
    mcp_ownership: McpOwnership
    mcp_ownership_state: McpOwnershipState
    mcp_route_profile: Literal["strict", "policy"] | None
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_request_id(self.request_id))
        if (
            type(self.action) is not ClaudeCodePluginAction
            or type(self.state_before) is not PluginArtifactState
        ):
            raise ValueError("claude_code_preview_invalid")
        for value in (
            self.target_identity,
            self.current_state_digest,
            self.artifact_digest,
            self.marketplace_digest,
            self.preview_digest,
        ):
            _validate_digest(value)
        if (
            type(self.mcp_ownership) is not McpOwnership
            or type(self.mcp_ownership_state) is not McpOwnershipState
        ):
            raise ValueError("claude_code_preview_invalid")
        if self.mcp_route_profile not in {None, "strict", "policy"}:
            raise ValueError("claude_code_preview_invalid")
        if self.warnings != tuple(sorted(set(self.warnings), key=str.encode)):
            raise ValueError("claude_code_preview_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodePluginResult:
    request_id: RequestId
    action: ClaudeCodePluginAction
    operation_state: PluginOperationState
    state_before: PluginArtifactState
    state_after: PluginArtifactState
    preview_digest: str
    artifact_digest: str
    installed_digest: str | None
    enabled: bool | None
    changed_files: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", validate_request_id(self.request_id))
        if (
            type(self.action) is not ClaudeCodePluginAction
            or type(self.operation_state) is not PluginOperationState
        ):
            raise ValueError("claude_code_result_invalid")
        if (
            type(self.state_before) is not PluginArtifactState
            or type(self.state_after) is not PluginArtifactState
        ):
            raise ValueError("claude_code_result_invalid")
        for value in (self.preview_digest, self.artifact_digest):
            _validate_digest(value)
        if self.installed_digest is not None:
            _validate_digest(self.installed_digest)
        if self.enabled is not None and type(self.enabled) is not bool:
            raise ValueError("claude_code_result_invalid")
        if self.changed_files != tuple(sorted(set(self.changed_files), key=str.encode)):
            raise ValueError("claude_code_result_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodeSessionObservation:
    session_boundary_digest: str
    loaded_root_digest: str
    skill_registered: bool
    mcp_connected: bool
    scoped_workflow_tools_visible: bool

    def __post_init__(self) -> None:
        _validate_digest(self.session_boundary_digest)
        _validate_digest(self.loaded_root_digest)
        if any(
            type(value) is not bool
            for value in (
                self.skill_registered,
                self.mcp_connected,
                self.scoped_workflow_tools_visible,
            )
        ):
            raise ValueError("claude_code_session_observation_invalid")


@dataclass(frozen=True, slots=True)
class ClaudeCodeIntegrationError(Exception):
    reason: PluginArtifactReason
    safe_details: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if type(self.reason) is not PluginArtifactReason:
            raise ValueError("claude_code_error_invalid")
        details = JsonObject(self.safe_details)
        if len(details) > 16 or len(canonical_encode(details)) > 4_096:
            raise ValueError("claude_code_error_invalid")
        object.__setattr__(self, "safe_details", details)
        Exception.__init__(self, self.reason.value)


@dataclass(frozen=True, slots=True)
class ClaudeCodeCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if (
            type(self.returncode) is not int
            or type(self.stdout) is not bytes
            or type(self.stderr) is not bytes
        ):
            raise ValueError("claude_code_command_result_invalid")
        if len(self.stdout) > _MAX_COMMAND_OUTPUT or len(self.stderr) > _MAX_COMMAND_OUTPUT:
            raise ValueError("claude_code_command_result_invalid")


class ClaudeCodeCommandPort(Protocol):
    def run(
        self, target: ClaudeCodePluginTarget, arguments: Sequence[str]
    ) -> ClaudeCodeCommandResult: ...


class SubprocessClaudeCodeCommands:
    """Run one absolute Claude executable with an explicit config root and project cwd."""

    def run(
        self, target: ClaudeCodePluginTarget, arguments: Sequence[str]
    ) -> ClaudeCodeCommandResult:
        environment = dict(os.environ)
        environment["CLAUDE_CONFIG_DIR"] = target.claude_config_root
        try:
            completed = subprocess.run(
                (target.executable, *arguments),
                cwd=target.project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                shell=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise _error(PluginArtifactReason.WRITE_FAILED) from exc
        if (
            len(completed.stdout) > _MAX_COMMAND_OUTPUT
            or len(completed.stderr) > _MAX_COMMAND_OUTPUT
        ):
            raise _error(PluginArtifactReason.WRITE_FAILED)
        return ClaudeCodeCommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True, repr=False)
class _SourceInspection:
    state: PluginArtifactState
    digest: str | None
    marker_valid: bool
    current_state_digest: str


def _error(
    reason: PluginArtifactReason, details: Mapping[str, JsonValue] | None = None
) -> ClaudeCodeIntegrationError:
    return ClaudeCodeIntegrationError(reason, {} if details is None else details)


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _validate_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ValueError("claude_code_digest_invalid")
    return value


def _version_tuple(value: object) -> tuple[int, int, int]:
    if type(value) is not str:
        raise ValueError("claude_code_version_invalid")
    matched = _VERSION_RE.fullmatch(value)
    if matched is None:
        raise ValueError("claude_code_version_invalid")
    major, minor, patch = matched.groups()
    return int(major), int(minor), int(patch)


def _digest_file(path: Path) -> str:
    if path.is_symlink():
        path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("claude_code_executable_unavailable")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def discover_claude_code(executable: Path) -> ClaudeCodeCapabilityIdentity:
    """Discover version and executable identity without reading ambient Claude state."""

    resolved = executable.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("claude_code_executable_unavailable")
    with tempfile.TemporaryDirectory(prefix="yoetz-claude-discovery-") as temporary:
        environment = dict(os.environ)
        environment["CLAUDE_CONFIG_DIR"] = str(Path(temporary) / "config")
        try:
            completed = subprocess.run(
                (str(resolved), "--version"),
                cwd=temporary,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                shell=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("claude_code_executable_unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > 4_096:
        raise ValueError("claude_code_identity_invalid")
    try:
        first = completed.stdout.decode("utf-8", errors="strict").split()[0]
    except (UnicodeDecodeError, IndexError) as exc:
        raise ValueError("claude_code_identity_invalid") from exc
    _version_tuple(first)
    return ClaudeCodeCapabilityIdentity(
        first,
        _digest_file(resolved),
        platform.system().lower(),
        platform.machine().lower(),
    )


def _inventory(members: Mapping[str, bytes]) -> tuple[ManagedPluginFile, ...]:
    return tuple(
        ManagedPluginFile(path, len(data), _sha(data))
        for path, data in sorted(members.items(), key=lambda item: item[0].encode("ascii"))
    )


def _mcp_json(route_profile: Literal["strict", "policy"]) -> bytes:
    args = ["mcp", "serve"]
    if route_profile == "strict":
        args.extend(("--semantic", "off"))
    return canonical_encode(
        cast(
            JsonValue,
            {"mcpServers": {"yoetz": {"args": args, "command": "yoetz", "type": "stdio"}}},
        )
    )


def _hooks_json() -> bytes:
    command = 'yoetz hooks claude-observe --workspace "${CLAUDE_PROJECT_DIR}"'

    def hook(event: str) -> dict[str, JsonValue]:
        return {"command": f"{command} --event {event}", "timeout": 3, "type": "command"}

    hooks: dict[str, JsonValue] = {
        "PostToolUse": [{"hooks": [hook("PostToolUse")], "matcher": _YOETZ_SCOPED_TOOL_MATCHER}],
        "PostToolUseFailure": [
            {"hooks": [hook("PostToolUseFailure")], "matcher": _YOETZ_SCOPED_TOOL_MATCHER}
        ],
        "SessionEnd": [{"hooks": [hook("SessionEnd")]}],
        "SessionStart": [
            {"hooks": [hook("SessionStart")], "matcher": "startup|resume|clear|compact|fork"}
        ],
        "Stop": [{"hooks": [hook("Stop")]}],
    }
    return canonical_encode(cast(JsonValue, {"hooks": hooks}))


def render_claude_code_plugin(
    *,
    mcp_ownership: McpOwnership = McpOwnership.EXTERNAL_REGISTRATION,
    route_profile: Literal["strict", "policy"] | None = None,
    source: PackagedPortableResources | None = None,
    version: str = __version__,
) -> ClaudeCodePluginArtifact:
    """Render Claude-native bytes from canonical packaged guidance."""

    _version_tuple(version)
    if type(mcp_ownership) is not McpOwnership:
        raise ValueError("claude_code_mcp_ownership_invalid")
    if (mcp_ownership is McpOwnership.PLUGIN_MANAGED) != (route_profile in {"strict", "policy"}):
        raise ValueError(
            "claude_code_mcp_route_required"
            if mcp_ownership is McpOwnership.PLUGIN_MANAGED
            else "claude_code_mcp_route_forbidden"
        )
    resources = PackagedPortableResources() if source is None else source
    manifest: dict[str, JsonValue] = {
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "author": {"name": "Yoetz contributors"},
        "defaultEnabled": False,
        "description": _DESCRIPTION,
        "displayName": "Yoetz",
        "license": "Apache-2.0",
        "name": "yoetz",
        "repository": "https://github.com/TheGaySupreme123/yoetz",
        "version": version,
    }
    members: dict[str, bytes] = {
        ".claude-plugin/plugin.json": canonical_encode(manifest),
        "hooks/hooks.json": _hooks_json(),
        "skills/yoetz/SKILL.md": resources.read_bytes("skills/portable/yoetz/SKILL.md"),
    }
    for name in _GUIDANCE_NAMES:
        members[f"skills/yoetz/references/{name}"] = resources.read_bytes(f"guidance/{name}")
    if mcp_ownership is McpOwnership.PLUGIN_MANAGED:
        assert route_profile is not None
        members[".mcp.json"] = _mcp_json(route_profile)
    plan = PortablePluginPlan(
        name="yoetz",
        version=version,
        description=_DESCRIPTION,
        format_profile=PluginFormatProfile.CLAUDE_CODE_PLUGIN_NATIVE,
        mcp_ownership=mcp_ownership,
        mcp_route_profile=route_profile,
        host_extension_profile=CLAUDE_CODE_NATIVE_PROFILE_ID,
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
    artifact_digest = canonical_digest(
        {
            "files": [
                {"relative_path": item.relative_path, "sha256": item.sha256, "size": item.size}
                for item in plan.inventory
            ],
            "format_profile": plan.format_profile.value,
            "renderer_version": plan.renderer_version,
        }
    )
    marketplace_manifest = canonical_encode(
        cast(
            JsonValue,
            {
                "description": "Private local Yoetz marketplace",
                "name": _MARKETPLACE_NAME,
                "owner": {"name": "Yoetz contributors"},
                "plugins": [{"name": "yoetz", "source": "./plugins/yoetz", "strict": True}],
            },
        )
    )
    return ClaudeCodePluginArtifact(
        plan,
        members,
        marketplace_manifest,
        artifact_digest,
        _sha(marketplace_manifest),
    )


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
    facts = current.stat()
    if (hasattr(os, "geteuid") and facts.st_uid != os.geteuid()) or facts.st_mode & 0o022:
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    if any(item.name in {"", ".", ".."} for item in missing):
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    return current


def _validate_target(target: ClaudeCodePluginTarget) -> str:
    if type(target) is not ClaudeCodePluginTarget:
        raise _error(PluginArtifactReason.TARGET_UNTRUSTED)
    for raw in (
        target.project_root,
        target.claude_config_root,
        target.cache_root,
        target.marketplace_root,
    ):
        path = Path(raw)
        _safe_existing_ancestor(path)
        for candidate in (path, *path.parents):
            if candidate.exists() and candidate.is_symlink():
                raise _error(PluginArtifactReason.TARGET_UNSAFE)
            if candidate.parent == candidate:
                break
    executable = Path(target.executable)
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    facts = executable.stat()
    if (hasattr(os, "geteuid") and facts.st_uid != os.geteuid()) or facts.st_mode & 0o022:
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    if _digest_file(executable) != target.identity.executable_digest:
        raise _error(PluginArtifactReason.PREVIEW_STALE)
    return canonical_digest(
        {
            "architecture": target.identity.architecture,
            "cache_root": target.cache_root,
            "claude_config_root": target.claude_config_root,
            "executable_digest": target.identity.executable_digest,
            "marketplace_name": target.marketplace_name,
            "marketplace_root": target.marketplace_root,
            "os_name": target.identity.os_name,
            "project_root": target.project_root,
            "scope": target.scope,
            "version": target.identity.version,
        }
    )


def _safe_tree(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")):
        if path.is_symlink():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        if path.is_dir():
            continue
        facts = path.stat()
        if (
            not path.is_file()
            or facts.st_nlink != 1
            or facts.st_size > _MAX_FILE_BYTES
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


def _source_members(artifact: ClaudeCodePluginArtifact) -> dict[str, bytes]:
    members = {".claude-plugin/marketplace.json": artifact.marketplace_manifest}
    members.update({f"plugins/yoetz/{path}": data for path, data in artifact.members.items()})
    return members


def _marker(artifact: ClaudeCodePluginArtifact) -> bytes:
    source = _source_members(artifact)
    body: dict[str, JsonValue] = {
        "artifact_digest": artifact.artifact_digest,
        "format_profile": artifact.plan.format_profile.value,
        "hook_mapping_version": CLAUDE_CODE_HOOK_MAPPING_VERSION,
        "managed_files": [
            {"relative_path": path, "sha256": _sha(data), "size": len(data)}
            for path, data in sorted(source.items(), key=lambda item: item[0].encode("ascii"))
        ],
        "marketplace_digest": artifact.marketplace_digest,
        "marketplace_name": _MARKETPLACE_NAME,
        "mcp_ownership": artifact.plan.mcp_ownership.value,
        "mcp_route_profile": artifact.plan.mcp_route_profile,
        "renderer_version": artifact.plan.renderer_version,
        "schema": _MARKER_SCHEMA,
        "yoetz_version": artifact.plan.version,
    }
    return canonical_encode({**body, "marker_digest": canonical_digest(body)})


def _load_object(data: bytes) -> Mapping[str, JsonValue] | None:
    try:
        parsed = strict_json_parse(data)
    except ProtocolValueError, UnicodeError:
        return None
    return cast(Mapping[str, JsonValue], parsed) if isinstance(parsed, Mapping) else None


def _valid_source_marker(files: Mapping[str, bytes]) -> tuple[bool, str | None]:
    raw = files.get(_MARKER_NAME)
    marker = None if raw is None else _load_object(raw)
    if marker is None or marker.get("schema") != _MARKER_SCHEMA:
        return False, None
    digest = marker.get("marker_digest")
    body = {key: value for key, value in marker.items() if key != "marker_digest"}
    if type(digest) is not str or digest != canonical_digest(body):
        return False, None
    rows = marker.get("managed_files")
    if type(rows) is not list:
        return False, None
    expected: dict[str, tuple[int, str]] = {}
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            return False, None
        path = raw_row.get("relative_path")
        size = raw_row.get("size")
        sha = raw_row.get("sha256")
        if (
            type(path) is not str
            or type(size) is not int
            or type(sha) is not str
            or path in expected
        ):
            return False, None
        expected[path] = (size, sha)
    content = {path: data for path, data in files.items() if path != _MARKER_NAME}
    if set(content) != set(expected) or any(
        expected[path] != (len(data), _sha(data)) for path, data in content.items()
    ):
        value = marker.get("artifact_digest")
        return False, value if type(value) is str else None
    value = marker.get("artifact_digest")
    return True, value if type(value) is str else None


def _inspect_source(
    target: ClaudeCodePluginTarget, artifact: ClaudeCodePluginArtifact
) -> _SourceInspection:
    root = Path(target.marketplace_root)
    if not root.exists():
        return _SourceInspection(
            PluginArtifactState.ABSENT,
            None,
            False,
            canonical_digest({"state": "absent"}),
        )
    if root.is_symlink() or not root.is_dir():
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    files = _safe_tree(root)
    digest = _tree_digest(files)
    valid, installed_digest = _valid_source_marker(files)
    if not valid:
        return _SourceInspection(
            PluginArtifactState.MODIFIED
            if installed_digest is not None
            else PluginArtifactState.UNMANAGED,
            installed_digest,
            False,
            digest,
        )
    expected = {**_source_members(artifact), _MARKER_NAME: _marker(artifact)}
    return _SourceInspection(
        PluginArtifactState.NATIVE_MANAGED,
        installed_digest,
        True,
        digest if files != expected else _tree_digest(expected),
    )


def _json_file(path: Path) -> tuple[Mapping[str, JsonValue] | None, bool]:
    if not path.exists() and not path.is_symlink():
        return None, True
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return None, False
        parsed = strict_json_parse(path.read_bytes())
    except OSError, ProtocolValueError, UnicodeError:
        return None, False
    return (
        (cast(Mapping[str, JsonValue], parsed), True)
        if isinstance(parsed, Mapping)
        else (None, False)
    )


def _host_state_digest(target: ClaudeCodePluginTarget) -> str:
    rows: list[dict[str, JsonValue]] = []
    for name, path in (
        ("claude_json", Path(target.claude_config_root) / ".claude.json"),
        (
            "installed_plugins",
            Path(target.claude_config_root) / "plugins" / "installed_plugins.json",
        ),
        (
            "known_marketplaces",
            Path(target.claude_config_root) / "plugins" / "known_marketplaces.json",
        ),
        ("project_settings", Path(target.project_root) / ".claude" / "settings.json"),
    ):
        if not path.exists() and not path.is_symlink():
            rows.append({"name": name, "state": "absent"})
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            rows.append({"name": name, "state": "unsafe"})
            continue
        data = path.read_bytes()
        rows.append({"name": name, "sha256": _sha(data), "size": len(data), "state": "present"})
    return canonical_digest(cast(JsonValue, {"files": rows}))


def _config_entry(raw: object) -> tuple[Mapping[str, JsonValue] | None, bool]:
    if raw is None:
        return None, True
    if not isinstance(raw, Mapping):
        return None, False
    config = cast(Mapping[str, JsonValue], raw)
    servers = config.get("mcpServers")
    if servers is None:
        return None, True
    if not isinstance(servers, Mapping):
        return None, False
    typed_servers = cast(Mapping[str, JsonValue], servers)
    relevant: list[Mapping[str, JsonValue]] = []
    for name, raw_entry in typed_servers.items():
        if name == "yoetz":
            if not isinstance(raw_entry, Mapping):
                return None, False
            relevant.append(cast(Mapping[str, JsonValue], raw_entry))
        elif (
            isinstance(raw_entry, Mapping)
            and _route_profile(cast(Mapping[str, JsonValue], raw_entry)) is not None
        ):
            relevant.append(cast(Mapping[str, JsonValue], raw_entry))
    if not relevant:
        return None, True
    if len(relevant) != 1:
        return None, False
    return relevant[0], True


def _route_profile(entry: Mapping[str, JsonValue] | None) -> Literal["strict", "policy"] | None:
    if entry is None or set(entry) != {"args", "command", "type"}:
        return None
    if entry.get("command") != "yoetz" or entry.get("type") != "stdio":
        return None
    args = entry.get("args")
    if args in (["mcp", "serve"], ("mcp", "serve")):
        return "policy"
    if args in (
        ["mcp", "serve", "--semantic", "off"],
        ("mcp", "serve", "--semantic", "off"),
    ):
        return "strict"
    return None


def observe_claude_code_mcp(
    *,
    plugin_root: Path,
    project_root: Path,
    claude_config_root: Path,
    connector_entry: Mapping[str, JsonValue] | None = None,
) -> ClaudeCodeMcpObservation:
    """Observe all reachable sources without treating Claude precedence as ownership."""

    candidates: list[tuple[ClaudeCodeMcpSource, Mapping[str, JsonValue]]] = []
    uncertain: list[ClaudeCodeMcpSource] = []
    config, config_observed = _json_file(claude_config_root / ".claude.json")
    if not config_observed:
        uncertain.extend((ClaudeCodeMcpSource.LOCAL, ClaudeCodeMcpSource.USER))
    elif config is not None:
        projects = config.get("projects")
        local_raw = projects.get(str(project_root)) if isinstance(projects, Mapping) else None
        local_entry, local_observed = _config_entry(local_raw)
        if not local_observed:
            uncertain.append(ClaudeCodeMcpSource.LOCAL)
        elif local_entry is not None:
            candidates.append((ClaudeCodeMcpSource.LOCAL, local_entry))
        user_entry, user_observed = _config_entry(config)
        if not user_observed:
            uncertain.append(ClaudeCodeMcpSource.USER)
        elif user_entry is not None:
            candidates.append((ClaudeCodeMcpSource.USER, user_entry))
    project, project_observed = _json_file(project_root / ".mcp.json")
    project_entry, project_shape_observed = _config_entry(project)
    if not project_observed or not project_shape_observed:
        uncertain.append(ClaudeCodeMcpSource.PROJECT)
    elif project_entry is not None:
        candidates.append((ClaudeCodeMcpSource.PROJECT, project_entry))
    plugin, plugin_observed = _json_file(plugin_root / ".mcp.json")
    plugin_entry, plugin_shape_observed = _config_entry(plugin)
    if not plugin_observed or not plugin_shape_observed:
        uncertain.append(ClaudeCodeMcpSource.PLUGIN)
    elif plugin_entry is not None:
        candidates.append((ClaudeCodeMcpSource.PLUGIN, plugin_entry))
    if connector_entry is not None:
        candidates.append((ClaudeCodeMcpSource.CLAUDE_AI_CONNECTOR, connector_entry))
    order = {source: index for index, source in enumerate(ClaudeCodeMcpSource)}
    candidates.sort(key=lambda item: order[item[0]])
    if uncertain:
        present = tuple(source for source, _entry in candidates)
        combined = tuple(
            sorted(
                set((*present, *uncertain)),
                key=lambda source: order[source],
            )
        )
        return ClaudeCodeMcpObservation(
            McpOwnershipState.AMBIGUOUS,
            None,
            None,
            combined,
            False,
        )
    if not candidates:
        return ClaudeCodeMcpObservation(McpOwnershipState.ABSENT, None, None, (), True)
    profiles = [(source, _route_profile(entry)) for source, entry in candidates]
    present = tuple(source for source, _profile in profiles)
    if any(profile is None for _source, profile in profiles):
        source = next(source for source, profile in profiles if profile is None)
        return ClaudeCodeMcpObservation(McpOwnershipState.FOREIGN, source, None, present, True)
    plugin_profiles = [
        profile for source, profile in profiles if source is ClaudeCodeMcpSource.PLUGIN
    ]
    external_profiles = [
        profile for source, profile in profiles if source is not ClaudeCodeMcpSource.PLUGIN
    ]
    if plugin_profiles and external_profiles:
        return ClaudeCodeMcpObservation(McpOwnershipState.DUAL, present[0], None, present, True)
    if len(profiles) > 1:
        return ClaudeCodeMcpObservation(
            McpOwnershipState.AMBIGUOUS, present[0], None, present, True
        )
    source, profile = profiles[0]
    assert profile is not None
    return ClaudeCodeMcpObservation(
        McpOwnershipState.PLUGIN
        if source is ClaudeCodeMcpSource.PLUGIN
        else McpOwnershipState.EXTERNAL,
        source,
        cast(Literal["strict", "policy"], profile),
        present,
        True,
    )


def _list_plugins(
    target: ClaudeCodePluginTarget, commands: ClaudeCodeCommandPort
) -> tuple[list[Mapping[str, JsonValue]], bool]:
    result = commands.run(target, ("plugin", "list", "--json"))
    if result.returncode != 0:
        return [], False
    try:
        parsed = strict_json_parse(result.stdout)
    except ProtocolValueError, UnicodeError:
        return [], False
    if type(parsed) is not list or any(not isinstance(item, Mapping) for item in parsed):
        return [], False
    return [cast(Mapping[str, JsonValue], item) for item in parsed], True


def _exact_plugin_entry(
    target: ClaudeCodePluginTarget, rows: Sequence[Mapping[str, JsonValue]]
) -> Mapping[str, JsonValue] | None:
    matches = [row for row in rows if row.get("id") == _PLUGIN_ID]
    if len(matches) != 1:
        return None
    row = matches[0]
    if row.get("scope") != "project" or row.get("projectPath") != target.project_root:
        return None
    return row


def _marketplace_registered(target: ClaudeCodePluginTarget) -> bool | None:
    settings, observed = _json_file(Path(target.project_root) / ".claude" / "settings.json")
    if not observed:
        return None
    if settings is None:
        return False
    marketplaces = settings.get("extraKnownMarketplaces")
    if marketplaces is None:
        return False
    if not isinstance(marketplaces, Mapping):
        return None
    entry = marketplaces.get(_MARKETPLACE_NAME)
    if entry is None:
        return False
    if not isinstance(entry, Mapping):
        return None
    source = entry.get("source")
    if not isinstance(source, Mapping):
        return None
    if source != {"source": "directory", "path": target.marketplace_root}:
        return None
    known, known_observed = _json_file(
        Path(target.claude_config_root) / "plugins" / "known_marketplaces.json"
    )
    if not known_observed or known is None:
        return None
    known_entry = known.get(_MARKETPLACE_NAME)
    if not isinstance(known_entry, Mapping):
        return None
    known_source = known_entry.get("source")
    if not isinstance(known_source, Mapping):
        return None
    return (
        True
        if known_source == {"source": "directory", "path": target.marketplace_root}
        and known_entry.get("installLocation") == target.marketplace_root
        else None
    )


def _cache_digest(
    target: ClaudeCodePluginTarget,
    artifact: ClaudeCodePluginArtifact,
    row: Mapping[str, JsonValue] | None,
) -> tuple[str | None, str | None, bool]:
    if row is None:
        return None, None, True
    version = row.get("version")
    install_path = row.get("installPath")
    if type(version) is not str or type(install_path) is not str:
        return None, None, False
    path = Path(install_path)
    expected_parent = Path(target.cache_root) / _MARKETPLACE_NAME / "yoetz"
    try:
        path.relative_to(expected_parent)
    except ValueError:
        return None, version, False
    if path != expected_parent / version or path.is_symlink() or not path.is_dir():
        return None, version, False
    files = _safe_tree(path)
    exact = files == dict(artifact.members)
    return artifact.artifact_digest if exact else _tree_digest(files), version, exact


def _proof(
    *,
    installed_exact: bool,
    discovered: bool,
    mcp: ClaudeCodeMcpObservation,
    session: ClaudeCodeSessionObservation | None,
) -> tuple[PluginProofStatus, ...]:
    return tuple(
        PluginProofStatus(
            facet,
            (
                "proven"
                if facet in {PluginProofFacet.SOURCE, PluginProofFacet.RENDERED_ARTIFACT}
                or (installed_exact and facet is PluginProofFacet.INSTALLED_BYTES)
                or (discovered and facet is PluginProofFacet.HOST_DISCOVERY)
                or (session is not None and facet is PluginProofFacet.HOST_ACTIVATION)
                or (
                    session is not None
                    and session.skill_registered
                    and facet is PluginProofFacet.SKILL_DELIVERY
                )
                or (
                    session is not None
                    and session.scoped_workflow_tools_visible
                    and facet is PluginProofFacet.MCP_BINDING
                )
                or (
                    session is not None
                    and session.mcp_connected
                    and facet is PluginProofFacet.MCP_RUNTIME
                )
                or (
                    mcp.observed
                    and mcp.ownership_state
                    in {McpOwnershipState.EXTERNAL, McpOwnershipState.PLUGIN}
                    and facet is PluginProofFacet.MCP_OWNER
                )
                else "not_observed"
            ),
        )
        for facet in PluginProofFacet
    )


def status_claude_code_plugin(
    target: ClaudeCodePluginTarget,
    artifact: ClaudeCodePluginArtifact,
    *,
    commands: ClaudeCodeCommandPort | None = None,
    connector_entry: Mapping[str, JsonValue] | None = None,
    session_observation: ClaudeCodeSessionObservation | None = None,
) -> ClaudeCodePluginStatus:
    _validate_target(target)
    source = _inspect_source(target, artifact)
    command_port = SubprocessClaudeCodeCommands() if commands is None else commands
    rows, list_observed = _list_plugins(target, command_port)
    row = _exact_plugin_entry(target, rows) if list_observed else None
    installed_digest, installed_version, cache_observed = _cache_digest(target, artifact, row)
    installed_exact = (
        cache_observed
        and installed_digest == artifact.artifact_digest
        and installed_version == artifact.plan.version
    )
    registered = _marketplace_registered(target)
    enabled_raw = None if row is None else row.get("enabled")
    enabled = enabled_raw if type(enabled_raw) is bool else None
    discovered = row is not None
    plugin_root = (
        Path(target.marketplace_root) / "plugins" / "yoetz"
        if source.state is not PluginArtifactState.ABSENT
        else Path(target.marketplace_root) / "plugins" / "yoetz"
    )
    mcp = observe_claude_code_mcp(
        plugin_root=plugin_root,
        project_root=Path(target.project_root),
        claude_config_root=Path(target.claude_config_root),
        connector_entry=connector_entry,
    )
    if (
        source.state in {PluginArtifactState.MODIFIED, PluginArtifactState.UNMANAGED}
        or not cache_observed
    ):
        state = PluginArtifactState.MODIFIED
    elif source.state is PluginArtifactState.ABSENT and row is None and registered is False:
        state = PluginArtifactState.ABSENT
    elif source.marker_valid and installed_exact and registered is True:
        state = PluginArtifactState.NATIVE_MANAGED
    else:
        state = PluginArtifactState.PARTIAL
    operation = (
        PluginOperationState.NOT_STARTED
        if state is PluginArtifactState.ABSENT
        else PluginOperationState.COMPLETED
        if state is PluginArtifactState.NATIVE_MANAGED
        else PluginOperationState.OUTCOME_UNKNOWN
    )
    notes = tuple(
        sorted(
            {
                "agent_plugins_not_claimed",
                "cache_install_does_not_prove_loaded_session",
                "enabled_state_does_not_prove_loaded_session",
                "marketplace_project_scope_only",
                "model_use_not_observed",
            },
            key=str.encode,
        )
    )
    return ClaudeCodePluginStatus(
        state,
        operation,
        artifact.artifact_digest,
        artifact.marketplace_digest,
        _host_state_digest(target),
        source.digest,
        installed_digest,
        installed_version,
        registered,
        discovered,
        enabled,
        None if session_observation is None else session_observation.loaded_root_digest,
        source.marker_valid,
        mcp,
        _proof(
            installed_exact=installed_exact,
            discovered=discovered,
            mcp=mcp,
            session=session_observation,
        ),
        notes,
    )


def observe_claude_code_session_init(
    payload: Mapping[str, JsonValue],
    *,
    target: ClaudeCodePluginTarget,
    artifact: ClaudeCodePluginArtifact,
) -> ClaudeCodeSessionObservation:
    """Reduce one Claude stream-json init event to bounded activation evidence.

    Raw cwd, plugin paths, session ID, memory paths, tool payloads, and model output
    are used only for exact local validation and never returned.
    """

    if payload.get("type") != "system" or payload.get("subtype") != "init":
        raise ValueError("claude_code_session_init_invalid")
    if payload.get("claude_code_version") != target.identity.version:
        raise ValueError("claude_code_session_init_invalid")
    if payload.get("cwd") != target.project_root:
        raise ValueError("claude_code_session_init_invalid")
    session_id = payload.get("session_id")
    if type(session_id) is not str or not session_id or len(session_id) > 128:
        raise ValueError("claude_code_session_init_invalid")
    plugins: object = payload.get("plugins")
    if not isinstance(plugins, (list, tuple)):
        raise ValueError("claude_code_session_init_invalid")
    matches: list[Mapping[str, JsonValue]] = []
    for raw in cast(Sequence[object], plugins):
        if isinstance(raw, Mapping):
            row = cast(Mapping[str, JsonValue], raw)
            if row.get("name") == "yoetz" and row.get("source") == _PLUGIN_ID:
                matches.append(row)
    if len(matches) != 1 or matches[0].get("version") != artifact.plan.version:
        raise ValueError("claude_code_session_init_invalid")
    loaded_path = matches[0].get("path")
    if type(loaded_path) is not str or not Path(loaded_path).is_absolute():
        raise ValueError("claude_code_session_init_invalid")
    root = Path(loaded_path)
    allowed_roots = {
        Path(target.marketplace_root) / "plugins" / "yoetz",
        Path(target.cache_root) / _MARKETPLACE_NAME / "yoetz" / artifact.plan.version,
    }
    if root not in allowed_roots or root.is_symlink() or not root.is_dir():
        raise ValueError("claude_code_session_init_invalid")
    if _safe_tree(root) != dict(artifact.members):
        raise ValueError("claude_code_session_init_invalid")
    skills: object = payload.get("skills")
    tools: object = payload.get("tools")
    servers: object = payload.get("mcp_servers")
    skill_registered = isinstance(skills, (list, tuple)) and "yoetz:yoetz" in skills
    required_tools = {
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
    }
    visible_tools: set[str] = set()
    if isinstance(tools, (list, tuple)):
        visible_tools = {item for item in cast(Sequence[object], tools) if type(item) is str}
    mcp_connected = False
    if isinstance(servers, (list, tuple)):
        for raw in cast(Sequence[object], servers):
            if isinstance(raw, Mapping):
                server = cast(Mapping[str, JsonValue], raw)
                if (
                    server.get("name") == "plugin:yoetz:yoetz"
                    and server.get("status") == "connected"
                ):
                    mcp_connected = True
                    break
    return ClaudeCodeSessionObservation(
        canonical_digest(
            {
                "artifact_digest": artifact.artifact_digest,
                "session_id": session_id,
                "version": target.identity.version,
            }
        ),
        artifact.artifact_digest,
        skill_registered,
        mcp_connected,
        required_tools.issubset(visible_tools),
    )


def _admissible_owner_states(artifact: ClaudeCodePluginArtifact) -> set[McpOwnershipState]:
    if artifact.plan.mcp_ownership is McpOwnership.PLUGIN_MANAGED:
        return {McpOwnershipState.ABSENT, McpOwnershipState.PLUGIN}
    return {McpOwnershipState.ABSENT, McpOwnershipState.EXTERNAL}


def preview_claude_code_plugin(
    request: RequestId,
    target: ClaudeCodePluginTarget,
    action: ClaudeCodePluginAction,
    artifact: ClaudeCodePluginArtifact,
    *,
    commands: ClaudeCodeCommandPort | None = None,
    connector_entry: Mapping[str, JsonValue] | None = None,
) -> ClaudeCodePluginPreview:
    request = validate_request_id(request)
    if type(action) is not ClaudeCodePluginAction or action is ClaudeCodePluginAction.NOOP:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    if _version_tuple(target.identity.version) < _version_tuple(CLAUDE_CODE_MINIMUM_VERSION):
        raise _error(PluginArtifactReason.FORMAT_UNSUPPORTED)
    target_identity = _validate_target(target)
    status = status_claude_code_plugin(
        target, artifact, commands=commands, connector_entry=connector_entry
    )
    if action is ClaudeCodePluginAction.INSTALL and status.state is not PluginArtifactState.ABSENT:
        if (
            status.state is PluginArtifactState.NATIVE_MANAGED
            and status.installed_digest == artifact.artifact_digest
        ):
            action = ClaudeCodePluginAction.NOOP
        else:
            raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    elif action is ClaudeCodePluginAction.UPDATE and (
        not status.marker_valid or not status.discovered
    ):
        raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    elif action is ClaudeCodePluginAction.ENABLE and status.enabled is not False:
        if status.enabled is True:
            action = ClaudeCodePluginAction.NOOP
        else:
            raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    elif action is ClaudeCodePluginAction.DISABLE and status.enabled is not True:
        if status.enabled is False:
            action = ClaudeCodePluginAction.NOOP
        else:
            raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    elif action is ClaudeCodePluginAction.REMOVE and (
        not status.marker_valid or status.state is PluginArtifactState.ABSENT
    ):
        raise _error(PluginArtifactReason.REMOVE_REFUSED)
    if action not in {
        ClaudeCodePluginAction.REMOVE,
        ClaudeCodePluginAction.DISABLE,
        ClaudeCodePluginAction.NOOP,
    } and status.mcp_observation.ownership_state not in _admissible_owner_states(artifact):
        raise _error(
            PluginArtifactReason.MCP_OWNERSHIP_CONFLICT,
            {"mcp_ownership_state": status.mcp_observation.ownership_state.value},
        )
    current_state_digest = canonical_digest(
        {
            "discovered": status.discovered,
            "enabled": status.enabled,
            "installed_digest": status.installed_digest,
            "installed_version": status.installed_version,
            "marketplace_registered": status.marketplace_registered,
            "mcp_ownership_state": status.mcp_observation.ownership_state.value,
            "observed_host_state_digest": status.host_state_digest,
            "source_digest": status.source_digest,
            "state": status.state.value,
        }
    )
    preview_digest = canonical_digest(
        {
            "action": action.value,
            "artifact_digest": artifact.artifact_digest,
            "current_state_digest": current_state_digest,
            "host_identity": {
                "architecture": target.identity.architecture,
                "executable_digest": target.identity.executable_digest,
                "os_name": target.identity.os_name,
                "version": target.identity.version,
            },
            "marketplace_digest": artifact.marketplace_digest,
            "marketplace_name": target.marketplace_name,
            "mcp_ownership": artifact.plan.mcp_ownership.value,
            "mcp_ownership_state": status.mcp_observation.ownership_state.value,
            "mcp_route_profile": artifact.plan.mcp_route_profile,
            "request_id": request,
            "scope": target.scope,
            "target_identity": target_identity,
        }
    )
    warnings = tuple(
        sorted(
            {
                "activation_requires_new_session_or_reload",
                "agent_plugins_compatibility_not_claimed",
                "claude_desktop_cloud_sdk_not_covered",
                "observation_requires_separate_consent",
                "plugin_data_does_not_hold_yoetz_state",
            },
            key=str.encode,
        )
    )
    return ClaudeCodePluginPreview(
        request,
        action,
        status.state,
        target_identity,
        current_state_digest,
        artifact.artifact_digest,
        artifact.marketplace_digest,
        preview_digest,
        artifact.plan.mcp_ownership,
        status.mcp_observation.ownership_state,
        artifact.plan.mcp_route_profile,
        warnings,
    )


def _ensure_private_parent(path: Path) -> None:
    existing = _safe_existing_ancestor(path)
    current = existing
    for part in path.relative_to(existing).parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        facts = current.stat()
        if current.is_symlink() or not current.is_dir() or facts.st_mode & 0o022:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)


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


def _write_source(
    target: ClaudeCodePluginTarget,
    artifact: ClaudeCodePluginArtifact,
    request: RequestId,
) -> tuple[str, ...]:
    destination = Path(target.marketplace_root)
    parent = destination.parent
    _ensure_private_parent(parent)
    stage = parent / f"{_STAGE_PREFIX}{request.removeprefix('req_')}"
    rollback = parent / _ROLLBACK_NAME
    if stage.exists() or stage.is_symlink() or rollback.exists() or rollback.is_symlink():
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    stage.mkdir(mode=0o700)
    members = {**_source_members(artifact), _MARKER_NAME: _marker(artifact)}
    try:
        for relative, data in sorted(members.items(), key=lambda item: item[0].encode("ascii")):
            path = stage / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_file(path, data)
        _fsync_dir(stage)
        moved = destination.exists()
        if moved:
            os.replace(destination, rollback)
            _fsync_dir(parent)
        os.replace(stage, destination)
        _fsync_dir(parent)
        if _safe_tree(destination) != members:
            raise OSError("claude_source_bytes_mismatch")
        if moved:
            shutil.rmtree(rollback)
            _fsync_dir(parent)
    except BaseException as exc:
        with contextlib.suppress(OSError):
            if stage.exists():
                shutil.rmtree(stage)
            if rollback.exists() and not destination.exists():
                os.replace(rollback, destination)
                _fsync_dir(parent)
        if isinstance(exc, ClaudeCodeIntegrationError):
            raise
        raise _error(PluginArtifactReason.WRITE_FAILED) from exc
    return tuple(sorted(members, key=str.encode))


class _DenyStandaloneClaudeReview:
    def consume_setup_authority(self, _authority: ArtifactAuthority, _preview_digest: str) -> None:
        raise _error(PluginArtifactReason.HUMAN_AUTHORITY_UNAVAILABLE)

    def consume_artifact_review(self, _authority: ArtifactAuthority, _preview_digest: str) -> None:
        raise _error(PluginArtifactReason.HUMAN_AUTHORITY_UNAVAILABLE)


def _consume_authority(
    review: PluginMutationReviewPort | None,
    authority: ArtifactAuthority | None,
    preview_digest: str,
) -> None:
    if authority is None or authority.target_digest != preview_digest:
        raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
    port = _DenyStandaloneClaudeReview() if review is None else review
    if authority.channel == "setup_composition":
        port.consume_setup_authority(authority, preview_digest)
    else:
        port.consume_artifact_review(authority, preview_digest)


def _run_mutation(
    target: ClaudeCodePluginTarget,
    commands: ClaudeCodeCommandPort,
    arguments: Sequence[str],
) -> bool:
    return commands.run(target, arguments).returncode == 0


def _remove_source(target: ClaudeCodePluginTarget) -> tuple[str, ...]:
    root = Path(target.marketplace_root)
    files = tuple(sorted(_safe_tree(root), key=str.encode))
    rollback = root.parent / _ROLLBACK_NAME
    if rollback.exists() or rollback.is_symlink():
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    try:
        os.replace(root, rollback)
        _fsync_dir(root.parent)
        shutil.rmtree(rollback)
        _fsync_dir(root.parent)
    except OSError as exc:
        raise _error(PluginArtifactReason.WRITE_FAILED) from exc
    return files


def apply_claude_code_plugin(
    request: RequestId,
    target: ClaudeCodePluginTarget,
    action: ClaudeCodePluginAction,
    artifact: ClaudeCodePluginArtifact,
    *,
    accepted_preview_digest: str,
    authority: ArtifactAuthority | None,
    review: PluginMutationReviewPort | None = None,
    commands: ClaudeCodeCommandPort | None = None,
) -> ClaudeCodePluginResult:
    """Apply one exact Claude lifecycle mutation and reconcile by read-back."""

    _validate_digest(accepted_preview_digest)
    command_port = SubprocessClaudeCodeCommands() if commands is None else commands
    preview = preview_claude_code_plugin(request, target, action, artifact, commands=command_port)
    if preview.preview_digest != accepted_preview_digest:
        raise _error(PluginArtifactReason.PREVIEW_STALE)
    if preview.action is ClaudeCodePluginAction.NOOP:
        unchanged = status_claude_code_plugin(target, artifact, commands=command_port)
        return ClaudeCodePluginResult(
            preview.request_id,
            preview.action,
            PluginOperationState.COMPLETED,
            preview.state_before,
            preview.state_before,
            preview.preview_digest,
            preview.artifact_digest,
            unchanged.installed_digest,
            unchanged.enabled,
            (),
        )
    _consume_authority(review, authority, accepted_preview_digest)
    changed: tuple[str, ...] = ()
    command_ok = True
    if action is ClaudeCodePluginAction.INSTALL:
        changed = _write_source(target, artifact, preview.request_id)
        command_ok = _run_mutation(
            target,
            command_port,
            ("plugin", "marketplace", "add", "--scope", "project", target.marketplace_root),
        )
        if command_ok:
            command_ok = _run_mutation(
                target,
                command_port,
                ("plugin", "install", _PLUGIN_ID, "--scope", "project"),
            )
    elif action is ClaudeCodePluginAction.UPDATE:
        changed = _write_source(target, artifact, preview.request_id)
        command_ok = _run_mutation(
            target, command_port, ("plugin", "marketplace", "update", _MARKETPLACE_NAME)
        )
        if command_ok:
            command_ok = _run_mutation(
                target,
                command_port,
                ("plugin", "update", _PLUGIN_ID, "--scope", "project"),
            )
    elif action is ClaudeCodePluginAction.ENABLE:
        command_ok = _run_mutation(
            target, command_port, ("plugin", "enable", _PLUGIN_ID, "--scope", "project")
        )
    elif action is ClaudeCodePluginAction.DISABLE:
        command_ok = _run_mutation(
            target, command_port, ("plugin", "disable", _PLUGIN_ID, "--scope", "project")
        )
    elif action is ClaudeCodePluginAction.REMOVE:
        command_ok = _run_mutation(
            target,
            command_port,
            ("plugin", "uninstall", _PLUGIN_ID, "--scope", "project", "--keep-data"),
        )
        if command_ok:
            command_ok = _run_mutation(
                target,
                command_port,
                ("plugin", "marketplace", "remove", _MARKETPLACE_NAME),
            )
        if command_ok:
            changed = _remove_source(target)
    after = status_claude_code_plugin(target, artifact, commands=command_port)
    reached = (
        after.state is PluginArtifactState.NATIVE_MANAGED
        and after.installed_digest == artifact.artifact_digest
        and after.enabled is False
        if action is ClaudeCodePluginAction.INSTALL
        else after.state is PluginArtifactState.NATIVE_MANAGED
        and after.installed_digest == artifact.artifact_digest
        if action is ClaudeCodePluginAction.UPDATE
        else after.enabled is True
        if action is ClaudeCodePluginAction.ENABLE
        else after.enabled is False
        if action is ClaudeCodePluginAction.DISABLE
        else after.state is PluginArtifactState.ABSENT
    )
    operation = (
        PluginOperationState.COMPLETED
        if reached
        else PluginOperationState.OUTCOME_UNKNOWN
        if not command_ok
        else PluginOperationState.REFUSED
    )
    return ClaudeCodePluginResult(
        preview.request_id,
        action,
        operation,
        preview.state_before,
        after.state,
        preview.preview_digest,
        preview.artifact_digest,
        after.installed_digest,
        after.enabled,
        changed,
    )
