# fixtures/adversarial/ADV-007-crash-retry-duplicate.case.json — crash retry duplicate adversarial case

**Wave:** A-C | **ADRs:** ADR-001, ADR-003 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/subprocess/test_reopen_retry_replay.py, tests/conformance/protocol/test_idempotency_and_frontiers.py

## Purpose

Freeze exactly-once logical publication across crash-before-commit and crash-after-commit-before-response as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "ADV-007"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains fixed request IDs, a two-event batch, semantic fault-script kill points, identical retry, and changed-request retry. The `expected` section freezes one atomic logical batch, stable replayed result, no partial entries, and IDEMPOTENCY_CONFLICT for changed logical input. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/subprocess/test_reopen_retry_replay.py` and `tests/conformance/protocol/test_idempotency_and_frontiers.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
