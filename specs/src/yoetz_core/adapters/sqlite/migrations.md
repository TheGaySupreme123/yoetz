# src/yoetz_core/adapters/sqlite/migrations.py — schema migrations and the migration runner

**Wave:** C | **ADRs:** ADR-003 | **Imports (spec-tree):**
`specs/src/yoetz_core/adapters/sqlite/connection.md`,
`specs/src/yoetz_core/adapters/sqlite/recovery.md` (backup sequence),
`specs/src/yoetz_core/adapters/sqlite/maintenance.py.md`,
`specs/src/yoetz_core/adapters/sqlite/importer.md`,
`specs/migrations/catalog/0001.sql.md`, `specs/migrations/bundle/0001.sql.md` | **Imported by:**
`specs/src/yoetz_core/adapters/sqlite/start_catalog.md`,
`specs/src/yoetz_core/adapters/sqlite/repository.md`, `specs/src/yoetz_core/application/start.md`

## Purpose

Owns the migration registries, byte-identity checks, and rules for moving a database between released
schema versions. ADR-003 decision 5 freezes `specs/migrations/catalog/0001.sql.md` and
`specs/migrations/bundle/0001.sql.md` as migration `0001`; the installed package contains
byte-identical resource copies. This module loads those frozen bytes and implements the migration
runner rules: exclusive maintenance generation,
verified backup first, `user_version` and schema metadata bumped together, fail-closed on newer
unknown schemas, and the absolute rule that canonical event bytes are never rewritten.

## Public surface

- `CATALOG_MIGRATIONS: tuple[Migration, ...]` and `BUNDLE_MIGRATIONS: tuple[Migration, ...]` —
  ordered frozen migration registries; v0.1 each contains exactly `Migration("0001", ddl)`
  as registered in `specs/INTERFACES.md`.
- `initialize_catalog(db) -> None`, `initialize_bundle(db, bundle_meta_seed) -> None` — run all
  registered migrations on a fresh (`uninitialized`) database.
- `run_migrations(db, registry, *, maintenance: MaintenanceHandle) -> MigrationReport` — upgrade
  an existing database to the current registered version.
- `current_schema_version(registry) -> int`.

Reviewable DDL lives in `specs/migrations/catalog/0001.sql.md` and
`specs/migrations/bundle/0001.sql.md`; their future SQL files are mirrored byte-identically under
`src/yoetz_core/resources/migrations/`. Packaging and startup tests assert
source/resource/registry equality before any SQL executes.

## Behavior

### Migration `0001` — catalog

Executes the exact frozen statements in `specs/migrations/catalog/0001.sql.md`: application ID;
`catalog_meta`; generation- and identity-bound `task_routes`; `start_operations`;
`privacy_policy_versions`; `privacy_policy_transitions`; `privacy_audit_records`;
`privacy_root_sets`; `maintenance_operations`; `retained_task_routes`; all nine named indexes; then
user version `1`.
Privacy audit state includes the separate objectless agent-projection/local-consume branch, exact
terminal outcome/reason constraints, indexed structural receipt inspection, and monotonic catalog
ObjectRef roots independent of ledger inventory. Canonical catalog fields forbid content-derived
unkeyed digests and proposal/result plaintext.
The catalog table admits exactly one pending maintenance operation per task. Its fenced phase CAS
binds the operation kind, request and plan digests, source frontier, route identities, committed
locations, privacy-root generation/digest, owner generation, lease generation, and any completed
backup manifest. Restore switches routes in one catalog transaction: retain the old generated route,
advance active route generation and identity, preserve/update the exact privacy-root route binding,
and move `target_verified -> route_switched`, or commit none of those changes.

### Migration `0001` — task bundle

Executes the exact frozen DDL in `specs/migrations/bundle/0001.sql.md`, in this order (foreign keys may reference
later-created tables; SQLite defers resolution to DML, and the runner completes all tables before
any DML):

