# tests/capability/test_platform_filesystem_keyring.py — platform durability and key-backend cells

**Wave:** C/F | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):** capability evidence,
config/SQLite/object/key specs | **Imported by:** platform support matrix

## Purpose

Empirically qualify exact OS/CPU/ABI/Python/SQLite/filesystem/key-backend combinations used in
public support claims, including safe failure for unsupported/shared paths and unavailable keys.

## Public surface

Matrix: certified macOS arm64 and manylinux/glibc x86-64 candidates × local filesystem profile × OS
keyring/passphrase states. Cases cover identity, owner permissions, file/directory fsync, atomic
rename, hard/symlink rejection, WAL/checkpoint/backup, disposable key roundtrip, locked/missing/
headless backend, and recovery artifact.

## Behavior

Assert exact platform and installed APSW/SQLite source ID/options before mutation. Probe app-data
path classification and deny network/shared/unsafe filesystem. Create isolated bundle, exercise
durable write/rename/dir-fsync/WAL FULL/checkpoint/online backup/reopen and permission fences.

Use a disposable namespaced keyring entry or synthetic passphrase; wrap/unwrap/rotate/reopen and
delete. For pristine keyring initialization, join this exact key-backend evidence with the same-
artifact `user_presence_cells` evidence; a keyring-only pass authorizes no service mutation. Test
missing/locked/headless and existing-keyring/no-current-presence states separately, ensuring six operations fail closed as policy
and `version` remains structural. Recovery artifact contains no raw key in logs/evidence. Scan all
plaintext surfaces with canaries.

## Errors and edge cases

- Runner label alone does not prove platform; observed identity must match policy.
- Real user keychain/default app data is never touched.
- Cleanup failure or unavailable platform is incomplete/unsupported, not pass.
- Performance numbers are platform-scoped and bounded.

## Invariants

1. Support cell binds exact runtime/SQLite/filesystem/key backend.
2. Unsafe filesystem and unavailable key fail closed.
3. Raw keys/user content never enter evidence/plaintext surfaces.
4. Disposable external state is deleted and verified.
5. Pristine automatic keyring support is the exact same-artifact intersection with verified
   action-bound presence; existing-keyring ready-local is reported separately.

## Tests

Each platform job emits structural capability evidence plus private probe digest. Negative controls
include symlink, wrong source ID/options, network mount, locked keyring, and cleanup failure.

## Open questions

None.
