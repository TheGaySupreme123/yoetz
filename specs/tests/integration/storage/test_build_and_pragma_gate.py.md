# tests/integration/storage/test_build_and_pragma_gate.py — SQLite build gate and PRAGMA identity

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/connection.md`, `src/yoetz_core/version.md`
**Imported by:** integration storage tests

## Purpose

Prove the writable SQLite path only opens when the certified build identity and startup PRAGMAs
match the release contract.

## Public surface

- `test_certified_build_identity_is_accepted` — exact APSW/SQLite/source-ID/amalgamation values pass.
- `test_wrong_build_identity_fails_closed` — mismatched build identity blocks write startup.
- `test_pragma_state_matches_contract` — the observed PRAGMA state matches the frozen contract.
- `test_read_only_inspection_does_not_promote_write_safety` — inspection success is not write
  safety.

## Behavior

The test uses a fresh temporary bundle and a real APSW connection. It asserts:

- the connection reports the exact build identity required by the version manifest;
- the startup gate reads back the actual PRAGMA state, not just setter success;
- a wrong amalgamation/source ID/SQLite version fails closed before writes;
- read-only inspection may succeed on unsupported builds, but writes still fail later.

## Errors and edge cases

- A build mismatch that still permits writes fails the test.
- A PRAGMA that is only “set” but not “observed” fails the test.

## Invariants

1. Build identity is checked before writable use.
2. PRAGMA truth is observed, not assumed.
3. Inspection is weaker than write support.

## Tests

- `tests/integration/storage/test_build_and_pragma_gate.py`

## Open questions

None.
