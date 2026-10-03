"""Shared public projections for the bounded review-input manifest."""

from __future__ import annotations

from yoetz.domain.review_input_render import (
    render_missing_for_assessment_lines,
    render_review_input_manifest_compact,
    render_review_input_manifest_compat_line,
    render_review_input_manifest_coverage_note,
    render_review_input_manifest_lines,
)

_DIGEST = "sha256:" + "a" * 64


def _section(status: str, *, digest: str | None = _DIGEST) -> dict[str, object]:
    return {
        "status": status,
        "source_refs": ["evt_00000000-0000-4000-8000-000000000001"],
        "item_ids": ["item-1"],
        "omitted_refs": [],
        "omission_reasons": [],
        "revision": 7,
        "content_digest": digest,
        "content_bytes": 123,
    }


def _manifest(*, specification_status: str = "complete") -> dict[str, object]:
    return {
        "schema": "yoetz.review-input-manifest/1",
        "specification": _section(specification_status),
        "current_diff": _section("partial"),
        "caller_evidence": _section("missing", digest=None),
        "latest_verification": _section("complete"),
        "prior_finding_context": _section("not_selected", digest=None),
        "phase": "provider_bound",
        "missing_inputs": [
            {
                "kind": "verification_output",
                "status": "pending",
                "target_refs": ["res_00000000-0000-4000-8000-000000000002"],
                "supplied_refs": [],
            }
        ],
        "selected_item_count": 3,
        "selected_excerpt_bytes": 246,
        "omitted_item_count": 2,
    }


def test_manifest_renderer_keeps_distinct_statement_state_and_digests() -> None:
    lines = render_review_input_manifest_lines(_manifest(specification_status="title_only"))

    assert lines[0] == (
        "Review input manifest: provider_bound; selected items 3; "
        "selected excerpt bytes 246; omitted items 2."
    )
    assert any(line.startswith("- specification: title_only;") for line in lines)
    assert any(_DIGEST in line for line in lines)
    assert any("Manifest missing inputs: verification_output=pending" in line for line in lines)
    assert "Provider-bound review input: specification complete" in (
        render_review_input_manifest_compat_line(_manifest())
    )
    assert "provider_bound" in render_review_input_manifest_compact(_manifest())


def test_manifest_renderer_rejects_untrusted_or_incomplete_shapes() -> None:
    assert render_review_input_manifest_lines({"schema": "other"}) == ()
    malformed = _manifest()
    malformed["selected_item_count"] = "3"
    assert render_review_input_manifest_lines(malformed) == ()


def test_receipt_note_lists_missing_availability_without_content() -> None:
    note = render_review_input_manifest_coverage_note(
        _manifest(),
        [
            {
                "kind": "task_statement",
                "target_refs": ["clm_00000000-0000-4000-8000-000000000003"],
                "availability": "agent_suppliable",
            }
        ],
    )

    assert note is not None
    assert "Review input coverage (metadata only):" in note
    assert "specification: complete" in note
    assert "task_statement (1 target refs): agent_suppliable" in note
    assert len(note.encode("utf-8")) <= 4096


def test_missing_renderer_preserves_refs_for_cli_and_mcp_surfaces() -> None:
    lines = render_missing_for_assessment_lines(
        [
            {
                "kind": "current_diff_for_path",
                "target_refs": ["evd_00000000-0000-4000-8000-000000000004"],
                "availability": "structurally_unavailable_on_this_host",
            }
        ]
    )

    assert lines == (
        "Missing for assessment (the reviewer could not assess the packet):",
        "- current_diff_for_path (evd_00000000-0000-4000-8000-000000000004): "
        "structurally_unavailable_on_this_host",
    )
