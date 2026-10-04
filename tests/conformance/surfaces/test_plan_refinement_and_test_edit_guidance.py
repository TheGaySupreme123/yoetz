"""Guidance keeps plans current and pre-existing test edits attributable (#970/#973)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parents[3]
_PLAN_RULE: Final = "before the first material edit"
_SKILLS: Final = tuple(
    _ROOT / "skills" / host / "yoetz" / "SKILL.md"
    for host in ("codex", "claude-code", "cursor", "portable")
)


def _collapsed(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_core_startup_and_publication_describe_plan_refinement() -> None:
    for relative in (
        "guidance/agent-instructions.md",
        "guidance/startup.md",
        "guidance/publication.md",
    ):
        text = _collapsed(_ROOT / relative)
        assert _PLAN_RULE in text
        assert "bounded exploration" in text
        assert "plan_revised" in text
        assert "testable obligation" in text
    for skill in _SKILLS:
        assert "yoetz://guidance/agent-instructions.md" in _collapsed(skill)


def test_publication_topic_requires_the_closed_test_change_marker() -> None:
    text = _collapsed(_ROOT / "guidance/publication.md")
    assert "Do not change an existing test's assertion or expectation" in text
    assert "yoetz:test-change:<action_id>:sha256:<path_digest>" in text
    assert "lowercase SHA-256 of the captured path's UTF-8 spelling" in text
    assert "generic prose does not clear the structural edit finding" in text
