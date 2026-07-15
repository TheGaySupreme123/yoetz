# tests/unit/protocol/test_coverage.py — coverage lattice and weakest-merge rules

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/protocol/coverage.py`, `src/yoetz/domain/receipts.md`
**Imported by:** the protocol and kernel unit suite

## Purpose

Lock the coverage dimensions, ordering, and merge behavior so weaker evidence always stays weaker
and no helper accidentally computes an average.

## Public surface

- `test_ordered_dimensions_sort_as_defined` — ordered enums compare in the registry order.
- `test_weakest_merge_is_componentwise` — merge picks the weaker ordered value per dimension.
- `test_known_gaps_union_is_sorted_unique` — gaps are deduplicated and sorted.
- `test_channel_defaults_are_conservative` — channel-derived coverage starts weak.
- `test_no_averaging_or_strengthening_exists` — no helper strengthens coverage without proof.

## Behavior

The suite proves that:

- coverage values follow the exact registry ordering;
- ordered dimensions never strengthen during merge;
- unordered kinds are preserved, not normalized into a fake scalar;
- gap lists are append-only in effect but canonicalized to sorted unique tuples;
- channel-derived defaults are conservative baselines only.

## Errors and edge cases

- A merge that strengthens evidence fails the test.
- A gap union that preserves duplicates fails the test.

## Invariants

1. Coverage is a lattice of honesty, not a score.
2. Merge semantics are deterministic.
3. Gap metadata survives replay.

## Tests

- `tests/unit/protocol/test_coverage.py`

## Open questions

None.
