# tests/unit/kernel/test_policy_research_evidence.py — research-evidence pack rule coverage

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/kernel/policies/research_evidence.md`, `src/yoetz_core/domain/findings.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the research-evidence policy pack with exact trigger and non-trigger fixtures.

## Public surface

- `test_evidence_does_not_support_claim` — support refs fail to justify the claim.
- `test_diff_does_not_match_account` — diff/state evidence diverges from the written account.
- `test_material_limitation_omitted` — material caveats are omitted.
- `test_questionable_finding_rejection` — unsupported rejection/waiver is detected.

## Behavior

The suite checks:

- each rule triggers on the minimum supported mismatch;
- the closest non-trigger does not produce a finding;
- coverage weakens when evidence is only partial;
- the pack never escalates into a probabilistic or semantic conclusion.

## Errors and edge cases

- A mere wording difference without a material mismatch fails the test.
- A supported rejection/waiver must not be flagged.

## Invariants

1. Research-evidence findings are subject-bound and conservative.
2. Rule order is fixed.
3. Pack identity is fixed.

## Tests

- `tests/unit/kernel/test_policy_research_evidence.py`

## Open questions

None.
