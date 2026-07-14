# tests/property/test_ranking_properties.py — ranking stability and cap properties

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`tests/property/strategies/events.py`, `src/yoetz_core/kernel/ranking.py`,
`src/yoetz_core/domain/findings.py`
**Imported by:** property-based ranking tests

## Purpose

Prove that ranking is permutation-invariant, stable, capped, and deduplicated under randomized
input sets.

## Public surface

- `test_permutation_invariance` — input order does not change the ranked prefix.
- `test_cap_and_suppressed_count_properties` — the hard cap is honored exactly.
- `test_tie_break_is_finding_id` — deterministic ID tie-break holds.
- `test_verdict_tracks_visible_prefix` — verdict logic follows the selected findings only.

## Behavior

The property suite varies finding collections and proves:

- stable ordering by the documented sort key;
- exact suppressed-count accounting;
- no duplicate ID leakage;
- verdict consistency with the visible prefix.

## Errors and edge cases

- A ranking result that depends on input order fails.

## Invariants

1. Ranking is pure and stable.
2. The visible prefix is all that matters to the verdict.
3. Cap accounting is exact.

## Tests

- `tests/property/test_ranking_properties.py`

## Open questions

None.
