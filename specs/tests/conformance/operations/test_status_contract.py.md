# tests/conformance/operations/test_status_contract.py — status public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/status.md`, `src/yoetz/kernel/projections.md`
**Imported by:** conformance operations tests

## Purpose

Prove status is read-only and returns the same canonical projection page across surfaces.

## Public surface

- `test_status_request_result_parity` — structured pages match.
- `test_status_is_read_only` — no state mutation occurs.
- `test_status_frontier_and_pagination_parity` — lag, page size, and frontier are exact.

## Behavior

The test asserts:

- status does not write events or mutate the projection;
- requested/head/effective frontiers and page contents match;
- latest/current vs lagged cache disclosure is surfaced honestly;
- CLI and MCP wrappers do not alter the page shape.

## Errors and edge cases

- A status call that mutates state fails.

## Invariants

1. Status is read-only.
2. Frontier disclosure is exact.
3. Page content is canonical.

## Tests

- `tests/conformance/operations/test_status_contract.py`

## Open questions

None.
