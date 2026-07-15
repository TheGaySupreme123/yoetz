# tests/conformance/honesty/test_adversarial_cases.py — adversarial public-claim cases

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):**
`tests/conformance/claims/test_public_claim_map.py.md`, `src/yoetz/domain/findings.md`
**Imported by:** conformance honesty tests

## Purpose

Prove adversarial fixtures do not let the project overclaim capability, coverage, or verification.

## Public surface

- `test_adv_claim_fixtures_fail_closed` — malformed or adversarial inputs do not produce false
  confidence.
- `test_claim_language_remains_within_supported_bounds` — public wording stays mapped to evidence.
- `test_counterexample_shrinks_to_named_claim` — failures stay explainable and reviewable.

## Behavior

The test uses adversarial fixtures and asserts:

- public claims remain bounded by the evidence map;
- bad inputs fail closed instead of upgrading wording;
- minimized counterexamples map to named public claims.

## Errors and edge cases

- A claim that survives without a mapped test/evidence entry fails.

## Invariants

1. Public claims are evidence-bound.
2. Adversarial inputs fail closed.
3. Counterexamples remain nameable.

## Tests

- `tests/conformance/honesty/test_adversarial_cases.py`

## Open questions

None.
