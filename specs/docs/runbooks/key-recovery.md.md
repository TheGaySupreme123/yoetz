# docs/runbooks/key-recovery.md — locked, missing, and portable key recovery procedure

**Wave:** C/F | **ADRs:** ADR-004, ADR-007 | **Imports (spec-tree):** key ports/adapters, object
envelope, maintenance and backup/restore specs | **Imported by:** key errors, backup/restore and
quarantine runbooks

## Purpose

Explain how to respond safely to key-locked/key-missing/backend/recovery-secret/artifact failures and
how machine-bound versus portable recovery works. It must not suggest plaintext key export, key reset
over encrypted data, secret in shell/environment, or claim that encrypted data is recoverable without
the correct BMK/recovery artifact.

## Public surface

Future headings:

1. Threat/recovery scope
2. Identify key mode and failure reason
3. `key_locked`
4. `key_missing` / backend unavailable
5. Machine-bound backup restore
6. Portable recovery artifact restore
7. Clean-profile recovery drill
8. Key/recovery custody
9. Permanent loss and honest next steps
10. Prohibited actions and safe support evidence

## Behavior

### Scope and terms

Define bundle master key, nonsecret key slot/fingerprint, OS backend, passphrase mode, recovery secret,
portable recovery artifact and encrypted objects. State raw BMK/derived keys are never shown/exported;
live account/root/memory attacks are outside at-rest protection. Logical redaction is not forensic
erasure.

Error decision table:

- `key_locked`: expected backend/entry exists but user denied/locked; unlock/authorize verified
  backend, then retry same operation. Never create replacement.
- `key_missing`: expected slot entry absent. Check correct OS profile/account/backend and backup mode;
  do not initialize/reset. Machine-bound restore cannot work elsewhere without entry.
- `unsupported_backend|backend_unverified`: stop; install/configure supported backend through public
  package policy, no plaintext fallback.
- `key_id_mismatch|recovered_key_cannot_decrypt`: wrong bundle/key or corruption; quarantine/stop.
- `recovery_secret_wrong`: re-enter secret safely; distinct from artifact tamper.
- `recovery_artifact_tampered|format_unsupported|key_id_mismatch`: stop and use another verified
  artifact/package; do not edit KDF/nonce/header.

### Machine-bound procedure

Verify restore occurs under same installation/profile with original verified key backend entry and
matching nonsecret slot/fingerprint. Preview backup/restore; if entry unavailable, copying keychain/
secret-service internals or backup directory does not make it portable. Follow new-target verified
restore and compare object authentication/replay before switch.

### Portable procedure

Require finalized backup manifest mode `portable_recovery`, matching separate recovery artifact
digest/format/task/key identity and recovery secret. Inspect/confirm restore plan. Supply secret only
through interactive protected prompt/approved anonymous file descriptor; never argv/env/JSON/config/
shell history/chat/issue. Adapter authenticates artifact metadata, derives wrapping key with recorded
approved Argon2id policy, unwraps BMK into verified destination backend, proves sample/all required
object decrypt/authentication and full replay, then switches route only after complete restore.

Wrong secret makes no persistent replacement key/target activation. Tampered artifact is not retried
as wrong secret indefinitely. Secret and transient handles are released promptly; CPython cannot
promise perfect memory zeroization, so documentation says “minimize lifetime,” not guaranteed erase.

### Recovery drill

Before calling a backup portable:

1. use synthetic/controlled bundle and finalized backup/artifact;
2. provision a clean supported profile with no original key entry;
3. install exact supported artifact offline, verify versions;
4. restore with recovery artifact/secret into new target;
5. verify key slot, object authentication, canonical chains, replay/frontier/receipt;
6. close/reopen and repeat read/check;
7. retain only redacted structural drill evidence/digests and destroy test state.

Production user data is not uploaded to prove drill. Repeat after recovery-format/KDF/backend/platform
changes.

### Custody and permanent loss

Backup ciphertext, recovery artifact and secret should have separated custody appropriate to user
threat model; exact storage policy is user-owned. A checksum detects accidental change but is not
signature or secret. Never store secret beside artifact in Yoetz config/repo.

If BMK and valid portable recovery path are both unavailable, encrypted object content is not
recoverable by Yoetz. Preserve catalog/bundle/backup and structural evidence; do not promise recovery,
brute force, or reset over existing bundle. A new task/key may be created only as a clearly separate
history after user accepts loss/limitations; it cannot validate/replace old receipts.

### Safe support evidence

Share only error reason, package/object/recovery format, nonsecret fingerprint hash if policy permits,
backend classification, artifact/manifest digest, platform identity and drill step. Never share key,
secret, wrapped BMK/artifact bytes, keychain label/account, path, object/database, config/env or raw
exception/log.

## Errors and edge cases

- Repeated unlock prompts/cancellation are not key absence; preserve distinction.
- Restoring the correct key does not validate database/object/route—full restore gate still required.
- Expired/rotated secret policy is not silently weakened; use supported predecessor/rotation records.
- Portable artifact can be valid but backup incomplete/tampered; both must verify.
- No runbook command demonstrates a literal recovery secret.

## Invariants

1. Missing/locked/wrong/tampered/unsupported key states remain distinct.
2. No plaintext/environment/shell key fallback exists.
3. Existing encrypted data is never “fixed” by generating a replacement key.
4. Portable claim requires artifact + secret + clean-profile drill.
5. Key success is followed by full object/ledger/replay verification.

## Tests

- Execute decision table with scripted/real isolated backends and all `KeyStoreReason` values.
- Clean-profile machine-bound failure and portable success/failure/tamper/wrong-secret drills.
- Docs/security scan rejects secret-bearing command forms and plaintext fallback/reset advice.
- Evidence canary tests ensure key/secret/locator/path absent.

## Open questions

None.

E-006 and R-001 are the sole central recovery release gates.
