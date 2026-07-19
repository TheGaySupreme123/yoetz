# fixtures/imports/codex/secret-redaction.case.json — Codex import secret redaction case

**Wave:** A-D | **ADRs:** ADR-004, ADR-005 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/privacy/test_plaintext_canary_sweep.py, tests/capability/test_codex_jsonl_import.py

## Purpose

Freeze non-disclosure of source secrets through importer diagnostics, logs, or public report as synthetic, public, deterministic evidence before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "IMP-005"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains synthetic canaries in commands, paths, model text, environment-shaped
values, and one raw-stderr-classified disclosure vector that cannot enter the ordinary import
request. Capture metadata freezes the required stderr-absent false/zero constants and carries no
stderr bytes or commitment. Command variants include shell environment assignments, inline
`Authorization`/header flags, bearer and API-key forms, credential-bearing URLs, and tokens split
across JSON escapes, UTF-8 chunks, and scanner chunk boundaries. The import expectation freezes
exact encrypted raw source/payload retention with no destructive import-time content scan,
allowlisted structural outputs, keyed source commitments where allowed, and zero canary occurrences
outside encrypted objects. Disclosure variants then select each imported item for external
`llm_inference`, `local_model`, `agent_context`, and `local_human_view` sinks: every variant
traverses the ordinary classifier
and exact-byte scanner, and every secret match is blocked before sink serialization/I/O. A clean
imported control reaches only the independently policy-approved sink. Every referenced identifier,
timestamp, key, digest, nonce, provider response, and fault point is explicit test data; a test may
not replace it with current time, randomness, network state, or host paths. Multi-variant cases
evaluate each variant independently and declare the relationship between their outcomes.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has
one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance
beyond the owning protocol and policy specs. Encrypted local retention is never treated as
disclosure authority.

## Tests

Consumed directly by `tests/integration/privacy/test_plaintext_canary_sweep.py` and `tests/capability/test_codex_jsonl_import.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
