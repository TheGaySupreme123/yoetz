from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from yoetz.domain.findings import CheckVerdict
from yoetz.domain.receipts import (
    ReceiptConclusion,
    ReceiptDocument,
    ReceiptRedactionCategory,
    ReceiptRedactionReason,
    ReceiptSectionKey,
    receipt_document_from_json,
    receipt_document_to_json,
    receipt_weakest_coverage,
    render_receipt_compact,
    render_receipt_human,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError

_RECEIPT_FIXTURES = Path(__file__).parents[3] / "fixtures" / "receipts"


def _fixture_variants() -> tuple[tuple[str, str, dict[str, Any], str, str], ...]:
    result: list[tuple[str, str, dict[str, Any], str, str]] = []
    for path in sorted(_RECEIPT_FIXTURES.glob("*.case.json")):
        case = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        variants = cast(dict[str, dict[str, Any]], case["expected"]["variants"])
        for name, expected in sorted(variants.items()):
            if "receipt_document" not in expected:
                continue
            result.append(
                (
                    path.name,
                    name,
                    cast(dict[str, Any], expected["receipt_document"]),
                    cast(str, expected["compact_markdown"]),
                    cast(str, expected["canonical_receipt_digest"]),
                )
            )
    return tuple(result)


_VARIANTS = _fixture_variants()


def _variant(file_name: str, variant_name: str) -> dict[str, Any]:
    for candidate_file, candidate_name, document, _, _ in _VARIANTS:
        if candidate_file == file_name and candidate_name == variant_name:
            return deepcopy(document)
    raise AssertionError(f"unknown fixture variant: {file_name}/{variant_name}")


def _assert_reason(exc_info: pytest.ExceptionInfo[ProtocolValueError], reason: str) -> None:
    assert exc_info.value.reason_code == reason
    assert exc_info.value.args == (reason,)


@pytest.mark.parametrize(
    ("file_name", "variant_name", "wire", "_compact", "expected_digest"),
    _VARIANTS,
    ids=[f"{file_name}:{variant_name}" for file_name, variant_name, *_ in _VARIANTS],
)
def test_receipt_document_exact_codec_and_digest(
    file_name: str,
    variant_name: str,
    wire: dict[str, Any],
    _compact: str,
    expected_digest: str,
) -> None:
    del file_name, variant_name
    document = receipt_document_from_json(wire)
    encoded = receipt_document_to_json(document)
    assert canonical_encode(cast(JsonValue, encoded)) == canonical_encode(cast(JsonValue, wire))
    assert canonical_digest(cast(JsonValue, encoded)) == expected_digest
    assert receipt_document_from_json(encoded) == document


@pytest.mark.parametrize(
    ("file_name", "variant_name", "wire", "expected", "_digest"),
    _VARIANTS,
    ids=[f"{file_name}:{variant_name}" for file_name, variant_name, *_ in _VARIANTS],
)
def test_render_receipt_compact_never_outruns_evidence(
    file_name: str,
    variant_name: str,
    wire: dict[str, Any],
    expected: str,
    _digest: str,
) -> None:
    del file_name, variant_name
    rendered = render_receipt_compact(receipt_document_from_json(wire))
    assert rendered == expected
    assert "\n" not in rendered
    assert len(rendered) <= 32_768
    assert "fully verified" not in rendered.lower()
    assert "all work is complete" not in rendered.lower()


@pytest.mark.parametrize(
    "missing_key",
    ("receipt_id", "task_id", "session_id", "generated_at", "subject_frontier", "versions"),
)
def test_receipt_document_requires_frontier_and_versions(missing_key: str) -> None:
    wire = _variant("deterministic-current.case.json", "current_complete")
    del wire[missing_key]
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "receipt_json_shape_invalid")


