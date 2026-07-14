# fixtures/canonical/restricted-json-rejections.case.json — restricted JSON rejection vectors

**Wave:** A | **ADRs:** ADR-002 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/unit/protocol/test_strict_json.py, tests/conformance/protocol/test_frozen_schemas.py

## Purpose

Freeze every syntax or value class the restricted JSON boundary must reject as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "CAN-003"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains invalid UTF-8, BOM, NUL, duplicate names, floats, NaN, infinity, negative zero, unsafe integers, lone surrogates, and malformed syntax as base64 bytes. The `expected` section freezes the exact stable validation category and JSON pointer or byte offset where safe; no raw rejected value appears in public errors. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/unit/protocol/test_strict_json.py` and `tests/conformance/protocol/test_frozen_schemas.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
