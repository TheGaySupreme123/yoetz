# tests/unit/service/test_unlock_throttle.py — restart-safe passphrase throttle vectors

**Wave:** C/D | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):**
`src/yoetz/service/unlock.md`, `src/yoetz/config/paths.md` | **Imported by:** unit suite

## Purpose

Freeze the owner, record bytes/digest, atomic state transitions, exact delay table, and fresh-
monotonic restart behavior for unlock and passphrase reauthentication throttling.
The matrix covers `vault_unlock`, `provider_reauthentication`, `privacy_reauthentication`, and
`security_reauthentication` and proves no vault/store access path bypasses the coordinator.

## Public surface

Table tests for record validation/digest, attempt reservation/resolution, crash recovery, success
reset, delay calculation, restart, wall-clock anomaly, and unsafe/missing path handling.

## Behavior

Use injected canonicalizer, filesystem journal, wall/monotonic clocks, and service IDs. Assert the
domain-separated digest/LF placement, `0600` temp/fsync/rename/dir-fsync/reopen order, attempt-in-
progress before KDF, failure charge before return, success reset before authority publication, and
delays 0,0,0,30,60,120,240,300 seconds with cap thereafter. Restart re-arms the full derived delay
from fresh monotonic; persisted monotonic values are impossible.

## Errors and edge cases

Crash at every write/fsync/rename boundary, in-progress restart, wall rollback/future/non-UTC,
digest/mode/install mismatch, missing-after-passphrase-publication, symlink/multilink/wrong owner/
mode/type, count/generation overflow, and cancellation never yield an immediate attempt.

## Invariants

1. The record contains no secret, entered length, target, user/task content, or KDF output.
2. Every KDF verification has a durable in-progress reservation first.
3. Restart/clock/storage anomalies only preserve or lengthen delay.
4. `UnlockCoordinator` is the sole store caller and reserves every passphrase KDF before vault
   verification; `VaultService` owns no delay/counter transition.

## Tests

This file is the executable owner and uses no real home, clock, KDF, or keyring.

## Open questions

None.