def test_receipt_document_is_frozen_and_exactly_shaped() -> None:
    document = receipt_document_from_json(
        _variant("deterministic-current.case.json", "current_complete")
    )
    expected_fields = (
        "schema_version",
        "receipt_id",
        "task_id",
        "session_id",
        "generated_at",
        "subject_frontier",
        "conclusion",
        "suppressed_finding_count",
        "versions",
        "coverage",
        "findings",
        "obligations",
        "responses",
        "claim_refs",
        "evidence_refs",
        "gaps",
        "redactions",
        "sections",
    )
    assert is_dataclass(document)
    assert ReceiptDocument.__slots__ == expected_fields
    assert tuple(item.name for item in fields(document)) == expected_fields
    with pytest.raises(FrozenInstanceError):
        setattr(document, "suppressed_finding_count", 1)


def test_receipt_conclusion_vocab_is_conservative() -> None:
    assert {value.value for value in ReceiptConclusion} == {
        "no_unresolved_deterministic_findings",
        "unresolved_findings_remain",
        "insufficient_coverage",
    }
    assert all(
        "verified" not in value.value and value.value != "pass" for value in ReceiptConclusion
    )


def test_verdict_conclusion_correspondence_is_exhaustive() -> None:
    fixed = {
        CheckVerdict.ACTION_REQUIRED: ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN,
        CheckVerdict.NO_ISSUE_DETECTED: ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS,
        CheckVerdict.INSUFFICIENT_COVERAGE: ReceiptConclusion.INSUFFICIENT_COVERAGE,
    }
    incomplete = {
        False: ReceiptConclusion.INSUFFICIENT_COVERAGE,
        True: ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN,
    }
    assert set(fixed) | {CheckVerdict.INCOMPLETE_CHECK} == set(CheckVerdict)
    assert incomplete[False] is ReceiptConclusion.INSUFFICIENT_COVERAGE
    assert incomplete[True] is ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN


def test_suppressed_latest_check_cannot_claim_clear() -> None:
    wire = _variant("deterministic-current.case.json", "current_complete")
    wire["suppressed_finding_count"] = 1
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "invalid_receipt_document")

    wire["conclusion"] = "insufficient_coverage"
    document = receipt_document_from_json(wire)
    assert document.suppressed_finding_count == 1
    assert document.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE


def test_receipt_weakest_coverage_matches_supports() -> None:
    wire = _variant("semantic-advisory.case.json", "success_after_durable_receipt")
    document = receipt_document_from_json(wire)
    assert receipt_weakest_coverage(document) == document.coverage

    finding = cast(list[dict[str, Any]], wire["findings"])[0]
    finding_coverage = cast(dict[str, Any], finding["coverage"])
    finding_coverage["artifact_observation"] = "published_only"
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "receipt_coverage_mismatch")


def test_section_order_and_redaction_notes_are_stable() -> None:
    wire = _variant("redacted-gap.case.json", "redacted_object")
    document = receipt_document_from_json(wire)
    assert tuple(section.key for section in document.sections) == (
        ReceiptSectionKey.SUMMARY,
        ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
        ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
    )
    assert len(document.redactions) == 1
    assert document.redactions[0].category is ReceiptRedactionCategory.EVIDENCE_CONTENT
    assert document.redactions[0].reason is ReceiptRedactionReason.SOURCE_REDACTED
    assert document.redactions[0].count == 1
    assert (
        cast(list[dict[str, Any]], receipt_document_to_json(document)["redactions"])[0]["count"]
        == "1"
    )

    sections = cast(list[dict[str, Any]], wire["sections"])
    sections[0], sections[1] = sections[1], sections[0]
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "invalid_receipt_section_order")


@pytest.mark.parametrize(
    ("refs", "reason"),
    (
        (
            [
                "res_40000000-0000-4000-8000-000000000007",
                "evd_40000000-0000-4000-8000-000000000005",
            ],
            "unsorted_set_field",
        ),
        (
            [
                "evd_40000000-0000-4000-8000-000000000005",
                "evd_40000000-0000-4000-8000-000000000005",
            ],
            "duplicate_set_member",
        ),
        (["act_40000000-0000-4000-8000-000000000007"], "invalid_receipt_response"),
    ),
)
def test_receipt_response_rejects_invalid_evidence_result_sets(
    refs: list[str], reason: str
) -> None:
    wire = _variant("waiver-expiry.case.json", "active_exact_scope")
    response = cast(list[dict[str, Any]], wire["responses"])[0]
    response["evidence_refs"] = refs
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, reason)


