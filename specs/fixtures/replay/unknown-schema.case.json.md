# fixtures/replay/unknown-schema.case.json — unknown schema replay case

**Wave:** A-B | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/protocol/test_unknown_events.py, tests/conformance/compatibility/test_backward_read.py

## Purpose

Freeze forward-compatible preservation of a syntactically valid unknown event schema as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "REP-004"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains known entries surrounding one opaque unknown event with canonical bytes and fixed schema URI. The `expected` section freezes continued replay, exact unknown-event retention, a material projection gap, weakened coverage, and no invented interpretation. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/protocol/test_unknown_events.py` and `tests/conformance/compatibility/test_backward_read.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
