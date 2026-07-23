# src/yoetz/adapters/sqlite/migrations.py — schema migrations and the migration runner

**Wave:** C | **ADRs:** ADR-003 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/sqlite/connection.md`,
`specs/src/yoetz/ports/maintenance.py.md` (`MaintenanceHandle` only),
`specs/migrations/catalog/0001.sql.md`, `specs/migrations/bundle/0001.sql.md`,
`specs/migrations/bundle/0002.sql.md`, `specs/migrations/bundle/0003.sql.md` | **Imported by:**
`specs/src/yoetz/adapters/sqlite/start_catalog.md`,
`specs/src/yoetz/adapters/sqlite/repository.md`,
`specs/src/yoetz/adapters/sqlite/maintenance.py.md`, `specs/src/yoetz/application/start.md`

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
  ordered frozen migration registries; catalog is exactly `Migration("0001", ddl)`; bundle is
  contiguous `0001`, `0002`, then `0003` (encrypted observation content/bindings, logical
  identity, check-policy trust, verification, and advice history) as registered in
  `specs/INTERFACES.md`.
- `initialize_catalog(db) -> None`, `initialize_bundle(db, bundle_meta_seed) -> None` — run all
  registered migrations on a fresh (`uninitialized`) database.
- `run_migrations(db, registry, *, maintenance: MaintenanceHandle) -> MigrationReport` — upgrade
  an existing database to the current registered version; `MaintenanceHandle` is the neutral
  service-internal authority value owned by `ports/maintenance.py`, not an import from the concrete
  SQLite maintenance adapter.
- `current_schema_version(registry) -> int`.

Reviewable DDL lives in `specs/migrations/catalog/0001.sql.md`,
`specs/migrations/bundle/0001.sql.md`, `specs/migrations/bundle/0002.sql.md`, and
`specs/migrations/bundle/0003.sql.md`; their future SQL files are mirrored byte-identically under
`src/yoetz/resources/migrations/`. Packaging and startup tests assert source/resource/registry
equality before any SQL executes.

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
    projection_status TEXT NOT NULL
        CHECK (projection_status IN ('projected', 'unknown_unprojected')),
    summary_code TEXT NOT NULL CHECK (summary_code IN (
        'session_opened',
        'session_resumed',
        'plan_published',
        'obligation_published',
        'assignment_recorded',
        'decision_recorded',
        'action_recorded',
        'result_recorded',
        'evidence_recorded',
        'claim_recorded',
        'plan_revised',
        'finding_recorded',
        'response_recorded',
        'redaction_recorded',
        'check_recorded',
        'receipt_recorded',
        'opaque_unknown'
    )),
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
    occurred_at TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    UNIQUE (writer_id, writer_seq),
    UNIQUE (writer_id, operation_id, event_id),
    CHECK (
        (
            projection_status = 'unknown_unprojected'
            AND summary_code = 'opaque_unknown'
        )
        OR
        (
            projection_status = 'projected'
            AND summary_code = schema_name
            AND summary_code <> 'opaque_unknown'
        )
    )
) STRICT;

CREATE TABLE event_projection_locators (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    schema_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    logical_key TEXT,
    canonical_payload_digest TEXT NOT NULL,
    redaction_target_event_ids BLOB NOT NULL,
    redaction_target_object_ids BLOB NOT NULL
) STRICT, WITHOUT ROWID;

CREATE TABLE event_parents (
    child_event_id TEXT NOT NULL REFERENCES events(event_id),
    parent_event_id TEXT NOT NULL REFERENCES events(event_id),
    PRIMARY KEY (child_event_id, parent_event_id),
    CHECK (child_event_id <> parent_event_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE event_refs (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    ref_type TEXT NOT NULL CHECK (ref_type IN ('artifact', 'evidence', 'result', 'finding', 'claim')),
    target_id TEXT NOT NULL,
    PRIMARY KEY (event_id, ref_type, target_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE projection_state (
    projection_name TEXT PRIMARY KEY CHECK (projection_name = 'work'),
    projection_version TEXT NOT NULL CHECK (projection_version = 'yoetz/0.1.0'),
    projection_generation INTEGER NOT NULL CHECK (projection_generation = 1),
    applied_through_seq INTEGER NOT NULL CHECK (applied_through_seq >= 0),
    state_digest TEXT NOT NULL,
    engine_version TEXT NOT NULL CHECK (engine_version = '0.1.0')
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
CREATE INDEX events_session_schema_seq
ON events(session_id, schema_name, ingestion_seq);
CREATE INDEX events_session_author_seq
ON events(session_id, author_id, ingestion_seq);
CREATE INDEX events_session_schema_author_seq
ON events(session_id, schema_name, author_id, ingestion_seq);
CREATE INDEX events_schema_seq ON events(schema_name, ingestion_seq);
CREATE INDEX events_writer_seq ON events(writer_id, writer_seq);
CREATE UNIQUE INDEX events_payload_object ON events(payload_object_id);
CREATE INDEX refs_target ON event_refs(ref_type, target_id);
CREATE INDEX semantic_jobs_operation_state
ON semantic_jobs(writer_id, operation_id, state);
CREATE INDEX import_request_aliases_source
ON import_request_aliases(source_identity_digest, requesting_writer_id, request_id);
CREATE INDEX import_jobs_session_state
ON import_jobs(session_id, state, source_identity_digest);
CREATE INDEX import_jobs_session_terminal
ON import_jobs(session_id, terminal_at, source_identity_digest);
CREATE INDEX import_batches_next
ON import_batches(source_identity_digest, state, batch_index);

CREATE TABLE import_publication_requests (
    publishing_writer_id TEXT NOT NULL REFERENCES writers(writer_id),
    request_id TEXT NOT NULL,
    source_identity_digest TEXT NOT NULL REFERENCES import_jobs(source_identity_digest),
    publication_ordinal INTEGER NOT NULL CHECK (publication_ordinal BETWEEN 0 AND 1024),
    PRIMARY KEY (publishing_writer_id, request_id),
    UNIQUE (source_identity_digest, publication_ordinal)
) STRICT, WITHOUT ROWID;
```