```sql
PRAGMA application_id = 0x594F4554;   -- then, at the end, PRAGMA user_version = 1

CREATE TABLE bundle_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE counters (
    name TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL CHECK (next_value > 0)
) STRICT, WITHOUT ROWID;

CREATE TABLE writers (
    writer_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    next_writer_seq INTEGER NOT NULL CHECK (next_writer_seq > 0),
    head_entry_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'closed', 'quarantined')),
    created_at TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE operations (
    writer_id TEXT NOT NULL REFERENCES writers(writer_id),
    operation_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL
        CHECK (operation_kind IN ('publish_work', 'check', 'respond', 'receipt')),
    request_digest TEXT NOT NULL,
    resume_object_id TEXT REFERENCES objects(object_id),
    state TEXT NOT NULL CHECK (state IN ('pending', 'complete', 'quarantined')),
    phase TEXT NOT NULL CHECK (phase IN (
        'reserved',
        'local_ready',
        'semantic_wait',
        'ready_to_finalize',
        'terminal'
    )),
    owner_generation TEXT,
    lease_owner_id TEXT,
    lease_generation INTEGER CHECK (lease_generation > 0),
    lease_expires_at TEXT,
    first_ingestion_seq INTEGER,
    last_ingestion_seq INTEGER,
    result_canonical BLOB,
    result_digest TEXT,
    result_object_id TEXT REFERENCES objects(object_id),
    quarantine_code TEXT,
    terminal_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (writer_id, operation_id),
    CHECK (
        (
            state = 'pending'
            AND operation_kind = 'check'
            AND phase != 'terminal'
            AND resume_object_id IS NOT NULL
            AND owner_generation IS NOT NULL
            AND lease_owner_id IS NOT NULL
            AND lease_generation IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND result_canonical IS NULL
            AND result_digest IS NULL
            AND quarantine_code IS NULL
            AND terminal_at IS NULL
        )
        OR
        (
            state = 'complete'
            AND phase = 'terminal'
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND result_canonical IS NOT NULL
            AND result_digest IS NOT NULL
            AND quarantine_code IS NULL
            AND terminal_at IS NOT NULL
        )
        OR
        (
            state = 'quarantined'
            AND phase = 'terminal'
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND result_canonical IS NOT NULL
            AND result_digest IS NOT NULL
            AND quarantine_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
    ),
    CHECK (phase != 'semantic_wait' OR operation_kind = 'check'),
    CHECK (
        (first_ingestion_seq IS NULL AND last_ingestion_seq IS NULL)
        OR
        (first_ingestion_seq IS NOT NULL AND last_ingestion_seq >= first_ingestion_seq)
    )
) STRICT, WITHOUT ROWID;

CREATE TABLE objects (
    object_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    plaintext_size INTEGER NOT NULL CHECK (plaintext_size >= 0),
    commitment TEXT NOT NULL,
    envelope_digest TEXT NOT NULL,
    encryption_format TEXT NOT NULL,
    key_slot TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('present', 'redacted', 'missing', 'quarantined')),
    durable_at TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE semantic_jobs (
    job_id TEXT PRIMARY KEY,
    writer_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    case_digest TEXT NOT NULL,
    case_object_id TEXT NOT NULL REFERENCES objects(object_id),
    state TEXT NOT NULL
        CHECK (state IN ('queued', 'leased', 'succeeded', 'failed', 'quarantined')),
    active_attempt_id TEXT,
    selected_attempt_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    owner_generation TEXT,
    lease_owner_id TEXT,
    lease_generation INTEGER CHECK (lease_generation > 0),
    lease_expires_at TEXT,
    selected_result_object_id TEXT REFERENCES objects(object_id),
    terminal_code TEXT,
    terminal_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (writer_id, operation_id)
        REFERENCES operations(writer_id, operation_id),
    FOREIGN KEY (job_id, active_attempt_id)
        REFERENCES semantic_attempts(job_id, attempt_id),
    FOREIGN KEY (job_id, selected_attempt_id)
        REFERENCES semantic_attempts(job_id, attempt_id),
    UNIQUE (writer_id, operation_id, case_digest),
    CHECK (
        (
            state = 'queued'
            AND active_attempt_id IS NULL
            AND selected_attempt_id IS NULL
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND selected_result_object_id IS NULL
            AND terminal_code IS NULL
            AND terminal_at IS NULL
        )
        OR
        (
            state = 'leased'
            AND active_attempt_id IS NOT NULL
            AND selected_attempt_id IS NULL
            AND owner_generation IS NOT NULL
            AND lease_owner_id IS NOT NULL
            AND lease_generation IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND selected_result_object_id IS NULL
            AND terminal_code IS NULL
            AND terminal_at IS NULL
        )
        OR
        (
            state = 'succeeded'
            AND active_attempt_id IS NULL
            AND selected_attempt_id IS NOT NULL
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND selected_result_object_id IS NOT NULL
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
        OR
        (
            state IN ('failed', 'quarantined')
            AND active_attempt_id IS NULL
            AND selected_attempt_id IS NULL
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND selected_result_object_id IS NULL
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
    )
) STRICT, WITHOUT ROWID;

CREATE TABLE semantic_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES semantic_jobs(job_id),
    attempt_ordinal INTEGER NOT NULL CHECK (attempt_ordinal > 0),
    provider_request_id TEXT NOT NULL,
    owner_generation TEXT NOT NULL,
    lease_owner_id TEXT NOT NULL,
    lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
    state TEXT NOT NULL CHECK (state IN (
        'started',
        'response_durable',
        'selected',
        'failed',
        'expired',
        'late'
    )),
    result_object_id TEXT REFERENCES objects(object_id),
    terminal_code TEXT,
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    UNIQUE (job_id, attempt_id),
    UNIQUE (job_id, attempt_ordinal),
    UNIQUE (job_id, lease_generation),
    CHECK (
        (
            state = 'started'
            AND result_object_id IS NULL
            AND terminal_code IS NULL
            AND terminal_at IS NULL
        )
        OR
        (
            state = 'response_durable'
            AND result_object_id IS NOT NULL
            AND terminal_code IS NULL
            AND terminal_at IS NULL
        )
        OR
        (
            state = 'selected'
            AND result_object_id IS NOT NULL
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
        OR
        (
            state = 'failed'
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
        OR
        (
            state = 'expired'
            AND result_object_id IS NULL
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
        OR
        (
            state = 'late'
            AND result_object_id IS NOT NULL
            AND terminal_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
    )
) STRICT, WITHOUT ROWID;

CREATE UNIQUE INDEX semantic_attempts_one_selected
ON semantic_attempts(job_id)
WHERE state = 'selected';

CREATE TABLE events (
    ingestion_seq INTEGER PRIMARY KEY CHECK (ingestion_seq > 0),
    event_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_type TEXT NOT NULL,
    author_assurance TEXT NOT NULL,
    writer_id TEXT NOT NULL REFERENCES writers(writer_id),
    writer_seq INTEGER NOT NULL CHECK (writer_seq > 0),
    operation_id TEXT NOT NULL,
    previous_ledger_digest TEXT NOT NULL,
    previous_writer_digest TEXT NOT NULL,
    entry_digest TEXT NOT NULL UNIQUE,
    canonical_entry BLOB NOT NULL,
    payload_object_id TEXT NOT NULL REFERENCES objects(object_id),
    payload_commitment TEXT NOT NULL,
    publication_channel TEXT NOT NULL,
    redaction_state TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    UNIQUE (writer_id, writer_seq),
    UNIQUE (writer_id, operation_id, event_id)
) STRICT;

CREATE TABLE event_parents (
    child_event_id TEXT NOT NULL REFERENCES events(event_id),
    parent_event_id TEXT NOT NULL REFERENCES events(event_id),
    PRIMARY KEY (child_event_id, parent_event_id),
    CHECK (child_event_id <> parent_event_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE event_refs (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    ref_type TEXT NOT NULL CHECK (ref_type IN ('artifact', 'evidence', 'finding', 'claim')),
    target_id TEXT NOT NULL,
    PRIMARY KEY (event_id, ref_type, target_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE projection_state (
    projection_name TEXT PRIMARY KEY,
    projection_version INTEGER NOT NULL,
    applied_through_seq INTEGER NOT NULL CHECK (applied_through_seq >= 0),
    state_digest TEXT NOT NULL,
    engine_version TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE maintenance_pins (
    pin_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('backup', 'export', 'rebuild')),
    frontier_seq INTEGER NOT NULL CHECK (frontier_seq >= 0),
    frontier_digest TEXT NOT NULL,
    privacy_root_generation INTEGER NOT NULL CHECK (privacy_root_generation >= 0),
    privacy_root_digest TEXT NOT NULL,
    owner_generation TEXT NOT NULL,
    lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'released', 'expired')),
    created_at TEXT NOT NULL,
    released_at TEXT,
    CHECK (
        (state = 'active' AND released_at IS NULL)
        OR
        (state IN ('released', 'expired') AND released_at IS NOT NULL)
    )
) STRICT, WITHOUT ROWID;

CREATE UNIQUE INDEX maintenance_pins_one_active_operation
ON maintenance_pins(operation_id)
WHERE state = 'active';

CREATE INDEX events_session_seq ON events(session_id, ingestion_seq);
CREATE INDEX events_schema_seq ON events(schema_name, ingestion_seq);
CREATE INDEX events_writer_seq ON events(writer_id, writer_seq);
CREATE INDEX refs_target ON event_refs(ref_type, target_id);
CREATE INDEX semantic_jobs_operation_state
ON semantic_jobs(writer_id, operation_id, state);
```

