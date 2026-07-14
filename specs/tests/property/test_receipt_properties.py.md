# tests/property/test_receipt_properties.py — receipt honesty and frontier properties

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`tests/property/strategies/events.py`, `src/yoetz_core/kernel/receipt_builder.py`,
`src/yoetz_core/domain/receipts.py`
**Imported by:** property-based receipt tests

## Purpose

Prove that receipts stay honest across randomized states, redaction profiles, and frontier
combinations.

## Public surface

- `test_receipt_frontier_matches_frozen_state` — the builder summarizes the supplied frontier only.
- `test_conclusion_never_outruns_findings` — conclusions remain conservative.
- `test_weakest_coverage_bounds_the_render` — render text is no stronger than coverage.
- `test_redaction_profiles_only_weaken_output` — redaction changes visibility only.

## Behavior

The property suite generates frozen states and checks that:

- frontier and version identity are explicit;
- conclusions never exceed the evidence and findings;
- compact/rendered views never outrun the document;
- redaction profiles only reduce visible detail.

## Errors and edge cases

- A stronger-than-evidence conclusion fails.

## Invariants

1. Receipts are honest summaries.
2. Redaction does not strengthen claims.
3. Frontier mismatches are explicit defects.

## Tests

- `tests/property/test_receipt_properties.py`

## Open questions

None.
