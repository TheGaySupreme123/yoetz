# schemas/receipts/receipt-document-1.0.0.schema.json — receipt document schema

**Wave:** B/D/F | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/receipts.md`, `src/yoetz/kernel/receipt_builder.md`
**Imported by:** receipt builder, storage, and parity fixtures

## Purpose

Describe the canonical immutable receipt document stored as an encrypted object.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/receipts/receipt-document-1.0.0.schema.json`.
- Owning model: `ReceiptDocument`.
- Reusable fragment `$defs/receipt_conclusion`, the schema form of domain
  `ReceiptConclusion`.

## Behavior

Closed object with required fields:

- `receipt_id`, `task_id`, `session_id`, `generated_at`;
- `subject_frontier`;
- `conclusion`;
- `suppressed_finding_count`, a required JSON integer `0..9_007_199_254_740_991` copied from the
  applicable latest tested state (zero when none were suppressed);
- `versions`;
- `coverage`;
- findings, obligations, responses, claims/evidence refs, gaps/redactions;
- section list / render structure where the contract stores it.

`conclusion` references the exact local `$defs/receipt_conclusion` enum:
`no_unresolved_deterministic_findings|unresolved_findings_remain|insufficient_coverage`. This
fragment is the sole schema definition of `ReceiptConclusion`; receipt operation/event schemas
reuse the exact absolute `$id#/$defs/receipt_conclusion` reference and may not restate or extend the
tokens.

The schema intentionally excludes a post-append `result_frontier`; that lives in the outer operation
result and would create a self-reference. Extra properties are forbidden.

## Errors and edge cases

- Missing provenance or coverage fails.
- A conclusion outside the three-value domain enum fails.
- A missing, negative, noninteger, or count inconsistent with the projection fails.
- Any stronger-than-evidence conclusion shape fails.

## Invariants

1. Receipt documents are immutable.
2. Result frontier is excluded.
3. Coverage and gaps are explicit.

## Tests

- `tests/unit/domain/test_receipts.py`
- `tests/conformance/operations/test_receipt_contract.py`

## Open questions

None.
