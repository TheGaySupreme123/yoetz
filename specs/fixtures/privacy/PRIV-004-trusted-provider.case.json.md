# fixtures/privacy/PRIV-004-trusted-provider.case.json — scoped trusted-provider disclosure

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas and data-egress protocol | **Imported by:** privacy integration, conformance and capability
tests

## Purpose

Freeze both the recommended `assisted` and deliberately broader `expanded` meanings of
`trusted_provider`: named categories for one exact destination, purpose and scope, never access to
everything locally available.

## Public surface

Canonical multi-variant fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-004`, installation
`ins_44444444-4444-4444-8444-444444444444`, workspace scope
commitment `hmac-sha256:4444444444444444444444444444444444444444444444444444444444444444`,
policy `pvy_44444444-4444-4444-8444-444444444444`, provider
`provider.example`, endpoint `ep_trusted_v2`, model allowlist `model-example-2`, and purpose
`selected-code-review`. The `assisted` variant uses a current recommendation-eligible data-use
profile with training `prohibited`, retention `bounded`, and provider human access `restricted`;
public-structural plus ordinary-user-content classes; goal/obligation/claim/decision/
finding/timeline/diff/evidence categories, one linked ordinary `repository_excerpt`, and
the canonical assisted selector with finding prose on and exact command text off plus exact agent-context selection
`finding_summary|bounded_structural_metadata|declared_file_type` with
`public_structural|ordinary_user_content`. Its current-data-use guard is true. The `expanded`
variant adds one explicitly selected
`repository_excerpt` classified `sensitive_confidential` after a separate widening.

## Behavior

A fresh scoped local-human authorization commits each variant's exact listed categories and
destination. The assisted policy then performs ordinary checks/retries without another prompt,
while every physical attempt still gets a fresh authorization/receipt. The gateway includes only
mechanically linked selected IDs whose workspace scope establishes that exact keyed commitment; it
blocks an otherwise readable raw database, unrelated workspace file, full transcript and
environment entry. The adapter receives no
repository/object-store handles. Receipt scope, consent, categories, counts, transformations and
request commitment match the exact final application body bytes; auth metadata/framing are
excluded. Same provider at a different endpoint/model/purpose is
denied.

The assisted packet freezes goal/obligation/claim, ordered timeline, deterministic basis, an
observed-changed state, linked failing-test and changed-hunk/enclosing-symbol excerpts, and explicit
unrelated/not-recorded omissions. The expanded variant proves sensitive content is not silently
inherited from the recommendation. Known-broad/stale/unknown data-use posture trips the assisted
variant's explicit current-evidence guard; a separate trusted custom transition may turn the guard
off without inheriting the recommendation claim.

## Errors and edge cases

Expired authorization, broader wildcard purpose, new category, child task outside the workspace,
adapter enrichment attempt, or endpoint redirect fails before/at the gateway and cannot fall back to
the trusted binding.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. “Trusted” is a
specific authorization tuple, never a bypass around minimization, never-send, or scope. “Assisted”
is a bounded standing recipe, not whole-repository access or technical proof of provider behavior.

## Tests

`tests/integration/privacy/test_egress_gateway.py`,
`tests/conformance/privacy/test_privacy_profiles.py`, and
`tests/capability/test_privacy_provider_and_local_model_profiles.py`.

## Open questions

None.
