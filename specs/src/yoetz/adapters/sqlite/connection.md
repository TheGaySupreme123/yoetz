# src/yoetz/adapters/sqlite/connection.py — verified APSW connection factory and single-writer thread

**Wave:** C | **ADRs:** ADR-001, ADR-003 | **Imports (spec-tree):**
`specs/src/yoetz/protocol/errors.md`, `specs/src/yoetz/config/paths.md`,
`specs/src/yoetz/version.md` | **Imported by:**
`specs/src/yoetz/adapters/sqlite/repository.md`,
`specs/src/yoetz/adapters/sqlite/start_catalog.md`,
`specs/src/yoetz/adapters/sqlite/migrations.md`,
`specs/src/yoetz/adapters/sqlite/recovery.md`

## Purpose

Every SQLite connection Yoetz opens — catalog writer, bundle writer, read-only inspector — is
created here and nowhere else. This file turns ADR-003's "verification, not assumption" rule into
code: it opens APSW connections with the exact PRAGMA sequence specified below, proves (rather
than requests) WAL and FULL-sync, proves the exact SQLite build against the shipped support
manifest, proves database identity (`application_id`/`user_version`), and enforces ADR-001's
generation fence before any writer exists. It also owns the single-writer-thread queue policy
specified below so that no other module ever touches an `apsw.Connection` directly. Without this
file, a wrong SQLite build, a silently-degraded PRAGMA, or a second writer process could corrupt
the ledger while every higher layer still believes the invariants hold.

## Public surface

- `open_writer(path: Path) -> apsw.Connection` — open the single authoritative read-write
  connection for a bundle or catalog database, per ADR-003 and this file's verified sequence.
- `open_read_only(path: Path) -> apsw.Connection` — open a verification/inspection connection that
  can never write (registered in `specs/INTERFACES.md`).
- `verify_sqlite_build(db: apsw.Connection) -> SqliteBuildReport` — assert the exact
  APSW/SQLite/amalgamation/source-id/compile-option identity registered in
  `specs/INTERFACES.md`.
- `verify_schema_identity(db: apsw.Connection) -> SchemaIdentity` — assert
  `application_id`/`user_version` identity and migration state registered in
  `specs/INTERFACES.md`.
- `assert_active_bundle_generation(path: Path) -> None` — assert that this process currently holds
  the bundle's owner generation before a writer opens, as registered in
  `specs/INTERFACES.md`.
- `SqliteWriterThread` — the dedicated thread + bounded queue that owns one writer connection
  (registered shared boundary): `submit(fn: Callable[[apsw.Connection], T]) -> Future[T]`,
  `close()`.
- `StorageUnsafeError(reason_code: str)` — internal typed failure mapped to public
  `STORAGE_UNSAFE` (registered in `specs/INTERFACES.md`).
- Module constants: `REQUIRED_APSW_VERSION = "3.53.3.1"`, `REQUIRED_SQLITE_VERSION = "3.53.3"`,
  `REQUIRED_SQLITE_SOURCE_ID =
  "2026-06-26 20:14:12 d4c0e51e4aeb96955b99185ab9cde75c339e2c29c3f3f12428d364a10d782c62"`,
  `YOETZ_APPLICATION_ID = 0x594F4554`, `BUSY_TIMEOUT_MS = 5_000`,
  `STATEMENT_CACHE_SIZE = 100`, `WRITER_QUEUE_DEPTH = 64` (registered frozen constants).

No function in this module returns raw cursors to callers outside `adapters/sqlite`; the module
docstring states that exporting the connection object beyond this package is forbidden.

## Behavior

### `open_writer(path)`

Implements ADR-003's verified-connection decision exactly, in this order. Any deviation of an observed value from the
required value raises `StorageUnsafeError` with the listed reason code; the connection is closed
before raising.

1. `assert_active_bundle_generation(path)` — exclusive process ownership was acquired first (see
   below). For `catalog.sqlite3` the catalog's own generation row is checked by the same function
   (the catalog and every task bundle use the same generation-fenced authoritative process).
2. `verify_private_local_bundle(path)` — delegated to
   `specs/src/yoetz/config/paths.md`: rejects
   repo/cloud-synced/network/world-readable locations, symlinks anywhere on the resolved path,
   and non-owner-only permissions where the OS supports them. Failure reason:
   `storage_path_unsafe`.
3. Construct `apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READWRITE |
   apsw.SQLITE_OPEN_CREATE, statementcachesize=STATEMENT_CACHE_SIZE)`.
4. `db.set_busy_timeout(5_000)`.
5. `db.enable_load_extension(False)` — dynamic extension loading stays disabled permanently.
6. `PRAGMA foreign_keys=ON`, then read back `PRAGMA foreign_keys`; must be `1`
   (`foreign_keys_not_enabled`). Connection-local, so verified on every connection.
