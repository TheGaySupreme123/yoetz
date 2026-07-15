# tests/integration/objects/test_envelope_and_encrypted_files.py — object envelope and encrypted file lifecycle

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/objects/envelope.md`, `src/yoetz/adapters/objects/encrypted_files.md`
**Imported by:** integration object-store tests

## Purpose

Prove the object envelope and encrypted file backend produce durable, verified, bounded artifacts.

## Public surface

- `test_stage_fsync_rename_dirfsync_finalize` — the full publication path succeeds durably.
- `test_verified_open_reads_only_finalized_objects` — only finalized objects can be opened.
- `test_header_and_envelope_digest_fields_match_reference` — every authenticated metadata field
  is exact and the external complete-frame digest matches.
- `test_reviewed_envelope_vectors_round_trip_and_reject_mutations` — golden vectors round-trip and
  truncation, corruption, appended bytes, wrong versions, and checksum changes fail closed.
- `test_staging_path_derivation_is_collision_safe` — generated staging paths remain contained,
  distinct, and deterministic under reviewed collision/path mutations.

## Behavior

The test uses reviewed vectors and a temporary filesystem root to assert:

- staging writes an opaque temporary artifact;
- fsync, rename, and directory fsync happen in order;
- the finalized object can be reopened only after publication;
- headers, complete-frame envelope digests, raw-byte/domain commitments, RFC 3394 wrapped DEKs, and
  algorithms match the reviewed envelope format;
- reviewed explicit-input envelope bytes parse and re-emit exactly; `header_len` is u32 big-endian,
  header JSON is JCS, and size/digest/header/AAD/nonce/ciphertext/tag/trailing-byte mutations fail;
- structural decoding alone never claims GCM authentication, while `open_verified` rejects every
  task/kind/created-at/object/slot/media/algorithm/reference substitution before yielding bytes;
- two logical publications of the same plaintext retain the same commitment but intentionally use
  fresh object IDs/DEKs/nonces and may have different envelope digests;
- staging path derivation rejects traversal, aliasing, case-collision, symlink, hardlink, and
  destination-collision cases without exposing or overwriting another staged object;
- a finalized object with a missing key, corrupted/truncated envelope, unsupported version, wrong
  slot/algorithm, or mismatched commitment is never returned as verified plaintext.

## Errors and edge cases

- A temp/orphan object that becomes visible too early fails.
- A finalized object with mismatched metadata fails.
- Any parser that accepts trailing bytes, truncation, frame-digest drift, or an unsupported version
  fails.
- A derived staging path that escapes the object root or aliases another object fails.

## Invariants

1. Publication is atomic and durable.
2. Verification happens before open.
3. Metadata matches the reviewed envelope.
4. Rejection behavior is deterministic for every reviewed malformed vector.
5. Staging paths remain contained and collision-safe.

## Tests

- `tests/integration/objects/test_envelope_and_encrypted_files.py`

## Open questions

None.
