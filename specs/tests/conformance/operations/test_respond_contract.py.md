# tests/conformance/operations/test_respond_contract.py — respond public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/respond.md`, `src/yoetz_core/domain/findings.md`
**Imported by:** conformance operations tests

## Purpose

Prove finding responses behave identically across application-direct, CLI, and MCP surfaces.

## Public surface

- `test_respond_request_result_parity` — the structured response matches everywhere.
- `test_waiver_scope_and_expiry_parity` — waiver fields are identical.
- `test_response_error_parity` — invalid or stale responses fail the same way.

## Behavior

The test checks that:

- the same finding response produces the same public result;
- waiver scope and expiry are preserved exactly;
- error mapping and correlation are consistent across surfaces.

## Errors and edge cases

- A surface that broadens a waiver fails.

## Invariants

1. Response scope is bounded.
2. Public shape is surface-neutral.
3. Errors remain consistent.

## Tests

- `tests/conformance/operations/test_respond_contract.py`

## Open questions

None.
