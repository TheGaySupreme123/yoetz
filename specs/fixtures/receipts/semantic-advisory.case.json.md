# fixtures/receipts/semantic-advisory.case.json — semantic advisory receipt case

**Wave:** A-E | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/application/test_respond_status_receipt.py, tests/conformance/honesty/test_receipt_wording.py

## Purpose

Freeze receipt representation of successful advisory semantic evaluation without upgrading deterministic assurance as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "RCP-002"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains a current deterministic check, provisional provider-attempt facts, a
durable terminal privacy receipt, and advisory findings. The expected success variant freezes
`semantic_status=succeeded`, `semantic_reason=semantic_completed`, and final
`SemanticProvenance` constructed only after that receipt, including exact attempt, dispatch kind,
authority/reservation, receipt, commitment, provider/profile/model, and bounded usage fields. A
predispatch blocked variant freezes an exact status/reason with no provenance and no semantic
findings. Cross-paired reasons and provisional provenance are explicit rejection variants. The
expected section also freezes coverage limits and wording that labels semantic conclusions
advisory. Because the success variant's advisory findings remain unresolved at the frozen
frontier, its exact receipt `conclusion` is `unresolved_findings_remain`; deterministic coverage is
not upgraded merely because semantic evaluation succeeded. Every referenced identifier, timestamp,
key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace
it with current time, randomness, network
state, or host paths. Multi-variant cases evaluate each variant independently and declare the
relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid
base64/hex, member digest mismatch, unknown/cross-paired semantic reason, predispatch or provisional
provenance, a non-durable referenced privacy receipt, unknown control token, unsorted set-valued
field, or reference outside this case. Rejection diagnostics identify the fixture and structural
pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/integration/application/test_respond_status_receipt.py` and `tests/conformance/honesty/test_receipt_wording.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
