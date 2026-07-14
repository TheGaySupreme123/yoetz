# schemas/events/receipt-recorded-1.0.0.schema.json — receipt-recorded payload schema

**Wave:** D/F | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/domain/events.md`, `src/yoetz_core/domain/receipts.md`
**Imported by:** receipt and replay tests

## Purpose

Describe the payload that records a canonical receipt object in the ledger.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/events/receipt-recorded/1.0.0`.
- Owning model: `ReceiptRecordedPayload`.

## Behavior

Closed payload object with:

- `receipt_id`;
- `subject_frontier`;
- `receipt_digest`;
- `receipt_object_id`;
- `conclusion_code`;
- `redaction_profile`.

The schema keeps the subject frontier exact and excludes any post-event result frontier from the
payload itself.

## Errors and edge cases

- Missing receipt digest/object identity fails.
- Invalid conclusion or redaction profile fails.

## Invariants

1. Receipt is recorded immutably.
2. Subject frontier is exact.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/operations/test_receipt_contract.py`

## Open questions

None.
