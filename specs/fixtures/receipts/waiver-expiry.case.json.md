# fixtures/receipts/waiver-expiry.case.json — waiver expiry receipt case

**Wave:** A-D | **ADRs:** ADR-002, ADR-004 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/application/test_respond_status_receipt.py, tests/conformance/operations/test_respond_contract.py

## Purpose

Freeze frontier- and time-bounded recording of a confirmed local human waiver without treating it
as finding resolution, using synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "RCP-004"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains active, expired, wrong-frontier, and noninteractive waiver variants.
The active, expired, and wrong-frontier variants begin with valid interactive-local-human
`response_recorded` events. All three remain dispositions only: they retain exact scope/expiry
data, but never resolve or suppress the current finding. Compact wording may describe whether a
recorded expiry is before the explicit receipt timestamp, but that comparison never changes
resolution. The noninteractive variant is an operation-level
`respond(disposition=waived)` attempt from noninteractive CLI/MCP authority. It is rejected as
`INVALID_REQUEST` before append, creates no `response_recorded` event, and therefore never reaches
or mutates receipt-builder finding state. This fixture does not fabricate an invalid stored
waiver as a receipt input. Every referenced identifier, timestamp, key, digest, nonce, provider
response, and fault point is explicit test data; a test may not replace it with current time,
randomness, network state, or host paths. Multi-variant cases evaluate each variant independently
and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/integration/application/test_respond_status_receipt.py` and `tests/conformance/operations/test_respond_contract.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
