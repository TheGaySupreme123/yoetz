# tests/unit/kernel/test_deterministic_checks.py — deterministic policy engine behavior

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/deterministic_checks.md`, `src/yoetz/kernel/policies/work_integrity.md`,
`src/yoetz/kernel/policies/research_evidence.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the deterministic checker as a pure rule engine that emits the expected assessments and no
more, with one exact machine-readable basis for every candidate finding.

## Public surface

- `test_each_pack_has_minimum_trigger_and_closest_nontrigger` — every rule has a positive and a
  near-miss fixture.
- `test_unknown_pack_is_rejected` — tampered pack wiring does not approximate.
- `test_findings_are_origin_deterministic` — deterministic findings never carry semantic provenance.
- `test_pack_results_are_order_stable` — rule order and deduping remain stable.
- `test_candidate_and_finding_basis_are_one_to_one` — every candidate has one stable rule/fact/ref
  explanation and no orphan basis exists.
- `test_basis_separates_state_relation_from_source_availability` — unrecorded source never means
  equal state or no change, and later privacy facts never enter the pure basis.

## Behavior

The suite exercises:

- work-integrity and research-evidence packs separately;
- minimal triggering cases and closest non-trigger cases;
- redacted, weak, and stale coverage variants;
- exact subject refs, priority, policy identity, and coverage behavior;
- exact `FindingBasis` rule ID, observed facts, required-but-missing facts, supporting refs,
  subject-state relation, frozen-source availability, and coverage gaps;
- deduplication of repeated logical findings.

## Errors and edge cases

- A pack that reads provider output fails the test by design.
- A finding that appears with semantic provenance in the deterministic path fails the test.
- Free-form model prose, provider output, or an unsupported state/visibility inference in a basis
  fails the test.

## Invariants

1. Deterministic checks are pure.
2. Pack behavior is fixed and separate.
3. Findings are auditable and bounded.
4. Basis data is deterministic input to semantic review, not semantic authority over the finding.

## Tests

- `tests/unit/kernel/test_deterministic_checks.py`

## Open questions

None.