7. `PRAGMA trusted_schema=OFF` (read back; `trusted_schema_not_disabled`).
8. `PRAGMA temp_store=MEMORY` (read back value `2`; `temp_store_not_memory`).
9. `mode = first row of PRAGMA journal_mode=WAL`; if `mode.lower() != "wal"` raise
   `wal_not_enabled`. The returned mode is checked, never assumed (a network filesystem or
   read-only medium can silently refuse WAL).
10. `PRAGMA synchronous=FULL`, then read back `PRAGMA synchronous`; the integer must equal `2`
    (`full_sync_not_active`).
11. `PRAGMA wal_autocheckpoint=0` — checkpoint ownership is explicit; the
    repository runs bounded PASSIVE checkpoints on the writer thread.
12. `PRAGMA mmap_size=0` (read back `0`; `mmap_not_disabled`).
13. `verify_sqlite_build(db)`.
14. `verify_schema_identity(db)`.
15. Return the connection.

The function never retries internally; a failed step is a startup failure, not a degraded mode.

### `verify_sqlite_build(db)`

Loads the shipped support manifest (a packaged resource under `resources/`; see
`specs/src/yoetz/version.md`) and asserts, in order:

1. `apsw.apsw_version() == REQUIRED_APSW_VERSION` (`apsw_version_mismatch`).
2. `apsw.sqlite_lib_version() == REQUIRED_SQLITE_VERSION` (`sqlite_version_mismatch`).
3. `apsw.using_amalgamation` is true — APSW must carry its own SQLite amalgamation, not link a
   system library (`not_amalgamation`).
4. `first row of SELECT sqlite_source_id()` equals `REQUIRED_SQLITE_SOURCE_ID` byte-for-byte
   (`sqlite_source_id_mismatch`). ADR-003 makes the version string alone insufficient.
5. `PRAGMA compile_options` rows are compared against the support manifest: every
   manifest-required option must be present and every manifest-denied option absent
   (`compile_options_mismatch`). The manifest also pins the expected thread-safety mode
   (`THREADSAFE=…` compile option); mismatch uses the same reason.

Returns a `SqliteBuildReport` (frozen dataclass: apsw version, sqlite version, source id,
compile options tuple, manifest id) which `DiagnosticsPort.record` consumes during startup. An
unknown build never gets a writer: callers on the read-only inspection path may catch
`StorageUnsafeError` and proceed under the read-only policy below, but `open_writer` always
propagates it.

### `verify_schema_identity(db)`

1. Read `PRAGMA application_id`.
   - `0` **and** the database contains no tables (`sqlite_master` empty): a fresh file; return
     `SchemaIdentity(state="uninitialized", user_version=0)` — the migration runner is the only
     caller allowed to proceed on this state.
   - `YOETZ_APPLICATION_ID` (`0x594F4554`, "YOET"): continue.
   - anything else: `StorageUnsafeError("application_id_mismatch")` — this is not a Yoetz
     database; never write to it.
2. Read `PRAGMA user_version`.
   - equal to the supported schema version (`1` for both catalog and bundle in v0.1): return
     `SchemaIdentity(state="current", user_version=1)`.
   - lower than supported but a known released version: return `state="migration_required"`;
     `open_writer` maps this to public `MIGRATION_REQUIRED` unless invoked by the migration
     runner itself.
   - higher than supported (a newer binary wrote it): `StorageUnsafeError("schema_newer_than_
     binary")` → writes fail closed; read-only structural inspection may still be offered where
     explicitly tested by `specs/tests/integration.md`.
3. Cross-check `bundle_meta` keys `storage_schema_version` and `protocol_version` (bundle
   databases only, when tables exist) against `user_version` and `PROTOCOL_VERSION`; disagreement
   is `StorageUnsafeError("schema_metadata_disagrees")`: this file requires the pragma and the
   metadata to move together atomically.

### `assert_active_bundle_generation(path)`

Enforces ADR-001's ownership model. The authoritative record is the `owner_generation` value in
`bundle_meta` (catalog: its own equivalent meta table), advanced only under `BEGIN IMMEDIATE`
compare-and-swap; no filesystem lock is trusted for correctness.

- The process runtime holds an `OwnershipHandle` (owner generation as canonical integer string,
  owner nonce — an `ins_`-style random ID minted per acquisition as PID-reuse defense) produced by
  `recovery.acquire_bundle_ownership` (see `recovery.md`).
- This function opens a short-lived read connection, reads the ownership row, and asserts that
  stored generation and nonce equal the handle's values. Mismatch raises
  `StorageUnsafeError("bundle_generation_lost")`: another process took over; this process must
  not open a writer, write, lease, or checkpoint again.
- It never acquires ownership itself; acquisition/takeover is `recovery.md`'s job. Calling
  `open_writer` without a handle is a programming error (assertion, not a public error).

### `open_read_only(path)`

