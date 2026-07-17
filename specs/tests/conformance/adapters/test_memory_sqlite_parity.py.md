# tests/conformance/adapters/test_memory_sqlite_parity.py — memory and SQLite parity harness

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):**
`src/yoetz/adapters/memory/ledger.md`, `src/yoetz/adapters/sqlite/repository.md`,
`src/yoetz/kernel/reducers.md`
**Imported by:** conformance adapter tests

## Purpose

Provide the broad parity harness that compares canonical public artifacts across memory and SQLite.

## Public surface

- `test_public_artifacts_match_across_backends` — canonical artifacts are equal where expected.
- `test_private_artifacts_are_not_compared` — internal row IDs, paths, and timings are ignored.
- `test_supported_failures_match_by_code_and_shape` — public errors match across backends.

## Behavior

The harness compares request/result/event/projection/finding/receipt artifacts across backends and
asserts:

- the same logical scenario produces the same public outcome;
- a capped check with nonzero `suppressed_count` yields byte-identical latest-tested projection and
  receipt `suppressed_finding_count` in memory and SQLite before and after rebuild;
- private row shape differences are not part of the oracle;
- supported failures map to the same public code and bounded details.

## Errors and edge cases

- A mismatch in a public artifact fails.
- A comparison of private storage internals fails the suite’s purpose.

## Invariants

1. Public artifacts are the parity oracle.
2. Memory and SQLite are equivalent where the contract says they are.
3. Private storage internals stay out of parity.

## Tests

- `tests/conformance/adapters/test_memory_sqlite_parity.py`

## Open questions

None.
