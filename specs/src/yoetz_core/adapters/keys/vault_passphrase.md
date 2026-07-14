# src/yoetz_core/adapters/keys/vault_passphrase.py — explicit passphrase-backed IVK envelope

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):**
`ports/secret_memory.md`, pure passphrase policy from `service/confidential_protocol.md` |
**Imported by:** `service/vault.md`

## Purpose

Wraps/unwraps the installation vault key for an explicitly selected passphrase-mode vault. It is
not portable bundle recovery and never activates as keyring fallback.

## Public surface

- `create_vault_root_envelope(ivk_handle, initialize_handle) -> VaultRootEnvelope`; the second
  handle must have purpose `vault_initialize`.
- `unlock_vault_root_envelope(envelope, unlock_handle) -> SecretHandle(vault_root)`.
- Strict authenticated envelope/KDF structural types and bounded errors.
- Candidate creation parameters: Argon2 version `19`, `time_cost=3`, `memory_kib=262_144`,
  `parallelism=1`, output `32` bytes; E-006 may amend these exact creation values before release
  without changing the v1 field/validation contract.

## Behavior

Creation consumes a `vault_initialize` one-shot handle and generates an independent 32-byte
OS-CSPRNG salt. `Argon2id(secret_bytes, salt, version=19, time_cost, memory_kib, parallelism,
output_bytes=32)` produces `argon_root`. Derive two 32-byte subkeys with HKDF-SHA-256, exact salt
`b"yoetz/passphrase-subkey-root/v1"`, and exact ASCII info
`b"yoetz/vault-root-wrap/v1"` / `b"yoetz/vault-root-auth/v1"`. Wrap the exact 32-byte IVK with
nonce-free AES-256-KW RFC 3394 under the wrap subkey, producing exactly 40 bytes.

The persisted envelope is exact JCS UTF-8. Its authenticated body has only:

```text
{
  "binding": {"installation_id": <ins_>, "vault_mode": "passphrase"},
  "format": "yoetz-vault-root/1",
  "kdf": {
    "algorithm": "argon2id", "memory_kib": <int>, "output_bytes": 32,
    "parallelism": <int>, "salt": <base64url-no-padding 32 bytes>,
    "time_cost": <int>, "version": 19
  },
  "wrap_algorithm": "aes-256-kw-rfc3394",
  "wrapped_ivk": <base64url-no-padding 40 bytes>
}
```

Compute `auth_tag = HMAC-SHA-256(K_auth,
b"yoetz/vault-root-envelope/v1\x00" || JCS(body))`; the final JCS envelope adds exactly
`"auth_algorithm":"hmac-sha256"` and base64url-no-padding 32-byte `"auth_tag"` beside the body
fields. Unknown/duplicate fields, noncanonical JSON/base64url, wrong lengths/constants/binding, or
trailing bytes fail. Before Argon2 work, require `time_cost 1..10`, `memory_kib 65_536..1_048_576`,
`parallelism 1..8`, exact output/version/salt lengths, and a total envelope cap of 16 KiB. Creation
uses exactly the candidate parameters above until E-006 replaces them in this owner and vectors.

Setup requires the explicit pristine-install local-human confidential ceremony. Later unwrap
consumes a distinct `vault_unlock` handle, derives the same subkeys, verifies HMAC in constant-work
logic, RFC-3394 unwraps the IVK, and verifies the encrypted vault sentinel before ready. Creation
and unwrap both enforce 16..1,024 bytes, strict UTF-8, no U+0000/U+000A/U+000D, and no trim/
normalization/case-fold/replacement. There is no AES-GCM use under a passphrase-derived stable key,
password guessing, purpose substitution, or immutable secret input.

## Errors and edge cases

Wrong secret, HMAC mismatch, RFC 3394 integrity failure, or tamper expose only bounded unlock
failure. Parameter validation occurs before allocation/KDF to bound hostile envelopes; secret-
dependent failures remain indistinguishable. Cancellation/failure overwrites buffers.
No env/argv/config/stdin/file/password-FD input path exists.
Creation with `vault_unlock`, unwrap with `vault_initialize`, or either handle in an existing/wrong
mode is `secret_purpose_mismatch`/`initialization_forbidden` before KDF or mutation.

## Invariants

1. Only explicitly passphrase-mode vault uses this adapter.
2. Persisted envelope contains only authenticated parameters/binding and RFC-3394-wrapped IVK,
   never passphrase/plain IVK.
3. Secret purpose and one-shot consumption are enforced.
4. Envelope creation and later unwrap require distinct initialize/unlock purposes.
5. The persisted envelope binds the exact bytes entered; no alternate Unicode representation is
   guessed or silently canonicalized.

## Tests

- `tests/integration/service/test_locked_ready_transitions.py` covers setup/unlock/relock vectors.
- `fixtures/canonical/object-envelope.case.json` freezes the exact vault-root KDF/HKDF/AES-KW/
  HMAC/JCS/base64url known-answer and hostile-parameter vectors.
- `tests/subprocess/test_service_unlock_boundary.py` covers forbidden channels.

## Open questions

None.
