# tests/integration/storage/test_quarantine_and_recovery.py — quarantine and recovery paths

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/start_catalog.md`, `src/yoetz_core/adapters/sqlite/repository.md`,
`src/yoetz_core/adapters/sqlite/recovery.md`, `src/yoetz_core/adapters/objects/envelope.md`,
`src/yoetz_core/adapters/privacy/catalog.md`
**Imported by:** integration storage tests

## Purpose

Prove recovery from failed start/migration/check states uses quarantine and does not re-open the
corrupted route.

## Public surface

- `test_quarantine_records_the_failed_route` — quarantine preserves the failure reason.
- `test_recovery_switches_only_after_verified_restore` — recovery does not switch early.
- `test_original_route_is_preserved_until_success` — the old route stays intact until verified.
- `test_recovery_state_classification_is_bounded_and_stable` — identical evidence yields the same
  public state and safe facts without raw SQLite or object details.
- `test_corrupt_object_envelope_is_classified_before_route_switch` — malformed encrypted objects
  quarantine recovery rather than being guessed, repaired, or activated.
- `test_dangling_privacy_catalog_ref_fences_disclosure` — catalog-root corruption is not swept or
  repaired through a fake ledger row.

## Behavior

The test injects failure into start, migration, and recovery flows and checks:

- failed routes become quarantined, not half-open;
- restore/recovery only switches after full verification;
- original bundles/routes remain preserved until the new route is proven;
- recovery-state classification is table-driven for clean, projection-rebuildable, quarantined,
  restore-required, and unsupported evidence and is stable across repeated read-only inspection;
- every referenced object envelope is parsed and authenticated during quarantine verification, and
  truncation, corruption, wrong version/key slot/algorithm, or commitment mismatch blocks switch.
- every catalog `PrivacyAuditObjectRoots` ref is verified against the active route despite absent
  task-ledger inventory; a missing/wrong-task/kind/digest/key object quarantines the audit row,
  preserves the ref/evidence, blocks content disclosure/resume and backup/restore, while bounded
  deterministic no-egress work may continue.

## Errors and edge cases

- A recovery that changes route before verification fails.
- A quarantined route that still accepts writes fails.
- Classification that echoes paths, SQL, ciphertext, or raw exception text fails.
- Repeated inspection of unchanged evidence that produces a different state fails.
- Clearing a dangling catalog ref, inventing ledger inventory, or allowing disclosure from the
  quarantined row fails.

## Invariants

1. Quarantine is explicit.
2. Recovery is verified before switch.
3. Originals are preserved until success.
4. Recovery classification is deterministic and exposes only bounded safe facts.
5. Object-envelope failure blocks activation before any recovered route accepts work.
6. Catalog-owned privacy roots are verified and quarantined under their own authority boundary.

## Tests

- `tests/integration/storage/test_quarantine_and_recovery.py`

## Open questions

None.
