"""Human check rendering preserves preview and advisory boundaries."""

from __future__ import annotations

import pytest

from yoetz.cli.render import render_human_check
from yoetz.protocol.models import (
    CheckAdvisoryNoteModel,
    CheckChildrenPreviewModel,
    CheckSuccessModel,
    CoverageModel,
)


@pytest.mark.parametrize("label", ["recorded", "preview"])
def test_check_keeps_child_facts_and_project_advice_outside_findings(label: str) -> None:
    child = "tsk_59000000-0000-4000-8000-000000000002"
    project = "prj_59000000-0000-4000-8000-000000000001"
    # This pure renderer consumes an already projected result. Wire acceptance is exercised by
    # the public workflow and model suites; only the fields it reads are constructed here.
    result = CheckSuccessModel.model_construct(
        verdict="insufficient_coverage",
        semantic_status="not_requested",
        semantic_reason="deterministic_mode",
        findings=(),
        suppressed_count="0",
        coverage=CoverageModel.model_construct(known_gaps=()),
        children=CheckChildrenPreviewModel.model_validate(
            {
                "label": label,
                "tested_manifest_frontier": {"sequence": "7", "head_digest": "sha256:" + "a" * 64},
                "items": [
                    {
                        "child_task_id": child,
                        "origin": "self_registered",
                        "acceptance": "pending",
                        "work_state": "open",
                        "session_health": "contact_lost",
                        "rollup_state": "annotation",
                        "blocking_conditions": [],
                    }
                ],
            }
        ),
        advisory_notes=(
            CheckAdvisoryNoteModel.model_validate(
                {
                    "kind": "live_member_present",
                    "project_id": project,
                    "task_ids": [child],
                    "count": "1",
                }
            ),
        ),
    )

    rendered = render_human_check(result)
    assert rendered.startswith("Verdict: insufficient_coverage\n")
    assert "Findings: none\n" in rendered
    assert f"Child dependencies ({label}):" in rendered
    assert f"{child}: self_registered, pending; work open, session contact_lost" in rendered
    assert "Tested manifest frontier: 7" in rendered
    assert ("Preview facts do not change the recorded check." in rendered) == (label == "preview")
    assert "Project advice (does not affect the verdict):" in rendered
    assert f"live_member_present: 1; project {project}; tasks {child}" in rendered


def test_check_renders_the_finding_checklist_with_structural_tokens_only() -> None:
    """Issue #905: ``[ ] F-1 ... open (2/2)`` lines and one closed "Next:" sentence."""

    from yoetz.protocol.models import CheckFindingChecklistModel

    checklist = CheckFindingChecklistModel.model_validate(
        {
            "attempt_budget": "2",
            "counts": {
                "acknowledged_not_done": "1",
                "open": "1",
                "open_at_budget": "1",
                "rejection_accepted": "0",
                "verified_resolved": "1",
            },
            "next": "decide_at_budget",
            "items": [
                {
                    "finding_id": "fnd_59000000-0000-4000-8000-000000000001",
                    "todo_state": "open",
                    "review_rounds": "2",
                },
                {
                    "finding_id": "fnd_59000000-0000-4000-8000-000000000002",
                    "todo_state": "verified_resolved",
                    "review_rounds": "0",
                },
                {
                    "finding_id": "fnd_59000000-0000-4000-8000-000000000003",
                    "todo_state": "acknowledged_not_done",
                    "review_rounds": "1",
                },
            ],
        }
    )
    result = CheckSuccessModel.model_construct(
        verdict="no_issue_detected",
        semantic_status="not_requested",
        semantic_reason="deterministic_mode",
        findings=(),
        suppressed_count="0",
        coverage=CoverageModel.model_construct(known_gaps=()),
        children=None,
        advisory_notes=(),
        finding_checklist=checklist,
    )
    rendered = render_human_check(result)
    assert "To-do list (review-round budget 2):" in rendered
    assert "- [ ] F-1 fnd_59000000-0000-4000-8000-000000000001 open (2/2)" in rendered
    assert "- [x] F-2 fnd_59000000-0000-4000-8000-000000000002 verified_resolved" in rendered
    assert "- [~] F-3 fnd_59000000-0000-4000-8000-000000000003 acknowledged_not_done" in rendered
    assert "Next: An open finding reached the review-round budget" in rendered
