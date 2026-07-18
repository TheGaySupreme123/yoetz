# tests/unit/kernel/test_policy_research_evidence.py — research-evidence pack rule coverage

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/policies/research_evidence.md`, `src/yoetz/domain/findings.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the research-evidence policy pack with exact trigger and non-trigger fixtures.

## Public surface

- `test_evidence_does_not_support_claim` — support refs fail to justify the claim.
- `test_diff_does_not_match_account` — diff/state evidence diverges from the written account.
- `test_material_limitation_omitted` — material caveats are omitted.
- `test_questionable_finding_rejection` — unsupported rejection/waiver is detected.
- `test_finding_kind_does_not_imply_semantic_origin` — research/evidence kinds remain valid for
  deterministic and semantic findings when their explicit `origin` is correct.
- `test_rule_groups_raw_triggers_by_complete_subject_tuple` — all raw triggers for one rule and
  exact complete canonical subject tuple are aggregated and evaluated once.
- `test_duplicate_emitted_key_is_rejected` — a second assessment with the same
  `(policy_id, rule_id, subject_refs)` is rejected as a policy-wiring defect.

## Behavior

The suite checks:

- each rule triggers on the minimum supported mismatch;
- the closest non-trigger does not produce a finding;
- coverage weakens when evidence is only partial;
- finding coverage folds the exact basis supporting refs, preserves all four ordered material
  dimensions and gaps, adds only `engine_derived` and `deterministic`, removes `none`, and uses the
  case frontier exactly;
- every candidate returns an exact `FindingBasis` with observed/missing facts and supporting refs;
- all four research-evidence kinds use inline immutable trigger/closest-nontrigger values in this
  test module; no runtime fixture generation or separate policy-resource lookup is permitted;
- the pack never escalates into a probabilistic or semantic conclusion.
- repeated raw triggers for one rule and complete canonical subject tuple produce one grouped rule
  evaluation and at most one assessment;
- duplicate emitted keys fail closed, while two different rule IDs may each emit one assessment
  for the same complete subject tuple;
- the cardinality assertions leave the existing exact fixture output, deterministic templates,
  basis, and coverage unchanged.

## Errors and edge cases

- A mere wording difference without a material mismatch fails the test.
- A supported rejection/waiver must not be flagged.
- Any path that accepts a duplicate emitted key fails the test.

## Invariants

1. Research-evidence findings are subject-bound and conservative.
2. Rule order is fixed.
3. Pack identity is fixed.
4. `origin`, not finding kind, owns provenance semantics.
5. Cardinality is one evaluation and at most one emitted value per rule and complete subject
   tuple.

## Tests

- `tests/unit/kernel/test_policy_research_evidence.py`

## Open questions

None.