Migration `0001` also creates the versioned **projection tables** for projection generation 1. The
canonical root `migrations/bundle/0001.sql` owns their exact `p1_` table/column/constraint/index
bytes, and the installed migration resource is executed unchanged after manifest verification.
`projection_state.projection_version` is text because the active identity is
`yoetz/0.1.0`; the independent generation number is the integer `PROJECTION_GENERATION = 1` and
is stored in its own constrained `projection_generation` integer column.
The current-record inventory is `p1_projection_state`, `p1_plans`, `p1_obligations`,
`p1_obligation_replacements`, `p1_decisions`, `p1_assignments`, `p1_actions`, `p1_results`,
`p1_evidence`, `p1_claims`, `p1_contradictions`, `p1_findings`, `p1_responses`, and
`p1_coverage_gaps`. The exact temporal query inventory is `p1_query_snapshots`,
`p1_query_assignments`, `p1_query_assignment_obligations`, `p1_query_obligations`,
`p1_query_obligation_source_refs`, `p1_query_obligation_evidence_refs`,
`p1_query_obligation_actors`, `p1_query_findings`, `p1_query_finding_subject_refs`,
`p1_query_finding_order`, `p1_query_responses`, `p1_query_response_evidence_refs`,
`p1_query_checks`, `p1_query_check_scope_refs`, `p1_query_check_policy_executions`,
`p1_query_check_returned_findings`, and `p1_query_evidence`, plus all named `p1_` indexes frozen
beside those tables. Normalized
`sqlite_schema.sql`, `PRAGMA table_xinfo`, foreign-key lists, index lists, and STRICT/WITHOUT-ROWID
flags must match that root byte contract; a semantically similar lazy-created schema is not legal.
`specs/src/yoetz/kernel/projections.md` owns the corresponding pure typed record families and
derivation semantics only; this runner never concatenates a Python DDL constant or synthesizes SQL.
Projection tables are disposable; dropping and replaying them through a later registered migration
is always legal.

