"""Descriptor-safe bounded workspace artifact inspection adapter."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from yoetz.ports.subject_state import LocalWorkspaceHandle
from yoetz.ports.workspace_inspect import (
    MAX_INSPECT_EXCERPT_BYTES,
    MAX_INSPECT_FILES,
    InspectedArtifact,
    WorkspaceInspectCommand,
    WorkspaceInspectLimitation,
    WorkspaceInspectResult,
    WorkspaceInspectStatus,
)
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "WORKSPACE_INSPECT_FORMAT",
    "LocalWorkspaceInspectAdapter",
    "open_inspect_workspace",
]

WORKSPACE_INSPECT_FORMAT: Final = "yoetz.workspace-inspect/1"
_MAX_FILE_BYTES: Final = 1_048_576
_DOMAIN: Final = b"yoetz/workspace-inspect/v1\x00"


@dataclass(frozen=True, slots=True)
class _InspectRoot:
    root: Path
    device: int
    inode: int


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _lexically_safe_absolute(path: Path) -> Path:
    if path.is_absolute() is False:
        path = path.resolve(strict=False)
    text = os.fspath(path)
    if "\x00" in text:
        raise ValueError("unsafe_root")
    return Path(os.path.abspath(text))


def open_inspect_workspace(path: Path) -> LocalWorkspaceHandle:
    """Validate one explicit local workspace root for inspection."""

    root = _lexically_safe_absolute(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe_root")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(os.fspath(root), flags)
    try:
        facts = os.fstat(descriptor)
        if not stat.S_ISDIR(facts.st_mode):
            raise ValueError("unsafe_root")
        payload = _InspectRoot(root=root, device=facts.st_dev, inode=facts.st_ino)
        return LocalWorkspaceHandle._from_validated_descriptor(payload)
    finally:
        os.close(descriptor)


class LocalWorkspaceInspectAdapter:
    """Read only relative files under a consented workspace; never emit absolute paths."""

    def inspect(self, command: WorkspaceInspectCommand) -> WorkspaceInspectResult:
        if type(command) is not WorkspaceInspectCommand:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                (WorkspaceInspectLimitation.UNSAFE_ROOT,),
                None,
            )
        try:
            descriptor = command.workspace._validated_descriptor()
        except ValueError:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                (WorkspaceInspectLimitation.UNSAFE_ROOT,),
                None,
            )
        root = self._root_from_descriptor(descriptor)
        if root is None:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                (WorkspaceInspectLimitation.UNSAFE_ROOT,),
                None,
            )

        artifacts: list[InspectedArtifact] = []
        limitations: set[WorkspaceInspectLimitation] = set()
        if len(command.relative_paths) > MAX_INSPECT_FILES:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                (WorkspaceInspectLimitation.FILE_LIMIT_EXCEEDED,),
                None,
            )

        for relative in command.relative_paths:
            artifact, limitation = self._inspect_one(root, relative)
            if artifact is not None:
                artifacts.append(artifact)
            if limitation is not None:
                limitations.add(limitation)

        if not artifacts and limitations:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                tuple(sorted(limitations, key=lambda item: item.value.encode("ascii"))),
                None,
            )
        if not artifacts:
            return WorkspaceInspectResult(
                WorkspaceInspectStatus.REJECTED,
                (),
                (WorkspaceInspectLimitation.EMPTY_SELECTION,),
                None,
            )

        selection = canonical_digest(
            {
                "format": WORKSPACE_INSPECT_FORMAT,
                "artifacts": tuple(
                    {
                        "relative_path": item.relative_path,
                        "content_digest": item.content_digest,
                        "byte_length": item.byte_length,
                        "excerpt_truncated": item.excerpt_truncated,
                    }
                    for item in artifacts
                ),
            }
        )
        ordered_limitations = tuple(
            sorted(limitations, key=lambda item: item.value.encode("ascii"))
        )
        status = (
            WorkspaceInspectStatus.PARTIAL
            if ordered_limitations
            else WorkspaceInspectStatus.INSPECTED
        )
        return WorkspaceInspectResult(status, tuple(artifacts), ordered_limitations, selection)

    def _root_from_descriptor(self, descriptor: object) -> Path | None:
        if type(descriptor) is _InspectRoot:
            return descriptor.root
        root = getattr(descriptor, "root", None)
        if isinstance(root, Path):
            return root
        return None

    def _inspect_one(
        self, root: Path, relative: str
    ) -> tuple[InspectedArtifact | None, WorkspaceInspectLimitation | None]:
        try:
            root_resolved = root.resolve(strict=True)
        except OSError:
            return None, WorkspaceInspectLimitation.UNSAFE_ROOT
        candidate = root_resolved / relative
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            return None, WorkspaceInspectLimitation.PATH_OUT_OF_SCOPE
        if candidate.is_symlink():
            try:
                target = candidate.resolve(strict=True)
                target.relative_to(root_resolved)
            except OSError, ValueError:
                return None, WorkspaceInspectLimitation.SYMLINK_ESCAPE
        try:
            if not candidate.is_file():
                return None, WorkspaceInspectLimitation.NOT_A_FILE
            size = candidate.stat().st_size
            if size > _MAX_FILE_BYTES:
                return None, WorkspaceInspectLimitation.OVERSIZED_CONTENT
            data = candidate.read_bytes()
        except OSError:
            return None, WorkspaceInspectLimitation.READ_FAILED
        if len(data) > _MAX_FILE_BYTES:
            return None, WorkspaceInspectLimitation.OVERSIZED_CONTENT
        truncated = len(data) > MAX_INSPECT_EXCERPT_BYTES
        excerpt = data[:MAX_INSPECT_EXCERPT_BYTES]
        digest = _sha256(_DOMAIN + relative.encode("utf-8") + b"\x00" + data)
        return (
            InspectedArtifact(
                relative_path=relative,
                content_digest=digest,
                excerpt=excerpt,
                excerpt_truncated=truncated,
                byte_length=len(data),
            ),
            None,
        )
