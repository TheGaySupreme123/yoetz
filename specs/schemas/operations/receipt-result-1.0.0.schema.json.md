# schemas/operations/receipt-result-1.0.0.schema.json — receipt result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/receipt.md`, `src/yoetz_core/domain/receipts.md`,
`src/yoetz_core/protocol/errors.md`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the public result shape for receipts, including both subject and post-commit frontiers.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/operations/receipt-result/1.0.0`.
- Owning model: `ReceiptResultModel`.

## Behavior

Union of success and public-error branches. The success branch carries:

- `receipt_id`, `task_id`, `session_id`;
- `subject_frontier` and `result_frontier`;
- canonical receipt digest/object identity;
- conclusion code and redaction profile;
- rendered or structured receipt payload according to the requested format.

The schema must admit the canonical machine document and the bounded human derivative without
changing the stored truth.

Receipt/task/session/object IDs, frontiers, conclusion/redaction enums, digests, counts, coverage,
and version identities are structural. Claim/finding/evidence text, human wording, task labels, and
other user/task-derived receipt leaves are content-bearing and admit only their exact original type
or the common omission marker. The success branch requires the common `agent_context` privacy
projection and durable local-disclosure receipt. This verification `receipt` remains distinct from
the privacy receipt named by the projection.

## Errors and edge cases

- A result that omits `result_frontier` fails.
- A schema that rejects the common error fallback fails.

## Invariants

1. Subject and result frontiers are both explicit.
2. Receipt digest/object identity are preserved.
3. Public-error fallback is shared.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_receipt_contract.py`

## Open questions

None.
