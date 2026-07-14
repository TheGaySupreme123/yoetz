# tests/integration/service/test_encrypted_vault.py — encrypted vault record integration

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** encrypted-vault/secret-memory specs | **Imported by:** test runner

## Purpose

Verify authenticated record vectors, atomicity, bindings, and no-plaintext persistence.

## Public surface

Golden RFC 3394/YZV1 record and kill/fault matrix for sentinel, bundle, provider-credential, and
recovery-metadata records, plus a negative inventory proving installation MAC keys have no
independent record.

## Behavior

Cover exact HKDF locator/index vectors, `record_id`, u32-big-endian framing, JCS header,
base64url-wrapped fresh record DEK, one-use AES-GCM payload nonce, complete-frame digest,
create/load/credential generation CAS, sentinel, permissions, record/index fsync/rename recovery,
and keyed filenames. Provider credential cases run the installed OpenAI profile's exact 16..512-byte
token68 accept/reject vectors inside protected consumption, prove invalid input writes/stages/logs
nothing, and prove accepted bytes are unchanged when used by the one-attempt transport callback.

## Errors and edge cases

Wrong IVK, RFC 3394 integrity failure, tamper/truncate/append/header/binding/generation/index,
foreign or unindexed files, every crash point, credential
length 0/15/16/512/513, misplaced `=`, whitespace/CRLF/NUL/control/non-ASCII, and validator failure
at every pre-publication point.

## Invariants

1. Disk contains ciphertext/structural headers only.
2. Bundle key create never replaces.
3. No `K_lookup`, `K_log`, or `K_audit` vault record exists; they derive from the IVK at unlock.
4. Credential validation is exact-profile, non-normalizing, no-log, and atomic before persistence.

## Tests

This file is the executable owner.

## Open questions

None.
