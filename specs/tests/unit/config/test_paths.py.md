# tests/unit/config/test_paths.py — platform-path resolution and bundle safety classification

**Wave:** C | **ADRs:** ADR-003 | **Imports (spec-tree):**
`src/yoetz/config/paths.md`, `src/yoetz/config/models.md`
**Imported by:** the config unit suite

## Purpose

Lock deterministic platform-path resolution and the reason-coded, non-mutating bundle safety gate
without reading the developer's real home, mount table, repository, or platform directories.

## Public surface

- `test_platformdirs_resolution_uses_yoetz_without_author_or_roaming` — injected macOS and Linux
  platform probes produce the reviewed data/config/cache/state/log paths.
- `test_each_unsafe_synthetic_layout_has_one_reason_code` — symlink, ownership, broad permission,
  shared-temp, repository, and sync-folder layouts fail with the exact first reason.
- `test_network_mount_parser_uses_longest_mount_and_reviewed_fstypes` — injected Linux and macOS
  mount facts classify reviewed local/network cases deterministically.
- `test_explicit_data_dir_override_is_still_gated` — user selection never bypasses safety.
- `test_ensure_owner_only_dir_creates_and_rechecks_mode` — creation uses `0o700` and validates
  owner/mode through supplied filesystem facts.
- `test_locked_state_metadata_paths_are_fixed_and_private` — service-generation and unlock-throttle
  basenames share the verified state root and reject override/symlink/multilink/broad modes.

## Behavior

Build isolated synthetic directory trees and inject effective UID, home, platformdirs results,
temporary-directory identity, mount-table/statfs results, and diagnostics. For each ordered gate,
assert the first failing condition yields exactly its bounded `PathSafetyError.reason_code`, never
the source path or raw exception. Fake repository markers include both `.git` directories and
worktree `.git` files. Sync cases cover the reviewed Dropbox, iCloud/CloudStorage, OneDrive,
GoogleDrive, Nextcloud/ownCloud/Insync/Sync, and Syncthing markers.

Mount parsing fixtures cover escaped mount fields, prefix-boundary collisions, nested mount points,
the longest matching mount, every reviewed network filesystem value, local filesystem controls,
unreadable mount data, and injected `statfs` failure. Best-effort detection failure records only the
bounded diagnostic and continues; it does not manufacture `STORAGE_UNSAFE`.

Run the identical classifier against default and explicit `storage.data_dir` roots. The override
can select a candidate but cannot waive symlink, ownership, permission, temp, repository, sync, or
network checks. Safety verification performs no creation or mutation. Separately, test
`ensure_owner_only_dir` through an isolated temporary root and injected ownership/mode observations.

## Errors and edge cases

- Reason precedence is tested with layouts containing multiple defects; only the earliest reviewed
  gate is public.
- Case-sensitive component matching and the explicitly listed macOS provider patterns are distinct
  test rows.
- Paths outside home but inside an approved per-user root produce a diagnostic rather than failure.
- An unreadable mount table or failed best-effort platform probe cannot leak an exception or path.
- No case reads real `/proc/mounts`, real cloud folders, real VCS metadata, or ambient `XDG_*` state.

## Invariants

1. Path safety is a pure classification of supplied path and platform evidence.
2. Explicit configuration never bypasses the safety gate.
3. Every failure has one bounded closed-enum reason code and no caller-path disclosure.
4. The verifier never mutates; owner-only creation is isolated in its dedicated helper.
5. Synthetic layouts cover every reviewed detector and reason-precedence edge.

## Tests

- `tests/unit/config/test_paths.py`
- Run offline with the unit suite under both supported platform-probe fixtures and multiple
  hash-seed/locale settings; it must not access network, keyring, SQLite, or the real user profile.

## Open questions

None.
