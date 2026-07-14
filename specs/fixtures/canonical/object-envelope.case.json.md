# fixtures/canonical/object-envelope.case.json — object, vault-root, and recovery envelope vectors

**Wave:** A | **ADRs:** ADR-004 | **Imports (spec-tree):** protocol schemas, fixture case contract, owning policy specs | **Imported by:** tests/integration/objects/test_envelope_and_encrypted_files.py, tests/integration/objects/test_portable_recovery.py

## Purpose

Freeze the reviewed object binary frame plus passphrase vault-root and portable-recovery canonical
envelopes and their authentication failure behavior as synthetic, public, deterministic evidence
before implementation.

## Public surface

One canonical strict-JSON fixture case with `fixture_schema: "yoetz.fixture-case/1.0.0"`, `fixture_version: "1.0.0"`, `fixture_id: "CAN-009"`, a publication-safe purpose, owning requirement IDs, minimum component versions, deterministic controls, typed input variants, and typed expected assertions.

## Behavior

The `input` section contains test-only `K_wrap`/`K_commit`/payload-DEK bytes, all 17 exact
trailing-NUL object-kind domains, raw binary and canonical-JSON plaintexts, RFC 3394 known-answer
wrap vectors, fixed object/task/kind/slot/media/creation metadata, exact JCS header bytes, fixed
payload nonces, and tampered header/nonce/ciphertext/tag/reference variants. The `expected` section
freezes `HMAC-SHA-256(K_commit, domain || raw_plaintext)` commitments, the exact
`YZO1 | 0x01 | u32be header_len | header | nonce | ciphertext | tag` bytes, complete-frame
`envelope_digest`, structural-decode results, and indistinguishable safe full-verification failures.
It explicitly distinguishes fixed-input encoder determinism from two fresh logical publications,
which share a plaintext commitment but not an object ID or required envelope digest. Every
identifier, timestamp, key, digest, nonce, and fault point is explicit synthetic test data; a test
may not replace it with current time, randomness, network state, or host paths. Multi-variant cases
evaluate each variant independently and declare the relationship between their outcomes.

Separate vault-root and portable-recovery groups contain fixed strict-UTF-8 passphrase bytes,
32-byte salts, candidate and boundary Argon2id parameters, exact HKDF subkeys, RFC 3394 wrapped
IVK/BMK bytes, canonical authenticated bodies, HMAC tags, final JCS/base64url envelopes and SHA-256
artifact digests. Rejections cover every out-of-range parameter before KDF, unknown/duplicate field,
wrong binding/purpose, noncanonical encoding, tag/wrap tamper, wrong secret, and trailing byte.

## Errors and edge cases

The loader rejects a wrong fixture ID/schema, undeclared field, float, duplicate key, invalid base64/hex, member digest mismatch, unknown control token, unsorted set-valued field, or reference outside this case. Rejection diagnostics identify the fixture and structural pointer but never echo secret-shaped fixture values.

## Invariants

The file is canonical JSON, self-contained, offline, synthetic, immutable after release, and has one unambiguous expected outcome per declared variant. It cannot strengthen coverage or assurance beyond the owning protocol and policy specs.

## Tests

Consumed directly by `tests/integration/objects/test_envelope_and_encrypted_files.py` and `tests/integration/objects/test_portable_recovery.py`; fixture manifest and packaging tests additionally lock its exact bytes.

## Open questions

None.
