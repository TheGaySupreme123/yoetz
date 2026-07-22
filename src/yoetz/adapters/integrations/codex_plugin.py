"""Codex plugin bundle renderer, installer, and hook-presence inspection."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, cast

from yoetz import __version__
from yoetz.adapters.integrations.codex_skill import (
    SkillResourceSource,
    load_packaged_skill_members,
    load_packaged_skill_source,
)
from yoetz.ports.harness_mcp import MCP_SERVE_COMMAND, MCP_SERVER_NAME
from yoetz.ports.integrations import (
    HarnessId,
    IntegrationError,
    IntegrationReason,
    IntegrationScope,
    IntegrationTarget,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "PLUGIN_ROOT",
    "PluginHookPresence",
    "PluginInspection",
    "inspect_plugin",
    "install_plugin",
    "render_plugin_tree",
]

_ADAPTER_VERSION: Final = "codex-plugin/0.1.0"
_MARKER_NAME: Final = ".yoetz-plugin-install.json"
_MARKER_SCHEMA: Final = "yoetz.codex-plugin-install/1"
_PLUGIN_ROOT: Final = ".agents/plugins/yoetz"
PLUGIN_ROOT: Final = _PLUGIN_ROOT
_SOURCE_FILE_LIMIT: Final = 262_144


class PluginHookPresence(str, Enum):  # noqa: UP042 - exact structural enum
    ABSENT = "absent"
    INSTALLED_UNTRUSTED_UNKNOWN = "installed_untrusted_unknown"
    INSTALLED = "installed"


@dataclass(frozen=True, slots=True, repr=False)
class PluginInspection:
    """Read-only plugin/hook presence classification.

    Codex hook trust is never inferred from filesystem presence alone.
    """

    presence: PluginHookPresence
    trust_observable: bool
    installed_digest: str | None
    notes: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "PluginInspection("
            f"presence={self.presence.value!r}, trust_observable={self.trust_observable!r}, "
            f"installed_digest={self.installed_digest!r})"
        )


def _error(reason: IntegrationReason) -> IntegrationError:
    return IntegrationError(reason, {})


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _hooks_json() -> bytes:
    body: dict[str, JsonValue] = {
        "description": "Yoetz Codex lifecycle hooks (activation cue, start correlation, re-ground).",
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "yoetz hooks user-prompt-submit",
                            "timeout": 10,
                            "statusMessage": "Yoetz intake cue",
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "^mcp__yoetz__start$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "yoetz hooks post-tool-use",
                            "timeout": 10,
                            "statusMessage": "Yoetz start correlation",
                        }
                    ],
                }
            ],
            "SessionStart": [
                {
                    "matcher": "resume|compact",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "yoetz hooks session-start",
                            "timeout": 15,
                            "statusMessage": "Yoetz re-ground",
                        }
                    ],
                }
            ],
        },
    }
    return canonical_encode(body) + b"\n"


def _plugin_json() -> bytes:
    body: dict[str, JsonValue] = {
        "name": "yoetz",
        "version": __version__,
        "description": (
            "Yoetz local work ledger plugin: skill, MCP server, and lifecycle hooks. "
            "Automatic activation is not claimed without exact-host capability evidence."
        ),
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "hooks": "./hooks/hooks.json",
    }
    return canonical_encode(body) + b"\n"


def _mcp_json() -> bytes:
    body: dict[str, JsonValue] = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": MCP_SERVE_COMMAND[0],
                "args": list(MCP_SERVE_COMMAND[1:]),
            }
        }
    }
    return canonical_encode(body) + b"\n"


def render_plugin_tree(*, resource_source: SkillResourceSource | None = None) -> dict[str, bytes]:
    """Render the plugin file tree as an in-memory path → bytes mapping."""

    skill_members = load_packaged_skill_members(resource_source)
    members: dict[str, bytes] = {
        ".codex-plugin/plugin.json": _plugin_json(),
        "hooks/hooks.json": _hooks_json(),
        ".mcp.json": _mcp_json(),
    }
    for relative_path, data in skill_members.items():
        members[f"skills/yoetz/{relative_path}"] = data
    return members


def _validated_project(target: IntegrationTarget) -> Path:
    if (
        type(target) is not IntegrationTarget
        or target.scope is not IntegrationScope.TRUSTED_PROJECT
    ):
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    root = Path(target.project_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    if root == Path(root.anchor) or root == Path.home() or root.is_symlink() or not root.is_dir():
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    try:
        stat = root.stat()
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if hasattr(os, "geteuid") and stat.st_uid != os.geteuid():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if stat.st_mode & 0o022:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    return root


def _validated_plugin_parent(root: Path, *, create: bool) -> Path:
    """Resolve the managed parent without following project-local symlink ancestors."""

    current = root
    for component in (".agents", "plugins"):
        candidate = current / component
        if not candidate.exists() and not candidate.is_symlink():
            if not create:
                return root / ".agents" / "plugins"
            try:
                candidate.mkdir(mode=0o700)
            except OSError as exc:
                raise _error(IntegrationReason.WRITE_FAILED) from exc
        if candidate.is_symlink() or not candidate.is_dir():
            raise _error(IntegrationReason.TARGET_UNSAFE)
        try:
            stat = candidate.stat()
        except OSError as exc:
            raise _error(IntegrationReason.TARGET_UNSAFE) from exc
        if hasattr(os, "geteuid") and stat.st_uid != os.geteuid():
            raise _error(IntegrationReason.TARGET_UNSAFE)
        if stat.st_mode & 0o022:
            raise _error(IntegrationReason.TARGET_UNSAFE)
        current = candidate
    return current


def _build_marker(members: Mapping[str, bytes]) -> bytes:
    managed = [
        {
            "relative_path": path,
            "sha256": _sha(data),
            "size": len(data),
        }
        for path, data in sorted(members.items(), key=lambda item: item[0].encode("ascii"))
    ]
    body: dict[str, JsonValue] = {
        "adapter_version": _ADAPTER_VERSION,
        "harness_id": HarnessId.CODEX.value,
        "managed_files": cast(JsonValue, managed),
        "schema": _MARKER_SCHEMA,
        "scope": IntegrationScope.TRUSTED_PROJECT.value,
        "yoetz_version": __version__,
    }
    body["marker_digest"] = canonical_digest(body)
    return canonical_encode(body) + b"\n"


def _write_tree(stage: Path, members: Mapping[str, bytes], marker: bytes) -> None:
    stage.mkdir(mode=0o700)
    for relative_path, data in members.items():
        if len(data) > _SOURCE_FILE_LIMIT:
            raise _error(IntegrationReason.SOURCE_INVALID)
        destination = stage / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
    marker_path = stage / _MARKER_NAME
    marker_path.write_bytes(marker)
    with marker_path.open("rb") as handle:
        os.fsync(handle.fileno())


def install_plugin(
    target: IntegrationTarget,
    *,
    replace_modified: bool = False,
    resource_source: SkillResourceSource | None = None,
) -> PluginInspection:
    """Install the rendered plugin tree under the trusted-project plugin root.

    Fail-closed when the packaged Codex tested set is empty (no supported profile yet).
    Refuses to overwrite user-modified managed files unless ``replace_modified`` is true.
    """

    source = load_packaged_skill_source(resource_source)
    if not source.harness_tested_set:
        raise _error(IntegrationReason.VERSION_INCOMPATIBLE)
    root = _validated_project(target)
    parent = _validated_plugin_parent(root, create=True)
    destination = parent / "yoetz"
    members = render_plugin_tree(resource_source=resource_source)
    marker = _build_marker(members)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise _error(IntegrationReason.TARGET_UNSAFE)
        for relative_path, expected in members.items():
            path = destination / relative_path
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise _error(IntegrationReason.TARGET_UNSAFE)
            current = path.read_bytes()
            if current != expected and not replace_modified:
                raise _error(IntegrationReason.MODIFIED_COPY)
        existing_marker = destination / _MARKER_NAME
        if (
            existing_marker.is_file()
            and existing_marker.read_bytes() != marker
            and not replace_modified
        ):
            raise _error(IntegrationReason.MODIFIED_COPY)
    stage = parent / f".yoetz.plugin-stage-{os.urandom(6).hex()}"
    rollback = parent / f".yoetz.plugin-rollback-{os.urandom(6).hex()}"
    destination_moved = False
    try:
        _write_tree(stage, members, marker)
        if destination.exists():
            os.replace(destination, rollback)
            destination_moved = True
        os.replace(stage, destination)
        if rollback.exists():
            shutil.rmtree(rollback)
    except IntegrationError:
        raise
    except OSError as exc:
        if destination_moved and rollback.exists():
            try:
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(rollback, destination)
            except OSError:
                pass
        raise _error(IntegrationReason.WRITE_FAILED) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return inspect_plugin(target, resource_source=resource_source)


def inspect_plugin(
    target: IntegrationTarget,
    *,
    resource_source: SkillResourceSource | None = None,
) -> PluginInspection:
    """Classify plugin/hook presence without inferring Codex trust from files."""

    trust_note = "codex_hook_trust_not_observable_from_installation_state"
    try:
        root = _validated_project(target)
    except IntegrationError:
        return PluginInspection(
            PluginHookPresence.ABSENT,
            False,
            None,
            (trust_note,),
        )
    try:
        parent = _validated_plugin_parent(root, create=False)
    except IntegrationError:
        return PluginInspection(
            PluginHookPresence.INSTALLED_UNTRUSTED_UNKNOWN,
            False,
            None,
            (trust_note, "destination_unsafe"),
        )
    destination = parent / "yoetz"
    if not destination.exists():
        return PluginInspection(PluginHookPresence.ABSENT, False, None, (trust_note,))
    if destination.is_symlink() or not destination.is_dir():
        return PluginInspection(
            PluginHookPresence.INSTALLED_UNTRUSTED_UNKNOWN,
            False,
            None,
            (trust_note, "destination_unsafe"),
        )
    members = render_plugin_tree(resource_source=resource_source)
    expected_marker = _build_marker(members)
    marker_path = destination / _MARKER_NAME
    marker_valid = marker_path.is_file() and marker_path.read_bytes() == expected_marker
    exact = marker_valid
    for relative_path, expected in members.items():
        path = destination / relative_path
        if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
            exact = False
            break
    digest = canonical_digest(
        {
            "files": [
                {"path": path, "digest": _sha((destination / path).read_bytes())}
                for path in sorted(members)
                if (destination / path).is_file()
            ],
            "marker_valid": marker_valid,
        }
    )
    if exact:
        return PluginInspection(
            PluginHookPresence.INSTALLED,
            False,
            digest,
            (trust_note,),
        )
    return PluginInspection(
        PluginHookPresence.INSTALLED_UNTRUSTED_UNKNOWN,
        False,
        digest,
        (trust_note,),
    )


def parse_hooks_json(raw: bytes) -> Mapping[str, JsonValue]:
    """Strict-parse hooks.json for tests and status helpers."""

    if len(raw) > _SOURCE_FILE_LIMIT:
        raise ProtocolValueError("unsupported_json_type")
    parsed = strict_json_parse(raw)
    if not isinstance(parsed, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return cast(Mapping[str, JsonValue], parsed)
