# schemas/operations/status-request-1.0.0.schema.json — status request schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/status.md`, `src/yoetz/kernel/projections.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the read-only status query boundary.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/status-request-1.0.0.schema.json`.
- Owning model: `StatusRequestModel`.

## Behavior

Closed request object with:

- shared envelope fields;
- session identity;
- `view`;
- optional `filter`;
- optional `at_frontier`;
- `limit`;
- `cursor`.

The schema keeps the query closed and bounded. It does not allow ad hoc predicates or arbitrary SQL
expressions.

## Errors and edge cases

- Unknown view/filter values fail.
- Future or malformed frontier values fail.

## Invariants

1. Status query is closed.
2. Page size is bounded.
3. No ad hoc predicates are allowed.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_status_contract.py`

## Open questions

None.
