"""The bounded 0.2 recovery procedure reaches every supported host surface."""

from __future__ import annotations

from pathlib import Path
from typing import Final

_ROOT = Path(__file__).resolve().parents[3]
_HOST_RUNBOOKS: Final = (
    "docs/runbooks/codex-integration.md",
    "docs/runbooks/claude-code-integration.md",
    "docs/runbooks/cursor-integration.md",
)
_RECOVERY_EXAMPLES: Final = (
    "Read retry",
    "Ambiguous write",
    "Exact-session attach",
    "Same-pair fresh conversation",
    "Explicit sibling handoff",
)


def _collapsed(relative: str) -> str:
    return " ".join((_ROOT / relative).read_text(encoding="utf-8").split())


def test_workflow_recovery_table_preserves_the_0_2_boundaries() -> None:
    text = _collapsed("guidance/workflow.md")

    assert "## Recovery decision table (0.2)" in text
    for phrase in (
        "A read-only timeout or reconnect",
        "Any write has an unknown outcome",
        "A typed `OPERATION_PENDING` result is returned",
        "An exact held `session_id` is available",
        "The same work resumes in a fresh host conversation",
        "The same-task pair/session cannot be recovered",
        "Recovery is exhausted but no new scope is declared",
    ):
        assert phrase in text
    for phrase in (
        "new read `request_id`",
        "exact original write `request_id`",
        "exact canonical `workspace_ref` + `external_ref` pair",
        "one intentional sibling",
        "new ledger boundary",
        "predecessor's receipt",
        "old task identity is unknown",
        "operation-recovery row always wins",
    ):
        assert phrase in text
    assert "bare `task_id`" in text
    assert "remote URL" in text
    assert "invent lineage" in text


def test_each_native_host_runbook_has_bounded_recovery_examples() -> None:
    for relative in _HOST_RUNBOOKS:
        text = _collapsed(relative)
        assert "Bounded workflow recovery examples (#613)" in text
        for example in _RECOVERY_EXAMPLES:
            assert example in text, (relative, example)
        for phrase in (
            "status view=operation",
            "same request ID",
            "mode=attach",
            "mode=create_or_attach",
            "mode=create",
            "canonical",
            "mapping",
            "predecessor receipt",
            "known terminal outcome",
        ):
            assert phrase in text, (relative, phrase)


def test_native_examples_keep_their_host_binding_route_specific() -> None:
    codex = _collapsed("docs/runbooks/codex-integration.md")
    claude = _collapsed("docs/runbooks/claude-code-integration.md")
    cursor = _collapsed("docs/runbooks/cursor-integration.md")

    assert "SessionStart" in codex
    assert "Codex mapping" in codex
    assert "${CLAUDE_PROJECT_DIR}" in claude
    assert "PostToolUse" in claude
    assert "workspace_roots" in cursor
    assert "afterMCPExecution" in cursor