Migration `0001` also creates the versioned **projection tables** for projection generation 1
(purpose-built typed tables from `specs/src/yoetz_core/kernel/projections.md` — obligations, claims, evidence
edges, findings, responses, coverage; exact columns owned by
`specs/src/yoetz_core/kernel/projections.md`, created here from that spec's DDL constant with a
`p1_` generation prefix). Projection tables are disposable; dropping and replaying them is always
legal.

Before projection tables, the same frozen bundle migration creates `import_jobs`,
`import_request_aliases`, `import_batches`, and their bounded pending/status/next-batch indexes
with the exact columns/CHECK/FK/uniqueness contract in
`specs/migrations/bundle/0001.sql.md` and `adapters/sqlite/importer.md`. These are release schema,
not lazy adapter-owned DDL. The runner's schema inventory rejects their absence or any normalized
SQL/index mismatch.

### `initialize_bundle(db, bundle_meta_seed)`

Runs on a fresh database inside one transaction (`BEGIN IMMEDIATE` … `COMMIT`): executes
migration `0001` DDL, seeds `counters` with `('ingestion_sequence', 1)`, and inserts the required
`bundle_meta` keys — protocol version, storage schema version, task ID, current global head
sequence (`0`)/head digest (`"genesis"`), active projection generation, accepted SQLite
support-manifest ID, encryption-format ID (`yoetz-object/1`), commitment-key ID, plus the
ADR-001 ownership row (`owner_generation`, `owner_nonce`, `heartbeat_at`), and route facts
`route_generation`, `route_identity_digest`, and `route_state`, plus
`import_schema_version=1`. A new bundle starts as `staging`;
the catalog route transaction makes it active only after full validation. A restored target also
receives `restored_from_manifest_digest` and `restore_operation_id`. `PRAGMA user_version`
and the `storage_schema_version` metadata key are set in this same transaction — they move
together or not at all. (`PRAGMA application_id`/`user_version` are executed via the same
connection immediately around the transaction since PRAGMA writes in SQLite are transactional
for these two pragmas; if the platform build proves otherwise, the runner verifies both values
after COMMIT and deletes the file on mismatch — a fresh initialize is always safely restartable
because the caller creates the file in a staging path and renames it into place only after
verification.)

