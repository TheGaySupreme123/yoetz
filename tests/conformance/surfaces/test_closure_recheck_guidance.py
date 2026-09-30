"""Closure guidance never sends an agent on a frontier hunt or into a recheck that cannot matter.

Issue #911 (DeepSWE 2026-09 post-mortem): agents probed historical frontiers to answer a finding
because the guidance said to use "the result frontier of the check that returned it"; they
rechecked after `work_closed`, after answering the check's own findings, and after answering the
observation advisory; and they ran a `deterministic_only` "fallback" after `insufficient_packet`.
Every one of those rechecks returned the identical result. These cases pin the corrected closure
order in the shared guidance and every host skill, while keeping the productive recheck after a
recorded repair.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_GUIDANCE: Final = _REPO_ROOT / "guidance"
_SKILLS: Final = tuple(
    _REPO_ROOT / "skills" / host / "yoetz" / "SKILL.md"
    for host in ("claude-code", "codex", "cursor", "portable")
)


def _collapsed(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    start = text.index(f"\n{heading}\n")
    rest = text[start + len(heading) + 2 :]
    pattern = re.compile(rf"^#{{1,{len(level)}}} ", re.MULTILINE)
    ends = [m.start() for m in pattern.finditer(rest)]
    return " ".join(rest[: ends[0] if ends else len(rest)].split())


def test_repair_then_finish_orders_findings_check_receipt_then_work_closed() -> None:
    section = _section(_GUIDANCE / "coverage-and-receipts.md", "## Repair then finish")
    before = section.index("Before the final check, read `status view=findings`")
    after = section.index(
        "After the final check, respond only to the findings it returned that are still unanswered"
    )
    closing = section.index("Request `receipt`, then publish `work_closed`")
    assert before < after < closing
    for phrase in (
        "Responses to the check's own findings, an acknowledgement of an observation-authored "
        "non-actionable finding, and `work_closed` need no recheck.",
        # The convergence guard: removing futile rechecks never removes the productive one.
        "a recorded repair (it always gets at least one re-check)",
        "`work_closed` never requires another check",
    ):
        assert phrase in section, phrase


def test_lifecycle_closure_and_limitation_acknowledgements_are_not_material() -> None:
    attribution = _section(_GUIDANCE / "coverage-and-receipts.md", "## Coverage attribution")
    assert "acknowledgements of observation-authored non-actionable findings" in attribution
    assert "`work_closed` (and the other work-state and delegation lifecycle events) never " in (
        attribution
    )
    # Productive rechecks stay: any other answer to a finding the check did not return is
    # material, including a rejection of an observation-authored finding.
    assert "any other response to a finding the check did not return" in attribution
    assert "a rejection of an observation-authored finding included" in attribution


def test_observation_limitation_findings_never_block_and_need_no_wait() -> None:
    findings = _section(_GUIDANCE / "coverage-and-receipts.md", "## Findings and responses")
    for phrase in (
        "Observation-authored findings that are not actionable (priority 3",
        "They never block the receipt and never count in `unanswered_finding_count` or "
        "`findings_unanswered`, so they need no response.",
        "acknowledge one once with `acknowledged`",
        "never resolves the finding",
        "a standing gap such as `unpaired_event` does not recover within the session",
    ):
        assert phrase in findings, phrase
    workflow = _collapsed(_GUIDANCE / "workflow.md")
    assert "wait for drain to recover" not in workflow
    assert "wait only while it reports lag or a drain backlog" in workflow


@pytest.mark.parametrize(
    "name",
    (
        "coverage-and-receipts.md",
        "workflow.md",
        "request-templates.md",
        "publication-policy.md",
        "agent-instructions.md",
    ),
)
def test_no_guidance_requires_the_exact_check_result_frontier(name: str) -> None:
    text = _collapsed(_GUIDANCE / name)
    assert "Use `finding_frontier` = the result frontier" not in text
    assert "at the result frontier of the check that returned it" not in text
    assert "The response frontier is the result frontier" not in text
    assert "current status frontier" in text


def test_respond_template_names_the_frontier_to_use() -> None:
    text = _collapsed(_GUIDANCE / "request-templates.md")
    assert (
        "Use `finding_frontier` = any frontier at or after the finding's own record: the item's "
        "`finding_frontier` from `status view=findings` when it carries one, otherwise the "
        "current status frontier"
    ) in text
    assert "No historical frontier search is needed." in text


@pytest.mark.parametrize(
    "name", ("coverage-and-receipts.md", "workflow.md", "agent-instructions.md")
)
def test_insufficient_packet_goes_to_the_receipt_not_a_deterministic_fallback(name: str) -> None:
    text = _collapsed(_GUIDANCE / name)
    assert "insufficient_packet" in text
    assert "deterministic_only` fallback" in text
    assert "go to the receipt" in text


def test_the_initialize_safety_floor_carries_the_closure_order() -> None:
    text = _collapsed(_GUIDANCE / "agent-instructions.md")
    assert "- `respond` — once per finding; `finding_frontier` may be the current status" in text
    assert "Answer each unanswered finding before the final check." in text
    assert "answer its still-unanswered findings" in text
    assert "Only a repair or other material record needs a recheck; those answers and " in text
    assert "`work_closed` do not." in text
    assert "Non-actionable observation-authored findings need no answer." in text


@pytest.mark.parametrize("path", _SKILLS, ids=lambda path: path.parent.parent.name)
def test_every_host_skill_carries_the_same_closure_order(path: Path) -> None:
    text = _collapsed(path)
    assert "at its result frontier" not in text
    assert "at the result frontier" not in text
    assert "current status frontier" in text
    assert "`work_closed`" in text
    assert "need no answer" in text
