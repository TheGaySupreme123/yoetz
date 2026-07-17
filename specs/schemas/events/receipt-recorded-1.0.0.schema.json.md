# schemas/events/receipt-recorded-1.0.0.schema.json — receipt-recorded payload schema

**Wave:** D/F | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/receipts.md`,
`schemas/receipts/receipt-document-1.0.0.schema.json`
**Imported by:** receipt and replay tests

## Purpose

Describe the payload that records a canonical receipt object in the ledger.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/receipt-recorded/1.0.0`.
- Owning model: `ReceiptRecordedPayload`.

## Behavior

Closed payload object with:

- `receipt_id`;
- `subject_frontier`;
- `receipt_digest`;
- `receipt_object_id`;
- `conclusion_code`;
- `redaction_profile`.

`conclusion_code` uses the exact offline `$ref`
`https://schemas.yoetz.dev/0.1/receipts/receipt-document/1.0.0#/$defs/receipt_conclusion`; this event
schema does not maintain a second conclusion vocabulary.

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
