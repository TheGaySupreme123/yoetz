# tests/unit/protocol/test_canonical_vectors.py — golden canonical bytes and digests

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/protocol/canonical.py`, `specs/fixtures/README.md`
**Imported by:** the protocol unit suite

## Purpose

Lock a reviewed set of canonical JSON vectors so the encoder, digest helper, and fixture loader all
agree on exact bytes.

## Public surface

- `test_canonical_bytes_match_golden_vectors` — each fixture’s encoded bytes match the golden
  artifact.
- `test_digest_matches_reviewed_sha256` — digest output matches the fixture checksum.
- `test_round_trip_idempotence` — canonical input stays canonical after parse/encode.
- `test_vector_loader_is_order_stable` — fixture discovery order does not change semantics.

## Behavior

The suite uses frozen bytes from `specs/fixtures/` and asserts:

- exact byte-for-byte encoding for reviewed canonical values;
- digest format `sha256:<hex>` with lowercase hex;
- no accidental dependence on file order or filesystem metadata;
- parse/encode round trips preserve the same canonical representation.

## Errors and edge cases

- A changed fixture requires an explicit review update.
- A digest mismatch is a contract failure, not a test data nuisance.

## Invariants

1. Golden canonical vectors are immutable contract evidence.
2. The test never recomputes expected values from the implementation under test.
3. Canonical bytes and canonical digests are both locked.

## Tests

- `tests/unit/protocol/test_canonical_vectors.py`

## Open questions

None.
