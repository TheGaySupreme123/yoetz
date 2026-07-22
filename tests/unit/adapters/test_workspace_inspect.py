"""Unit tests for descriptor-safe workspace inspection."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoetz.adapters.workspace_inspect import LocalWorkspaceInspectAdapter, open_inspect_workspace
from yoetz.ports.workspace_inspect import (
    WorkspaceInspectCommand,
    WorkspaceInspectLimitation,
    WorkspaceInspectStatus,
)


def test_inspect_relative_file_returns_digest_and_excerpt(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    file_path = target / "note.txt"
    file_path.write_text("hello-advice\n", encoding="utf-8")
    handle = open_inspect_workspace(tmp_path)
    result = LocalWorkspaceInspectAdapter().inspect(
        WorkspaceInspectCommand(handle, ("src/note.txt",))
    )
    assert result.status is WorkspaceInspectStatus.INSPECTED
    assert result.selection_digest is not None
    assert result.artifacts[0].relative_path == "src/note.txt"
    assert b"hello-advice" in result.artifacts[0].excerpt
    assert str(tmp_path) not in result.artifacts[0].relative_path


def test_inspect_rejects_path_escape(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    with pytest.raises(Exception):
        WorkspaceInspectCommand(handle, ("../outside.txt",))


def test_inspect_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("SECRET=super\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    handle = open_inspect_workspace(tmp_path)
    result = LocalWorkspaceInspectAdapter().inspect(WorkspaceInspectCommand(handle, ("link.txt",)))
    assert result.status is WorkspaceInspectStatus.REJECTED
    assert WorkspaceInspectLimitation.SYMLINK_ESCAPE in result.limitations
    assert result.artifacts == ()


def test_inspect_never_returns_absolute_paths(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    handle = open_inspect_workspace(tmp_path)
    result = LocalWorkspaceInspectAdapter().inspect(WorkspaceInspectCommand(handle, ("a.py",)))
    payload = repr(result)
    assert str(tmp_path) not in payload
    assert "/workspace" not in result.artifacts[0].relative_path
