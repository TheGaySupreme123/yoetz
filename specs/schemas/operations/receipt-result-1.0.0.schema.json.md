# schemas/operations/receipt-result-1.0.0.schema.json — receipt result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/receipt.md`, `src/yoetz/domain/receipts.md`,
`src/yoetz/protocol/errors.md`, `schemas/receipts/receipt-document-1.0.0.schema.json`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the public result shape for receipts, including both subject and post-commit frontiers.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/receipt-result-1.0.0.schema.json`.
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

The conclusion code is the exact offline `$ref`
`https://schemas.yoetz.dev/0.1/receipts/receipt-document-1.0.0.schema.json#/$defs/receipt_conclusion`; it is not
a separately restated string enum.

The `versions` member is the exact 11-field `ReceiptVersionSlice` from the receipt-document
schema. Its fields are `package_name`, `package_version`, `protocol_version`, `engine_version`,
`projection_version`, `object_format_version`, `catalog_schema_version`, `bundle_schema_version`,
`policy_versions`, `schema_versions`, and `resource_manifest_digest`. The receipt-result schema
keeps the same closed definition in its local `$defs` and references it with
`#/$defs/version_slice`; the two nested entry records are likewise exact. The externally referenced
receipt document is resolved through the packaged catalog. Neither path performs DNS or HTTP.

Receipt/task/session/object IDs, frontiers, conclusion/redaction enums, digests, counts, coverage,
and version identities are structural. Obligation summaries, finding/response/gap prose, canonical
section prose, and human wording are content-bearing. `human_text` admits its exact string or the
common omission marker. A JSON `document` remains the exact canonical stored ReceiptDocument:
because `receipt_digest` binds those bytes, its in-document strings never admit field-level
omission markers. The local-disclosure gate returns that whole document only when every present
content category is authorized and otherwise fails before success serialization. The success
branch requires the common `agent_context` privacy projection and durable local-disclosure receipt.
This verification `receipt` remains distinct from the privacy receipt named by the projection.

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
