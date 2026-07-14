# tests/conformance/protocol/test_canonical_cross_process.py — cross-process canonical bytes

**Wave:** A | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`src/yoetz_core/protocol/canonical.md`, `tests/property/strategies/json_values.py.md`
**Imported by:** conformance protocol tests

## Purpose

Prove canonical bytes and digests remain identical when exercised across process boundaries.

## Public surface

- `test_parse_encode_bytes_match_across_processes` — canonical bytes do not drift.
- `test_digest_match_across_processes` — digests do not drift.
- `test_environment_noise_does_not_change_output` — hash seed, locale, and TZ do not matter.

## Behavior

The test launches separate processes around the same canonical fixtures and asserts:

- exact byte equality;
- exact digest equality;
- no dependence on environment noise or cwd differences;
- no hidden stdout/stderr noise from the canonical path.

## Errors and edge cases

- A process-only variation in canonical output fails.

## Invariants

1. Canonicalization is cross-process stable.
2. Environment noise does not matter.
3. Output bytes are the oracle.

## Tests

- `tests/conformance/protocol/test_canonical_cross_process.py`

## Open questions

None.
