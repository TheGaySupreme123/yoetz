# tests/integration/storage/test_append_and_replay.py — append atomicity and replay parity

**Wave:** C | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/repository.md`, `src/yoetz/adapters/memory/ledger.md`,
`src/yoetz/kernel/reducers.md`
**Imported by:** integration storage tests

## Purpose

Prove append batches are atomic, idempotent, and replay to the same projection as the reference
model.

## Public surface

- `test_single_and_max_batch_boundaries` — 1 event and 100-event batches succeed at the boundary.
- `test_same_and_equivalent_retry_is_stable` — canonical-equivalent retries return the same result.
- `test_reused_request_with_changed_identity_conflicts` — logical mismatch under the same request ID
  fails closed.
- `test_wrong_sequence_predecessor_and_invalid_known_event_reject_batch` — atomic rejection works.
- `test_replay_after_append_matches_reference_projection` — durable replay equals the memory
  oracle.

## Behavior

The test uses real append, load, and replay paths. It asserts:

- batches commit atomically or not at all;
- duplicate or skipped writer sequence/predecessor rules fail cleanly;
- unknown events are preserved as opaque gaps;
- projection replay after append equals the reference model’s canonical digest.

## Errors and edge cases

- A partial logical append that still reports success fails the test.
- A replay that depends on SQLite-specific row order fails the test.

## Invariants

1. Append is atomic.
2. Retry identity is stable.
3. Durable replay matches the pure reducer.

## Tests

- `tests/integration/storage/test_append_and_replay.py`

## Open questions

None.
