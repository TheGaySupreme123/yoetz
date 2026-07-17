# schemas/operations/respond-result-1.0.0.schema.json — respond result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/respond.md`, `src/yoetz/protocol/errors.md`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the public result shape for response operations.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/respond-result-1.0.0.schema.json`.
- Owning model: `RespondResultModel`.

## Behavior

Union of success and public-error branches. The success branch carries the stored response summary,
frontiers, disposition, and bounded evidence/waiver details. The failure branch is the shared
public-error schema.

Response/finding IDs, disposition enum, frontiers, timestamps, counts, and digests are structural.
Any echoed response reason, evidence description, waiver prose, or user/task-derived text is
content-bearing and admits only its exact original type or the common omission marker. The success
branch requires the common `agent_context` privacy projection and durable local-disclosure receipt.

## Errors and edge cases

- A result that changes waiver scope on retry fails.
- Missing fallback parity fails release.

## Invariants

1. Response scope is stable.
2. Public-error fallback is shared.
3. Result details stay bounded.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_respond_contract.py`

## Open questions

None.
