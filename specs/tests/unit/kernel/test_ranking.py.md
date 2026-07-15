# tests/unit/kernel/test_ranking.py — ranked finding ordering and verdict rules

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/ranking.md`, `src/yoetz/domain/findings.md`,
`src/yoetz/protocol/coverage.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the stable ordering, cap handling, and verdict selection for findings returned to callers.

## Public surface

- `test_ordering_by_priority_actionability_evidence_id` — the lexicographic sort key is stable.
- `test_deterministic_out_ranks_semantic_on_ties` — deterministic findings win tie conditions.
- `test_max_findings_cap_and_suppressed_count` — the cap is hard and suppression is exact.
- `test_verdict_selection_from_selected_prefix` — verdicts come from the visible prefix only.
- `test_empty_set_verdict_uses_coverage_context` — empty results still choose an honest verdict.
- `test_one_material_reviewer_challenge_slot_at_default_cap` — the highest-ranked priority-1/2
  semantic challenge displaces at most one lower selected item when the cap is at least two.

## Behavior

The suite proves:

- higher priority sorts first;
- actionability and evidence strength break ties before ID;
- deterministic findings outrank semantic findings when other fields tie;
- one material semantic reviewer challenge is retained when the cap is at least two, while max-one and
  priority-three cases preserve the ordinary strongest prefix;
- the returned prefix never exceeds the cap and always reports the suppressed count;
- verdicts are conservative and do not inspect the suppressed tail.

## Errors and edge cases

- Duplicate IDs in input fail the test.
- A cap overflow without suppression accounting fails the test.

## Invariants

1. Ranking is stable and pure.
2. The visible prefix determines the verdict.
3. Suppressed count is exact.
4. Reviewer delivery never rewrites or relabels deterministic findings.

## Tests

- `tests/unit/kernel/test_ranking.py`

## Open questions

None.
