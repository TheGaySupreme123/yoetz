# tests/property/test_canonical_properties.py — canonical parser and encoder properties

**Wave:** A | **ADRs:** ADR-002, ADR-005 | **Imports (spec-tree):**
`tests/property/strategies/json_values.py`, `src/yoetz/protocol/canonical.py`,
`tests/fixture_loader.py`, `fixtures/manifest.json`
**Imported by:** property-based canonicalization tests

## Purpose

Prove canonical parse/encode properties beyond the fixed vectors used by unit tests.

## Public surface

- `test_parse_encode_round_trip` — accepted list/tuple values re-encode to identical canonical
  bytes even though wire parsing materializes arrays as lists.
- `test_duplicate_keys_and_invalid_bytes_fail_first` — byte-level rejection happens before model
  validation.
- `test_object_insertion_order_does_not_change_bytes` — generated permutations of one mapping
  have one canonical representation.

## Behavior

The property suite explores the full generated JSON surface and proves:

- idempotent canonical bytes for accepted values:
  `canonical_encode(strict_json_parse(canonical_encode(value))) == canonical_encode(value)`;
- rejection of every `(invalid_bytes, expected_reason)` pair with that exact reason before any
  model logic;
- stable bytes and digests across generated object insertion-order variants, while array order
  remains significant;
- shrinking keeps a minimal reproducible counterexample.

## Errors and edge cases

- A property that passes only because the generator is too narrow is invalid.
- A result that depends on mapping insertion order, or silently normalizes array order, fails.
- Cross-process environment cells are owned by
  `tests/conformance/protocol/test_canonical_cross_process.py`; property generation does not define
  a competing matrix.

## Invariants

1. Parse/encode is byte-idempotent on the accepted canonical subset; tuple/list representation is
   intentionally not a Python object-identity promise.
2. Rejection precedes model construction.
3. Mapping insertion order does not affect canonical bytes; array order does.

## Tests

- `tests/property/test_canonical_properties.py`
- Reviewed examples are loaded from root `fixtures/` through `tests/fixture_loader.py`; installed
  mirror byte parity belongs to `tests/packaging/test_resource_byte_parity.py`.

## Open questions

None.
