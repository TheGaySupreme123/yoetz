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
- `test_memory_check_resume_survives_state_cache_loss` — clearing the process-local frozen-case
  cache and reconstructing the adapter recovers both reserved and local-ready work from the sole
  current pointer without publishing another resume object.
- `test_sqlite_reopen_recovers_pending_check_from_sole_resume_pointer` — a file-backed reopen after
  either reservation or deterministic-result publication verified-decodes the exact case, renews
  the recorded phase, and preserves the current `resume_object_id` byte-for-byte.

## Behavior

The test uses real append, load, and replay paths. It asserts:

- batches commit atomically or not at all;
- duplicate or skipped writer sequence/predecessor rules fail cleanly;
- unknown events are preserved as opaque gaps;
- projection replay after append equals the reference model’s canonical digest.
- an orphan deterministic object produced before the phase CAS never supplants the reserved row
  pointer, while an installed deterministic result becomes the one local-ready pointer and reaches
  its prior full-case object through the authenticated envelope link;
- memory cache loss and SQLite process reopen produce the same frozen case and phase without
  rebuilding, republishing, or allocating IDs.

## Errors and edge cases

- A partial logical append that still reports success fails the test.
- A replay that depends on SQLite-specific row order fails the test.

## Invariants

1. Append is atomic.
2. Retry identity is stable.
3. Durable replay matches the pure reducer.
4. Pending CHECK recovery is object-backed and has one current row pointer.

## Tests

- `tests/integration/storage/test_append_and_replay.py`

## Open questions

None.
