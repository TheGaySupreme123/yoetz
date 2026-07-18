# fixtures/receipts/redacted-gap.case.json — redacted gap receipt case

**Wave:** A-D | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/objects/test_redaction_and_gc.py, tests/conformance/honesty/test_receipt_wording.py

## Purpose

Freeze honest receipt degradation when referenced encrypted content is unavailable or redacted as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "RCP-005"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains a fixed receipt subject with redacted, locked-key, and available-object variants. The `expected` section freezes conclusion insufficient_coverage with explicit structural gaps and no leaked content or strengthened Markdown. The locked-key input keeps its synthetic key ID as adapter-side test data, but the expected receipt and compact render name only `captured_object_unavailable`, rooted at the evidence source event; the redacted variant names `redacted_object`, rooted at the causative redaction event. The key ID is an explicit forbidden-output marker because `CaseAvailabilityFacts` carries neither key identity nor adapter reason. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/integration/objects/test_redaction_and_gc.py` and `tests/conformance/honesty/test_receipt_wording.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
