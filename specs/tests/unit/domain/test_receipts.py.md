# tests/unit/domain/test_receipts.py — receipt document and compact render behavior

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/receipts.md`, `src/yoetz/protocol/coverage.py`,
`src/yoetz/domain/findings.py`
**Imported by:** the domain and kernel unit suite

## Purpose

Lock the receipt document’s canonical structure and the compact render’s honesty rules.

## Public surface

- `test_receipt_document_requires_frontier_and_versions` — canonical documents reject missing core
  provenance.
- `test_receipt_conclusion_vocab_is_conservative` — the public conclusion set stays small.
- `test_verdict_conclusion_correspondence_is_exhaustive` — all four check verdicts, including both
  `incomplete_check` branches, obey the unchanged-frontier correspondence.
- `test_suppressed_latest_check_cannot_claim_clear` — nonzero suppression yields unresolved or
  insufficient, never the strongest conclusion.
- `test_receipt_weakest_coverage_matches_supports` — document coverage is the weakest support.
- `test_render_receipt_compact_never_outruns_evidence` — compact text is no stronger than the
  document.
- `test_section_order_and_redaction_notes_are_stable` — presentation order and redaction behavior
  stay fixed.

## Behavior

The suite proves:

- receipt documents are frozen and versioned;
- receipt conclusions do not claim verification;
- every verdict/conclusion correspondence and capped-check branch is exhaustive;
- weakest coverage is derived from supports, not guessed;
- compact rendering can omit detail but cannot strengthen the statement;
- redaction leaves an honest trace in the document.

## Errors and edge cases

- A render that sounds stronger than the document fails.
- A document with missing provenance or coverage summary fails.

## Invariants

1. Receipt documents are immutable truth records.
2. Rendered views are always weaker or equal to the document.
3. Coverage is computed from supports, not prose.

## Tests

- `tests/unit/domain/test_receipts.py`

## Open questions

None.