No `p1_` table has a payload, body, open JSON, description, statement, reason, command, or summary
column. A current record row follows `source_event_id` to the authenticated encrypted event
payload object when the record is readable; a tombstone never opens that object. The only canonical
structural blobs in projection storage are closed Coverage/gap/finding-ID values and complete
finding issue keys. Query versions keep half-open frontier validity and nullable typed filter/rank
facts; a redaction scrubs those facts and their edges/fanout from every old interval. The migration
module treats the root SQL as opaque bytes and therefore cannot weaken this privacy boundary by
generating an alternate projection table.

The unique `events_payload_object` index is part of the same frozen schema identity. Combined with
the indexed `event_refs` artifact mirror and canonical locator target arrays, it reconstructs all
three non-plaintext `ReplayIndex` reverse mappings. A payload object reused by two event envelopes
is rejected at insertion rather than left as iteration-order-dependent corruption.

`event_projection_locators` is not a projection cache. It has exactly one row per accepted event,
inserted in the same transaction as `events`. The two target blobs are JCS arrays of sorted typed
IDs and are `[]` except for `redaction_recorded`; known-event `logical_key` follows the closed
family mapping in `domain/events.md`, while an unknown event requires `NULL`. The digest is over
the normalized canonical payload. Before binding, the
repository validates every string/array/digest and proves schema identity and locator equality with
the decoded/frozen payload when it is available. On load it joins the row into runtime-only
`ProjectionLocator` metadata for either ledger-record variant. No
payload text or open JSON object is permitted. Missing, extra, or mismatched locator rows fail
integrity/rebuild rather than falling back to disposable `p1_` state.

Before projection tables, the same frozen bundle migration creates `import_jobs`,
`import_request_aliases`, `import_batches`, and their bounded pending/status/next-batch indexes
with the exact columns/CHECK/FK/uniqueness contract in
`specs/migrations/bundle/0001.sql.md`. These are release schema, not lazy adapter-owned DDL; the
importer adapter conforms to them but neither supplies nor co-owns their DDL. The runner's schema
inventory rejects their absence or any normalized SQL/index mismatch.

The exact importer index names and ordered columns are `import_request_aliases_source` on
`(source_identity_digest, requesting_writer_id, request_id)`, `import_jobs_session_state` on
`(session_id, state, source_identity_digest)`, `import_jobs_session_terminal` on
`(session_id, terminal_at, source_identity_digest)`, and `import_batches_next` on
`(source_identity_digest, state, batch_index)`. They are executed from the frozen migration bytes;
the runner never creates a missing access path during open or first use.

The history row-query paths are `events_session_seq(session_id, ingestion_seq)`,
`events_session_schema_seq(session_id, schema_name, ingestion_seq)`,
`events_session_author_seq(session_id, author_id, ingestion_seq)`, and
`events_session_schema_author_seq(session_id, schema_name, author_id, ingestion_seq)`. The
repository selects the one matching the supplied filter shape, applies `after_sequence` and the
typed cursor as an exclusive range on `ingestion_seq`, and reads at most `limit + 1` rows without a
temporary sort.

The generation-1 query sidecars and all forty-six named `p1_` indexes execute only from the same
frozen root migration bytes. The finite finding fanout is sixteen rows for an unresolved readable
finding and eight for a resolved readable finding; tombstones own no fanout. Actor-filtered
obligation SQL drives from `p1_query_obligation_actors` (fixed join order) and point-joins its
owning version. Every other list filter shape selects its exact named covering index and applies
the typed exclusive position before reading at most `limit + 1` candidate keys. The runner never
creates, repairs, or analyzes an access path at open time.