def test_receipt_response_preserves_evidence_and_result_refs() -> None:
    wire = _variant("waiver-expiry.case.json", "active_exact_scope")
    refs = [
        "evd_40000000-0000-4000-8000-000000000005",
        "res_40000000-0000-4000-8000-000000000007",
    ]
    response = cast(list[dict[str, Any]], wire["responses"])[0]
    response["evidence_refs"] = refs
    document = receipt_document_from_json(wire)
    assert list(document.responses[0].evidence_refs) == refs
    encoded_responses = cast(list[dict[str, Any]], receipt_document_to_json(document)["responses"])
    assert encoded_responses[0]["evidence_refs"] == refs


def test_receipt_section_items_are_required_and_exact() -> None:
    wire = _variant("deterministic-current.case.json", "current_complete")
    section = cast(list[dict[str, Any]], wire["sections"])[0]
    del section["items"]
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "invalid_receipt_section")

    wire = _variant("deterministic-current.case.json", "current_complete")
    document = receipt_document_from_json(wire)
    assert document.sections[0].items == ()
    encoded_sections = cast(list[dict[str, Any]], receipt_document_to_json(document)["sections"])
    assert "items" in encoded_sections[0]
    assert encoded_sections[0]["items"] == []


def test_every_explicit_gap_occurs_in_coverage_summary() -> None:
    wire = _variant("redacted-gap.case.json", "locked_key")
    coverage = cast(dict[str, Any], wire["coverage"])
    coverage["known_gaps"] = []
    with pytest.raises(ProtocolValueError) as exc_info:
        receipt_document_from_json(wire)
    _assert_reason(exc_info, "receipt_gap_not_in_coverage")


def test_semantic_review_not_configured_receipt_states_not_run() -> None:
    """Requirement: receipt with semantic evaluator not configured discloses not-run."""

    from yoetz.domain.receipts import (
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    )

    wire = _variant("deterministic-current.case.json", "current_complete")
    wire["conclusion"] = "insufficient_coverage"
    coverage = cast(dict[str, Any], wire["coverage"])
    coverage["known_gaps"] = [SEMANTIC_REVIEW_NOT_CONFIGURED_GAP]
    coverage["ledger_freshness"] = "partial"
    wire["gaps"] = [{"code": SEMANTIC_REVIEW_NOT_CONFIGURED_GAP, "subject_refs": []}]
    for section in cast(list[dict[str, Any]], wire["sections"]):
        if section["key"] == "limitations_and_coverage":
            section["body"] = (
                "Semantic relevance review was not run. "
                f"Coverage is limited by: {SEMANTIC_REVIEW_NOT_CONFIGURED_GAP}."
            )
            section["items"] = [SEMANTIC_REVIEW_NOT_CONFIGURED_GAP]
        if section["key"] == "summary":
            section["body"] = "Coverage is insufficient at frontier 7."
    document = receipt_document_from_json(wire)
    assert SEMANTIC_REVIEW_NOT_CONFIGURED_GAP in {gap.code for gap in document.gaps}
    assert OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP not in {gap.code for gap in document.gaps}
    rendered = render_receipt_compact(document)
    assert "semantic relevance review was not run" in rendered
    assert "optional semantic review was blocked" not in rendered
    assert "coverage is insufficient" in rendered
    assert "no unresolved deterministic issue was found in the published record" not in rendered


def test_semantic_relevance_review_not_run_gap_shares_not_run_wording() -> None:
    """Evaluator failure/timeout uses the same truthful not-run disclosure family."""

    from yoetz.domain.receipts import SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP

    wire = _variant("deterministic-current.case.json", "current_complete")
    wire["conclusion"] = "insufficient_coverage"
    coverage = cast(dict[str, Any], wire["coverage"])
    coverage["known_gaps"] = [SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP]
    coverage["ledger_freshness"] = "partial"
    wire["gaps"] = [{"code": SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP, "subject_refs": []}]
    for section in cast(list[dict[str, Any]], wire["sections"]):
        if section["key"] == "limitations_and_coverage":
            section["body"] = (
                "Semantic relevance review was not run. "
                f"Coverage is limited by: {SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP}."
            )
            section["items"] = [SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP]
        if section["key"] == "summary":
            section["body"] = "Coverage is insufficient at frontier 7."
    rendered = render_receipt_compact(receipt_document_from_json(wire))
    assert "semantic relevance review was not run" in rendered


