# tests/conformance/operations/test_start_contract.py — start operation public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/start.md`, `tests/conformance/adapters/test_start_catalog_port.py.md`
**Imported by:** conformance operations tests

## Purpose

Prove the public start operation behaves identically across application-direct, CLI, and MCP
surfaces.

## Public surface

- `test_start_request_result_parity` — the same request yields the same structured result.
- `test_start_error_and_retry_contract` — failures and retries map to the same public code/shape.
- `test_start_human_summary_is_weaker_than_json` — text summary never outruns structured truth.

## Behavior

The test executes the start operation through each surface and asserts:

- exact request/result parity;
- the same IDs, route, and result identity are surfaced everywhere;
- errors are mapped consistently;
- human summaries remain weaker than structured JSON.

## Errors and edge cases

- A CLI or MCP wrapper that mutates the public result fails.

## Invariants

1. Public start behavior is surface-neutral.
2. Structured truth outranks summaries.
3. Retry identity is stable.

## Tests

- `tests/conformance/operations/test_start_contract.py`

## Open questions

None.
