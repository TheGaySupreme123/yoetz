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
- `test_semantic_packet_and_challenge_fixtures_are_exact` — enriched deterministic bases, split
  refs, change visibility, excerpts, omissions, and reviewer actions retain their fences.

## Behavior

The test uses adversarial fixtures and asserts:

- public claims remain bounded by the evidence map;
- bad inputs fail closed instead of upgrading wording;
- minimized counterexamples map to named public claims;
- the exact mapping in `fixtures/README.md` covers every registered `FindingKind`, with one trigger,
  remediation, and closest non-trigger and no lookup of an undeclared policy-resource path.

The semantic cases additionally lock:

- `ADV-002`: the failed-test excerpt is mechanically linked, its deterministic basis is unchanged,
  and the reviewer challenge asks the main agent for the smallest useful response;
- `ADV-003`: claimed change, `same|different|unknown` state relation, and content visibility vary
  independently; missing or withheld source never renders as “no diff”;
- `ADV-004`: refs in `local_check_refs` are admissible, refs outside
  `frontier_refs ∪ local_check_refs` and model-authored basis mutations are rejected;
- `ADV-009`: accepted reviewer guidance maps only through existing respond/publish/recheck branches,
  every material branch requires a fresh check, and the model cannot waive the finding.

## Errors and edge cases

- A claim that survives without a mapped test/evidence entry fails.
- An adversarial semantic output that invents source visibility, rewrites deterministic basis, or
  starts an open-ended fetch/conversation round fails.

## Invariants

1. Public claims are evidence-bound.
2. Adversarial inputs fail closed.
3. Counterexamples remain nameable.
4. Richer context improves advice without enlarging semantic authority.

## Tests

- `tests/conformance/honesty/test_adversarial_cases.py`

## Open questions

None.
