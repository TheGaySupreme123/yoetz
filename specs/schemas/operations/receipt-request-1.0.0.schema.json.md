# schemas/operations/receipt-request-1.0.0.schema.json — receipt request schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/receipt.md`, `src/yoetz/domain/receipts.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the public request shape for freezing and rendering a receipt.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/receipt-request/1.0.0`.
- Owning model: `ReceiptRequestModel`.

## Behavior

Closed request object with:

- shared envelope fields;
- `subject_frontier` / `expected_frontier`;
- `receipt_id` identity when the contract names it;
- `task_id`, `session_id`, and actor/client metadata;
- `format` closed to `json|markdown|text`, `include` closed to `summary|standard|full`, and
  `redaction_profile` closed to `full_local|default_local_export|redacted_share`.

The schema forbids unknown format/include profiles and keeps the frontier exact. It does not permit
the post-commit result frontier in the request body.

## Errors and edge cases

- Invalid redaction or include profiles fail.
- Future frontier values fail.
- Extra keys fail.

## Invariants

1. Receipt request identity is exact.
2. Redaction/include are closed enums.
3. Request does not include post-commit frontier.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_receipt_contract.py`

## Open questions

None.
