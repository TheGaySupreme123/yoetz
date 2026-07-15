# tests/property/test_coverage_lattice.py — coverage lattice algebra properties

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`tests/property/strategies/json_values.py`, `src/yoetz/protocol/coverage.py`
**Imported by:** property-based coverage tests

## Purpose

Prove the weakest-merge algebra across randomized coverage combinations.

## Public surface

- `test_weakest_merge_is_commutative_and_associative` — compatible dimensions obey lattice rules.
- `test_weakest_merge_is_idempotent` — merging identical coverage leaves it unchanged.
- `test_gaps_union_and_sorting_are_stable` — known gaps remain sorted unique.
- `test_channel_defaults_do_not_strengthen_input` — channel defaults stay conservative.

## Behavior

The property suite varies coverage dimensions, ordered and unordered kinds, and known gaps to show
that:

- merge is deterministic;
- ordered dimensions only weaken or stay equal;
- gaps behave like a sorted unique union;
- no averaging or implicit strengthening exists.

## Errors and edge cases

- A property that discovers a stronger-than-input merge fails.

## Invariants

1. Coverage algebra is stable.
2. Merge does not score or average.
3. Gap metadata survives normalization.

## Tests

- `tests/property/test_coverage_lattice.py`

## Open questions

None.
