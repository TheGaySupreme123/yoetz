# tests/integration/storage/test_migration_rollback.py — migration failure and rollback semantics

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/migrations.md`
**Imported by:** integration storage tests

## Purpose

Prove failed migrations roll back cleanly and leave the original bundle or quarantine state usable.

## Public surface

- `test_failure_before_commit_leaves_original_intact` — pre-commit failure is harmless to source.
- `test_failure_after_commit_is_quarantined_not_corrupted` — post-commit failure does not pretend
  success.
- `test_retry_is_idempotent_after_rollback` — rerun after failure is safe.

## Behavior

The test injects failures around migration boundaries and checks:

- rollback preserves the original usable state when commit has not occurred;
- quarantined states remain quarantined if the failure happens after commit;
- retrying the migration after rollback does not compound damage.

## Errors and edge cases

- A rollback that leaves a partially trusted state fails.
- A retry that mutates the original bundle fails.

## Invariants

1. Rollback is explicit and safe.
2. Post-commit failure is quarantined.
3. Retry is idempotent.

## Tests

- `tests/integration/storage/test_migration_rollback.py`

## Open questions

None.
