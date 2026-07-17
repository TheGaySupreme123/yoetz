# tests/unit/kernel/test_replay_and_projections.py — replay parity and projection digest stability

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/reducers.md`, `src/yoetz/kernel/projections.md`
**Imported by:** the kernel unit suite

## Purpose

Prove that full replay, incremental replay, and stored projection snapshots all converge on the same
derived state.

## Public surface

- `test_empty_full_incremental_replay_match` — replay strategies land on the same state.
- `test_projection_snapshot_order_is_stable` — snapshot key ordering is deterministic.
- `test_projection_digest_is_hash_seed_and_locale_stable` — digest bytes do not drift.
- `test_check_suppressed_count_survives_replay_and_snapshot` — reducer and canonical snapshot
  preserve the exact returned IDs/count.
- `test_corruption_requires_rebuild` — broken snapshots are rejected rather than patched.

## Behavior

The suite checks:

- replay from `empty_projection_state()` matches replay from partitioned prefixes;
- projection snapshots preserve registry order and deterministic map ordering;
- `check_recorded -> latest_tested_state -> projection_snapshot -> replay` preserves the exact
  `returned_finding_ids` and `suppressed_count`, including a nonzero count;
- digests remain stable across interpreter seed and locale variants;
- a corrupt or stale projection forces a rebuild path instead of silent repair.

## Errors and edge cases

- A digest change without an input change fails the test.
- A replay that depends on sorting the ledger stream fails the test.

## Invariants

1. Replay is deterministic.
2. Snapshot bytes are canonical.
3. Corruption is explicit, not patched over.

## Tests

- `tests/unit/kernel/test_replay_and_projections.py`

## Open questions

None.
