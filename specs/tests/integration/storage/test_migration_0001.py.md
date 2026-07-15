# tests/integration/storage/test_migration_0001.py — first migration and schema identity

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/migrations.md`, `src/yoetz/version.md`
**Imported by:** integration storage tests

## Purpose

Prove the first durable migration creates the exact v0.1 schema identity and is idempotent on a
fresh bundle.

## Public surface

- `test_fresh_migration_creates_required_tables_and_indexes` — the full v0001 structure appears.
- `test_migration_is_idempotent_on_reopen` — rerunning on the same bundle is safe.
- `test_wrong_application_id_or_version_fails_closed` — mismatched storage identity is rejected.
- `test_rollback_leaves_original_bundle_usable` — partial migration failure does not corrupt the
  source bundle.
- `test_catalog_privacy_tables_and_pin_root_columns_are_exact` — privacy policy/audit/root tables,
  indexes, and bundle-pin privacy-root fields match the frozen DDL.
- `test_catalog_privacy_tables_and_pin_root_columns_are_exact` — privacy policy/audit/root tables,
  indexes, and bundle-pin privacy-root fields match the frozen DDL.

## Behavior

The test checks that the migration:

- creates the reviewed schema objects and CHECK/STRICT/WITHOUT ROWID choices;
- creates exact policy history/transition, three-branch audit/local-disclosure, receipt-query, and
  live-root catalog objects without a content-bearing plaintext column;
- requires every maintenance pin to carry nonnegative `privacy_root_generation` and exact
  `privacy_root_digest` in addition to its frontier;
- requires every maintenance pin to carry nonnegative `privacy_root_generation` and exact
  `privacy_root_digest` in addition to its frontier;
- stamps the exact application and bundle schema identities;
- leaves no half-migrated writable bundle behind on failure;
- can be re-run on an already-migrated bundle without changing the identity.

## Errors and edge cases

- A migration that auto-heals a corrupt schema without failing is wrong.
- A rollback that leaves ambiguous state behind fails the test.
- A legacy privacy-blind pin row shape, incomplete catalog privacy table/index inventory, agent-
  projection content/object column, or invalid outcome/reason pair fails.
- A legacy privacy-blind pin row shape or incomplete catalog privacy table/index inventory fails.

## Invariants

1. Migration identity is explicit.
2. Failure leaves a usable or quarantined prior state.
3. Reopen is idempotent.
4. Schema identity includes privacy audit reachability and cannot regress to ledger-only roots.
4. Schema identity includes privacy audit reachability and cannot regress to ledger-only roots.

## Tests

- `tests/integration/storage/test_migration_0001.py`

## Open questions

None.
