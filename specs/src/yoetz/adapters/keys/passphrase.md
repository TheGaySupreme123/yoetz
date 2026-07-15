# src/yoetz/adapters/keys/passphrase.py — portable bundle-recovery passphrase adapter

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `ports/keys.md`,
`ports/secret_memory.md`, pure passphrase policy from `service/confidential_protocol.md` |
**Imported by:** service maintenance recovery only

## Purpose

Implements portable per-bundle recovery artifacts. It is not the local vault unlock adapter and is
never an automatic fallback when the OS keyring is unavailable.

## Public surface

- `wrap_recovery_artifact(BundleKeys, RecoverySecret) -> RecoveryArtifact`.
- `unlock_recovery_artifact(RecoveryArtifact, RecoverySecret) -> recovered opaque key handle`.
- KDF validation/calibration helpers over one-shot `portable_recovery` secret handles.
- Candidate creation parameters: Argon2 version `19`, `time_cost=3`, `memory_kib=262_144`,
  `parallelism=1`, output `32` bytes; E-006 may amend the exact creation values before release.

## Behavior

Consume the secret once through `SecretMemoryPort`. Generate a fresh 32-byte OS-CSPRNG salt and
derive 32-byte `argon_root` with the same exact Argon2id call, validation ranges, and candidate
parameters frozen in `vault_passphrase.md`. HKDF-SHA-256 uses exact salt
`b"yoetz/passphrase-subkey-root/v1"`, output length 32, and exact ASCII info
`b"yoetz/recovery-wrap/v1"` / `b"yoetz/recovery-auth/v1"`. AES-256-KW RFC 3394 wraps the exact
32-byte BMK to 40 bytes; no AES-GCM nonce exists.

The exact JCS authenticated body contains only `binding={task_id,key_slot}`,
`format="yoetz-portable-recovery/1"`, the same closed `kdf` object, `wrap_algorithm`, and
base64url-no-padding `wrapped_bmk`. The final envelope adds only
`auth_algorithm="hmac-sha256"` and
`auth_tag=base64url(HMAC-SHA-256(K_auth,
b"yoetz/portable-recovery-envelope/v1\x00" || JCS(body)))`. Canonical envelope bytes are capped at
16 KiB and `artifact_digest` is SHA-256 over those exact bytes. Unknown/duplicate fields,
noncanonical JSON/base64url, binding/length/constant mismatch, out-of-range KDF values, or trailing
bytes fail closed before unbounded work. The local-human secret arrives only through
confidential ingress; no raw `str`/`bytes`, normalization guess, argv/env/config/stdin source, or
vault-unlock type is accepted.

Both wrap and unwrap enforce the shared exact 16..1,024-byte strict-UTF-8 rule, reject U+0000,
U+000A, and U+000D, and perform no trim/normalization/case-fold/replacement. Recovery artifact
creation is allowed only from a `portable_recovery` binding tagged `operation=create`, for which the
helper performed two independent exact entries and sent one handle. Artifact restore accepts only
`operation=restore` and one entry. The adapter consumes exactly one handle in either branch and
cannot observe or trust the helper's confirmation buffer.

## Errors and edge cases

Wrong secret, HMAC/RFC-3394 integrity failure, tamper, unsupported format, missing artifact, and key mismatch are distinct internal
maintenance reasons with bounded public handling. No zeroization beyond best effort is claimed.

## Invariants

1. Portable recovery is explicit, authenticated, and separate from vault startup.
2. Artifact contains no raw key, plaintext payload, or recovery secret.
3. One-shot purpose typing prevents unlock/recovery secret reuse.
4. Create and restore are operation-bound; double-entry creation cannot be confused with
   single-entry restore.

## Tests

- `tests/integration/objects/test_portable_recovery.py` covers vectors, failures, and clean-profile
  restore.
- `fixtures/canonical/object-envelope.case.json` freezes exact recovery KDF/HKDF/AES-KW/HMAC/JCS/
  base64url vectors and hostile parameter/binding cases.
- `tests/subprocess/test_service_secret_boundary.py` covers forbidden input channels.

## Open questions

None.
