# schemas/receipts/receipt-document-1.0.0.schema.json — receipt document schema

**Wave:** B/D/F | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/domain/receipts.md`, `src/yoetz_core/kernel/receipt_builder.md`
**Imported by:** receipt builder, storage, and parity fixtures

## Purpose

Describe the canonical immutable receipt document stored as an encrypted object.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/receipts/receipt-document/1.0.0`.
- Owning model: `ReceiptDocument`.

## Behavior

Closed object with required fields:

- `receipt_id`, `task_id`, `session_id`, `generated_at`;
- `subject_frontier`;
- `conclusion`;
- `versions`;
- `coverage`;
- findings, obligations, responses, claims/evidence refs, gaps/redactions;
- section list / render structure where the contract stores it.

The schema intentionally excludes a post-append `result_frontier`; that lives in the outer operation
result and would create a self-reference. Extra properties are forbidden.

## Errors and edge cases

- Missing provenance or coverage fails.
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
