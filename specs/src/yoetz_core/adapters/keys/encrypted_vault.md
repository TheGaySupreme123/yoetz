# src/yoetz_core/adapters/keys/encrypted_vault.py — authenticated encrypted vault records

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `ports/secret_memory.md`,
`config/paths.md` | **Imported by:** `service/vault.md`

## Purpose

Persists IVK-wrapped bundle keys and provider credentials as authenticated owner-only records
without exposing plaintext or requiring another migration resource/database.

## Public surface

- `class EncryptedVaultStore` with `initialize`, `load_record`, `create_record`, `replace_
  credential_record`, `delete_after_migration`, `verify_sentinel`, and `close`.
- Closed `VaultRecordKind` — `vault_sentinel`, `bundle_key`, `provider_credential`,
  `recovery_metadata`. Installation lookup/log/privacy MAC keys are derivations, never records.
- Frozen record header/envelope values and bounded `EncryptedVaultError`.

## Behavior

Use one immutable file per record generation under the owner-only vault directory. A record never
encrypts payload directly under the stable IVK. It generates a fresh 256-bit record DEK and random
96-bit payload nonce, performs exactly one AES-256-GCM encryption with canonical header bytes as
AAD, and wraps the exact 32-byte record DEK under the IVK with nonce-free AES-256-KW (RFC 3394),
which yields exactly 40 bytes.

The frame is exactly `b"YZV1" | 0x01 | u32be(header_length) | header_json | 12-byte payload_nonce |
plaintext_size bytes ciphertext | 16-byte tag`; `header_length` is `1..16384`, total length is exact,
and trailing bytes are forbidden. JCS UTF-8 `header_json` contains only `binding_digest`,
`format="yoetz-vault-record/1"`, positive `generation`, `payload_algorithm="aes-256-gcm"`,
`plaintext_size`, `record_id`, `record_kind`, `wrap_algorithm="aes-256-kw-rfc3394"`, and
`wrapped_record_dek` (base64url without padding of exactly 40 bytes). It contains no provider/task/
user plaintext, wrap nonce, embedded checksum, credential length detail, or open map.

The vault internally derives nonexported `K_vault_locator` from IVK with HKDF-SHA-256, salt
`b"yoetz/vault-internal-root/v1"`, info `b"yoetz/vault-record-locator/v1"`, length 32. `record_id`
uses exact `binding_bytes = JCS({"kind": kind, "structural_binding": binding})`, where the closed
binding union is: sentinel `{installation_id}`; bundle key `{task_id, key_slot}`; provider credential
`{provider_id, endpoint_profile_id, endpoint_profile_version, authorization_scope_digest, purpose}`;
recovery metadata `{task_id, recovery_artifact_digest}`. All values are validated bounded IDs/enums/
digests; no provider URL, task title, raw scope/path, or credential is legal.
`binding_digest` is `hmac-sha256:` plus lowercase
`HMAC-SHA-256(K_vault_locator, b"yoetz/vault-record-binding/v1\x00" || binding_bytes)` hex, and
`record_id` is `vrec_` plus those same 64 hex digits. It is not an independent vault record or
cross-module `MacKeyHandle`. The exact path is `<record_id>.<generation>.yzv`. Each record's
`envelope_digest` is lowercase `sha256:` of the complete exact YZV1 frame.

The structural current-generation `index_value` is exact JCS
`{"format":"yoetz-vault-index/1","records":[...]}`, with records ASCII-sorted by `record_id` and
each closed entry exactly `{record_id, generation, envelope_digest}`. `index_mac` is
`hmac-sha256:` plus lowercase
`HMAC-SHA-256(K_vault_locator, b"yoetz/vault-record-index/v1\x00" || JCS(index_value))` hex. The
canonical `vault-index.json` wrapper is exactly `{ "index": index_value, "index_mac": index_mac }`
under JCS (spaces shown here are explanatory only). Unknown/duplicate/unsorted entries, a generation
below 1, digest mismatch, or MAC mismatch fail closed. The index contains no binding plaintext or
secret payload.

Atomic publication writes/fsyncs/verifies a new owner-only no-overwrite record, then atomically
publishes and directory-fsyncs the generation-CAS index; a crash before the index swap leaves only
an unreferenced encrypted record. Bundle-key records are immutable generation 1. Credential
replacement requires explicit human authorization, a fresh record DEK/nonce, generation+1, and an
exact current-index CAS. No plaintext temp exists.

## Errors and edge cases

Truncation, appended bytes, wrong IVK, RFC 3394 integrity failure, frame-digest/nonce/header/binding/
generation mismatch, duplicate/foreign file, unsafe permissions, or ambiguous crash state fails
closed. An unindexed record is an encrypted orphan and never current authority. Listing records is
service-internal and never public status.

## Invariants

1. Persisted key/credential material is always authenticated ciphertext.
2. Record path/name/header reveal no user/provider/credential plaintext.
3. Existing bundle-key records are immutable; credential rotation is explicit generation-CAS with
   a fresh record DEK, and stable IVK performs only nonce-free key wrapping plus internal derivation.
4. No client/provider adapter receives IVK or raw record payload.

## Tests

- `tests/integration/service/test_encrypted_vault.py` covers RFC 3394 and exact YZV1/JCS/u32be/
  base64url vectors, fresh-record-DEK behavior, record/index atomic crash matrix, binding,
  keyed binding/record-ID/index-MAC known answers, sort/duplicate/generation/digest mutations,
  permissions, tamper/truncation/appended bytes, and no-plaintext canaries.

## Open questions

None.
