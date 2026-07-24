# schemas/operations/check-request-1.0.0.schema.json — check request schema

**Wave:** D/E | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/domain/findings.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the request shape for the `check` operation, including the frozen case and evaluation mode.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/check-request-1.0.0.schema.json`.
- Owning model: `CheckRequestModel`.

## Behavior

Closed request object with:

- shared envelope fields;
- `expected_frontier`;
- check `mode`, optional on the wire: a present value is a closed enum member, and an omitted one
  resolves through the configured verification policy in the application facade;
- policy references / policy pack selectors;
- requested maximum findings;
- actor/client metadata.

The schema keeps the frozen case boundary explicit. It does not allow arbitrary filters or free-form
policy names beyond the registry contract.

## Errors and edge cases

- Missing frontier or invalid mode fails. A missing `mode` does not fail; it is resolved from
  policy, never defaulted inside the schema.
- Extra keys fail.

## Invariants

1. Frozen-case identity is explicit.
2. Mode is a closed enum when present, and is never `required`.
3. Policy selection stays bounded.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
