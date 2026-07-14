# tests/integration/storage/test_backup_restore.py — backup and restore workflow

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/repository.md`, `src/yoetz_core/adapters/sqlite/start_catalog.md`,
`src/yoetz_core/adapters/privacy/catalog.md`, `src/yoetz_core/ports/maintenance.py.md`
**Imported by:** integration storage tests

## Purpose

Prove backup pins the frontier, captures referenced objects, and restore switches routes only after
verification.

## Public surface

- `test_backup_pins_and_captures_manifest` — backup produces a canonical manifest and pin.
- `test_restore_verifies_manifest_keys_and_objects` — restore checks all required artifacts.
- `test_restore_switches_routes_atomically` — the route swap is all-or-nothing.
- `test_backup_includes_privacy_catalog_roots_and_sidecar` — catalog-held bundle objects need no
  ledger inventory but are never omitted.
- `test_restore_invalidates_nonterminal_privacy_authority` — backed-up approval cannot dispatch.

## Behavior

The test asserts that backup:

- pins the source frontier;
- uses the reviewed online backup path;
- copies referenced objects and emits a canonical manifest;
- pins the privacy-root generation/digest, emits canonical `privacy-audit-snapshot.json`, and copies
  every catalog-rooted `privacy_audit` object even when absent from task-ledger inventory;
- releases the pin only after the backup is complete.

It also asserts that restore:

- verifies manifest, keys, objects, and replay in quarantine;
- for same-installation route move, unions current catalog roots (including post-backup refs), copies
  them into the target, invalidates nonterminal authority under the new owner generation, repairs
  receipt-pending evidence, and CASes the exact root generation/digest while keeping ObjectRefs
  unchanged;
- for clean-profile restore, preserves terminal audit evidence, expires
  `reserved|awaiting_human|approved|authorized`, completes `decision_receipt_pending`, and resolves
  `receipt_pending` as `transport_failed/outcome_unknown` without any usable restored authority;
- leaves the active route untouched on any mismatch;
- performs the switch only after all checks pass.

## Errors and edge cases

- A restore that switches before verification fails.
- A backup that omits referenced objects fails.
- A missing/tampered privacy sidecar/root, root-generation race, fabricated ledger inventory row, or
  restored authorization that remains dispatchable fails.

## Invariants

1. Backup is pinned and manifest-backed.
2. Restore is quarantine-verified.
3. Route switches are atomic.
4. Privacy audit roots are in the pinned backup/restore set independently of ledger inventory.
5. Restore preserves evidence but never revives disclosure authority.

## Tests

- `tests/integration/storage/test_backup_restore.py`

## Open questions

None.
