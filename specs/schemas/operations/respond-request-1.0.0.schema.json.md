# schemas/operations/respond-request-1.0.0.schema.json — respond request schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/respond.md`, `src/yoetz/domain/findings.md`
**Imported by:** CLI, MCP, and validation fixtures

## Purpose

Describe the request shape for responding to a finding.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/respond-request/1.0.0`.
- Owning model: `RespondRequestModel`.

## Behavior

Closed request object with:

- shared envelope fields;
- `finding_id`;
- `finding_frontier`;
- `disposition` (`acknowledged`, `rejected`, `waived`);
- optional `reason`;
- optional `waiver_scope` and `waiver_expiry` only when waived;
- optional `evidence_refs`.

Disposition-specific field gating is strict. The schema rejects waiver-only fields on non-waived
dispositions and requires a reason for rejection or waiver.

## Errors and edge cases

- Waiver fields on acknowledgement fail.
- Missing reason on rejection/waiver fails.

## Invariants

1. Disposition gates its dependent fields.
2. Finding frontier is explicit.
3. Extra keys are forbidden.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_respond_contract.py`

## Open questions

None.
