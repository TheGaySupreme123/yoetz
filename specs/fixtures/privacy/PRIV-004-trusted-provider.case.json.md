# fixtures/privacy/PRIV-004-trusted-provider.case.json — scoped trusted-provider disclosure

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas and data-egress protocol | **Imported by:** privacy integration, conformance and capability
tests

## Purpose

Freeze the bounded meaning of `trusted_provider`: broader named categories for one exact destination,
purpose and scope, never access to everything locally available.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-004`, workspace scope `wsp_44444444`, policy
`pvy_44444444-4444-4444-8444-444444444444`, provider `provider.example`, endpoint `ep_trusted_v2`,
model allowlist `model-example-2`, purpose `selected-code-review`, approved categories
`task_description`, `evidence_excerpt`, `diff_metadata`, and one explicitly selected
`repository_excerpt` classified `sensitive_confidential`.

## Behavior

A fresh scoped local-human authorization permits exactly the listed categories and destination.
The gateway includes only selected IDs within `wsp_44444444`; it blocks an otherwise readable raw
database, unrelated workspace file, full transcript and environment entry. The adapter receives no
repository/object-store handles. Receipt scope, consent, categories, counts, transformations and
request commitment match the exact final application body bytes; auth metadata/framing are
excluded. Same provider at a different endpoint/model/purpose is
denied.

## Errors and edge cases

Expired authorization, broader wildcard purpose, new category, child task outside the workspace,
adapter enrichment attempt, or endpoint redirect fails before/at the gateway and cannot fall back to
the trusted binding.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. “Trusted” is a
specific authorization tuple, never a bypass around minimization, never-send, or scope.

## Tests

`tests/integration/privacy/test_egress_gateway.py`,
`tests/conformance/privacy/test_privacy_profiles.py`, and
`tests/capability/test_privacy_provider_and_local_model_profiles.py`.

## Open questions

None.
