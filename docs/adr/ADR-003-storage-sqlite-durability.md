# ADR-003 — Storage, SQLite build, and durability

**Status:** Working decision for spec drafting (2026-07-13). Ratification requires the full
fault/contention matrix on both advertised platforms.
**Owning public specs:** `specs/INTERFACES.md`, `specs/src/yoetz/adapters/sqlite/`,
`specs/src/yoetz/ports/ledger.md`, `specs/src/yoetz/ports/runtime.md`, root and packaged
migration specs, and the integration/subprocess storage specifications.

## Decisions

1. **SQLite delivery:** APSW pinned exactly `3.53.3.1` carrying SQLite `3.53.3` amalgamation.
   Startup verifies `apsw.apsw_version()`, `apsw.sqlite_lib_version()`, `using_amalgamation`,
   exact `sqlite_source_id()`
   (`2026-06-26 20:14:12 d4c0e51e4aeb96955b99185ab9cde75c339e2c29c3f3f12428d364a10d782c62`),
   and support-manifest compile options. Unknown builds: read-only inspection allowed, writes
   fail closed (`STORAGE_UNSAFE`). Multi-connection WAL floor: 3.51.3+ (WAL-reset fix). The
   stdlib `sqlite3` adapter is test/reference only.
2. **PRAGMAs / connection contract:** WAL (verified, not assumed),
   `synchronous=FULL` (verified), `foreign_keys=ON`, `trusted_schema=OFF`, `temp_store=MEMORY`,
   `busy_timeout=5000`, `wal_autocheckpoint=0` (owner-run bounded PASSIVE checkpoints),
   `mmap_size=0`, extension loading disabled. `PRAGMA application_id = 0x594F4554` ("YOET"),
   `user_version = 1` for both catalog and bundle schemas.
3. **Transactions:** `BEGIN IMMEDIATE` for every write path; the append transaction contains only
   bounded indexed reads/writes. All hashing, validation, encryption, object
   fsync, and network work happens outside. Acknowledge only after COMMIT returns.
4. **Layout:** platform app-data `…/yoetz/` with `catalog.sqlite3` + `tasks/<task-id>/` bundles.
   One task per bundle database. Owner-only permissions; symlink/hardlink/
   traversal rejection; repo, cloud-synced, network, and world-readable paths unsupported and
   detected where practical (`STORAGE_UNSAFE`).
5. **Schema:** migration `0001` is the canonical initial schema for both
   catalog and bundle (STRICT tables, WITHOUT ROWID where keyed by text, CHECK-enforced state
   machines). Structural columns never contain user plaintext.
6. **Object publication protocol:** encrypted temp file → flush → fsync(file) → atomic rename
   into `objects/<2-hex-prefix>/` → fsync(dir) → only then referenced inside the append
   transaction. Orphans are collectable after a 24 h safety window, never while referenced by a
   maintenance pin.
7. **Backup/restore/migration:** online Backup API only (APSW destination-side `backup`);
   frontier-pinned manifests; restore into a quarantined new bundle then atomic catalog switch;
   canonical event bytes never rewritten by migration; newer unknown write-schema fails closed.
8. **Corruption response:** integrity failure quarantines the bundle (writes disabled,
   `STORAGE_CORRUPT`), preserves originals under `quarantine/`, and directs to
   backup/restore. Projection-only corruption is repaired by generation replay.

## Consequences

Platform wheels (not pure-Python) on macOS arm64 + manylinux_2_28 x86_64; Yoetz owns security
patching for the shipped SQLite; every dependency bump reruns the storage matrix.
