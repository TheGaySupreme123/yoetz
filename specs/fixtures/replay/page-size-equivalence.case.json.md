# fixtures/replay/page-size-equivalence.case.json — page-size equivalence replay case

**Wave:** A-C | **ADRs:** ADR-002, ADR-003 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/property/test_reducer_equivalence.py, tests/conformance/adapters/test_ledger_port.py

## Purpose

Freeze bounded pagination without logical dependence on page boundaries as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "REP-008"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains a sufficiently long mixed-event ledger replayed with page sizes 1, 2, 7, 50, 100, and 500 where only supported bounds are accepted. The `expected` section freezes identical public outputs for accepted sizes and INVALID_REQUEST for values above the public maximum. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/property/test_reducer_equivalence.py` and `tests/conformance/adapters/test_ledger_port.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
