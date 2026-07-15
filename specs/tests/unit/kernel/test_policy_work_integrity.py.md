# tests/unit/kernel/test_policy_work_integrity.py — work-integrity pack rule coverage

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/policies/work_integrity.md`, `src/yoetz/domain/findings.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the work-integrity policy pack with a fixture for every finding kind and the nearest
non-trigger for each one.

## Public surface

- `test_completion_with_open_obligations` — unresolved obligations under a completion claim are
  detected.
- `test_requested_item_never_attempted` — missing attempt/result linkage is detected.
- `test_failed_work_omitted` — omitted failure disclosure is detected.
- `test_claim_without_admissible_evidence` — weak or missing support is detected.
- `test_result_without_action` — orphan results are detected.
- `test_stale_evidence_for_changed_state` — state drift invalidates evidence.
- `test_contradictory_claims_unresolved` — unresolved contradiction is detected.
- `test_ledger_stale_or_incomplete` — unknown/redacted/stale history is detected.
- `test_weak_or_stale_response` — hollow rejection or waiver is detected.

## Behavior

The suite asserts for each rule:

- the rule triggers only for the documented subject state;
- the closest non-trigger does not produce a finding;
- coverage weakens when the source refs are partial;
- each finding uses deterministic origin and the pack’s stable policy identity.

## Errors and edge cases

- A rule that triggers on a harmless near-miss fails the test.
- A rule that does not trigger on the minimum case fails the test.

## Invariants

1. Work-integrity findings are conservative.
2. Every rule has a non-trigger proof.
3. Pack identity is fixed.

## Tests

- `tests/unit/kernel/test_policy_work_integrity.py`

## Open questions

None.
