# fixtures/adversarial/ADV-004-irrelevant-evidence.case.json — irrelevant evidence adversarial case

**Wave:** A-E | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/integration/providers/test_fake_provider_coordinator.py

## Purpose

Freeze safe semantic detection of evidence that does not support a cited material claim as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-004"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains fixed ledger inputs plus three deterministic rule variants. A claim
with absent/unavailable/stale typed support freezes `claim_without_admissible_evidence`; a claim with
present typed support that structurally contradicts its closed outcome/state freezes
`evidence_does_not_support_claim`; and comparable captured-versus-claimed state digests that differ
freeze `diff_does_not_match_account`. Each has exact basis facts/refs plus a closest non-trigger.
The scripted semantic case may independently return `evidence_does_not_support_claim` with explicit
semantic origin; it separately allows frozen-frontier refs and the newly allocated local
deterministic finding ref. The `expected` section freezes validated deterministic and semantic
origins, a direct-agent challenge requesting better evidence or claim revision, and rejection of
invented/out-of-case refs or basis mutation.
Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is
explicit test data; a test may not replace it with current time, randomness, network state, or host
paths. Multi-variant cases evaluate each variant independently and declare the relationship between
their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/integration/providers/test_fake_provider_coordinator.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
