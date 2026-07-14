# fixtures/canonical/rfc8785-applicable.case.json — RFC 8785 applicable vectors

**Wave:** A | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/unit/protocol/test_canonical_vectors.py, tests/conformance/protocol/test_canonical_cross_process.py

## Purpose

Freeze the RFC 8785 vectors that remain valid under Yoetz's no-float restricted JSON profile as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "CAN-001"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains the selected upstream input values, exact canonical-byte hex, and a source-vector label for each vector. The `expected` section freezes all accepted vectors canonicalize byte-for-byte; excluded numeric vectors are named as exclusions rather than silently adapted. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/unit/protocol/test_canonical_vectors.py` and `tests/conformance/protocol/test_canonical_cross_process.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
