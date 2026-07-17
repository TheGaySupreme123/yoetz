# fixtures/replay/page-size-equivalence.case.json — page-size equivalence replay case

**Wave:** A-C | **ADRs:** ADR-002, ADR-003 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/property/test_reducer_equivalence.py, tests/conformance/adapters/test_ledger_port.py

## Purpose

Freeze bounded pagination without logical dependence on page boundaries as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "REP-008"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains a sufficiently long mixed-event ledger and two explicitly separate
variant families. Internal replay variants inject test-only ledger chunk sizes `1`, `2`, `7`, `50`,
`100`, and `500`; all are accepted by the adapter harness and produce byte-equivalent logical
projections, while production fixes its internal `LEDGER_READ_PAGE_SIZE` to `500`. Public status
query variants set `ProjectionQuery.limit` to `1`, `2`, `7`, `50`, and `100`, all accepted, then to
`101` and `500`, both rejected as `INVALID_REQUEST` because `STATUS_PAGE_MAX=100` before any ledger
read. The internal chunk-size hook is not a public request field, and `LedgerPort.load_events` has no
caller-supplied page-size argument. Every referenced identifier, timestamp, key, digest, nonce,
provider response, and fault point is explicit test data; a test may not replace it with current
time, randomness, network state, or host paths. Multi-variant cases evaluate each variant
independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/property/test_reducer_equivalence.py` and `tests/conformance/adapters/test_ledger_port.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
