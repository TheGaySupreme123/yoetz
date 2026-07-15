# src/yoetz/config/paths.py — platform paths and bundle path safety gate

**Wave:** C | **ADRs:** ADR-003 | **Imports (spec-tree):** `specs/src/yoetz/config/models.md`
(for `StorageConfig`) | **Imported by:** `specs/src/yoetz/adapters/sqlite/connection.md`,
`specs/src/yoetz/adapters/objects/encrypted_files.md`,
`specs/src/yoetz/application/service.md`, `specs/src/yoetz/config/load.md`

## Purpose

Locates every directory Yoetz uses via `platformdirs`, and enforces the binding rule that a task
bundle never lives in a source repository, cloud-synced folder, network filesystem, or shared
temp directory (ADR-003 decision 4 and this file's path-safety rules). This is the
`STORAGE_UNSAFE` gate at startup step 2.

## Public surface

- `bundle_root() -> Path` — `<platform-app-data>/yoetz/` (`platformdirs.user_data_dir("yoetz")`),
  or the validated `storage.data_dir` override.
- `catalog_path() -> Path` — `bundle_root() / "catalog.sqlite3"`.
- `task_bundle_dir(task_id: str) -> Path` — `bundle_root() / "tasks" / task_id`, where
  `task_id` was already validated by `protocol/ids.validate_id` (never raw caller input).
- `config_file_path() -> Path` — `platformdirs.user_config_dir("yoetz")/config.toml`.
- `cache_dir() -> Path`, `state_dir() -> Path`, `log_dir() -> Path` — `platformdirs`
  user cache/state/log dirs for `"yoetz"`.
- `service_generation_path() -> Path` — fixed
  `state_dir() / "service-generation.json"`; no override/caller basename. Parent is verified
  owner-only `0700`; file is regular, single-link, owner-only `0600`, opened no-follow.
- `unlock_throttle_path() -> Path` — fixed `state_dir() / "unlock-throttle.json"`; same verified
  local owner-only parent and `0600` regular/single-link/no-follow contract, no override.
- `verify_private_local_bundle(path: Path) -> None` — the safety gate registered by
  `specs/INTERFACES.md`; raises `PathSafetyError`.
- `class PathSafetyError(Exception)` — bounded `reason_code`; mapped to public
  `STORAGE_UNSAFE`; registered in `specs/INTERFACES.md`.
- `ensure_owner_only_dir(path: Path) -> None` — create-with-`0o700`/verify helper used by the
  object and bundle adapters.

All functions accept an optional injected `StorageConfig`/platform probe for tests; none reads
global mutable state besides the filesystem.

## Behavior

### Directory resolution

`platformdirs` with app name `"yoetz"`, no author component, no roaming. Results (documentation
targets, not promises for unadvertised platforms):

- macOS: data `~/Library/Application Support/yoetz`, config `~/Library/Application Support/yoetz`
  (platformdirs default), cache `~/Library/Caches/yoetz`, logs `~/Library/Logs/yoetz`.
- Linux: XDG — data `~/.local/share/yoetz`, config `~/.config/yoetz`, cache `~/.cache/yoetz`,
  state `~/.local/state/yoetz`; `XDG_*` env vars honored via `platformdirs`.

Directories are created lazily with mode `0o700` (`ensure_owner_only_dir`), then re-verified:
owner is the current uid, and mode has no group/other bits where the OS supports POSIX modes.
The service-generation and unlock-throttle files are the only locked-state durable metadata paths.
They contain only lifecycle/security counters and commitments, never secret/user/task content. Their
state directory must additionally pass local-filesystem/no-symlink/no-shared-temp checks; failure
blocks service startup rather than falling back to catalog, runtime directory, environment, or
another path.

### `verify_private_local_bundle(path)` — checks in order, first failure raises

Every check emits exactly one bounded `reason_code`:

1. **Symlink rejection** — resolve `path` with `Path.resolve(strict=False)`; then `lstat` each
   component from `bundle_root()`'s parent downward; any symlink component →
   `path_contains_symlink`. Opens performed later by adapters use `O_NOFOLLOW` where available;
   this check is the policy layer, not the only defense.
2. **Ownership and permissions** — `stat.st_uid` must equal the effective uid
   (`path_not_owned`); mode group/other bits must be clear (`permissions_too_broad`). On
   filesystems that do not report POSIX modes faithfully, record a diagnostic and continue
   (best-effort per ADR-003 "detected where practical").
3. **Shared/world-readable temp** — reject when the resolved path is under `/tmp`, `/var/tmp`,
   `/dev/shm`, or the platform shared temp returned by `tempfile.gettempdir()` when that
   directory is not owner-only → `path_shared_temp`.
4. **Source repository** — walk ancestors of the resolved path up to the filesystem root; the
   presence of `.git`, `.hg`, `.svn`, or `.jj` in any ancestor (or the path itself) →
   `path_in_repository`. (A worktree `.git` *file* counts.)
5. **Cloud-sync directories** (heuristics, per platform):
   - path contains a component named `Dropbox`, or an ancestor contains `.dropbox` /
     `.dropbox.cache` → `path_in_sync_folder`;
   - macOS: under `~/Library/Mobile Documents` (iCloud Drive), under a `*.icloud` ancestor, or
     under `~/Library/CloudStorage/*` (OneDrive/GoogleDrive/Dropbox provider mounts) →
     `path_in_sync_folder`;
   - Linux: component named `OneDrive`, `Google Drive`, `GoogleDrive`, `Nextcloud`,
     `ownCloud`, `Insync`, or `Sync` with a sibling sync-metadata dir → `path_in_sync_folder`;
   - an ancestor containing `.sync`/`.stfolder` (Syncthing) → `path_in_sync_folder`.
6. **Network filesystems** —
   - Linux: `os.statvfs` + `/proc/mounts` lookup of the longest matching mount point; fstype in
     `{nfs, nfs4, cifs, smb3, smbfs, sshfs, fuse.sshfs, 9p, afs, glusterfs, ceph, davfs,
     fuse.rclone}` → `path_on_network_filesystem`;
   - macOS: `statfs` `f_fstypename` in `{nfs, smbfs, afpfs, webdav, acfs}` →
     `path_on_network_filesystem`.
7. **Root/other-user home** — the resolved path outside the current user's home *and* outside a
   system-approved per-user data location → diagnostic only (not a failure) recorded through
   `DiagnosticsPort`.

Heuristic misses do not create a support claim: an undetected sync client is still unsupported
(ADR-003 §4). Detection lists are versioned constants with fixture coverage, not scattered
literals.

### Interaction with overrides

An explicit `storage.data_dir` gets the identical gate — user consent selects the location but
cannot waive safety. There is no `--force` in v0.1.

## Errors and edge cases

- `PathSafetyError(reason_code)` maps to public `STORAGE_UNSAFE` (not retryable); CLI exit `20`.
  `safe_details` may carry the reason code, never the full offending path beyond the approved
  diagnostic view.
- `/proc/mounts` unreadable or `statfs` failing → record diagnostic, skip that heuristic
  (best-effort), continue; an *error* in detection is not itself `STORAGE_UNSAFE`.
- TOCTOU: the gate is advisory-time; the SQLite/object adapters re-assert ownership and
  no-symlink at open time (`assert_active_bundle_generation`, `O_NOFOLLOW`).
- Generation path missing on a proven pristine install is created once; missing, symlinked,
  multi-linked, nonregular, broad-mode, or changed-after-open on an existing install fails closed.
- The throttle path is created atomically with passphrase-mode initialization. Missing/corrupt/
  unsafe state later can only arm the maximum fail-closed delay/recovery rule owned by
  `service/unlock.md`; it never means zero failures.
- Case-insensitive filesystems: component-name matching is case-sensitive except the explicit
  macOS/Windowsy sync names listed above (documented table).

## Invariants

- No bundle, catalog, key metadata, log, or lock file is ever created outside
  `bundle_root()`/`log_dir()`-derived paths that passed the gate.
- All created directories/files are owner-only where the OS supports it.
- Reason codes are a closed reviewed enum; no exception text or user path leaks into public
  errors.
- The safety gate never mutates anything; creation happens only in `ensure_owner_only_dir`.

## Tests

- `specs/tests/unit.md` — `tests/unit/config/test_paths.py`: reason-code per synthetic layout
  (tmp trees with fake `.git`, `Dropbox`, symlinks, group-writable dirs), mount-table parsing
  fixtures, override-still-gated.
  It also freezes both locked-state basenames, state-dir containment, `0700/0600`, no-follow,
  single-link, and no caller/environment path override beyond platformdirs' verified state root.
- `specs/tests/integration.md` — real temp-bundle creation gets `0o700`; symlinked bundle
  rejected; `STORAGE_UNSAFE` surfaces through the public envelope with exit `20`.
- `specs/tests/subprocess.md` — kill/permission-loss cases exercise
  re-validation on reopen.

## Open questions

None.

A target equal to or below a recognized macOS provider-mount ancestor is blocked even when
the backing volume reports local; false-positive/expanded Linux and macOS detector lists are
versioned empirical platform evidence under E-003, never a user `--force` decision.
