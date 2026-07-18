# tests/unit/protocol/test_canonical_vectors.py — golden canonical bytes and digests

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/canonical.py`, `tests/fixture_loader.py`, `fixtures/manifest.json`
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
- `test_non_json_rejection_paths_are_inline` — Python-only inputs cover rejection reasons that
  cannot be faithfully encoded as frozen JSON fixture values.

## Behavior

The suite loads reviewed root `fixtures/canonical/*.case.json` bytes only through the
manifest-bound `tests/fixture_loader.py`; it never parses Markdown fixture-spec shadows and never
falls back to package-resource mirrors. It asserts:

- exact byte-for-byte encoding for reviewed canonical values;
- digest format `sha256:<hex>` with lowercase hex;
- no accidental dependence on file order or filesystem metadata;
- parse/encode round trips preserve the same canonical representation.
- inline cases assert exact reasons for: non-bytes parser input (`input_not_bytes`), bool/negative/
  above-int64 integer-string helper input (`integer_out_of_sqlite_range`), a mapping with a
  non-string key (`object_key_not_string`), an unsupported Python object
  (`unsupported_json_type`), and invalid entry preimages (`not_an_accepted_envelope`), including
  preimages with top-level `entry_digest` or decoded `payload`.
- list and tuple arrays encode identically, while every other sequence-like object is rejected;
  `strict_json_parse` snapshots `bytearray` input before parsing;
- `ensure_canonical_set` accepts only list/tuple outer values, maps every other outer type to
  `unsupported_json_type`, maps non-string/non-ASCII members to `set_member_not_ascii`, and keeps
  duplicate-versus-descending reasons distinct.

## Errors and edge cases

- A changed fixture requires an explicit review update.
- A digest mismatch is a contract failure, not a test data nuisance.
- Inline vectors use explicit constructed Python values and literal expected reasons; they are not
  serialized through JSON or added to the frozen fixture manifest merely to make them representable.
- Byte parity between root fixtures and installed package-resource mirrors is owned by
  `tests/packaging/test_resource_byte_parity.py`; this unit test owns canonical semantics.

## Invariants

1. Golden canonical vectors are immutable contract evidence.
2. The test never recomputes expected values from the implementation under test.
3. Canonical bytes and canonical digests are both locked.

## Tests

- `tests/unit/protocol/test_canonical_vectors.py`

## Open questions

None.
