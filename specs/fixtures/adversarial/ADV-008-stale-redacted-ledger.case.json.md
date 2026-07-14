# fixtures/adversarial/ADV-008-stale-redacted-ledger.case.json — stale or redacted ledger adversarial case

**Wave:** A-D | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/conformance/honesty/test_receipt_wording.py

## Purpose

Freeze honest weakening when supporting payloads are redacted or material unknown events exist as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-008"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains redaction and unknown-event trigger variants plus complete non-trigger control. The `expected` section freezes ledger_stale_or_incomplete or insufficient_coverage with explicit gaps and never verified wording. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/conformance/honesty/test_receipt_wording.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
