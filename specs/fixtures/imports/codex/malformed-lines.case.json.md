# fixtures/imports/codex/malformed-lines.case.json — malformed Codex lines import case

**Wave:** A-D | **ADRs:** ADR-004, ADR-005 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/application/test_import_review.py, tests/capability/test_codex_jsonl_import.py

## Purpose

Freeze bounded fail-safe behavior for malformed, overlong, duplicate-key, and invalid-UTF-8 source lines as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "IMP-003"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains base64 exact source bytes for each malformed-line class within the four-MiB source cap. The `expected` section freezes deterministic line classifications, bounded diagnostics, no plaintext leak, and a partial import report when safe. Every referenced identifier, timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may not replace it with current time, randomness, network state, or host paths. Multi-variant cases evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/integration/application/test_import_review.py` and `tests/capability/test_codex_jsonl_import.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
