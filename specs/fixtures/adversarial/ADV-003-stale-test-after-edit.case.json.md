# fixtures/adversarial/ADV-003-stale-test-after-edit.case.json — stale test after edit adversarial case

**Wave:** A-B | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/unit/kernel/test_deterministic_checks.py

## Purpose

Freeze detection of evidence bound to an older material subject state as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-003"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains state A test evidence, state B completion claim, fresh-remediation,
prose-only described-state, claimed-edit-without-state, and changed-state-content-withheld variants.
The `expected` section freezes `stale_evidence_for_changed_state` only when canonical state
commitments differ; prose never proves equality. It also freezes the independent
`claimed_change`, `subject_state_relation`, and `content_visibility` fields: claimed-but-unobserved
is basis `unknown/not_recorded`, while two unequal tree digests produce basis
`different/available` and separate packet visibility `withheld_by_policy` when policy hides the
excerpt. No variant may render either condition as “no diff.” Every
referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit
test data; a test may not replace it with current time, randomness, network state, or host paths.
Multi-variant cases evaluate each variant independently and declare the relationship between their
outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/unit/kernel/test_deterministic_checks.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
