# tests/conformance/protocol/test_canonical_cross_process.py — cross-process canonical bytes

**Wave:** A | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`src/yoetz/protocol/canonical.md`, `tests/property/strategies/json_values.py.md`,
`tests/fixture_loader.py`, `fixtures/manifest.json`
**Imported by:** conformance protocol tests

## Purpose

Prove canonical bytes and digests remain identical when exercised across process boundaries.

## Public surface

- `test_parse_encode_bytes_match_across_processes` — canonical bytes do not drift.
- `test_digest_match_across_processes` — digests do not drift.
- `test_registered_environment_matrix_does_not_change_output` — the frozen hash-seed, TZ,
  locale, and optimization cells do not alter output.

## Behavior

The test launches separate processes around the same canonical fixtures and asserts:

- exact byte equality;
- exact digest equality;
- no dependence on environment noise or cwd differences. The environment matrix is exactly the
  12 cells formed by `PYTHONHASHSEED` values `0`, `1`, and `4294967295`; `TZ="UTC"` and
  `TZ="Pacific/Honolulu"`; fixed `LC_ALL="C"`; and normal versus `-O` interpreter mode. The
  child runs from an unrelated working directory while importing the same installed/source tree;
- no hidden stdout/stderr noise from the canonical path.

Each child receives reviewed root fixture bytes through the manifest-bound fixture loader or an
explicit byte payload derived from those already-verified bytes. It never discovers
Markdown fixture-spec shadows or uses installed fixture mirrors as a fallback. Stdout contains only
the test's one exact machine result and stderr is empty.

## Errors and edge cases

- A process-only variation in canonical output fails.
- Missing any of the 12 required matrix cells is a conformance failure; additional ambient
  locale/timezone cells are not required by this owner.

## Invariants

1. Canonicalization is cross-process stable.
2. The complete registered environment matrix does not alter output.
3. Output bytes are the oracle.

## Tests

- `tests/conformance/protocol/test_canonical_cross_process.py`

## Open questions

None.
