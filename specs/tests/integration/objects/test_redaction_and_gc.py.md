# tests/integration/objects/test_redaction_and_gc.py — redaction history and garbage collection

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/repository.md`, `src/yoetz_core/adapters/objects/encrypted_files.md`,
`src/yoetz_core/adapters/privacy/catalog.md`
**Imported by:** integration object-store tests

## Purpose

Prove redaction weakens later evidence and that garbage collection only removes eligible encrypted
payloads.

## Public surface

- `test_redaction_appends_history_and_weakens_coverage` — redaction changes the projection.
- `test_redacted_or_revoked_object_cannot_be_reopened` — access revocation remains effective even
  when ciphertext is still locally present.
- `test_gc_keeps_live_refs_and_pins` — eligible objects only are collected.
- `test_gc_keeps_catalog_privacy_roots_without_ledger_inventory` — privacy audit reachability is
  catalog-owned and generation-fenced.
- `test_ledger_redaction_cannot_partially_delete_privacy_audit_content` — v0.1 rejects the wrong
  owning redaction path.
- `test_no_forensic_erasure_claim_is_made` — redaction is not represented as total deletion.

## Behavior

The test checks that redaction:

- is recorded as history;
- changes coverage immediately;
- removes only eligible encrypted payloads;
- preserves structural gaps for later checks and receipts;
- denies verified open after redaction/revocation without claiming the ciphertext was forensically
  erased.

It also checks garbage collection:

- honors safety windows and pins;
- builds one `ObjectRootSnapshot` from task-ledger, importer, privacy-catalog and pin roots;
- never removes live references, including a `privacy_audit` ObjectRef absent from task-ledger
  inventory;
- aborts before deletion when route/bundle/privacy-root generation or digest changes;
- does not pretend to remove forensic evidence entirely.

Catalog privacy roots remain live for the supported installation-data lifetime in v0.1. A generic
`redaction_recorded` request targeting such a catalog-only object is rejected; there is no partial
catalog-clear/ciphertext-delete path and no individual privacy-audit-content deletion claim.

## Errors and edge cases

- A redaction that erases history fails.
- A redacted or revoked object that can still be returned as verified plaintext fails.
- A GC pass that removes live refs fails.
- Missing task-ledger inventory for a catalog-rooted privacy object is expected; treating it as an
  orphan fails. A dangling/tampered catalog root quarantines privacy audit and fences disclosure
  rather than being swept.

## Invariants

1. Redaction weakens, it does not erase history.
2. GC is eligibility-bound.
3. Pins are respected.
4. Revoked access never yields verified plaintext.
5. Only the owning catalog root transaction could make privacy content collectible; none exists in
   v0.1.

## Tests

- `tests/integration/objects/test_redaction_and_gc.py`

## Open questions

None.
