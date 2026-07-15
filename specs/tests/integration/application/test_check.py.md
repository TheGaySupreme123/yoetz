# tests/integration/application/test_check.py — check end-to-end and semantic gates

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/adapters/providers/fake.md`,
`src/yoetz/kernel/deterministic_checks.md`
**Imported by:** integration application tests

## Purpose

Prove the public `check` operation freezes the case, runs the deterministic packs, and conditionally
admits semantic results only after post-validation.

## Public surface

- `test_deterministic_only_mode` — deterministic findings and verdict are returned without semantic
  work.
- `test_semantic_optional_and_required_modes` — mode gating is exact.
- `test_semantic_fake_outcomes_and_post_validation` — fake provider outcomes are validated.
- `test_stale_frontier_and_late_response_handling` — stale results do not steer the current check.

## Behavior

The test freezes a case and then asserts:

- deterministic findings are stable;
- semantic capability is optional or required only by mode;
- late, invented, or coverage-upgrading semantic results are rejected;
- the final check result respects the frozen frontier and returned findings.
- every terminal path selects the exact `SemanticReason`; provider/policy failures in
  `semantic_required` preserve deterministic findings and produce no semantic findings;
- provider adapters return provisional attempt provenance, while only the coordinator may publish
  receipt-finalized provenance after the terminal privacy receipt is durable.

## Errors and edge cases

- A semantic result that rewrites deterministic findings fails.
- A stale frontier that still steers the result fails.
- Missing/mismatched reason or provenance published before receipt durability fails.

## Invariants

1. Frozen case controls the check.
2. Semantic work is optional or required by mode.
3. Post-validation is mandatory.

## Tests

- `tests/integration/application/test_check.py`

## Open questions

None.
