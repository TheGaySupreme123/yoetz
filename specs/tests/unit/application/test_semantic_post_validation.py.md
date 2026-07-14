# tests/unit/application/test_semantic_post_validation.py — semantic result validation fences

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/application/check.md`, `src/yoetz_core/domain/findings.md`,
`src/yoetz_core/protocol/coverage.py`
**Imported by:** the application unit suite

## Purpose

Prove the application rejects semantic results that do not belong to the frozen case or that try to
strengthen evidence beyond what was actually seen.

## Public surface

- `test_invented_ids_are_rejected` — semantic results cannot mint unrelated IDs.
- `test_out_of_case_quotes_are_rejected` — text not tied to the frozen case does not pass validation.
- `test_coverage_upgrade_is_rejected` — semantic output cannot strengthen evidence beyond the case.
- `test_deterministic_claims_remain_deterministic` — semantic paths do not rewrite deterministic
  findings.
- `test_stale_frontier_is_rejected` — late semantic results against the wrong frontier fail.

## Behavior

The suite locks the post-validation boundary:

- semantic output must reference the frozen case and frontier;
- invented subject refs or claims are rejected;
- coverage can weaken but not strengthen;
- deterministic findings remain authoritative and are not rewritten by the semantic path;
- stale or late results are not admitted into the final check.

## Errors and edge cases

- A semantic result that passes by quoting unrelated text fails the test.
- A result that upgrades coverage without evidence fails the test.

## Invariants

1. Semantic output is advisory, not sovereign.
2. Case-bound refs are mandatory.
3. Coverage only weakens after semantic validation.

## Tests

- `tests/unit/application/test_semantic_post_validation.py`

## Open questions

None.
