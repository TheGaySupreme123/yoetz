# fixtures/canonical/identifiers.case.json — identifier vectors

**Wave:** A | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/unit/protocol/test_ids.py, tests/property/test_id_properties.py

## Purpose

Freeze positive and negative vectors for every public identifier kind as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "CAN-008"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains valid fixed-length prefixed IDs for all registered kinds plus wrong prefix, alphabet, length, case, and zero/randomness edge cases. The `expected` section freezes the exact parsed kind for valid IDs and stable INVALID_REQUEST detail category for invalid IDs. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/unit/protocol/test_ids.py` and `tests/property/test_id_properties.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
