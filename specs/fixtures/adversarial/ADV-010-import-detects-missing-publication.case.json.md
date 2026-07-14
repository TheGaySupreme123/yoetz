# fixtures/adversarial/ADV-010-import-detects-missing-publication.case.json — import detects missing publication adversarial case

**Wave:** A-D | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/integration/application/test_import_review.py

## Purpose

Freeze detection of a material command result present in a bounded Codex recording but absent from cooperative publication as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-010"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains a synthetic cooperative ledger, exact-version Codex byte stream, accepted mapping, and complete-publication control. The `expected` section freezes an immutable import report at import_observed coverage and an explicit channel disagreement only for the trigger. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/integration/application/test_import_review.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