def test_semantic_review_not_run_never_hides_unresolved_findings() -> None:
    """A semantic gap must not turn an unresolved deterministic receipt into a clean claim."""

    from yoetz.domain.receipts import SEMANTIC_REVIEW_NOT_CONFIGURED_GAP

    wire = _variant("deterministic-current.case.json", "current_complete")
    wire["conclusion"] = "unresolved_findings_remain"
    coverage = cast(dict[str, Any], wire["coverage"])
    coverage["known_gaps"] = [SEMANTIC_REVIEW_NOT_CONFIGURED_GAP]
    coverage["ledger_freshness"] = "partial"
    wire["gaps"] = [{"code": SEMANTIC_REVIEW_NOT_CONFIGURED_GAP, "subject_refs": []}]
    document = receipt_document_from_json(wire)

    rendered = render_receipt_compact(document)

    assert "unresolved finding" in rendered
    assert "semantic relevance review was not run" in rendered
    assert "no unresolved deterministic issue" not in rendered
    assert "blocked before dispatch" not in rendered


def test_render_receipt_human_projects_sections_and_advisory_count() -> None:
    """#437/#429: markdown/text human_text carries sections, not a compact one-liner."""

    from yoetz.domain.findings import FindingKind, FindingOrigin

    wire = _variant("unresolved-findings.case.json", "mixed_open_responses")
    wire["sections"][0]["coverage_note"] = "Coverage note"
    document = receipt_document_from_json(wire)
    markdown = render_receipt_human(document, markdown=True)
    text = render_receipt_human(document, markdown=False)
    assert "## Limitations" in markdown
    assert "Limitations" in text
    assert "\n" in markdown
    assert document.sections[0].body in markdown
    assert document.sections[0].body in text
    assert f"- {document.sections[1].items[0]}" in markdown
    assert f"- {document.sections[1].items[0]}" in text
    assert "Coverage note" in markdown
    assert "Coverage note" in text
    assert "do not by themselves select unresolved_findings_remain" not in markdown
    assert all(
        finding.kind is not FindingKind.LEDGER_STALE_OR_INCOMPLETE for finding in document.findings
    )
    assert FindingOrigin.DETERMINISTIC in {finding.origin for finding in document.findings}

    advisory = receipt_document_from_json(
        _variant("semantic-advisory.case.json", "success_after_durable_receipt")
    )
    advisory_text = render_receipt_human(advisory, markdown=True)
    assert "## Limitations" in advisory_text
    assert "Semantic review completed" in advisory_text
    # material_limitation_omitted is actionable, so no extra coverage-limitation appendix.
    assert "do not by themselves select unresolved_findings_remain" not in advisory_text

    stale = _variant("unresolved-findings.case.json", "mixed_open_responses")
    stale["findings"][0]["kind"] = "ledger_stale_or_incomplete"
    stale["findings"][0]["priority"] = 3
    stale_document = receipt_document_from_json(stale)
    stale_text = render_receipt_human(stale_document, markdown=True)
    assert "do not by themselves select unresolved_findings_remain" in stale_text


def test_render_receipt_human_marks_bounded_truncation() -> None:
    """#437: the wire bound cannot silently erase later receipt sections."""

    wire = _variant("unresolved-findings.case.json", "mixed_open_responses")
    wire["sections"][0]["body"] = "x" * 32_768
    document = receipt_document_from_json(wire)

    rendered = render_receipt_human(document, markdown=True)

    assert len(rendered) == 32_768
    assert rendered.endswith("remaining canonical section content is omitted.]")
