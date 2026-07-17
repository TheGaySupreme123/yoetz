# tests/integration/privacy/test_plaintext_canary_sweep.py — plaintext canary sweep

**Wave:** C–F | **ADRs:** ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/observability/privacy.md`, integration storage/object/application specs
**Imported by:** integration privacy tests

## Purpose

Prove user-controlled plaintext does not leak into databases, WAL/SHM, temp/orphan files, logs,
backups, manifests, or human summaries.

## Public surface

- `test_canaries_absent_from_structural_surfaces` — canary text never appears in structural stores.
- `test_ciphertext_matches_are_not_treated_as_plaintext` — encrypted bytes are not false positives.
- `test_fault_paths_do_not_add_plaintext_leaks` — failures do not create new leaks.

## Behavior

The test seeds unique canaries and sweeps every relevant surface after normal and fault workflows.
It asserts:

- application-controlled plaintext is absent from structural stores and logs;
- `IMP-005` Codex commands/model text remain only in encrypted source/payload objects; shell
  assignments, inline authorization/header flags, credential-bearing URLs, and chunk-split tokens
  are absent from every structural surface and are blocked when selected for any disclosure sink;
- ciphertext matches are not counted as plaintext leaks;
- backup/export/recovery artifacts do not introduce new leaks;
- any leak is a release failure, not a warning.

## Errors and edge cases

- A false positive on ciphertext fails the test only if the assertion treats it as plaintext.
- A leak that appears only on a fault path still fails the test.

## Invariants

1. Privacy checks cover success and fault paths.
2. Ciphertext is not plaintext.
3. Structural surfaces remain canary-free.
4. Exact encrypted import retention is not a privacy-scan bypass at disclosure time.

## Tests

- `tests/integration/privacy/test_plaintext_canary_sweep.py`

## Open questions

None.
