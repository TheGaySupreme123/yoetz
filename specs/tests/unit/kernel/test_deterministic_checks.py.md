# tests/unit/kernel/test_deterministic_checks.py — deterministic policy engine behavior

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/deterministic_checks.md`, `src/yoetz/kernel/policies/work_integrity.md`,
`src/yoetz/kernel/policies/research_evidence.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the deterministic checker as a pure rule engine that emits the expected findings and no more.

## Public surface

- `test_each_pack_has_minimum_trigger_and_closest_nontrigger` — every rule has a positive and a
  near-miss fixture.
- `test_unknown_pack_is_rejected` — tampered pack wiring does not approximate.
- `test_findings_are_origin_deterministic` — deterministic findings never carry semantic provenance.
- `test_pack_results_are_order_stable` — rule order and deduping remain stable.

## Behavior

The suite exercises:

- work-integrity and research-evidence packs separately;
- minimal triggering cases and closest non-trigger cases;
- redacted, weak, and stale coverage variants;
- exact subject refs, priority, policy identity, and coverage behavior;
- deduplication of repeated logical findings.

## Errors and edge cases

- A pack that reads provider output fails the test by design.
- A finding that appears with semantic provenance in the deterministic path fails the test.

## Invariants

1. Deterministic checks are pure.
2. Pack behavior is fixed and separate.
3. Findings are auditable and bounded.

## Tests

- `tests/unit/kernel/test_deterministic_checks.py`

## Open questions

None.
