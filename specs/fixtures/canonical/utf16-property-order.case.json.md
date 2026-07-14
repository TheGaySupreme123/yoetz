# fixtures/canonical/utf16-property-order.case.json — UTF-16 property ordering vectors

**Wave:** A | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/unit/protocol/test_canonical_vectors.py, tests/conformance/protocol/test_canonical_cross_process.py

## Purpose

Freeze object-key order cases where UTF-16 code-unit order differs from UTF-8 or code-point order as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "CAN-004"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains Hebrew, emoji and surrogate-pair keys arranged in deliberately misleading source order. The `expected` section freezes exact RFC-compatible canonical key order and bytes across Python hash seeds, locale, and process boundaries. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/unit/protocol/test_canonical_vectors.py` and `tests/conformance/protocol/test_canonical_cross_process.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
