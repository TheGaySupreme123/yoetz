# fixtures/adversarial/ADV-009-wrong-semantic-finding-rejected.case.json — wrong semantic finding rejection adversarial case

**Wave:** A-E | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/conformance/honesty/test_adversarial_cases.py, tests/integration/providers/test_fake_provider_coordinator.py

## Purpose

Freeze human rejection of a substantively wrong semantic suggestion without erasing provenance as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-009"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains scripted wrong suggestion, hollow rejection, supported rejection, and deterministic-control variants. The `expected` section freezes retained semantic provenance, weak_or_stale_response only for unsupported rejection, and no deterministic upgrade. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/conformance/honesty/test_adversarial_cases.py` and `tests/integration/providers/test_fake_provider_coordinator.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
