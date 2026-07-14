# fixtures/privacy/PRIV-003-minimal-external.case.json — automatic minimum approved disclosure

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas and data-egress protocol | **Imported by:** privacy unit, property, integration and
conformance tests

## Purpose

Freeze the positive `minimal_external` transformation from a larger candidate set to the smallest
policy-approved external case.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-003`, task-scoped policy
`pvy_33333333-3333-4333-8333-333333333333`, provider `provider.example`, endpoint `ep_example_v1`,
model `model-example-1`, purpose `semantic-review`, three selected candidate items, two irrelevant
items, and exact before/after byte/token/count/digest assertions.

## Behavior

The classifier admits bounded structural metadata, one task sentence, and one evidence excerpt;
minimization removes a redundant excerpt and unrelated conversation; redaction replaces the one
synthetic email span without retaining removed text; secret scanning passes the final case. The
gateway dispatches only the exact three approved items, and the structural receipt freezes category,
candidate/included/removed counts, byte/token totals, one redaction, policy/destination/scope, and
the keyed exact-final-request-body commitment. The vector includes the byte-exact
`b"yoetz/privacy-egress-request/v1\x00"` domain and excludes credential metadata/HTTP framing from
the commitment input. Its terminal attempt receipt freezes `audit_store_version=1`,
`request_commitment.algorithm=hmac-sha256/yoetz-privacy-egress-request-v1`, canonical prefixed
lowercase-hex commitment, and exact `counts.request_body_bytes`; because its outcome is `completed`,
it contains neither `safe_failure_reason` nor `key_slot_ref`.

## Errors and edge cases

Reordering candidate input yields identical canonical approved content. Adding sensitive content,
an unapproved category, over-cap excerpt, redirect, or new scope denies or removes it without
silently upgrading to `trusted_provider`.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. Minimal external
means material minimum under explicit policy, not unrestricted summarization.

## Tests

`tests/property/test_egress_policy_properties.py`,
`tests/integration/privacy/test_egress_gateway.py`, and
`tests/conformance/privacy/test_privacy_profiles.py`.

## Open questions

None.
