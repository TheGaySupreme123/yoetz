"""Native edit capture for AI-powered review (#883).

Every payload below follows a current host contract, not an invented shape:

* Codex 0.157.1 (``codex-rs/core/src/tools/handlers/apply_patch.rs`` and the generated
  ``post-tool-use.command.input.schema.json``): ``tool_name: "apply_patch"``,
  ``tool_input: {"command": <patch>}`` on both PreToolUse and PostToolUse; the post
  ``tool_response`` is the string ``"Exit code: 0\\nWall time: ...\\nOutput:\\nSuccess. ..."``.
* Claude Code PreToolUse/PostToolUse/PostToolUseFailure for ``Edit``, ``MultiEdit``, ``Write``
  and ``NotebookEdit`` with absolute ``file_path``/``notebook_path`` and the structured
  ``tool_response`` (``filePath``, ``originalFile``, ``structuredPatch``).
* Cursor ordinary ``preToolUse``/``postToolUse`` for its ``Write`` tool (``path``/``contents``,
  as in the existing Cursor ingress fixtures) and the documented ``afterFileEdit`` body
  (absolute ``file_path`` plus ``edits[{old_string,new_string}]``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import map_hook_payload_to_envelope, workspace_relative_edit_path
from yoetz.domain.observation import ObservationContentKind, ObservationSource
from yoetz.protocol.canonical import JsonValue

_KEY = b"k" * 32
_CODEX = ObservationSource.CODEX_HOOK
_CLAUDE = ObservationSource.CLAUDE_HOOK
_CURSOR = ObservationSource.CURSOR_HOOK

# One workspace per supported OS spelling: Linux, macOS, and Windows through WSL 2.
_WORKSPACES = {
    "linux": "/home/alice/proj",
    "macos": "/Users/alice/proj",
    "wsl": "/mnt/c/Users/alice/proj",
}


def _patch(path: str) -> str:
    return (
        "*** Begin Patch\n"
        f"*** Update File: {path}\n"
        "@@ def handler():\n"
        "-    return validate(request)\n"
        "+    return planted_bug(request)\n"
        "*** End Patch\n"
    )


def _chunks(
    event: str,
    payload: Mapping[str, JsonValue],
    tmp_path: Path,
    *,
    source: ObservationSource,
    workspace: str | None,
) -> tuple[tuple[ObservationContentKind, bytes], ...]:
    store = LocalObservationStore(_state=tmp_path)
    envelope = map_hook_payload_to_envelope(
        event,
        payload,
        session_commitment=store.session_commitment("edit-session"),
        event_ordinal=1,
        key_material=_KEY,
        source=source,
    )
    chunks, _ = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        event, payload, envelope=envelope, workspace_locator=workspace
    )
    grouped: dict[ObservationContentKind, bytes] = {}
    for chunk in chunks:
        grouped[chunk.content_kind] = grouped.get(chunk.content_kind, b"") + chunk.content
    return tuple(grouped.items())


def _all(chunks: tuple[tuple[ObservationContentKind, bytes], ...]) -> bytes:
    return b"".join(content for _, content in chunks)


def _codex(event: str, workspace: str, patch: str, response: str | None) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "session_id": "019a0000-0000-7000-8000-000000000001",
        "transcript_path": f"{workspace}/.codex/sessions/rollout.jsonl",
        "cwd": workspace,
        "hook_event_name": event,
        "model": "gpt-5.5-codex",
        "permission_mode": "default",
        "turn_id": "turn-1",
        "tool_name": "apply_patch",
        "tool_use_id": "call_patch_1",
        "tool_input": {"command": patch},
    }
    if response is not None:
        payload["tool_response"] = response
    return payload


# --- Codex --------------------------------------------------------------------------------


@pytest.mark.parametrize("os_name", sorted(_WORKSPACES))
def test_codex_0157_apply_patch_is_captured_once_after_the_tool_ran(
    os_name: str, tmp_path: Path
) -> None:
    workspace = _WORKSPACES[os_name]
    patch = _patch(f"{workspace}/app/module.py")
    pre = _chunks(
        "PreToolUse",
        _codex("PreToolUse", workspace, patch, None),
        tmp_path / "pre",
        source=_CODEX,
        workspace=workspace,
    )
    assert b"planted_bug" not in _all(pre), "pre-tool proposal must not duplicate the capture"

    response = (
        "Exit code: 0\nWall time: 0.1 seconds\nOutput:\n"
        f"Success. Updated the following files:\nM {workspace}/app/module.py\n"
    )
    post = dict(
        _chunks(
            "PostToolUse",
            _codex("PostToolUse", workspace, patch, response),
            tmp_path / "post",
            source=_CODEX,
            workspace=workspace,
        )
    )
    diff = post[ObservationContentKind.WORKSPACE_DIFF]
    assert diff.startswith(b"# yoetz edit outcome: applied\n")
    assert b"*** Update File: app/module.py\n" in diff
    assert b"+    return planted_bug(request)" in diff
    # A successful result only repeats the outcome; nothing else carries the absolute locator.
    assert ObservationContentKind.TOOL_OUTPUT not in post
    assert b"alice" not in b"".join(post.values())


def test_codex_failed_apply_patch_is_labelled_failed(tmp_path: Path) -> None:
    workspace = _WORKSPACES["macos"]
    patch = _patch(f"{workspace}/app/module.py")
    response = (
        "Exit code: 1\nWall time: 0.0 seconds\nOutput:\n"
        f"Failed to find expected lines in {workspace}/app/module.py\n"
    )
    post = dict(
        _chunks(
            "PostToolUse",
            _codex("PostToolUse", workspace, patch, response),
            tmp_path,
            source=_CODEX,
            workspace=workspace,
        )
    )
    assert post[ObservationContentKind.WORKSPACE_DIFF].startswith(b"# yoetz edit outcome: failed\n")
    assert b"Exit code: 1" in post[ObservationContentKind.TOOL_OUTPUT]


def test_codex_patch_outside_workspace_is_masked(tmp_path: Path) -> None:
    workspace = _WORKSPACES["linux"]
    patch = (
        "*** Begin Patch\n*** Add File: /etc/private.conf\n+x\n"
        f"*** Update File: {workspace}/a.py\n*** Move to: /home/bob/b.py\n@@\n-a\n+b\n"
        "*** End Patch\n"
    )
    diff = dict(
        _chunks(
            "PostToolUse",
            _codex("PostToolUse", workspace, patch, "Exit code: 0\nOutput:\nSuccess."),
            tmp_path,
            source=_CODEX,
            workspace=workspace,
        )
    )[ObservationContentKind.WORKSPACE_DIFF]
    assert b"*** Add File: <outside-workspace>\n" in diff
    assert b"*** Update File: a.py\n" in diff
    assert b"*** Move to: <outside-workspace>\n" in diff
    assert b"/etc/" not in diff and b"bob" not in diff and b"alice" not in diff


# --- Claude Code --------------------------------------------------------------------------


def _claude(
    event: str, tool: str, tool_input: dict[str, JsonValue], response: JsonValue | None
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "session_id": "5b3c1d2e-0000-4000-8000-000000000001",
        "transcript_path": "/Users/alice/.claude/projects/proj/session.jsonl",
        "cwd": "/Users/alice/proj",
        "permission_mode": "acceptEdits",
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "toolu_01EDIT",
    }
    if response is not None:
        payload["tool_response"] = response
    return payload


_STRUCTURED_PATCH: JsonValue = [
    {
        "oldStart": 3,
        "oldLines": 1,
        "newStart": 3,
        "newLines": 1,
        "lines": ["-    return validate(request)", "+    return planted_bug(request)"],
    }
]


def _claude_cases(workspace: str) -> list[tuple[str, dict[str, JsonValue], JsonValue]]:
    path = f"{workspace}/app/module.py"
    return [
        (
            "Edit",
            {
                "file_path": path,
                "old_string": "return validate(request)",
                "new_string": "return planted_bug(request)",
                "replace_all": False,
            },
            {
                "filePath": path,
                "oldString": "return validate(request)",
                "newString": "return planted_bug(request)",
                "originalFile": "ORIGINAL_FILE_CANARY",
                "structuredPatch": _STRUCTURED_PATCH,
                "userModified": False,
                "replaceAll": False,
            },
        ),
        (
            "MultiEdit",
            {
                "file_path": path,
                "edits": [
                    {
                        "old_string": "return validate(request)",
                        "new_string": "return planted_bug(request)",
                        "replace_all": False,
                    }
                ],
            },
            {
                "filePath": path,
                "edits": [],
                "originalFileContents": "ORIGINAL_FILE_CANARY",
                "structuredPatch": _STRUCTURED_PATCH,
                "userModified": False,
            },
        ),
        (
            "Write",
            {"file_path": path, "content": "def handler():\n    return planted_bug(request)\n"},
            {
                "type": "update",
                "filePath": path,
                "content": "def handler():\n    return planted_bug(request)\n",
                "structuredPatch": _STRUCTURED_PATCH,
                "originalFile": "ORIGINAL_FILE_CANARY",
            },
        ),
        (
            "NotebookEdit",
            {
                "notebook_path": f"{workspace}/nb/analysis.ipynb",
                "cell_id": "cell-3",
                "new_source": "planted_bug(request)",
                "cell_type": "code",
                "edit_mode": "replace",
            },
            {"new_source": "planted_bug(request)", "cell_id": "cell-3", "cell_type": "code"},
        ),
    ]


@pytest.mark.parametrize("os_name", sorted(_WORKSPACES))
@pytest.mark.parametrize("tool", ["Edit", "MultiEdit", "Write", "NotebookEdit"])
def test_claude_native_edit_tools_are_captured_with_relative_paths(
    os_name: str, tool: str, tmp_path: Path
) -> None:
    workspace = _WORKSPACES[os_name]
    tool_input, response = next(
        (item[1], item[2]) for item in _claude_cases(workspace) if item[0] == tool
    )
    tool_input = {**tool_input, "reasoning": "HIDDEN_CANARY"}
    pre = _chunks(
        "PreToolUse",
        _claude("PreToolUse", tool, tool_input, None),
        tmp_path / "pre",
        source=_CLAUDE,
        workspace=workspace,
    )
    assert pre == (), "pre-tool edit input is neither duplicated nor kept as raw tool input"

    post = dict(
        _chunks(
            "PostToolUse",
            _claude("PostToolUse", tool, tool_input, response),
            tmp_path / "post",
            source=_CLAUDE,
            workspace=workspace,
        )
    )
    assert set(post) == {ObservationContentKind.CHANGED_FILE}
    content = post[ObservationContentKind.CHANGED_FILE]
    assert b"planted_bug(request)" in content
    assert b'"edit_outcome":"applied"' in content
    expected_path = b"nb/analysis.ipynb" if tool == "NotebookEdit" else b"app/module.py"
    assert b'"path":"' + expected_path + b'"' in content
    for leaked in (b"alice", b"HIDDEN_CANARY", b"ORIGINAL_FILE_CANARY", b"/mnt/", b"/Users/"):
        assert leaked not in content


def test_claude_post_tool_use_failure_is_labelled_failed(tmp_path: Path) -> None:
    workspace = _WORKSPACES["macos"]
    tool_input = _claude_cases(workspace)[0][1]
    payload = _claude("PostToolUseFailure", "Edit", tool_input, None)
    payload["error"] = "String to replace not found in file."
    content = dict(_chunks("PostToolUse", payload, tmp_path, source=_CLAUDE, workspace=workspace))[
        ObservationContentKind.CHANGED_FILE
    ]
    assert b'"edit_outcome":"failed"' in content


# --- Cursor -------------------------------------------------------------------------------


def _cursor(event: str, workspace: str, **extra: JsonValue) -> dict[str, JsonValue]:
    return {
        "conversation_id": "cursor-conv-1",
        "generation_id": "gen-1",
        "model": "claude-opus-4-7-thinking-max",
        "hook_event_name": event,
        "cursor_version": "3.17.8",
        "workspace_roots": [workspace],
        "user_email": None,
        "transcript_path": None,
        **extra,
    }


@pytest.mark.parametrize("os_name", sorted(_WORKSPACES))
def test_cursor_write_tool_is_captured_with_relative_path(os_name: str, tmp_path: Path) -> None:
    workspace = _WORKSPACES[os_name]
    tool_input: JsonValue = {
        "path": f"{workspace}/app/module.py",
        "contents": "def handler():\n    return planted_bug(request)\n",
    }
    pre = _chunks(
        "PreToolUse",
        _cursor(
            "preToolUse", workspace, tool_name="Write", tool_use_id="t1", tool_input=tool_input
        ),
        tmp_path / "pre",
        source=_CURSOR,
        workspace=workspace,
    )
    assert pre == ()
    post = dict(
        _chunks(
            "PostToolUse",
            _cursor(
                "postToolUse",
                workspace,
                tool_name="Write",
                tool_use_id="t1",
                tool_input=tool_input,
                tool_output='{"path":"' + workspace + '/app/module.py","ok":true}',
                cwd=workspace,
                duration=12,
            ),
            tmp_path / "post",
            source=_CURSOR,
            workspace=workspace,
        )
    )
    assert set(post) == {ObservationContentKind.CHANGED_FILE}
    content = post[ObservationContentKind.CHANGED_FILE]
    assert b'"path":"app/module.py"' in content and b"planted_bug" in content
    assert b'"edit_outcome":"applied"' in content and b"alice" not in content


def test_cursor_post_tool_use_failure_is_labelled_failed(tmp_path: Path) -> None:
    workspace = _WORKSPACES["linux"]
    payload = _cursor(
        "postToolUseFailure",
        workspace,
        tool_name="Write",
        tool_use_id="t2",
        tool_input={"path": "app/module.py", "contents": "planted_bug()"},
        error_message="Permission denied",
        failure_type="permission_denied",
    )
    content = dict(_chunks("PostToolUse", payload, tmp_path, source=_CURSOR, workspace=workspace))[
        ObservationContentKind.CHANGED_FILE
    ]
    assert b'"edit_outcome":"failed"' in content


def test_cursor_after_file_edit_body_is_captured_with_relative_path(tmp_path: Path) -> None:
    workspace = _WORKSPACES["wsl"]
    payload = _cursor(
        "afterFileEdit",
        workspace,
        file_path="C:\\Users\\alice\\proj\\app\\module.py",
        edits=[{"old_string": "validate(request)", "new_string": "planted_bug(request)"}],
    )
    content = dict(_chunks("PostToolUse", payload, tmp_path, source=_CURSOR, workspace=workspace))[
        ObservationContentKind.CHANGED_FILE
    ]
    assert b'"path":"app/module.py"' in content and b"planted_bug" in content
    assert b"alice" not in content


# --- Path privacy matrix ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workspace", "value", "expected"),
    [
        # Linux and macOS POSIX.
        ("/home/alice/proj", "/home/alice/proj/src/a.py", "src/a.py"),
        ("/Users/alice/proj", "/Users/alice/proj/src/a.py", "src/a.py"),
        ("/Users/alice/proj/", "/Users/alice/proj/src/a.py", "src/a.py"),
        ("/Users/alice/proj", "/Users/alice/proj-other/a.py", None),
        ("/Users/alice/proj", "/Users/bob/a.py", None),
        ("/Users/alice/proj", "/Users/alice/proj/../secret.py", None),
        ("/Users/alice/proj", "~/proj/a.py", None),
        # Relative input stays relative and never climbs out.
        ("/Users/alice/proj", "src/a.py", "src/a.py"),
        ("/Users/alice/proj", "./src\\a.py", "src/a.py"),
        ("/Users/alice/proj", "../a.py", None),
        # Windows through WSL 2: drive letters, WSL mounts and case-insensitive drives.
        ("/mnt/c/Users/alice/proj", "/mnt/c/Users/alice/proj/src/a.py", "src/a.py"),
        ("/mnt/c/Users/alice/proj", "C:\\Users\\alice\\proj\\src\\a.py", "src/a.py"),
        ("/mnt/c/Users/alice/proj", "c:/users/ALICE/proj/src/a.py", "src/a.py"),
        ("/mnt/c/Users/alice/proj", "D:\\Users\\alice\\proj\\a.py", None),
        ("/mnt/c/Users/alice/proj", "C:relative.py", None),
        ("C:\\Users\\alice\\proj", "/mnt/c/Users/alice/proj/src/a.py", "src/a.py"),
        # UNC shares (for example \\wsl$ or a network workspace).
        ("//server/share/proj", "\\\\server\\share\\proj\\src\\a.py", "src/a.py"),
        ("//server/share/proj", "\\\\other\\share\\proj\\a.py", None),
        ("/home/alice/proj", "\\\\wsl$\\Ubuntu\\home\\alice\\proj\\a.py", None),
        # No workspace: absolute locators are never retained.
        (None, "/Users/alice/proj/a.py", None),
    ],
)
def test_workspace_relative_edit_path_matrix(
    workspace: str | None, value: str, expected: str | None
) -> None:
    assert workspace_relative_edit_path(value, workspace) == expected


def test_unified_diff_headers_are_relativized_but_removed_lines_are_not() -> None:
    sanitize = observe_hooks_module._sanitize_patch_paths  # pyright: ignore[reportPrivateUsage]
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- /Users/alice/proj/src/a.py\t2026-09-01\n"
        "+++ /Users/alice/proj/src/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "--- literal removed line\n"
        " keep\n"
    )
    result = sanitize(diff, "/Users/alice/proj")
    assert "--- src/a.py\n+++ src/a.py\n" in result
    assert "diff --git a/src/a.py b/src/a.py\n" in result
    assert "--- literal removed line\n" in result
    assert "alice" not in result