### `run_migrations(db, registry, maintenance)` — migration runner rules

1. Requires a `MaintenanceHandle` proving an **exclusive maintenance generation** acquired by
   `specs/src/yoetz_core/adapters/sqlite/maintenance.py.md`; ordinary writes receive bounded
   retryable `BUNDLE_BUSY` during migration. The corresponding catalog operation must be
   `pending/migration` at the expected fenced phase.
2. `verify_schema_identity` first. `state="current"` → no-op report. `state="uninitialized"` →
   delegate to `initialize_*`. Newer unknown version → `StorageUnsafeError("schema_newer_than_
   binary")`, fail closed for writes (a payload-safe structural inspect may be offered only if
   explicitly tested). Unknown *older* version not in the registry → same failure with reason
   `schema_version_unknown`.
3. **Backup first**: run the verified backup sequence in
   `specs/src/yoetz_core/adapters/sqlite/maintenance.py.md` and
   `specs/docs/runbooks/backup-restore.md.md`, then
   record its manifest ID in the migration report. No backup, no migration.
4. Apply each pending migration inside one transaction where SQLite permits the DDL
   transactionally; bump `PRAGMA user_version` and the `storage_schema_version` /
   `catalog_meta.storage_schema_version` metadata **together in that same step**.
5. Canonical bytes are never rewritten: a migration MUST NOT touch `events.canonical_entry`,
   `entry_digest`, or any recorded digest/commitment column value. The runner asserts after each
   migration that `count(*)` and `sum(length(canonical_entry))` over `events` are unchanged and
   that a sampled set of `entry_digest` values still verifies against their bytes; violation
   aborts and restores from the step-3 backup.
