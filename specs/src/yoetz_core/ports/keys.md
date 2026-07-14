# src/yoetz_core/ports/keys.py — service-internal opaque key, MAC, and recovery operations

**Wave:** B/C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):**
`ports/secret_memory.md`, `protocol/errors.md` | **Imported by:** `service/vault.md`,
object crypto adapters, key adapters, maintenance recovery

## Purpose

Defines cryptographic operations available inside the ready service without returning IVK, BMK,
installation or bundle derived key, DEK, provider credential, unlock passphrase, or recovery-secret
bytes. The unlocked
`VaultService` implements this port; CLI, MCP, UI, application request types, and local control
schemas cannot import or serialize it.

## Public surface

- `class KeyStorePort(Protocol)` with async `load_bundle_keys`, `create_bundle_keys`, and
  `wrap_recovery`, plus synchronous
  `installation_mac_handle(purpose: MacKeyPurpose) -> MacKeyHandle`. The latter accepts only
  `catalog_lookup`, `log_correlation`, or `privacy_audit`; bundle commitment handles are returned
  only inside `BundleKeys`.
- `@dataclass(frozen=True, slots=True) class BundleKeys` — structural `key_slot` plus opaque
  `WrapKeyHandle` and bundle-commitment `MacKeyHandle`; bound to service/vault/bundle generation
  and invalid after relock. It has no lookup handle.
- `class WrapKeyHandle(Protocol)` — `wrap_dek(SecretHandle) -> WrappedDek` and
  `unwrap_dek(WrappedDek) -> SecretHandle`; both are exactly AES-256-KW per RFC 3394 over a
  32-byte DEK under bundle `K_wrap`, and raw DEK bytes never return.
- `enum MacKeyPurpose` — exactly `bundle_commitment`, `catalog_lookup`, `log_correlation`, and
  `privacy_audit`.
- `class MacKeyHandle(Protocol)` — opaque `mac(domain: bytes, message: bytes) ->
  hmac-sha256:<hex>`, defined exactly as `HMAC-SHA-256(key, domain || message)`. Its purpose,
  service/vault generation, optional bundle generation, and exact domain allowlist are fixed when
  minted; no method exports/rebinds/clones key material.
- `@dataclass(frozen=True, slots=True) class WrappedDek` — exactly `algorithm =
  "aes-256-kw-rfc3394"` and `wrapped: bytes` of length 40; it has no nonce field.
- `class RecoverySecret(Protocol)` — one-shot alias/protocol restricted to
  `SecretHandle(purpose=portable_recovery)`; no `str`/`bytes` constructor.
- `@dataclass(frozen=True, slots=True) class RecoveryArtifact` — authenticated versioned envelope
  and bounded structural manifest.
- `class KeyStoreError(Exception)` and exact bounded reasons: `vault_locked`, `key_missing`,
  `key_id_mismatch`, `unsupported_backend`, `backend_unverified`, recovery missing/wrong/tampered/
  unsupported, `machine_bound_key_missing`, `recovered_key_cannot_decrypt`, `stale_key_handle`,
  `mac_purpose_mismatch`, and `mac_domain_forbidden`.

## Behavior

The vault creates one random BMK per bundle, persists it only as an IVK-wrapped authenticated
record, and derives only `K_wrap` and `K_commit` with HKDF-SHA-256, exact salt
`b"yoetz/bundle-key-root/v1"`, exact ASCII info `b"yoetz/kek/v1"` or
`b"yoetz/commitment/v1"`, and output length 32. No empty/default salt or implicit encoding is legal. A
`BundleKeys` commitment handle has purpose `bundle_commitment` and only the object-kind domains
owned by `ports/objects.md`; it cannot create catalog, log, or privacy-audit commitments.
`K_wrap` is used only by RFC 3394 AES Key Wrap/Unwrap for exact 32-byte DEKs. A successful wrap is
exactly 40 bytes; unwrap rejects any wrong length, integrity failure, or algorithm before returning
an opaque one-use DEK handle. There is no wrap nonce and no AES-GCM use under stable `K_wrap`.

