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
- `test_verdict_selection_from_selection_and_context` — required incompleteness, selected
  actionability, coverage incompleteness, and no-issue use the registered precedence.
- `test_result_coverage_uses_full_pre_cap_context` — empty/nonempty/suppressed/diversity cases
  return exactly `RankingContext.coverage`.
- `test_one_material_reviewer_challenge_slot_at_default_cap` — the highest-ranked priority-1/2
  semantic challenge displaces at most one lower selected item when the cap is at least two.

## Behavior

The suite proves:

- registered priority 1, then 2, then 3 sorts first;
- actionability and evidence strength break ties before ID;
- deterministic findings outrank semantic findings when other fields tie;
- one material semantic reviewer challenge is retained when the cap is at least two, while max-one and
  priority-three cases preserve the ordinary top-N selection;
- the returned selection never exceeds the cap and always reports the suppressed count;
- verdicts use the selected set plus `RankingContext.completeness`, never the suppressed tail;
- suppressing or replacing any candidate never strengthens the full pre-cap coverage baseline.

## Errors and edge cases

- Duplicate IDs in input fail the test.
- A cap overflow without suppression accounting fails the test.

## Invariants

1. Ranking is stable and pure.
2. The ordered selection and immutable `RankingContext` determine the verdict.
3. Suppressed count is exact.
4. Reviewer delivery never rewrites or relabels deterministic findings.

## Tests

- `tests/unit/kernel/test_ranking.py`

## Open questions

None.
