# tests/conformance/honesty/test_receipt_wording.py — receipt wording and conclusion honesty

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/receipts.md`, `src/yoetz/kernel/receipt_builder.md`
**Imported by:** conformance honesty tests

## Purpose

Prove receipts and their rendered views never use stronger language than the frozen evidence
supports.

## Public surface

- `test_conclusion_vocabulary_is_not_upgraded` — receipt conclusions stay conservative.
- `test_rendered_text_is_no_stronger_than_document` — compact/human text stays bounded.
- `test_limitations_and_redactions_are_spelled_out` — weak points are explicit.

## Behavior

The test exercises reviewed receipt fixtures and asserts:

- conclusion wording matches the conclusion vocabulary;
- rendered text never says verified/proved/complete when the document does not;
- limitations and redactions remain visible in the honest surface.

## Errors and edge cases

- A stronger-than-evidence phrase fails.

## Invariants

1. Receipt wording is conservative.
2. Limits are explicit.
3. Renders are weaker than the document.

## Tests

- `tests/conformance/honesty/test_receipt_wording.py`

## Open questions

None.