6. Projection schema changes never migrate data in place: they create a new projection
   generation's empty tables and trigger the deterministic replay defined by
   `specs/src/yoetz_core/kernel/projections.md`; the old generation is
   removed only after verification.
7. Reopen through `open_writer`'s normal validation after the run; the report records
   from/to versions, backup manifest ID, and duration.

Mixed-version writers are out of scope; upgrade is all-or-nothing per installation.

## Errors and edge cases

- Failure mid-DDL: the transaction rolls back; `user_version` still names the old version;
  the verified backup guarantees restore even from non-transactional DDL failure.
- `MIGRATION_REQUIRED` is what non-runner callers see when identity state is
  `migration_required`; the runner is the only code allowed to change schema.
- The runner never runs concurrently with a writer: the maintenance generation fences all leases
  and write paths (ADR-001 — advancing/holding the maintenance generation invalidates leases).
- Empty registries, duplicate migration IDs, or non-contiguous version chains are programming
  errors detected at import time (module-level assertion), not runtime states.

## Invariants

- All executed DDL is a byte-verified registry entry sourced from the reviewable migration SQL and
  its byte-identical packaged resource; no other module issues `CREATE`/`ALTER`/`DROP` except
  projection-generation replacement driven by the runner.
- `user_version` and schema metadata always agree on every committed database.
- Migration never rewrites canonical entry bytes, digests, or commitments.
- A database at a newer schema than the binary is never written.
- Migration `0001`'s DDL is frozen once released; changes require migration `0002`.
- Every active maintenance pin binds task, operation, full frontier, owner generation, and lease
  generation; expiry alone never authorizes release or collection.
- Active and retained catalog routes have distinct positive generations and unique identity digests.

## Tests

- `specs/tests/integration.md`: fresh initialize (catalog + bundle) produces byte-identical schema to
  the fixture dump; CHECK constraints reject each forbidden state/phase/lease combination row by
  row; `user_version`/metadata agreement; catalog maintenance phase/location/lease constraints;
  retained-route uniqueness and atomic restore switch; pin create/release/expiry CAS.
- `specs/tests/integration.md` and `specs/tests/packaging.md`: golden old-bundle fixtures per released version; failed migration
  preserves the original via backup; newer-schema fail-closed; canonical-bytes-unchanged
  assertion; projection generation replacement.
- `specs/tests/subprocess.md`: kill during migration (kill point 14) leaves either old or new version,
  never a hybrid, and recovery restores the invariant.

## Open questions

None.

E-003 is the sole central platform-behavior gate.
