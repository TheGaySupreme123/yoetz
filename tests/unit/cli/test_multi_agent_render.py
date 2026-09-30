"""Human check rendering preserves preview and advisory boundaries."""

from __future__ import annotations

import pytest

from yoetz.cli.render import render_human_check
from yoetz.protocol.models import (
    CheckAdvisoryNoteModel,
    CheckChildrenPreviewModel,
    CheckMissingItemModel,
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


def test_check_lists_named_missing_items_as_a_limitation_not_a_finding() -> None:
    """Issue #907: the CLI names what an unassessable review needed and who can supply it."""

    claim = "clm_59000000-0000-4000-8000-000000000001"
    result = CheckSuccessModel.model_construct(
        verdict="insufficient_coverage",
        semantic_status="succeeded",
        semantic_reason="semantic_completed",
        findings=(),
        suppressed_count="0",
        coverage=CoverageModel.model_construct(
            known_gaps=("semantic_missing_agent_suppliable", "semantic_packet_insufficient")
        ),
        children=None,
        advisory_notes=(),
        missing_for_assessment=(
            CheckMissingItemModel.model_validate(
                {
                    "kind": "command_identity",
                    "target_refs": [],
                    "availability": "structurally_unavailable_on_this_host",
                }
            ),
            CheckMissingItemModel.model_validate(
                {
                    "kind": "verification_output",
                    "target_refs": [claim],
                    "availability": "agent_suppliable",
                }
            ),
        ),
    )
    rendered = render_human_check(result)
    assert "Findings: none\n" in rendered
    assert "Missing for assessment (the reviewer could not assess the packet):" in rendered
    assert f"- verification_output ({claim}): agent_suppliable" in rendered
    assert "- command_identity (no packet ref): structurally_unavailable_on_this_host" in rendered
