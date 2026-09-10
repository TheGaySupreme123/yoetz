"""Host entrypoints must name start-first and failed-start intro recovery.

Issue #692's instruction-delivery slice (not a PreToolUse deny gate): Codex, Claude Code, and
Cursor catalog descriptions and skill bodies, plus SessionStart inactive context, tell a new
session to call `start` before substantive work and to ask for intro and guidance if that call
fails. Wording is not enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from yoetz.cli.hooks import INACTIVE_CONTEXT
from yoetz.mcp.descriptors import descriptor_for

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_PACKAGED_ROOT: Final = _REPO_ROOT / "src" / "yoetz" / "resources"

_HOST_SKILLS: Final = (
    ("codex", "skills/codex/yoetz/SKILL.md"),
    ("claude-code", "skills/claude-code/yoetz/SKILL.md"),
    ("cursor", "skills/cursor/yoetz/SKILL.md"),
)

_START_FIRST = "In a new session call start before research, commands, edits, or delegation"
_FAIL_INTRO = "if start fails, ask for intro and guidance"
_BODY_START_FIRST = "first Yoetz operation is `start`"
_BODY_FAIL = "ask the user for intro and guidance"


def _frontmatter_description(text: str) -> str:
    assert text.startswith("---\n"), "skill is missing YAML frontmatter"
    closing = text.find("\n---\n", 4)
    assert closing != -1, "skill frontmatter is not closed"
    block = text[4:closing]
    for line in block.splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    raise AssertionError("skill frontmatter has no description")


@pytest.mark.parametrize(("host", "relative"), _HOST_SKILLS, ids=[item[0] for item in _HOST_SKILLS])
def test_host_skill_description_names_start_first_and_failed_start_intro(
    host: str, relative: str
) -> None:
    source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
    packaged = (_PACKAGED_ROOT / relative).read_text(encoding="utf-8")
    assert packaged == source, f"{host} packaged skill drifted from source"
    description = _frontmatter_description(source)
    collapsed = " ".join(source.split())
    assert _START_FIRST in description, host
    assert _FAIL_INTRO in description, host
    assert _BODY_START_FIRST in collapsed, host
    assert _BODY_FAIL in collapsed, host


def test_session_start_inactive_context_names_start_and_intro_recovery() -> None:
    assert "call start before substantive material work" in INACTIVE_CONTEXT
    assert "If start fails, ask the user for intro and guidance" in INACTIVE_CONTEXT
    assert "do not invent a workflow or continue without a ledger task" in INACTIVE_CONTEXT


def test_start_tool_description_names_new_session_and_failed_start_intro() -> None:
    description = descriptor_for("start").description
    assert "A new session's first Yoetz operation is this call" in description
    assert "ask the user for intro and guidance" in description
    assert "continuing without a task" in description
