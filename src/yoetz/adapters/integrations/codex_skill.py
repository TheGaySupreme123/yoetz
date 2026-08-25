"""Trusted-project Codex skill resource and filesystem integration adapter."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Protocol, cast

from yoetz.domain.values import JsonObject
from yoetz.ports.integrations import (
    HarnessId,
    HarnessProfile,
    IntegrationAction,
    IntegrationError,
    IntegrationFile,
    IntegrationPreview,
    IntegrationReason,
    IntegrationResult,
    IntegrationScope,
    IntegrationsPort,
    IntegrationState,
    IntegrationStatus,
    IntegrationTarget,
    SkillApplyCommand,
    SkillPreviewCommand,
    SkillSource,
    SkillStatusCommand,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "CODEX_HARNESS_PROFILE",
    "CodexSkillIntegration",
    "DestinationInspection",
    "SkillResourceSource",
    "build_managed_marker",
    "inspect_destination",
    "load_packaged_skill_members",
    "load_packaged_skill_source",
    "recover_interrupted_swap",
]

_ADAPTER_VERSION = "codex-skill/0.1.0"
_MARKER_NAME = ".yoetz-install.json"
_MARKER_SCHEMA = "yoetz.codex-skill-install/1"
_RESOURCE_MANIFEST_LIMIT = 1_048_576
_SOURCE_FILE_LIMIT = 262_144
_EXPECTED_PACKAGE_PATHS: Mapping[str, str] = {
    "SKILL.md": "skills/codex/yoetz/SKILL.md",
    "manifest.json": "skills/codex/yoetz/manifest.json",
    "references/agent-instructions.md": "guidance/agent-instructions.md",
    "references/coverage-and-receipts.md": "guidance/coverage-and-receipts.md",
    "references/publication-policy.md": "guidance/publication-policy.md",
    "references/request-templates.md": "guidance/request-templates.md",
    "references/workflow.md": "guidance/workflow.md",
}
_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


CODEX_HARNESS_PROFILE = HarnessProfile(
    HarnessId.CODEX,
    ".agents/skills/yoetz/",
    "codex_skill_frontmatter_v1",
    (),
    (),
    {},
)


class SkillResourceSource(Protocol):
    """Bounded read-only package-resource injection seam."""

    def read_bytes(self, package_path: str) -> bytes: ...


class _PackageSkillResources:
    def read_bytes(self, package_path: str) -> bytes:
        node = resources.files("yoetz.resources")
        for part in package_path.split("/"):
            node = node.joinpath(part)
        return node.read_bytes()


@dataclass(frozen=True, slots=True)
class _SourceBundle:
    source: SkillSource
    members: Mapping[str, bytes]


@dataclass(frozen=True, slots=True, repr=False)
class DestinationInspection:
    target: IntegrationTarget
    destination: Path
    state: IntegrationState
    installed_digest: str | None
    file_states: tuple[JsonObject, ...]
    managed_marker_valid: bool
    target_identity: str

    def __repr__(self) -> str:
        return (
            "DestinationInspection("
            f"state={self.state.value!r}, installed_digest={self.installed_digest!r}, "
            f"managed_marker_valid={self.managed_marker_valid!r}, target=<redacted>)"
        )

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("destination_inspection_not_serializable")


def _error(reason: IntegrationReason) -> IntegrationError:
    return IntegrationError(reason, {})


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _mapping(value: JsonValue, reason: IntegrationReason) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _error(reason)
    return cast(Mapping[str, JsonValue], value)


def _canonical_json(raw: bytes, reason: IntegrationReason) -> Mapping[str, JsonValue]:
    if len(raw) > _RESOURCE_MANIFEST_LIMIT:
        raise _error(reason)
    try:
        value = strict_json_parse(raw)
        mapping = _mapping(value, reason)
        if raw not in {canonical_encode(mapping), canonical_encode(mapping) + b"\n"}:
            raise _error(reason)
        return mapping
    except (ProtocolValueError, UnicodeError) as exc:
        raise _error(reason) from exc


def _resource_entries(
    source: SkillResourceSource,
) -> tuple[Mapping[str, JsonValue], Mapping[str, Mapping[str, JsonValue]]]:
    try:
        raw = source.read_bytes("manifest.json")
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    manifest = _canonical_json(raw, IntegrationReason.SOURCE_INVALID)
    expected_keys = {"schema", "package", "resource_set_version", "entries", "resource_set_digest"}
    if set(manifest) != expected_keys or manifest.get("schema") != "yoetz.resource-manifest/1":
        raise _error(IntegrationReason.SOURCE_INVALID)
    if manifest.get("package") != "yoetz":
        raise _error(IntegrationReason.SOURCE_INVALID)
    digest = manifest.get("resource_set_digest")
    raw_entries = manifest.get("entries")
    if type(raw_entries) is not list:
        raise _error(IntegrationReason.SOURCE_INVALID)
    by_path: dict[str, Mapping[str, JsonValue]] = {}
    digest_entries: list[JsonValue] = []
    for raw_entry in raw_entries:
        entry = _mapping(raw_entry, IntegrationReason.SOURCE_INVALID)
        package_path = entry.get("package_path")
        if type(package_path) is not str or package_path in by_path:
            raise _error(IntegrationReason.SOURCE_INVALID)
        by_path[package_path] = entry
        if entry.get("kind") == "runtime_support":
            digest_entries.append(
                {key: value for key, value in entry.items() if key not in {"sha256", "size"}}
            )
        else:
            digest_entries.append(dict(entry))
    digest_material: dict[str, JsonValue] = {
        "entries": digest_entries,
        "package": manifest["package"],
        "resource_set_version": manifest["resource_set_version"],
        "schema": manifest["schema"],
    }
    if type(digest) is not str or digest != canonical_digest(digest_material):
        raise _error(IntegrationReason.SOURCE_INVALID)
    return manifest, by_path


def _validated_text(path: str, data: bytes) -> None:
    if len(data) > _SOURCE_FILE_LIMIT or not data.endswith(b"\n") or b"\r" in data:
        raise _error(IntegrationReason.SOURCE_INVALID)
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    if text.startswith("\ufeff"):
        raise _error(IntegrationReason.SOURCE_INVALID)
    for link in _LINK_RE.findall(text):
        target = link.split("#", 1)[0]
        if not target or "://" in target or target.startswith("#"):
            continue
        parts = Path(target).parts
        if target.startswith(("/", "\\")) or ".." in parts or "\\" in target:
            raise _error(IntegrationReason.SOURCE_INVALID)
    if path == "SKILL.md":
        _validate_skill_frontmatter(text)


def _validate_skill_frontmatter(text: str) -> None:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise _error(IntegrationReason.SOURCE_INVALID)
    header = text[4 : text.index("\n---\n", 4)]
    lines = header.splitlines()
    top_level = {line.split(":", 1)[0] for line in lines if line and not line.startswith(" ")}
    if top_level not in ({"name", "description"}, {"name", "description", "metadata"}):
        raise _error(IntegrationReason.SOURCE_INVALID)
    if "name: yoetz" not in lines or not any(line.startswith("description: ") for line in lines):
        raise _error(IntegrationReason.SOURCE_INVALID)
    if "metadata" in top_level and not any(
        line.startswith("  short-description: ") for line in lines
    ):
        raise _error(IntegrationReason.SOURCE_INVALID)


def _load_source_bundle(resource_source: SkillResourceSource | None = None) -> _SourceBundle:
    source = _PackageSkillResources() if resource_source is None else resource_source
    manifest, entries = _resource_entries(source)
    members: dict[str, bytes] = {}
    files: list[IntegrationFile] = []
    for installed_path, package_path in sorted(
        _EXPECTED_PACKAGE_PATHS.items(), key=lambda item: item[0].encode("ascii")
    ):
        entry = entries.get(package_path)
        if entry is None:
            raise _error(IntegrationReason.SOURCE_INVALID)
        try:
            data = source.read_bytes(package_path)
        except (FileNotFoundError, OSError) as exc:
            raise _error(IntegrationReason.SOURCE_INVALID) from exc
        size = entry.get("size")
        digest = entry.get("sha256")
        media_type = entry.get("media_type")
        if (
            type(size) is not int
            or size != len(data)
            or type(digest) is not str
            or digest != _sha(data)
            or type(media_type) is not str
        ):
            raise _error(IntegrationReason.SOURCE_INVALID)
        _validated_text(installed_path, data)
        members[installed_path] = data
        files.append(IntegrationFile(installed_path, size, digest, media_type))

    skill_manifest = _canonical_json(members["manifest.json"], IntegrationReason.SOURCE_INVALID)
    member_digest = skill_manifest.get("member_digest")
    manifest_without_digest = {
        key: value for key, value in skill_manifest.items() if key != "member_digest"
    }
    if (
        skill_manifest.get("schema") != "yoetz.codex-skill-manifest/1"
        or skill_manifest.get("skill") != "yoetz"
        or skill_manifest.get("harness") != HarnessId.CODEX.value
        or type(member_digest) is not str
        or member_digest != canonical_digest(manifest_without_digest)
    ):
        raise _error(IntegrationReason.SOURCE_INVALID)
    managed_members = skill_manifest.get("managed_members")
    if type(managed_members) is not list:
        raise _error(IntegrationReason.SOURCE_INVALID)
    recorded_members: dict[str, Mapping[str, object]] = {}
    for raw_member in managed_members:
        if not isinstance(raw_member, Mapping):
            raise _error(IntegrationReason.SOURCE_INVALID)
        logical_name = raw_member.get("logical_name")
        if type(logical_name) is not str or logical_name in recorded_members:
            raise _error(IntegrationReason.SOURCE_INVALID)
        recorded_members[logical_name] = cast(Mapping[str, object], raw_member)
    if set(recorded_members) != set(members):
        raise _error(IntegrationReason.SOURCE_INVALID)
    for logical_name, data in members.items():
        record = recorded_members[logical_name]
        if logical_name == "manifest.json":
            if record.get("identity_status") != "self_excluded":
                raise _error(IntegrationReason.SOURCE_INVALID)
            continue
        if record.get("size") != len(data) or record.get("sha256") != _sha(data):
            raise _error(IntegrationReason.SOURCE_INVALID)
    skill_version = skill_manifest.get("skill_version")
    protocol_version = skill_manifest.get("protocol_version")
    profile_ids = skill_manifest.get("capability_profile_ids")
    hooks = skill_manifest.get("hooks_by_capability_profile")
    bounds = skill_manifest.get("codex_version_bounds")
    if (
        type(skill_version) is not str
        or type(protocol_version) is not str
        or type(profile_ids) is not list
        or any(type(item) is not str for item in profile_ids)
        or not isinstance(hooks, Mapping)
        or not isinstance(bounds, Mapping)
    ):
        raise _error(IntegrationReason.SOURCE_INVALID)
    profile_tuple = cast(tuple[str, ...], tuple(profile_ids))
    tested = bounds.get("tested")
    if type(tested) is not list or any(type(item) is not str for item in tested):
        raise _error(IntegrationReason.SOURCE_INVALID)
    tested_tuple = cast(tuple[str, ...], tuple(tested))
    if (
        profile_tuple != CODEX_HARNESS_PROFILE.capability_profile_ids
        or tested_tuple != CODEX_HARNESS_PROFILE.supported_versions
        or set(cast(Mapping[object, object], hooks)) != set(profile_tuple)
    ):
        raise _error(IntegrationReason.SOURCE_INVALID)
    resource_digest = manifest.get("resource_set_digest")
    assert type(resource_digest) is str
    return _SourceBundle(
        SkillSource(
            HarnessId.CODEX,
            skill_version,
            protocol_version,
            tested_tuple,
            resource_digest,
            tuple(files),
        ),
        members,
    )


def load_packaged_skill_source(resource_source: SkillResourceSource | None = None) -> SkillSource:
    """Verify the package manifest and return immutable structural skill source metadata."""

    return _load_source_bundle(resource_source).source


def load_packaged_skill_members(
    resource_source: SkillResourceSource | None = None,
) -> Mapping[str, bytes]:
    """Verify the package and return immutable skill member path → bytes."""

    return _load_source_bundle(resource_source).members


def build_managed_marker(source: SkillSource, scope: IntegrationScope) -> bytes:
    if type(source) is not SkillSource or source.harness_id is not HarnessId.CODEX:
        raise _error(IntegrationReason.SOURCE_INVALID)
    if scope is not IntegrationScope.TRUSTED_PROJECT:
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    body: dict[str, JsonValue] = {
        "adapter_version": _ADAPTER_VERSION,
        "harness_id": HarnessId.CODEX.value,
        "managed_files": [
            {
                "relative_path": file.relative_path,
                "sha256": file.sha256,
                "size": file.size,
            }
            for file in source.files
        ],
        "protocol_range": source.protocol_range,
        "resource_set_digest": source.resource_set_digest,
        "schema": _MARKER_SCHEMA,
        "scope": scope.value,
        "skill_version": source.skill_version,
    }
    body["marker_digest"] = canonical_digest(body)
    return canonical_encode(body) + b"\n"


def _validated_project(target: IntegrationTarget) -> tuple[Path, str]:
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
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current /= part
        if current.is_symlink():
            raise _error(IntegrationReason.TARGET_UNSAFE)
    try:
        stat = root.stat()
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if hasattr(os, "geteuid") and stat.st_uid != os.geteuid():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if stat.st_mode & 0o022:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    identity = canonical_digest(
        {"device": stat.st_dev, "inode": stat.st_ino, "mode": stat.st_mode & 0o777}
    )
    return root, identity


def _file_state(path: Path, expected: IntegrationFile) -> JsonObject:
    if not path.exists():
        return JsonObject({"relative_path": expected.relative_path, "state": "absent"})
    if path.is_symlink() or not path.is_file():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    stat = path.stat()
    if stat.st_nlink != 1 or stat.st_size > _SOURCE_FILE_LIMIT:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    data = path.read_bytes()
    digest = _sha(data)
    return JsonObject(
        {
            "relative_path": expected.relative_path,
            "state": "exact" if digest == expected.sha256 else "modified",
            "digest": digest,
            "size": len(data),
        }
    )


def inspect_destination(target: IntegrationTarget, source: SkillSource) -> DestinationInspection:
    """Classify only the profile-fixed destination without mutating it."""

    root, target_identity = _validated_project(target)
    destination = root / CODEX_HARNESS_PROFILE.skill_root.rstrip("/")
    if destination.is_symlink():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    siblings = destination.parent
    if siblings.exists() and any(
        child.name.startswith((".yoetz.stage-", ".yoetz.rollback-", ".yoetz.remove-"))
        for child in siblings.iterdir()
    ):
        state = IntegrationState.PARTIAL
    elif not destination.exists():
        state = IntegrationState.ABSENT
    elif not destination.is_dir():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    else:
        state = IntegrationState.MODIFIED

    rows = tuple(_file_state(destination / item.relative_path, item) for item in source.files)
    expected_paths = {item.relative_path for item in source.files} | {_MARKER_NAME}
    actual_paths: set[str] = set()
    if destination.is_dir():
        for path in destination.rglob("*"):
            if path.is_symlink():
                raise _error(IntegrationReason.TARGET_UNSAFE)
            if path.is_file():
                actual_paths.add(path.relative_to(destination).as_posix())
    marker = destination / _MARKER_NAME
    marker_valid = False
    if marker.is_file() and not marker.is_symlink():
        marker_data = marker.read_bytes()
        marker_valid = marker_data == build_managed_marker(source, target.scope)
    exact_count = sum(row["state"] == "exact" for row in rows)
    if destination.exists():
        if exact_count < len(rows) and any(row["state"] == "absent" for row in rows):
            state = IntegrationState.PARTIAL
        elif exact_count == len(rows) and marker_valid and actual_paths == expected_paths:
            state = IntegrationState.INSTALLED_EXACT
        else:
            state = IntegrationState.MODIFIED
    installed_digest = None
    if actual_paths:
        installed_digest = canonical_digest(
            {
                "files": [
                    {"path": row["relative_path"], "digest": row.get("digest")}
                    for row in rows
                    if row["state"] != "absent"
                ],
                "marker_valid": marker_valid,
            }
        )
    return DestinationInspection(
        target,
        destination,
        state,
        installed_digest,
        rows,
        marker_valid,
        target_identity,
    )


def _changes(
    inspection: DestinationInspection,
    source: SkillSource,
    action: IntegrationAction,
) -> tuple[JsonObject, ...]:
    changes: list[JsonObject] = []
    rows = {cast(str, row["relative_path"]): row for row in inspection.file_states}
    for file in source.files:
        row = rows[file.relative_path]
        before_digest = row.get("digest")
        before_size = row.get("size")
        if action is IntegrationAction.REMOVE:
            if before_digest is not None:
                changes.append(
                    JsonObject(
                        {
                            "action": "remove",
                            "before_digest": before_digest,
                            "before_size": before_size,
                            "relative_path": file.relative_path,
                        }
                    )
                )
            continue
        change = (
            "create"
            if before_digest is None
            else ("unchanged" if before_digest == file.sha256 else "replace")
        )
        value: dict[str, JsonValue] = {
            "action": change,
            "after_digest": file.sha256,
            "after_size": file.size,
            "relative_path": file.relative_path,
        }
        if before_digest is not None:
            value["before_digest"] = before_digest
            value["before_size"] = cast(int, before_size)
        changes.append(JsonObject(value))
    return tuple(changes)


def _preview(
    source: SkillSource,
    inspection: DestinationInspection,
    command: SkillPreviewCommand,
) -> IntegrationPreview:
    compatibility = "supported" if source.harness_tested_set else "unsupported"
    warnings = () if compatibility == "supported" else ("unprofiled_harness",)
    changes = _changes(inspection, source, command.requested_action)
    digest = canonical_digest(
        {
            "action": command.requested_action.value,
            "compatibility": compatibility,
            "current_files": [dict(row) for row in inspection.file_states],
            "installed_digest": inspection.installed_digest,
            "replace_modified": command.replace_modified,
            "scope": command.target.scope.value,
            "source_digest": source.resource_set_digest,
            "target_identity": inspection.target_identity,
            "warnings": list(warnings),
        }
    )
    return IntegrationPreview(
        command.requested_action,
        inspection.state,
        source.resource_set_digest,
        inspection.installed_digest,
        compatibility,
        changes,
        warnings,
        digest,
    )


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_bundle(stage: Path, bundle: _SourceBundle) -> None:
    stage.mkdir(mode=0o700)
    for relative_path, data in bundle.members.items():
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
    marker = stage / _MARKER_NAME
    marker.write_bytes(build_managed_marker(bundle.source, IntegrationScope.TRUSTED_PROJECT))
    with marker.open("rb") as handle:
        os.fsync(handle.fileno())
    _fsync_dir(stage)


def recover_interrupted_swap(
    target: IntegrationTarget,
    expected_preview: str | None = None,
    *,
    resource_source: SkillResourceSource | None = None,
) -> DestinationInspection:
    """Return conservative structural state; never choose between preserved copies."""

    bundle = _load_source_bundle(resource_source)
    inspection = inspect_destination(target, bundle.source)
    if expected_preview is not None and not expected_preview.startswith("sha256:"):
        raise _error(IntegrationReason.PREVIEW_STALE)
    return inspection


class CodexSkillIntegration(IntegrationsPort):
    """Exact Codex trusted-project integration adapter."""

    __slots__ = ("_allow_untested", "_resource_source")

    def __init__(
        self,
        resource_source: SkillResourceSource | None = None,
        *,
        allow_untested: bool = False,
    ) -> None:
        self._resource_source = resource_source
        self._allow_untested = allow_untested

    @staticmethod
    def _harness(harness: HarnessId) -> None:
        if type(harness) is not HarnessId or harness is not HarnessId.CODEX:
            raise _error(IntegrationReason.SOURCE_INVALID)

    def _bundle(self) -> _SourceBundle:
        return _load_source_bundle(self._resource_source)

    async def preview_skill(
        self, harness: HarnessId, command: SkillPreviewCommand
    ) -> IntegrationPreview:
        self._harness(harness)
        if type(command) is not SkillPreviewCommand:
            raise _error(IntegrationReason.SOURCE_INVALID)
        bundle = self._bundle()
        inspection = inspect_destination(command.target, bundle.source)
        return _preview(bundle.source, inspection, command)

    async def status_skill(
        self, harness: HarnessId, command: SkillStatusCommand
    ) -> IntegrationStatus:
        self._harness(harness)
        if type(command) is not SkillStatusCommand:
            raise _error(IntegrationReason.SOURCE_INVALID)
        bundle = self._bundle()
        inspection = inspect_destination(command.target, bundle.source)
        compatibility = "supported" if bundle.source.harness_tested_set else "unsupported"
        return IntegrationStatus(
            inspection.state,
            bundle.source.resource_set_digest,
            inspection.installed_digest,
            compatibility,
            inspection.file_states,
            inspection.managed_marker_valid,
        )

    async def install_skill(
        self, harness: HarnessId, command: SkillApplyCommand
    ) -> IntegrationResult:
        self._harness(harness)
        if type(command) is not SkillApplyCommand or command.requested_action not in {
            IntegrationAction.INSTALL,
            IntegrationAction.REPLACE,
        }:
            raise _error(IntegrationReason.DESTINATION_CONFLICT)
        bundle = self._bundle()
        if not bundle.source.harness_tested_set and not self._allow_untested:
            raise _error(IntegrationReason.VERSION_INCOMPATIBLE)
        inspection = inspect_destination(command.target, bundle.source)
        current = _preview(
            bundle.source,
            inspection,
            SkillPreviewCommand(
                command.request_id,
                command.target,
                command.requested_action,
                command.replace_modified,
            ),
        )
        if not command.explicitly_accepted:
            raise _error(IntegrationReason.CONFIRMATION_REQUIRED)
        if command.preview_digest != current.preview_digest:
            raise _error(IntegrationReason.PREVIEW_STALE)
        if inspection.state is IntegrationState.INSTALLED_EXACT:
            return IntegrationResult(
                IntegrationAction.NOOP,
                inspection.state,
                inspection.state,
                bundle.source.resource_set_digest,
                inspection.installed_digest,
                (),
                current.preview_digest,
            )
        replacing = inspection.state in {IntegrationState.MODIFIED, IntegrationState.PARTIAL}
        if replacing and not (
            command.requested_action is IntegrationAction.REPLACE and command.replace_modified
        ):
            reason = (
                IntegrationReason.MODIFIED_COPY
                if inspection.state is IntegrationState.MODIFIED
                else IntegrationReason.PARTIAL_INSTALL
            )
            raise _error(reason)
        parent = inspection.destination.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = parent / f".yoetz.stage-{command.request_id}"
        rollback = parent / f".yoetz.rollback-{command.request_id}"
        if stage.exists() or rollback.exists():
            raise _error(IntegrationReason.PREVIEW_STALE)
        try:
            _write_bundle(stage, bundle)
            if replacing:
                os.replace(inspection.destination, rollback)
                _fsync_dir(parent)
            os.replace(stage, inspection.destination)
            _fsync_dir(parent)
            final = inspect_destination(command.target, bundle.source)
            if final.state is not IntegrationState.INSTALLED_EXACT:
                raise _error(IntegrationReason.WRITE_FAILED)
            if rollback.exists():
                shutil.rmtree(rollback)
                _fsync_dir(parent)
        except IntegrationError:
            raise
        except OSError as exc:
            raise _error(IntegrationReason.WRITE_FAILED) from exc
        return IntegrationResult(
            command.requested_action,
            inspection.state,
            final.state,
            bundle.source.resource_set_digest,
            final.installed_digest,
            tuple(sorted((*bundle.members, _MARKER_NAME), key=str.encode)),
            current.preview_digest,
        )

    async def remove_skill(
        self, harness: HarnessId, command: SkillApplyCommand
    ) -> IntegrationResult:
        self._harness(harness)
        if (
            type(command) is not SkillApplyCommand
            or command.requested_action is not IntegrationAction.REMOVE
        ):
            raise _error(IntegrationReason.REMOVE_REFUSED)
        bundle = self._bundle()
        inspection = inspect_destination(command.target, bundle.source)
        current = _preview(
            bundle.source,
            inspection,
            SkillPreviewCommand(
                command.request_id, command.target, IntegrationAction.REMOVE, False
            ),
        )
        if (
            not command.explicitly_accepted
            or command.preview_digest != current.preview_digest
            or inspection.state is not IntegrationState.INSTALLED_EXACT
        ):
            raise _error(IntegrationReason.REMOVE_REFUSED)
        parent = inspection.destination.parent
        staging = parent / f".yoetz.remove-{command.request_id}"
        if staging.exists():
            raise _error(IntegrationReason.PREVIEW_STALE)
        try:
            os.replace(inspection.destination, staging)
            _fsync_dir(parent)
            shutil.rmtree(staging)
            _fsync_dir(parent)
        except OSError as exc:
            raise _error(IntegrationReason.WRITE_FAILED) from exc
        return IntegrationResult(
            IntegrationAction.REMOVE,
            inspection.state,
            IntegrationState.ABSENT,
            bundle.source.resource_set_digest,
            None,
            tuple(sorted((*bundle.members, _MARKER_NAME), key=str.encode)),
            current.preview_digest,
        )