At each successful unlock, the vault derives the sole cross-module installation `K_lookup`, `K_log`, and
`K_audit` from the stable IVK using HKDF-SHA-256 with the exact ADR-004 salt/info byte strings and
output length 32. They are not vault records and are never returned as bytes. The vault-private
`K_vault_locator` is not available through this port. `installation_mac_handle` returns an opaque view
over one already-derived key with this closed binding:

- `catalog_lookup`: `yoetz/start-title/v1\x00`, `yoetz/workspace-ref/v1\x00`, and
  `yoetz/external-task-ref/v1\x00` only;
- `log_correlation`: `yoetz/session-log-id/v1\x00` only;
- `privacy_audit`: `yoetz/privacy-egress-request/v1\x00` only.

The ready-service composition obtains each handle once and injects only that handle into its owning
consumer. The start catalog, logging privacy helper, and outbound audit helper accept a
`MacKeyHandle`, never `bytes`, a key locator, or a keyring/vault object. All handles perform
operations inside the service secret-memory boundary and check service/vault generation on every
call; bundle handles additionally check bundle generation. A purpose/domain mismatch fails before
MAC execution and emits only its bounded reason.

For every MAC purpose, `mac(domain, message)` first requires one byte-exact allowlisted domain that
already ends in `0x00`, then computes HMAC-SHA-256 over the simple concatenation of that domain and
the unmodified message bytes. It performs no JSON encoding, Unicode normalization, length prefix,
hash prepass, or implicit delimiter insertion. Consumers own message encoding explicitly.

`create_bundle_keys` is valid only for an exact fresh start allocation proven by the runtime. An
existing record, partial encrypted state, or conflicting slot never generates replacement
material. `load_bundle_keys` distinguishes locked/missing/mismatch and never returns empty state.

`wrap_recovery` consumes one portable-recovery-purpose handle, derives the reviewed Argon2id
wrapping key, and emits a separate authenticated artifact. Vault-unlock secrets cannot satisfy this
type. Restore requires a fresh confidential local-human secret and clean-profile decrypt proof.

Provider credentials are deliberately absent from this port; `SecretMemoryPort`/
`VaultService.provider_credential` returns a separate least-authority
`ProviderCredentialHandle` bound to provider/endpoint/scope/purpose.

## Errors and edge cases

- Locked/relocked/stale generation fails before any crypto operation.
- An installation handle requested with `bundle_commitment`, or any handle called with a domain
  outside its frozen allowlist, fails with a bounded reason and no digest/key-dependent output.
- Wrong recovery secret/tamper/format/key mismatch retain distinct internal maintenance reasons but
  no secret-derived text.
- `repr`, pickle, copy, dataclass conversion, exceptions, logs, receipts, and control frames never
  reveal handle internals or lengths.
- Python/crypto libraries may copy buffers; best-effort overwrite/page-lock evidence is owned by
  `SecretMemoryPort`, with no perfect-zeroization claim.

## Invariants

1. No public method returns plaintext key/DEK/secret bytes.
2. One BMK per bundle, one stable installation MAC family per IVK, domain-separated derived keys,
   one fresh DEK per object, and nonce-free RFC 3394 wrapping under stable `K_wrap`.
3. Every handle is service/vault/generation/purpose bound and invalid on relock.
4. No silent backend/vault-mode fallback or replacement key creation exists.
5. Recovery and vault unlock are distinct secret purposes and artifacts.
6. `K_lookup` is installation-scoped only; there is no second bundle lookup key or raw-key API.

## Tests

- `tests/integration/objects/test_key_backends.py` covers RFC 3394 AES-256-KW known-answer/wrong-
  length/integrity vectors, derivation vectors, opaque purpose/domain handles, exact
  `domain || message` trailing-NUL vectors, cross-purpose rejection, installation stability/
  separation, and stale relock.
- `tests/integration/objects/test_portable_recovery.py` covers artifact/tamper/wrong-secret and
  clean-profile restore.
- `tests/subprocess/test_service_secret_boundary.py` proves key/secret bytes never cross clients,
  control frames, process metadata, logs, traces, or files.

## Open questions

None.
