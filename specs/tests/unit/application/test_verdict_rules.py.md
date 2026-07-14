# tests/unit/application/test_verdict_rules.py — check verdict and response rules

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/application/check.md`, `src/yoetz_core/application/respond.md`,
`src/yoetz_core/domain/findings.md`
**Imported by:** the application unit suite

## Purpose

Lock the pure helper rules that decide when a check is deterministic-only, semantic-required, or
stale/incomplete.

## Public surface

- `test_deterministic_only_without_semantic_need` — the pure deterministic path is selected when
  appropriate.
- `test_semantic_required_marks_missing_capability_incomplete` — required semantic work is a
  verdict-completeness gate: deterministic findings survive and the verdict is
  `incomplete_check` when semantic evidence is unavailable.
- `test_ambiguous_outcome_is_conservative` — incomplete or stale inputs do not overclaim.
- `test_response_waiver_and_expiry_rules` — waivers and expiries are interpreted exactly.

## Behavior

The suite proves:

- the check helper chooses the least surprising honest verdict;
- absence of semantic capability is either optional or, for `semantic_required`, an explicit
  `incomplete_check` with deterministic findings preserved, no semantic findings, and the exact
  closed reason (`provider_not_configured` or `local_model_not_configured` as applicable);
- every `SemanticStatus` accepts only its registered `SemanticReason` values and success/
  not-requested paths also carry one reason;
- ambiguous outcomes do not become fake passes;
- response handling preserves waiver scope and expiry rather than inventing a blanket release.

## Errors and edge cases

- A helper that upgrades coverage without evidence fails.
- A response that ignores expiry semantics fails.
- A verdict helper that returns a semantic status without a reason, cross-pairs a reason, or emits
  semantic findings on a failed semantic path fails.

## Invariants

1. Verdict helpers are conservative.
2. Required semantic capability is enforced without turning an unavailable semantic result into
   an operation failure or discarding deterministic truth.
3. Waivers stay scoped and bounded.

## Tests

- `tests/unit/application/test_verdict_rules.py`

## Open questions

None.