Opens `apsw.Connection(str(path), flags=apsw.SQLITE_OPEN_READONLY)`, sets
`busy_timeout`, `PRAGMA query_only=ON`, and runs `verify_sqlite_build` +
`verify_schema_identity` in **tolerant** mode: build mismatch and newer schema produce a warning
in the returned report instead of raising, because this adapter explicitly allows bounded read-only
inspection of unknown builds. It never runs `assert_active_bundle_generation` (readers do not
fence) and never creates the file (`SQLITE_OPEN_CREATE` absent; missing file raises
`StorageUnsafeError("database_missing")`). Read-only connections are used by restore
verification, recovery preflight, and bounded projection reads under the reviewed read policy.

### `SqliteWriterThread`

- Exactly one instance exists per open bundle (plus one for the catalog). Constructor takes the
  database path; the thread itself calls `open_writer` so the connection is created and used on
  a single OS thread for its whole life.
- `submit(fn)` places `fn` on a bounded FIFO queue of exactly 64 jobs; a full queue raises the
  adapter's bounded busy condition, mapped to retryable public `BUNDLE_BUSY`, rather than blocking
  the event loop unboundedly. It returns a `Future`. The thread pops jobs in order and calls
  `fn(connection)`; the function's return value or exception resolves the future. Async callers
  await the future via `anyio.to_thread`-free wrapping (the future is bridged with an
  `anyio.Event`); no other `to_thread` use exists in the adapter.
- All write transactions, lease CAS updates, and PASSIVE checkpoints run as submitted jobs, so
  writes are strictly serialized and transaction state can never interleave.
- `close()` drains the queue (rejecting new submissions), runs a final bounded PASSIVE
  checkpoint attempt (failure is logged, never fatal — committed data does not depend on
  checkpoint success), closes the connection, and joins the thread.
- Cancellation of an awaiting caller does not cancel a job already running on the thread; the
  job completes or fails on its own and the durable operation row decides the outcome on retry.

### Busy handling

`busy_timeout=5000` bounds contention. If a submitted job still receives `SQLITE_BUSY`/
`SQLITE_LOCKED` after the timeout, the adapter raises the internal busy error that the
application layer maps to public `BUNDLE_BUSY` (retryable with backoff). There is no unbounded
internal retry loop; five seconds is provisional and must fit the MCP timeout/SLO.

## Errors and edge cases

- All failures here are `StorageUnsafeError(reason_code)` with a bounded reason from:
  `storage_path_unsafe`, `wal_not_enabled`, `full_sync_not_active`, `foreign_keys_not_enabled`,
  `trusted_schema_not_disabled`, `temp_store_not_memory`, `mmap_not_disabled`,
  `apsw_version_mismatch`, `sqlite_version_mismatch`, `not_amalgamation`,
  `sqlite_source_id_mismatch`, `compile_options_mismatch`, `application_id_mismatch`,
  `schema_newer_than_binary`, `schema_metadata_disagrees`, `bundle_generation_lost`,
  `database_missing`. The application layer maps them to
  `STORAGE_UNSAFE` (or `MIGRATION_REQUIRED` for `migration_required` identity state).
- Writer-queue saturation is not a storage-integrity failure; it maps to retryable `BUNDLE_BUSY`.
- Reason codes never embed paths, SQL, or observed foreign values beyond bounded enums; observed
  vs expected build strings go only to the structured diagnostics report.
- `SQLITE_FULL`, `SQLITE_IOERR`, failed sync: propagate as APSW exceptions from the job; the
  caller never acknowledges success under `specs/src/yoetz/ports/ledger.md`. They are not converted to
  `StorageUnsafeError` because they are transient I/O outcomes, not safety-gate failures.
- Opening a database whose file exists but is not SQLite at all (APSW `NotADBError`): mapped to
  `STORAGE_CORRUPT` by recovery, not here; this module lets the APSW error propagate.
- PRAGMA read-backs are performed even when the set statement "succeeded", because several
  PRAGMAs silently no-op on unsupported filesystems or builds.

## Invariants

- No `apsw.Connection` or cursor object crosses the `adapters/sqlite` package boundary.
- Exactly one writer connection per database file per process, living on one dedicated thread;
  a process that has lost its owner generation can never regain the writer without a fresh
  acquisition through recovery.
- Every safety property is verified by reading state back, never assumed from a successful
  statement.
- An unknown SQLite build or newer schema can be inspected read-only but can never be written.
- `wal_autocheckpoint` stays `0`; the only checkpoints are owner-run bounded PASSIVE ones.

## Tests

- `specs/tests/integration.md`: PRAGMA verification matrix (each read-back forced to a wrong value via
  fault injection fails closed); build-identity matrix (wrong apsw version, wrong source id,
  missing/extra compile option); schema identity (fresh file, current, older, newer,
  foreign application_id); read-only tolerant mode.
- `specs/tests/subprocess.md`: kill during `open_writer`; `SQLITE_BUSY` saturation returns
  `BUNDLE_BUSY`; writer-queue saturation; generation lost mid-session blocks further writes.
- `specs/tests/conformance.md` (independent-process ownership harness): second process
  cannot open a writer or checkpoint; exactly one acquisition winner.

## Open questions

None.

E-004 is the sole central queue/lease calibration gate.
