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
- `test_action_without_result` — an unresolved action followed by later work on another subject is
  detected, while the most recent action remains a closest non-trigger.
- `test_stale_evidence_for_changed_state` — state drift invalidates evidence.
- `test_contradictory_claims_unresolved` — unresolved contradiction is detected.
- `test_ledger_stale_or_incomplete` — unknown/redacted/stale history is detected.
- `test_weak_or_stale_response` — hollow rejection or waiver is detected.
- `test_change_claim_without_observed_state` — a claimed change with missing/hidden source stays
  `subject_state_relation=unknown`, never `same`.
- `test_rule_groups_raw_triggers_by_complete_subject_tuple` — all raw triggers for one rule and
  exact complete canonical subject tuple are aggregated and evaluated once.
- `test_duplicate_emitted_key_is_rejected` — a second assessment with the same
  `(policy_id, rule_id, subject_refs)` is rejected as a policy-wiring defect.

## Behavior

The suite asserts for each rule:

- the rule triggers only for the documented subject state;
- the closest non-trigger does not produce a finding;
- coverage weakens when the source refs are partial;
- finding coverage folds the exact basis supporting refs, preserves all four ordered material
  dimensions and gaps, adds only `engine_derived` and `deterministic`, removes `none`, and uses the
  case frontier exactly;
- each candidate has an exact `FindingBasis` naming observed/missing facts, refs, state relation,
  frozen-source availability, and coverage gaps;
- all ten work-integrity kinds use inline immutable trigger/closest-nontrigger values in this test
  module; no runtime fixture generation or separate policy-resource lookup is permitted;
- each finding uses deterministic origin and the pack’s stable policy identity, while finding kind
  alone makes no provenance claim.
- repeated raw triggers for one rule and complete canonical subject tuple produce one grouped rule
  evaluation and at most one assessment;
- duplicate emitted keys fail closed, while two different rule IDs may each emit one assessment
  for the same complete subject tuple;
- the cardinality assertions leave the existing exact fixture output, deterministic templates,
  basis, and coverage unchanged.

## Errors and edge cases

- A rule that triggers on a harmless near-miss fails the test.
- A rule that does not trigger on the minimum case fails the test.
- Any path that accepts a duplicate emitted key fails the test.

## Invariants

1. Work-integrity findings are conservative.
2. Every rule has a non-trigger proof.
3. Pack identity is fixed.
4. The pack reports what it observed and did not observe without treating undisclosed code as an
   unchanged tree.
5. Cardinality is one evaluation and at most one emitted value per rule and complete subject
   tuple.

## Tests

- `tests/unit/kernel/test_policy_work_integrity.py`

## Open questions

None.
