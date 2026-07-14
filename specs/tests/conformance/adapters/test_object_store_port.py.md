# tests/conformance/adapters/test_object_store_port.py — object store port parity

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/ports/objects.md`, `tests/conformance/adapters/test_ledger_port.py.md`
**Imported by:** conformance adapter tests

## Purpose

Prove the object store port publishes and opens the same canonical object behavior across backends.

## Public surface

- `test_stage_finalize_open_parity` — staged/finalized/open behavior matches.
- `test_failure_atomicity_parity` — partial failures do not create divergent public state.
- `test_redaction_and_missing_object_parity` — redaction and missing objects are surfaced the same
  way.
- `test_generation_fenced_sweep_parity` — all adapters retain the same owning-root union and abort
  collection on generation drift.

## Behavior

The test asserts:

- same logical object source yields the same contract/metadata shape and the same exact
  kind-domain `K_commit` plaintext commitment across backends; generated object ID, DEK, payload
  nonce, envelope bytes, and `envelope_digest` may differ unless the test injects all randomness;
- failed publication leaves no fake success on either backend;
- verified open returns the same bytes for the same finalized object;
- redacted or missing objects are represented with the same public limitations.
- `ObjectRootSnapshot` unions ledger, importer, catalog privacy, and pin roots identically; a
  catalog-rooted `privacy_audit` object remains live without ledger inventory and generation drift
  aborts collection.

## Errors and edge cases

- A backend that invents an object ref on failure fails.
- A backend that treats ledger inventory as the complete root set fails.

## Invariants

1. Object-store behavior is adapter-neutral.
2. Failure atomicity is preserved.
3. Public refs and digests stay exact.

## Tests

- `tests/conformance/adapters/test_object_store_port.py`

## Open questions

None.
