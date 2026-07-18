# tests/conformance/compatibility/test_backward_read.py — backward-read compatibility corpus

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/version.md`, `specs/fixtures/README.md`
**Imported by:** conformance compatibility tests

## Purpose

Prove released schemas, objects, projections, and public results remain readable across the support
window.

## Public surface

- `test_released_corpus_still_reads` — old fixtures remain readable.
- `test_unknown_events_remain_preservable` — opaque unknown data is preserved.
- `test_compatibility_window_is_honestly_reported` — unsupported versions are explicit.

## Behavior

The test loads released fixtures and asserts:

- old public results and receipts still interpret correctly;
- the BWR-001 source archive keeps its released nested projection bytes and stored digest exactly,
  while the supported backward-read adapter returns the fixture's current flat projection vector
  in memory without migration, database writes, or archive regeneration;
- unknown events remain opaque through read paths;
- unsupported versions fail closed with a bounded limitation.

## Errors and edge cases

- A backward-read path that rewrites history fails.

## Invariants

1. Released corpus remains readable.
2. Compatibility translation never mutates or silently refreshes its released source artifact.
3. Unknown data remains opaque.
4. Compatibility limits are explicit.

## Tests

- `tests/conformance/compatibility/test_backward_read.py`

## Open questions

None.
