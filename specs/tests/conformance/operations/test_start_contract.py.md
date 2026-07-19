# tests/conformance/operations/test_start_contract.py — start operation public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `tests/conformance/adapters/test_start_catalog_port.py.md`
**Imported by:** conformance operations tests

## Purpose

Prove the application START boundary rejects identity-invalid attach and preserves request-ID
idempotency before the facade adds any client-specific projection.

## Public surface

- `test_attach_requires_route_identity_before_catalog_reservation` — attach without session or
  reference identity is rejected before runtime provisioning.
- `test_same_request_id_with_changed_public_input_is_idempotency_conflict` — changed logical input
  under an already completed request ID fails with the stable conflict code.

## Behavior

The test executes the application operation directly and asserts that validation precedes catalog
and runtime mutation, and that request digest/idempotency behavior is exact. Service facade/CLI/MCP
projection parity is owned by the service control conformance suite.

## Errors and edge cases

- A failed identity validation that opens a runtime fails.
- Reusing a request ID with changed task input without conflict fails.

## Invariants

1. Identity validation precedes mutation.
2. Retry identity is stable.
3. Client projection is outside the persisted operation boundary.

## Tests

- `tests/conformance/operations/test_start_contract.py`

## Open questions

None.
