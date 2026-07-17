# tests/integration/storage/test_projection_rebuild.py — projection cache rebuild behavior

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/repository.md`, `src/yoetz/kernel/projections.md`
**Imported by:** integration storage tests

## Purpose

Prove a damaged or missing projection cache is rebuilt from the accepted ledger without changing
truth.

## Public surface

- `test_missing_projection_rebuilds_from_ledger` — cache deletion is recoverable.
- `test_corrupt_projection_digest_forces_rebuild` — digest mismatch blocks silent reuse.
- `test_rebuild_matches_reference_snapshot` — rebuild equals the pure replay snapshot.
- `test_rebuild_preserves_latest_check_suppression` — SQLite cache/rebuild keeps returned IDs and
  nonzero suppressed count exactly.
- `test_rebuild_does_not_mutate_ledger_history` — rebuild is a derived operation only.

## Behavior

The test deletes or corrupts the projection cache, then forces reload/rebuild. It asserts:

- the rebuild uses accepted events only;
- the rebuilt digest matches the pure projection digest;
- `p1_projection_state` round-trips the latest check event/frontier/verdict/returned IDs/
  suppressed count/coverage and rebuild reproduces them byte-for-byte;
- ledger history is left untouched;
- stale cache state is not trusted after digest mismatch.

## Errors and edge cases

- A cache repair that changes the ledger is wrong.
- A rebuild that silently accepts a digest mismatch fails.

## Invariants

1. Projection is derived, not authoritative.
2. Rebuild preserves ledger truth.
3. Corruption is explicit.

## Tests

- `tests/integration/storage/test_projection_rebuild.py`

## Open questions

None.
