# tests/conformance/honesty/test_coverage_weakening.py — coverage weakening honesty

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/protocol/coverage.py`, `src/yoetz/domain/findings.md`
**Imported by:** conformance honesty tests

## Purpose

Prove weaker observation, freshness, and immutability never produce stronger public claims.

## Public surface

- `test_dimension_by_dimension_weakening` — each weakened dimension stays weaker.
- `test_imported_partial_and_redacted_material_stays_weak` — imported/redacted data does not
  strengthen output.
- `test_result_language_tracks_weakest_coverage` — wording follows coverage, not wishful prose.

## Behavior

The test systematically weakens one coverage dimension at a time and asserts:

- conclusions do not upgrade;
- findings remain conservative;
- coverage labels and wording stay aligned with the weakest support.

## Errors and edge cases

- Any stronger wording on weaker evidence fails.

## Invariants

1. Weakening is monotonic.
2. Language follows coverage.
3. Imported partial evidence stays partial.

## Tests

- `tests/conformance/honesty/test_coverage_weakening.py`

## Open questions

None.
