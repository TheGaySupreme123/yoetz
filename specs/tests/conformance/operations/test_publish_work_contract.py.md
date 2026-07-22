# tests/conformance/operations/test_publish_work_contract.py — publish-work public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/publish_work.md`, `src/yoetz/kernel/reducers.md`
**Imported by:** conformance operations tests

## Purpose

Prove publish-work behaves the same across application-direct, CLI, and MCP surfaces and preserves
batch atomicity.

## Public surface

- `test_publish_work_request_result_parity` — structured results match.
- `test_batch_atomicity_and_idempotency` — all-or-none and retry behavior match.
- `test_unknown_event_and_error_shape_parity` — unknown/event-invalid behavior is consistent.

## Behavior

The test asserts:

- the same batch identity and result appear on each surface;
- atomic rejection or acceptance is surface-neutral;
- unknown events stay opaque and known invalid events reject the whole batch;
- summaries do not reveal more than the structured result.

## Errors and edge cases

- A wrapper that drops or reorders accepted events fails.
- Unsorted set fields such as `causal_parents` reject as `EVENT_INVALID` with
  `reason_code=unsorted_set_field` and are never auto-sorted.

## Invariants

1. Batch semantics are identical everywhere.
2. Atomicity is preserved.
3. Unknown events remain opaque.

## Tests

- `tests/conformance/operations/test_publish_work_contract.py`

## Open questions

None.
