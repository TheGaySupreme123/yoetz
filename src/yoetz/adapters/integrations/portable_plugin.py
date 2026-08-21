"""Skills-only Agent Plugins 1.0.0 renderer and safe artifact lifecycle."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Final, Protocol, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from yoetz import __version__
from yoetz.domain.values import RequestId
from yoetz.domain.values import request_id as validate_request_id
from yoetz.ports.plugin_artifacts import (
    ArtifactAuthority,
    ArtifactTarget,
    ManagedPluginFile,
    McpOwnership,
    McpOwnershipState,
    PluginArtifactAction,
    PluginArtifactApplyCommand,
    PluginArtifactError,
    PluginArtifactPort,
    PluginArtifactPreview,
    PluginArtifactReason,
    PluginArtifactResult,
    PluginArtifactState,
    PluginArtifactStatus,
    PluginArtifactStatusCommand,
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
    "AGENT_PLUGIN_ROOT",
    "ManifestValidation",
    "PackagedPortableResources",
    "ElevatedPortableArtifactReview",
    "PortablePluginArtifactAdapter",
    "PortableTreeValidation",
    "RenderedPortablePlugin",
    "build_portable_plugin_plan",
    "render_portable_plugin_tree",
    "prepare_portable_artifact_review",
    "validate_agent_plugin_manifest",
    "validate_portable_skill",
    "validate_portable_plugin_tree",
]

AGENT_PLUGIN_ROOT: Final = ".agents/plugins/yoetz"
_PLUGIN_SCHEMA_PATH: Final = "support/agent-plugins/1.0.0/plugin.schema.json"
_MCP_SCHEMA_PATH: Final = "support/agent-plugins/1.0.0/mcp.schema.json"
_SKILL_PATH: Final = "skills/portable/yoetz/SKILL.md"
_GUIDANCE_NAMES: Final = (
    "agent-instructions.md",
    "coverage-and-receipts.md",
    "publication-policy.md",
    "request-templates.md",
    "workflow.md",
)
_SCHEMA_ID: Final = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_PLUGIN_SCHEMA_SHA: Final = (
    "sha256:0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883"
)
_MCP_SCHEMA_SHA: Final = "sha256:6539175bfcdf43085855183e86da40ea94b166547a72b47ae9a0a390516d3acb"
_RENDERER_VERSION: Final = "portable-plugin/0.1.0"
_MARKER_NAME: Final = ".yoetz-plugin-install.json"
_MARKER_SCHEMA: Final = "yoetz.portable-plugin-install/1"
_NATIVE_MARKER_SCHEMA: Final = "yoetz.codex-plugin-install/1"
_STAGE_PREFIX: Final = ".yoetz.plugin-stage-"
_REMOVE_PREFIX: Final = ".yoetz.plugin-remove-"
_NATIVE_ROLLBACK_NAME: Final = ".yoetz.plugin-native-rollback"
_MAX_FILE_BYTES: Final = 262_144
_MAX_TREE_FILES: Final = 64
_PLUGIN_ALLOWED_FIELDS: Final = frozenset(
    {
        "$schema",
        "author",
        "description",
        "extensions",
        "homepage",
        "keywords",
        "license",
        "name",
        "repository",
        "version",
    }
)


class PortableResourceSource(Protocol):
    def read_bytes(self, package_path: str) -> bytes: ...


class _JsonSchemaValidator(Protocol):
    def iter_errors(self, instance: object) -> Iterable[ValidationError]: ...


class ArtifactUserPresencePort(Protocol):
    def verify_artifact_review(self, authority: ArtifactAuthority) -> None: ...


class PackagedPortableResources:
    """Read only the packaged resource tree; callers cannot select ambient files."""

    def read_bytes(self, package_path: str) -> bytes:
        node = resources.files("yoetz.resources")
        for part in package_path.split("/"):
            node = node.joinpath(part)
        return node.read_bytes()


@dataclass(frozen=True, slots=True)
class ManifestValidation:
    accepted: bool
    unknown_fields: tuple[str, ...]
    fatal_field: str | None


@dataclass(frozen=True, slots=True)
class RenderedPortablePlugin:
    plan: PortablePluginPlan
    members: Mapping[str, bytes]
    artifact_digest: str


@dataclass(frozen=True, slots=True)
class PortableTreeValidation:
    manifest: ManifestValidation
    loaded_components: tuple[str, ...]
    skipped_components: tuple[str, ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Inspection:
    root: Path
    parent: Path
    destination: Path
    state: PluginArtifactState
    target_identity: str
    current_state_digest: str
    installed_digest: str | None
    marker_valid: bool
    rollback_available: bool

    def __repr__(self) -> str:
        return (
            "_Inspection("
            f"state={self.state.value!r}, installed_digest={self.installed_digest!r}, "
            f"marker_valid={self.marker_valid!r}, rollback_available={self.rollback_available!r}, "
            "target=<redacted>)"
        )


def _error(
    reason: PluginArtifactReason,
    details: Mapping[str, JsonValue] | None = None,
) -> PluginArtifactError:
    return PluginArtifactError(reason, {} if details is None else details)


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _load_json(raw: bytes, reason: PluginArtifactReason) -> Mapping[str, JsonValue]:
    if len(raw) > _MAX_FILE_BYTES:
        raise _error(reason)
    try:
        parsed = strict_json_parse(raw)
    except (ProtocolValueError, UnicodeError) as exc:
        raise _error(reason) from exc
    if not isinstance(parsed, Mapping):
        raise _error(reason)
    return cast(Mapping[str, JsonValue], parsed)


def _field_from_jsonschema_path(path: Sequence[str | int]) -> str:
    parts = tuple(path)
    if not parts:
        return "manifest"
    first = parts[0]
    return first if type(first) is str and first in _PLUGIN_ALLOWED_FIELDS else "manifest"


def validate_agent_plugin_manifest(
    raw: bytes,
    *,
    schema_bytes: bytes,
) -> ManifestValidation:
    """Validate fatal known fields while reporting and ignoring unknown top-level fields."""

    try:
        manifest = _load_json(raw, PluginArtifactReason.MANIFEST_INVALID)
        schema = _load_json(schema_bytes, PluginArtifactReason.SOURCE_INVALID)
    except PluginArtifactError:
        return ManifestValidation(False, (), "manifest")
    unknown = tuple(sorted(set(manifest) - _PLUGIN_ALLOWED_FIELDS, key=str.encode))
    known = {key: value for key, value in manifest.items() if key in _PLUGIN_ALLOWED_FIELDS}
    validator = cast(_JsonSchemaValidator, Draft202012Validator(cast(Any, schema)))
    errors: list[ValidationError] = sorted(
        validator.iter_errors(known),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    if errors:
        return ManifestValidation(
            False,
            unknown,
            _field_from_jsonschema_path(errors[0].absolute_path),
        )
    return ManifestValidation(True, unknown, None)


def validate_portable_skill(raw: bytes) -> None:
    """Validate the portable Agent Skills subset used by the immediate-child skill."""

    if len(raw) > _MAX_FILE_BYTES or not raw.endswith(b"\n") or b"\r" in raw:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(PluginArtifactReason.SOURCE_INVALID) from exc
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    header = text[4 : text.index("\n---\n", 4)]
    lines = header.splitlines()
    fields = tuple(line.split(":", 1)[0] for line in lines if line and not line.startswith(" "))
    if set(fields) != {"name", "description"} or len(fields) != 2:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    if "name: yoetz" not in lines or not any(line.startswith("description: ") for line in lines):
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    forbidden = ("allowed-tools:", "compatibility:", "metadata:")
    if any(line.startswith(forbidden) for line in lines):
        raise _error(PluginArtifactReason.SOURCE_INVALID)


def validate_portable_plugin_tree(
    members: Mapping[str, bytes],
    *,
    schema_bytes: bytes,
) -> PortableTreeValidation:
    """Apply Agent Plugins component boundaries without upgrading them to activation proof."""

    manifest_raw = members.get("plugin.json")
    if manifest_raw is None:
        manifest = ManifestValidation(False, (), "manifest")
        return PortableTreeValidation(manifest, (), (), ("manifest_missing",))
    manifest = validate_agent_plugin_manifest(manifest_raw, schema_bytes=schema_bytes)
    if not manifest.accepted:
        return PortableTreeValidation(manifest, (), (), ("manifest_invalid",))
    skill = members.get("skills/yoetz/SKILL.md")
    if skill is None:
        return PortableTreeValidation(
            manifest,
            ("manifest",),
            ("skills/yoetz",),
            ("skill_missing",),
        )
    try:
        validate_portable_skill(skill)
    except PluginArtifactError:
        return PortableTreeValidation(
            manifest,
            ("manifest",),
            ("skills/yoetz",),
            ("skill_frontmatter_invalid",),
        )
    return PortableTreeValidation(manifest, ("manifest", "skills/yoetz"), (), ())


def _source(source: PortableResourceSource | None) -> PortableResourceSource:
    return PackagedPortableResources() if source is None else source


def _read_verified_source(source: PortableResourceSource, path: str) -> bytes:
    try:
        data = source.read_bytes(path)
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise _error(PluginArtifactReason.SOURCE_INVALID) from exc
    if len(data) > _MAX_FILE_BYTES:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    return data


def _plugin_json() -> bytes:
    body: dict[str, JsonValue] = {
        "$schema": _SCHEMA_ID,
        "author": {
            "name": "Yoetz contributors",
            "url": "https://github.com/TheGaySupreme123/yoetz",
        },
        "description": (
            "Yoetz cooperative work-ledger guidance. Artifact validation or installation does "
            "not establish host activation, observation, provider dispatch, or completion."
        ),
        "homepage": "https://github.com/TheGaySupreme123/yoetz",
        "license": "Apache-2.0",
        "name": "yoetz",
        "repository": "https://github.com/TheGaySupreme123/yoetz",
        "version": __version__,
    }
    return canonical_encode(body) + b"\n"


def _inventory(members: Mapping[str, bytes]) -> tuple[ManagedPluginFile, ...]:
    return tuple(
        ManagedPluginFile(path, len(data), _sha(data))
        for path, data in sorted(members.items(), key=lambda item: item[0].encode("ascii"))
    )


def _artifact_digest(inventory: tuple[ManagedPluginFile, ...]) -> str:
    return canonical_digest(
        {
            "format_profile": PluginFormatProfile.AGENT_PLUGINS_1.value,
            "inventory": [
                {
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "size": item.size,
                }
                for item in inventory
            ],
            "mcp_ownership": McpOwnership.EXTERNAL_REGISTRATION.value,
            "renderer_version": _RENDERER_VERSION,
            "schema_version": "1.0.0",
        }
    )


def build_portable_plugin_plan(
    *, resource_source: PortableResourceSource | None = None
) -> RenderedPortablePlugin:
    source = _source(resource_source)
    schema = _read_verified_source(source, _PLUGIN_SCHEMA_PATH)
    mcp_schema = _read_verified_source(source, _MCP_SCHEMA_PATH)
    if _sha(schema) != _PLUGIN_SCHEMA_SHA or _sha(mcp_schema) != _MCP_SCHEMA_SHA:
        raise _error(PluginArtifactReason.SOURCE_INVALID)
    skill = _read_verified_source(source, _SKILL_PATH)
    validate_portable_skill(skill)
    members: dict[str, bytes] = {
        "plugin.json": _plugin_json(),
        "skills/yoetz/SKILL.md": skill,
    }
    for name in _GUIDANCE_NAMES:
        data = _read_verified_source(source, f"guidance/{name}")
        if not data.endswith(b"\n") or b"\r" in data:
            raise _error(PluginArtifactReason.SOURCE_INVALID)
        members[f"skills/yoetz/references/{name}"] = data
    validation = validate_portable_plugin_tree(members, schema_bytes=schema)
    if (
        not validation.manifest.accepted
        or validation.manifest.unknown_fields
        or validation.skipped_components
    ):
        raise _error(
            PluginArtifactReason.MANIFEST_INVALID,
            {"fatal_field": validation.manifest.fatal_field or "manifest"},
        )
    inventory = _inventory(members)
    plan = PortablePluginPlan(
        name="yoetz",
        version=__version__,
        description=(
            "Yoetz cooperative work-ledger guidance carrier; no host activation or authority claim."
        ),
        format_profile=PluginFormatProfile.AGENT_PLUGINS_1,
        mcp_ownership=McpOwnership.EXTERNAL_REGISTRATION,
        mcp_route_profile=None,
        host_extension_profile=None,
        specification_version="1.0.0",
        renderer_version=_RENDERER_VERSION,
        source_refs=tuple(
            sorted(
                {
                    _MCP_SCHEMA_PATH,
                    _PLUGIN_SCHEMA_PATH,
                    _SKILL_PATH,
                    *(f"guidance/{name}" for name in _GUIDANCE_NAMES),
                },
                key=str.encode,
            )
        ),
        inventory=inventory,
    )
    return RenderedPortablePlugin(plan, members, _artifact_digest(inventory))


def render_portable_plugin_tree(
    *, resource_source: PortableResourceSource | None = None
) -> dict[str, bytes]:
    return dict(build_portable_plugin_plan(resource_source=resource_source).members)


def _marker(rendered: RenderedPortablePlugin) -> bytes:
    body: dict[str, JsonValue] = {
        "artifact_digest": rendered.artifact_digest,
        "format_profile": rendered.plan.format_profile.value,
        "managed_files": [
            {
                "relative_path": item.relative_path,
                "sha256": item.sha256,
                "size": item.size,
            }
            for item in rendered.plan.inventory
        ],
        "mcp_ownership": rendered.plan.mcp_ownership.value,
        "renderer_version": rendered.plan.renderer_version,
        "schema": _MARKER_SCHEMA,
        "schema_version": rendered.plan.specification_version,
        "yoetz_version": rendered.plan.version,
    }
    body["marker_digest"] = canonical_digest(body)
    return canonical_encode(body) + b"\n"


def _validate_project(target: ArtifactTarget) -> tuple[Path, str]:
    if type(target) is not ArtifactTarget:
        raise _error(PluginArtifactReason.TARGET_UNTRUSTED)
    root = Path(target.project_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    if root == Path(root.anchor) or root == Path.home() or root.is_symlink() or not root.is_dir():
        raise _error(PluginArtifactReason.TARGET_UNTRUSTED)
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current /= part
        if current.is_symlink():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
    try:
        facts = root.stat()
    except OSError as exc:
        raise _error(PluginArtifactReason.TARGET_UNSAFE) from exc
    if hasattr(os, "geteuid") and facts.st_uid != os.geteuid():
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    if facts.st_mode & 0o022:
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    return root, canonical_digest(
        {"device": facts.st_dev, "inode": facts.st_ino, "mode": facts.st_mode & 0o777}
    )


def _plugin_parent(root: Path, *, create: bool) -> Path:
    current = root
    for component in (".agents", "plugins"):
        candidate = current / component
        if not candidate.exists() and not candidate.is_symlink():
            if not create:
                return root / ".agents" / "plugins"
            try:
                candidate.mkdir(mode=0o700)
            except OSError as exc:
                raise _error(PluginArtifactReason.WRITE_FAILED) from exc
        if candidate.is_symlink() or not candidate.is_dir():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        facts = candidate.stat()
        if (hasattr(os, "geteuid") and facts.st_uid != os.geteuid()) or facts.st_mode & 0o022:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        current = candidate
    return current


def _safe_tree_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")):
        if path.is_symlink():
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > _MAX_FILE_BYTES:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        relative = path.relative_to(root).as_posix()
        if len(files) >= _MAX_TREE_FILES:
            raise _error(PluginArtifactReason.TARGET_UNSAFE)
        files[relative] = path.read_bytes()
    return files


def _tree_digest(files: Mapping[str, bytes]) -> str:
    return canonical_digest(
        {
            "files": [
                {"relative_path": path, "sha256": _sha(data), "size": len(data)}
                for path, data in sorted(files.items(), key=lambda item: item[0].encode("ascii"))
            ]
        }
    )


def _parsed_marker(files: Mapping[str, bytes]) -> Mapping[str, JsonValue] | None:
    raw = files.get(_MARKER_NAME)
    if raw is None:
        return None
    try:
        return _load_json(raw, PluginArtifactReason.SOURCE_INVALID)
    except PluginArtifactError:
        return None


def _marker_self_valid(marker: Mapping[str, JsonValue]) -> bool:
    digest = marker.get("marker_digest")
    if type(digest) is not str:
        return False
    body = {key: value for key, value in marker.items() if key != "marker_digest"}
    return digest == canonical_digest(body)


def _recorded_tree_valid(
    files: Mapping[str, bytes], marker: Mapping[str, JsonValue]
) -> tuple[bool, str | None]:
    rows = marker.get("managed_files")
    if type(rows) is not list:
        return False, None
    recorded: dict[str, tuple[int, str]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            return False, None
        path = raw.get("relative_path")
        size = raw.get("size")
        digest = raw.get("sha256")
        if (
            type(path) is not str
            or type(size) is not int
            or type(digest) is not str
            or path in recorded
        ):
            return False, None
        recorded[path] = (size, digest)
    content = {path: data for path, data in files.items() if path != _MARKER_NAME}
    if set(recorded) != set(content):
        return False, None
    for path, data in content.items():
        if recorded[path] != (len(data), _sha(data)):
            return False, None
    artifact = marker.get("artifact_digest")
    return True, artifact if type(artifact) is str else _tree_digest(content)


def _native_rollback_valid(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        files = _safe_tree_files(path)
    except PluginArtifactError:
        return False
    marker = _parsed_marker(files)
    if (
        marker is None
        or marker.get("schema") != _NATIVE_MARKER_SCHEMA
        or not _marker_self_valid(marker)
    ):
        return False
    valid, _ = _recorded_tree_valid(files, marker)
    return valid


def _inspect(
    target: ArtifactTarget,
    rendered: RenderedPortablePlugin,
) -> _Inspection:
    root, target_identity = _validate_project(target)
    parent = _plugin_parent(root, create=False)
    destination = parent / "yoetz"
    rollback = parent / _NATIVE_ROLLBACK_NAME
    rollback_present = rollback.exists() or rollback.is_symlink()
    rollback_valid = rollback_present and _native_rollback_valid(rollback)
    if rollback_present and not rollback_valid:
        return _Inspection(
            root,
            parent,
            destination,
            PluginArtifactState.RECOVERY_REQUIRED,
            target_identity,
            canonical_digest({"state": "rollback_ambiguous"}),
            None,
            False,
            False,
        )
    if parent.exists():
        interrupted = any(
            path.name.startswith((_STAGE_PREFIX, _REMOVE_PREFIX)) for path in parent.iterdir()
        )
        if interrupted:
            return _Inspection(
                root,
                parent,
                destination,
                PluginArtifactState.RECOVERY_REQUIRED,
                target_identity,
                canonical_digest({"state": "recovery_required"}),
                None,
                False,
                rollback_valid,
            )
    if not destination.exists():
        if rollback_valid:
            return _Inspection(
                root,
                parent,
                destination,
                PluginArtifactState.RECOVERY_REQUIRED,
                target_identity,
                canonical_digest({"state": "rollback_without_destination"}),
                None,
                False,
                True,
            )
        return _Inspection(
            root,
            parent,
            destination,
            PluginArtifactState.ABSENT,
            target_identity,
            canonical_digest({"state": "absent"}),
            None,
            False,
            False,
        )
    if destination.is_symlink() or not destination.is_dir():
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    files = _safe_tree_files(destination)
    current_digest = _tree_digest(files)
    marker = _parsed_marker(files)
    if marker is None or not _marker_self_valid(marker):
        return _Inspection(
            root,
            parent,
            destination,
            PluginArtifactState.UNMANAGED,
            target_identity,
            current_digest,
            None,
            False,
            rollback_valid,
        )
    recorded_valid, installed_digest = _recorded_tree_valid(files, marker)
    if not recorded_valid:
        return _Inspection(
            root,
            parent,
            destination,
            PluginArtifactState.MODIFIED,
            target_identity,
            current_digest,
            installed_digest,
            False,
            rollback_valid,
        )
    schema = marker.get("schema")
    if schema == _NATIVE_MARKER_SCHEMA:
        state = PluginArtifactState.NATIVE_MANAGED
    elif schema == _MARKER_SCHEMA:
        state = (
            PluginArtifactState.PORTABLE_EXACT
            if files == {**rendered.members, _MARKER_NAME: _marker(rendered)}
            else PluginArtifactState.PORTABLE_MANAGED
        )
    else:
        state = PluginArtifactState.UNMANAGED
    return _Inspection(
        root,
        parent,
        destination,
        state,
        target_identity,
        current_digest,
        installed_digest,
        state is not PluginArtifactState.UNMANAGED,
        rollback_valid,
    )


def _preview(
    request: RequestId,
    action: PluginArtifactAction,
    inspection: _Inspection,
    rendered: RenderedPortablePlugin,
) -> PluginArtifactPreview:
    if inspection.state is PluginArtifactState.RECOVERY_REQUIRED:
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    if inspection.state is PluginArtifactState.UNSAFE:
        raise _error(PluginArtifactReason.TARGET_UNSAFE)
    if (
        action is PluginArtifactAction.INSTALL
        and inspection.state is not PluginArtifactState.ABSENT
    ):
        raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    if action is PluginArtifactAction.REPLACE and inspection.state not in {
        PluginArtifactState.NATIVE_MANAGED,
        PluginArtifactState.PORTABLE_EXACT,
        PluginArtifactState.PORTABLE_MANAGED,
    }:
        raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
    if action is PluginArtifactAction.REMOVE and inspection.state not in {
        PluginArtifactState.PORTABLE_EXACT,
        PluginArtifactState.PORTABLE_MANAGED,
    }:
        raise _error(PluginArtifactReason.REMOVE_REFUSED)
    if inspection.rollback_available and inspection.state is PluginArtifactState.NATIVE_MANAGED:
        raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
    effective = (
        PluginArtifactAction.NOOP
        if inspection.state is PluginArtifactState.PORTABLE_EXACT
        and action in {PluginArtifactAction.INSTALL, PluginArtifactAction.REPLACE}
        else action
    )
    warnings = (
        "format_validation_does_not_prove_activation",
        "mcp_ownership_remains_external_registration",
    )
    preview_digest = canonical_digest(
        {
            "action": effective.value,
            "artifact_digest": rendered.artifact_digest,
            "current_state_digest": inspection.current_state_digest,
            "format_profile": rendered.plan.format_profile.value,
            "inventory": [
                {
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "size": item.size,
                }
                for item in rendered.plan.inventory
            ],
            "mcp_ownership": rendered.plan.mcp_ownership.value,
            "renderer_version": rendered.plan.renderer_version,
            "request_id": request,
            "schema_version": rendered.plan.specification_version,
            "state_before": inspection.state.value,
            "target_identity": inspection.target_identity,
        }
    )
    return PluginArtifactPreview(
        request,
        effective,
        inspection.state,
        inspection.target_identity,
        inspection.current_state_digest,
        rendered.artifact_digest,
        preview_digest,
        rendered.plan,
        warnings,
    )


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_tree(stage: Path, rendered: RenderedPortablePlugin) -> None:
    stage.mkdir(mode=0o700)
    for relative_path, data in rendered.members.items():
        if len(data) > _MAX_FILE_BYTES:
            raise _error(PluginArtifactReason.SOURCE_INVALID)
        destination = stage / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    marker_path = stage / _MARKER_NAME
    marker_path.write_bytes(_marker(rendered))
    with marker_path.open("rb") as handle:
        os.fsync(handle.fileno())
    _fsync_dir(stage)


class _DenyStandaloneReview:
    def consume_setup_authority(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        del authority, preview_digest
        raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)

    def consume_artifact_review(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        del authority, preview_digest
        raise _error(PluginArtifactReason.HUMAN_AUTHORITY_UNAVAILABLE)


def prepare_portable_artifact_review(
    preview_digest: str,
    *,
    _state: Path | None = None,
) -> ArtifactAuthority:
    """Prepare one review-only pending bound to the exact artifact preview digest."""

    from yoetz.service.elevated_bootstrap import prepare_pending

    pending = prepare_pending(
        "plugin_artifact_apply",
        target_digest=preview_digest,
        _state=_state,
    )
    return ArtifactAuthority("review_only", pending.target_digest, pending.pending_id)


class ElevatedPortableArtifactReview:
    """Consume one exact review-only pending after action-bound presence proof."""

    __slots__ = ("_presence", "_state")

    def __init__(
        self,
        presence: ArtifactUserPresencePort,
        *,
        _state: Path | None = None,
    ) -> None:
        self._presence = presence
        self._state = _state

    def consume_artifact_review(
        self,
        authority: ArtifactAuthority,
        preview_digest: str,
    ) -> None:
        from yoetz.service.elevated_bootstrap import (
            ElevatedBootstrapError,
            claim_pending_for_review,
            complete_review,
            load_pending,
        )

        if (
            type(authority) is not ArtifactAuthority
            or authority.channel != "review_only"
            or authority.target_digest != preview_digest
        ):
            raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
        pending = load_pending(_state=self._state)
        if (
            pending is None
            or pending.operation != "plugin_artifact_apply"
            or pending.pending_id != authority.review_id
            or pending.target_digest != preview_digest
        ):
            raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
        try:
            self._presence.verify_artifact_review(authority)
        except Exception as exc:
            raise _error(PluginArtifactReason.HUMAN_AUTHORITY_UNAVAILABLE) from exc
        claimed = None
        try:
            claimed = claim_pending_for_review(_state=self._state)
            if claimed != pending:
                raise ElevatedBootstrapError("pending_tampered")
            complete_review(claimed, outcome="approved", _state=self._state)
        except ElevatedBootstrapError as exc:
            if claimed is not None:
                try:
                    complete_review(claimed, outcome="failed", _state=self._state)
                except ElevatedBootstrapError:
                    pass
            raise _error(PluginArtifactReason.AUTHORITY_REQUIRED) from exc

    def consume_setup_authority(self, authority: ArtifactAuthority, preview_digest: str) -> None:
        del authority, preview_digest
        raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)


class PortablePluginArtifactAdapter(PluginArtifactPort):
    """Codex-root portable projection with replay and conservative reconciliation."""

    __slots__ = (
        "_mcp_owner_state",
        "_operations",
        "_resource_source",
        "_review",
    )

    def __init__(
        self,
        resource_source: PortableResourceSource | None = None,
        *,
        review: PluginMutationReviewPort | None = None,
        mcp_owner_state: McpOwnershipState = McpOwnershipState.AMBIGUOUS,
    ) -> None:
        if type(mcp_owner_state) is not McpOwnershipState:
            raise ValueError("plugin_mcp_owner_invalid")
        self._resource_source = resource_source
        self._review = _DenyStandaloneReview() if review is None else review
        self._mcp_owner_state = mcp_owner_state
        self._operations: dict[str, tuple[str, PluginArtifactResult]] = {}

    def _rendered(self) -> RenderedPortablePlugin:
        return build_portable_plugin_plan(resource_source=self._resource_source)

    def _authorize(self, command: PluginArtifactApplyCommand) -> None:
        if command.authority.target_digest != command.accepted_preview_digest:
            raise _error(PluginArtifactReason.AUTHORITY_REQUIRED)
        if command.authority.channel == "review_only":
            self._review.consume_artifact_review(
                command.authority,
                command.accepted_preview_digest,
            )
        else:
            self._review.consume_setup_authority(
                command.authority,
                command.accepted_preview_digest,
            )

    async def preview_artifact(
        self,
        request_id: RequestId,
        target: ArtifactTarget,
        action: PluginArtifactAction,
    ) -> PluginArtifactPreview:
        normalized = validate_request_id(request_id)
        if type(target) is not ArtifactTarget or type(action) is not PluginArtifactAction:
            raise _error(PluginArtifactReason.SOURCE_INVALID)
        rendered = self._rendered()
        return _preview(normalized, action, _inspect(target, rendered), rendered)

    def _replay(self, command: PluginArtifactApplyCommand) -> PluginArtifactResult | None:
        identity = canonical_digest(
            {
                "action": command.action.value,
                "authority_channel": command.authority.channel,
                "preview_digest": command.accepted_preview_digest,
            }
        )
        previous = self._operations.get(command.request_id)
        if previous is None:
            return None
        if previous[0] != identity:
            raise _error(PluginArtifactReason.REQUEST_IDENTITY_CONFLICT)
        return previous[1]

    def _store(
        self,
        command: PluginArtifactApplyCommand,
        result: PluginArtifactResult,
    ) -> PluginArtifactResult:
        identity = canonical_digest(
            {
                "action": command.action.value,
                "authority_channel": command.authority.channel,
                "preview_digest": command.accepted_preview_digest,
            }
        )
        self._operations[command.request_id] = (identity, result)
        return result

    async def status_artifact(self, command: PluginArtifactStatusCommand) -> PluginArtifactStatus:
        if type(command) is not PluginArtifactStatusCommand:
            raise _error(PluginArtifactReason.SOURCE_INVALID)
        rendered = self._rendered()
        inspection = _inspect(command.target, rendered)
        operation_state = PluginOperationState.NOT_STARTED
        if command.request_id is not None and command.request_id in self._operations:
            operation_state = self._operations[command.request_id][1].operation_state
        format_profile = (
            PluginFormatProfile.AGENT_PLUGINS_1
            if inspection.state
            in {PluginArtifactState.PORTABLE_EXACT, PluginArtifactState.PORTABLE_MANAGED}
            else PluginFormatProfile.CODEX_PLUGIN_NATIVE
            if inspection.state is PluginArtifactState.NATIVE_MANAGED
            else None
        )
        proof = tuple(
            PluginProofStatus(
                facet,
                "proven"
                if facet in {PluginProofFacet.SOURCE, PluginProofFacet.RENDERED_ARTIFACT}
                or (
                    facet is PluginProofFacet.INSTALLED_BYTES
                    and inspection.state is PluginArtifactState.PORTABLE_EXACT
                )
                else "not_observed",
            )
            for facet in PluginProofFacet
        )
        return PluginArtifactStatus(
            inspection.state,
            operation_state,
            format_profile,
            inspection.installed_digest,
            rendered.artifact_digest,
            rendered.plan.mcp_ownership,
            self._mcp_owner_state,
            inspection.marker_valid,
            inspection.rollback_available,
            proof,
        )

    async def install_artifact(self, command: PluginArtifactApplyCommand) -> PluginArtifactResult:
        if type(command) is not PluginArtifactApplyCommand or command.action not in {
            PluginArtifactAction.INSTALL,
            PluginArtifactAction.REPLACE,
        }:
            raise _error(PluginArtifactReason.DESTINATION_CONFLICT)
        replay = self._replay(command)
        if replay is not None:
            return replay
        rendered = self._rendered()
        inspection = _inspect(command.target, rendered)
        preview = _preview(command.request_id, command.action, inspection, rendered)
        if preview.preview_digest != command.accepted_preview_digest:
            raise _error(PluginArtifactReason.PREVIEW_STALE)
        self._authorize(command)
        if preview.action is PluginArtifactAction.NOOP:
            return self._store(
                command,
                PluginArtifactResult(
                    command.request_id,
                    PluginArtifactAction.NOOP,
                    PluginOperationState.COMPLETED,
                    inspection.state,
                    inspection.state,
                    preview.preview_digest,
                    rendered.artifact_digest,
                    inspection.installed_digest,
                    (),
                ),
            )
        parent = _plugin_parent(inspection.root, create=True)
        destination = parent / "yoetz"
        stage = parent / f"{_STAGE_PREFIX}{command.request_id}"
        rollback = parent / _NATIVE_ROLLBACK_NAME
        if stage.exists() or (
            inspection.state is PluginArtifactState.NATIVE_MANAGED and rollback.exists()
        ):
            raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
        native_moved = False
        portable_moved: Path | None = None
        try:
            _write_tree(stage, rendered)
            if inspection.state is PluginArtifactState.NATIVE_MANAGED:
                os.replace(destination, rollback)
                native_moved = True
                _fsync_dir(parent)
            elif inspection.state is PluginArtifactState.PORTABLE_MANAGED:
                portable_moved = parent / f"{_REMOVE_PREFIX}{command.request_id}"
                os.replace(destination, portable_moved)
                _fsync_dir(parent)
            os.replace(stage, destination)
            _fsync_dir(parent)
            final = _inspect(command.target, rendered)
            if final.state is not PluginArtifactState.PORTABLE_EXACT:
                raise OSError("installed_verification_failed")
            if portable_moved is not None and portable_moved.exists():
                shutil.rmtree(portable_moved)
                _fsync_dir(parent)
        except (OSError, PluginArtifactError) as exc:
            try:
                if native_moved and rollback.exists() and not destination.exists():
                    os.replace(rollback, destination)
                    _fsync_dir(parent)
                elif (
                    portable_moved is not None
                    and portable_moved.exists()
                    and not destination.exists()
                ):
                    os.replace(portable_moved, destination)
                    _fsync_dir(parent)
            except OSError:
                pass
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            result = PluginArtifactResult(
                command.request_id,
                command.action,
                PluginOperationState.OUTCOME_UNKNOWN,
                inspection.state,
                PluginArtifactState.RECOVERY_REQUIRED,
                preview.preview_digest,
                rendered.artifact_digest,
                None,
                (),
            )
            self._store(command, result)
            if isinstance(exc, PluginArtifactError):
                raise
            raise _error(
                PluginArtifactReason.WRITE_FAILED,
                {"operation_state": PluginOperationState.OUTCOME_UNKNOWN.value},
            ) from exc
        changed = tuple(sorted((*rendered.members, _MARKER_NAME), key=str.encode))
        return self._store(
            command,
            PluginArtifactResult(
                command.request_id,
                command.action,
                PluginOperationState.COMPLETED,
                inspection.state,
                PluginArtifactState.PORTABLE_EXACT,
                preview.preview_digest,
                rendered.artifact_digest,
                rendered.artifact_digest,
                changed,
            ),
        )

    async def remove_artifact(self, command: PluginArtifactApplyCommand) -> PluginArtifactResult:
        if (
            type(command) is not PluginArtifactApplyCommand
            or command.action is not PluginArtifactAction.REMOVE
        ):
            raise _error(PluginArtifactReason.REMOVE_REFUSED)
        replay = self._replay(command)
        if replay is not None:
            return replay
        rendered = self._rendered()
        inspection = _inspect(command.target, rendered)
        preview = _preview(command.request_id, command.action, inspection, rendered)
        if preview.preview_digest != command.accepted_preview_digest:
            raise _error(PluginArtifactReason.PREVIEW_STALE)
        self._authorize(command)
        parent = inspection.parent
        destination = inspection.destination
        removal = parent / f"{_REMOVE_PREFIX}{command.request_id}"
        rollback = parent / _NATIVE_ROLLBACK_NAME
        if removal.exists():
            raise _error(PluginArtifactReason.RECOVERY_REQUIRED)
        try:
            os.replace(destination, removal)
            _fsync_dir(parent)
            restored = False
            if rollback.exists():
                if rollback.is_symlink() or not rollback.is_dir():
                    raise OSError("rollback_unsafe")
                os.replace(rollback, destination)
                restored = True
                _fsync_dir(parent)
            shutil.rmtree(removal)
            _fsync_dir(parent)
        except OSError as exc:
            result = PluginArtifactResult(
                command.request_id,
                command.action,
                PluginOperationState.OUTCOME_UNKNOWN,
                inspection.state,
                PluginArtifactState.RECOVERY_REQUIRED,
                preview.preview_digest,
                rendered.artifact_digest,
                None,
                (),
            )
            self._store(command, result)
            raise _error(
                PluginArtifactReason.WRITE_FAILED,
                {"operation_state": PluginOperationState.OUTCOME_UNKNOWN.value},
            ) from exc
        final = _inspect(command.target, rendered)
        expected = PluginArtifactState.NATIVE_MANAGED if restored else PluginArtifactState.ABSENT
        if final.state is not expected:
            raise _error(PluginArtifactReason.WRITE_FAILED)
        changed = tuple(sorted((*rendered.members, _MARKER_NAME), key=str.encode))
        return self._store(
            command,
            PluginArtifactResult(
                command.request_id,
                PluginArtifactAction.REMOVE,
                PluginOperationState.COMPLETED,
                inspection.state,
                final.state,
                preview.preview_digest,
                rendered.artifact_digest,
                final.installed_digest,
                changed,
            ),
        )
