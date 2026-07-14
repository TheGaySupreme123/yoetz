# src/yoetz_core/adapters/objects/envelope.py — the versioned Yoetz object envelope

**Wave:** C | **ADRs:** ADR-004 | **Imports (spec-tree):** `protocol/canonical.md`,
`protocol/errors.md`, `protocol/models.md`
**Imported by:** `adapters/objects/encrypted_files.md`, `adapters/sqlite/repository.md`,
`adapters/sqlite/recovery.md`, `ports/objects.md`

## Purpose

This file defines the object envelope format that wraps encrypted immutable payloads. The envelope
is the file-format boundary for large or sensitive content that must move through the ledger by
reference rather than in plaintext.

The envelope is intentionally boring. Its job is to make one object blob self-describing enough to
support publication, recovery, and verification without exposing the plaintext or leaking mutable
state into the ledger. The envelope must be stable across platforms and deterministic for the same
fully explicit header, payload nonce, ciphertext, and tag inputs. Encrypting the same logical
plaintext again intentionally creates a fresh object ID, DEK, nonce, and different envelope bytes.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `ObjectEnvelope` | frozen dataclass for the versioned object envelope |
| `ObjectEnvelopeHeader` | frozen dataclass for non-secret header metadata |
| `encode_object_envelope(...)` | serialize header + ciphertext + auth tag into a single object blob |
| `decode_object_envelope(...)` | validate and parse an envelope blob back into metadata and ciphertext |
| `validate_object_envelope(...)` | structural guard for supported version, header shape, and size bounds |

## Behavior

The envelope is versioned and authenticated. It contains:

- a stable magic / format identifier;
- a format version;
- an object ID;
- non-secret metadata needed for replay, recovery, and diagnostics;
- the AEAD ciphertext and authentication tag;
- exact size information needed to parse the frame without ambiguity.

The non-secret header is the part that downstream code can inspect before decryption. The header
must answer four questions without revealing the payload:

1. what format version is this;
2. which object does this blob belong to;
3. how large is the expected object;
4. what extra non-secret classification data, if any, is needed by recovery or diagnostics.

`ObjectEnvelopeHeader` is the frozen structure that carries those answers. It is not a general
metadata bag. Fields that are not required for publication, recovery, or trust decisions do not
belong here.

`encode_object_envelope(...)` produces one canonical byte sequence for a given already-authenticated
header/payload-nonce/ciphertext/tag tuple. It keeps secret-bearing material out of the header and
does not derive filenames from plaintext. The object-encryption caller must have used the exact
canonical header bytes as AES-GCM associated data, so later full verification detects tampering.

Encoding is one-way with respect to trust. The encoder may validate the shape of the header before
serialization, but it must not try to interpret business semantics that live in the ledger or the
key backend. It only turns an already-decided object into a durable wire blob.

`decode_object_envelope(...)` performs the structural inverse only. It verifies magic/version,
big-endian length and total-size bounds, canonical header syntax/shape, component lengths, and no
trailing bytes, then returns the parsed header, payload nonce, ciphertext, and tag. It has no key and
cannot authenticate GCM AAD, unwrap a DEK, or call the result intact. `ObjectStorePort.open_verified`
owns full-envelope digest comparison, RFC 3394 unwrap, header-as-AAD GCM authentication, complete
header/reference equality, and plaintext commitment verification.

Decoding returns enough information for the caller to decide whether the object can be opened:

- the parsed header;
- the raw ciphertext;
- the exact 16-byte authentication tag;
- the observed component byte counts needed by the caller.

It does not unwrap the payload itself. That remains the responsibility of the key/object-store
layer that has the right secret material.

`validate_object_envelope(...)` is a structural guard used before publication and during recovery.
It does not establish cryptographic trust. It rejects:

- unknown format versions;
- truncated or overlong blobs;
- malformed header fields;
- unsupported object kinds or metadata combinations;
- envelopes whose declared size disagrees with the observed bytes, or that contain trailing bytes.

Validation is deliberately strict. A blob that cannot be proven valid is treated as invalid, not as
an empty object and not as an unknown-but-usable version. The module should prefer a hard failure
over guessing how to coerce malformed bytes into a tolerated shape.

Versioning rules:

- version changes are explicit and reviewable;
- new versions may add fields only when older readers can still reject cleanly;
- the current version must remain round-trippable within one repository revision;
- a decoder must never silently accept an unsupported future format as if it were current.

Canonicality rules:

- identical explicit header/payload-nonce/ciphertext/tag inputs produce identical frame bytes;
- the frame is exactly `b"YZO1" | 0x01 | u32be(header_length) | header_json |
  12-byte payload_nonce | plaintext_size bytes ciphertext | 16-byte tag`;
- `header_length` is `1..16384` and `header_json` is exact JCS canonical UTF-8;
- the header has only `created_at`, `encryption_format`, `key_slot`, `media_type`, `object_id`,
  `object_kind`, `payload_algorithm`, `plaintext_size`, `task_id`, `wrap_algorithm`, and
  `wrapped_dek`, with constants/encodings exactly as frozen by `ports/objects.md`;
- there is no embedded checksum, wrap nonce, optional metadata map, or alternate integer encoding;
- `ObjectRef.envelope_digest` is computed externally over the complete final frame and therefore
  cannot be an embedded self-checksum;
- the header's object kind uses the exact closed `ObjectKind` vocabulary in `specs/INTERFACES.md`.

The envelope module is intentionally narrow. It does not decide key storage, recovery policy,
object-store layout, or file-system safety. It only defines the object blob’s versioned wire
identity.

## Errors and edge cases

- Structural corruption, truncation, noncanonical header bytes, trailing bytes, or unsupported
  version results are hard failures.
- A decoded envelope must not expose raw plaintext unless the caller separately unwraps it.
- The envelope never validates a key itself; key availability belongs to the key modules.
- This module never claims an envelope header is authenticated. A header-only parse is acceptable
  only for bounded diagnostics; trust decisions require `ObjectStorePort.open_verified`.

## Invariants

1. One envelope version means one stable wire format.
2. The header stays non-secret.
3. This module proves structure only; the object store verifies digest, wrapped DEK, GCM tag/AAD,
   complete reference binding, and plaintext commitment before any object bytes are trusted.
4. Envelope parsing is deterministic and independent of system locale.
5. A valid envelope alone does not imply the bundle is writable; recovery and key checks still apply.
6. No checksum is embedded in the envelope; the external `envelope_digest` covers the exact final
   frame.
7. The envelope never carries mutable operational state such as locks, checkpoints, or leasing info.

## Tests

- `tests/integration/objects/test_envelope_and_encrypted_files.py` — exact u32-big-endian frame
  vectors, fixed-input byte identity, structural rejection, full object-store authentication,
  external envelope-digest stability, and proof that repeated logical encryption is intentionally
  different.
- `tests/integration/storage/test_quarantine_and_recovery.py` — envelope parsing during recovery and
  quarantine classification.

## Open questions

None.

R-001 is the sole central envelope threat-review gate.
