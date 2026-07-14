# tests/integration/storage/test_checkpoint_and_wal_bounds.py — checkpoint and WAL degradation bounds

**Wave:** C | **ADRs:** ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/connection.md`, `src/yoetz_core/adapters/sqlite/repository.md`
**Imported by:** integration storage tests

## Purpose

Prove checkpointing stays owner-only and WAL growth stays bounded under degraded but supported
conditions.

## Public surface

- `test_owner_only_passive_checkpoint` — only the current owner can checkpoint.
- `test_wal_thresholds_emit_diagnostics` — degraded WAL states are surfaced honestly.
- `test_busy_timeout_and_io_faults_fail_closed` — injected faults do not produce false success.

## Behavior

The test exercises:

- PASSIVE checkpoint behavior by the active owner only;
- WAL growth and threshold diagnostics;
- bounded failure on busy timeout, disk-full, quota, permission, and read-only cases;
- no unbounded growth or loss of acknowledged data.

## Errors and edge cases

- A checkpoint that rewrites data unexpectedly fails.
- A fault path that reports success fails.

## Invariants

1. Checkpointing is authority-bound.
2. WAL degradation is visible.
3. Faults fail closed.

## Tests

- `tests/integration/storage/test_checkpoint_and_wal_bounds.py`

## Open questions

None.