### `initialize_bundle(db, bundle_meta_seed)`

Runs on a fresh database inside one transaction (`BEGIN IMMEDIATE` … `COMMIT`): executes every
registered bundle migration in order (`0001`, `0002`, then `0003`), seeds `counters` with
`('ingestion_sequence', 1)`, and inserts the required `bundle_meta` keys — protocol version,
storage schema version equal to `current_schema_version(BUNDLE_MIGRATIONS)`, task ID, current
global head sequence (`0`)/head digest (`"genesis"`), active projection generation, accepted SQLite
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

The same transaction inserts exactly one `projection_state` row
`('work', 'yoetz/0.1.0', 1, 0,
'sha256:0f8ec0c66f196bee631ef5447ef5c914e812fe530ee1f4b7477e24b22a9911c9', '0.1.0')` and one matching
`p1_projection_state` row with frontier `0`, head `genesis`, NULL task-title/current-plan/status
Coverage/gap fields, zero compact counters, all seven latest-check columns NULL, freshness
`unknown`, and unknown-event count `0`. It also inserts one `p1_query_snapshots` genesis row with
`valid_from_seq=0`, open-ended validity, head `genesis`, the same NULL source/Coverage/gap fields,
zero counters, and `unknown` freshness. That digest is the frozen canonical encoding of
the exact 17-key `projection_snapshot(empty_projection_state())`, verified by the projection vector
tests; it is not computed from database row order. No other `p1_` row exists after initialization.

### `run_migrations(db, registry, maintenance)` — migration runner rules

1. Requires the `ports/maintenance.py` `MaintenanceHandle` proving an **exclusive maintenance
   generation**, acquired by `specs/src/yoetz/adapters/sqlite/maintenance.py.md`; ordinary writes
   receive bounded retryable `BUNDLE_BUSY` during migration. The runner revalidates its task,
   request, route, owner generation, lease generation, and plan digest against the corresponding
   catalog operation, which must be `pending/migration` at the expected fenced phase. The handle is
   never accepted on nominal type alone.
2. `verify_schema_identity` first. `state="current"` → no-op report. `state="uninitialized"` →
   delegate to `initialize_*`. Newer unknown version → `StorageUnsafeError("schema_newer_than_
   binary")`, fail closed for writes (a payload-safe structural inspect may be offered only if
   explicitly tested). Older versions in the contiguous registry advance by applying pending
   numbered SQL files; a non-contiguous gap or missing next version fails closed with
   `schema_version_unknown`.
3. **Backup first**: require the same fenced catalog operation to be at `backup_ready` with a
   completed, verified backup-manifest digest already committed by the owning maintenance adapter.
   The runner records that manifest ID in the migration report. It never imports or recursively
   calls the concrete maintenance adapter. No verified backup, no migration.
4. Apply each pending migration inside one transaction where SQLite permits the DDL
   transactionally; bump `PRAGMA user_version` (via the migration SQL) and the
   `storage_schema_version` / `catalog_meta.storage_schema_version` metadata **together in that
   same step**.
5. Canonical bytes are never rewritten: a migration MUST NOT touch `events.canonical_entry`,
   `entry_digest`, or any recorded digest/commitment column value. The runner asserts after each
   migration that `count(*)` and `sum(length(canonical_entry))` over `events` are unchanged and
   that a sampled set of `entry_digest` values still verifies against their bytes; violation
   aborts and restores from the step-3 backup.
6. Projection schema changes never migrate data in place: they create a new projection
   generation's empty tables and trigger the deterministic replay defined by
   `specs/src/yoetz/kernel/projections.md`; the old generation is
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

None for this module. `W-C-001` closed on 2026-07-19: the runner loads and verifies the standalone
root bundle-migration resource and contains no Python DDL copy.

E-003 is the sole central platform-behavior gate.
