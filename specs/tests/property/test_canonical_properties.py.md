# tests/property/test_canonical_properties.py — canonical parser and encoder properties

**Wave:** A | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`tests/property/strategies/json_values.py`, `src/yoetz/protocol/canonical.py`
**Imported by:** property-based canonicalization tests

## Purpose

Prove canonical parse/encode properties beyond the fixed vectors used by unit tests.

## Public surface

- `test_parse_encode_round_trip` — accepted values re-encode canonically.
- `test_duplicate_keys_and_invalid_bytes_fail_first` — byte-level rejection happens before model
  validation.
- `test_deterministic_bytes_across_environments` — hash seed, locale, and TZ do not alter bytes.

## Behavior

The property suite explores the full generated JSON surface and proves:

- idempotent parse/encode for accepted values;
- rejection of invalid-byte inputs before any model logic;
- stable bytes and digests across environment variants;
- shrinking keeps a minimal reproducible counterexample.

## Errors and edge cases

- A property that passes only because the generator is too narrow is invalid.
- A result that depends on ambient locale or hash seed fails.

## Invariants

1. Parse/encode is a bijection on the accepted canonical subset.
2. Rejection precedes model construction.
3. Environment noise does not affect canonical bytes.

## Tests

- `tests/property/test_canonical_properties.py`

## Open questions

None.
