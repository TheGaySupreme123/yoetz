# src/yoetz/adapters/sqlite/start_catalog.py — StartCatalogPort over catalog.sqlite3

**Wave:** C | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/sqlite/connection.md`,
`specs/src/yoetz/adapters/sqlite/migrations.md`,
`specs/migrations/catalog/0001.sql.md`,
`specs/src/yoetz/protocol/canonical.md`, `specs/src/yoetz/protocol/ids.md`,
`specs/src/yoetz/ports/start_catalog.md`, `specs/src/yoetz/ports/keys.md` |
**Imported by:** `specs/src/yoetz/application/start.md`,
`specs/src/yoetz/adapters/sqlite/recovery.md`

## Purpose

`start` is the only operation that runs before a task bundle or writer exists, so its idempotency
cannot live in a bundle. This file implements `StartCatalogPort` over `catalog.sqlite3`: the
structural task-routing table, the `start_operations` idempotency/state-machine table, and the
seven-step crash-safe start workflow specified below and exposed by
`specs/src/yoetz/ports/start_catalog.md`. It guarantees that one
`(installation_id, operation_id)` key allocates exactly one task/session/writer/lifecycle-event
tuple forever, that retries resume rather than reallocate, and that no raw workspace or task
reference ever enters the catalog — only commitments produced through the installation-scoped
opaque `K_lookup` handle.

## Public surface

- `SqliteStartCatalog(connection, *, installation_id, lookup: MacKeyHandle, ...)` — implements
  `specs/src/yoetz/ports/start_catalog.md`. Construction requires a ready-vault handle bound to
  `MacKeyPurpose.catalog_lookup`; it accepts no raw key bytes or key-store object:
  - `commit_identity(StartIdentityInput) -> StartIdentityCommitments`
  - `resolve_route(session_id) -> TaskRoute | None`
  - `reserve_or_resume(StartCommand) -> StartAllocation`
  - `advance_phase(allocation, StartPhase, EncryptedResultRef | None) -> StartAllocation`
  - `complete(allocation, EncryptedResultRef, StartCompletionEvidence)`
  - `quarantine(allocation, SafeReason)`
- `workspace_ref_commitment(lookup: MacKeyHandle, workspace_ref: JsonValue) -> str` and
  `external_ref_commitment(lookup: MacKeyHandle, external_ref: JsonValue) -> str` — keyed lookup
  commitments registered in `specs/INTERFACES.md`.
- `StartQuarantineCode` — registered bounded enum of catalog quarantine codes:
  `start_route_contradiction`, `start_bundle_invalid`, `start_lifecycle_contradiction`,
  `start_result_object_missing`, `start_catalog_integrity`, `start_allocation_ambiguous`.
- Constant: `CATALOG_SCHEMA_VERSION = 1`. The adapter imports, rather than redefines, the byte-exact
  `START_TITLE_DOMAIN`, `WORKSPACE_REF_DOMAIN`, and `EXTERNAL_REF_DOMAIN` constants from
  `ports/start_catalog.py`; each includes its trailing `\x00` delimiter.

`operation_id` in the physical schema is the public mutating `request_id`; the exact mapping is
owned by `specs/src/yoetz/ports/start_catalog.md`, which receives it inside `StartCommand`.

## Behavior

### Schema (migration `0001` of the catalog; DDL owned by `migrations.md`, reproduced here as the
behavioral contract)

```sql
PRAGMA user_version = 1;   -- plus PRAGMA application_id = 0x594F4554

CREATE TABLE task_routes (
    task_id TEXT PRIMARY KEY,
    workspace_ref_commitment TEXT,
    external_ref_commitment TEXT,
    active_session_id TEXT NOT NULL UNIQUE,
    bundle_relpath TEXT NOT NULL UNIQUE,
    route_generation INTEGER NOT NULL CHECK (route_generation > 0),
    active_route_identity_digest TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('initializing', 'active', 'quarantined')),
    quarantine_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (workspace_ref_commitment IS NULL AND external_ref_commitment IS NULL)
        OR
        (workspace_ref_commitment IS NOT NULL AND external_ref_commitment IS NOT NULL)
    ),
    CHECK (
        (state IN ('initializing', 'active') AND quarantine_code IS NULL)
        OR
        (state = 'quarantined' AND quarantine_code IS NOT NULL)
    )
) STRICT, WITHOUT ROWID;

CREATE UNIQUE INDEX task_routes_scoped_attachment
ON task_routes(workspace_ref_commitment, external_ref_commitment)
WHERE workspace_ref_commitment IS NOT NULL
  AND external_ref_commitment IS NOT NULL;

CREATE TABLE start_operations (
    installation_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    requested_mode TEXT NOT NULL
        CHECK (requested_mode IN ('create', 'attach', 'create_or_attach')),
    route_action TEXT NOT NULL CHECK (route_action IN ('created', 'attached')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'complete', 'quarantined')),
    phase TEXT NOT NULL CHECK (phase IN (
        'route_reserved',
        'bundle_ready',
        'lifecycle_committed',
        'result_published',
        'terminal'
    )),
    task_id TEXT NOT NULL REFERENCES task_routes(task_id),
    session_id TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    lifecycle_event_id TEXT NOT NULL,
    route_generation INTEGER NOT NULL CHECK (route_generation > 0),
    route_identity_digest TEXT NOT NULL,
    owner_generation TEXT,
    lease_owner_id TEXT,
    lease_generation INTEGER CHECK (lease_generation > 0),
    lease_expires_at TEXT,
    response_object_id TEXT,
    terminal_result_canonical BLOB,
    terminal_result_digest TEXT,
    quarantine_code TEXT,
    terminal_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (installation_id, operation_id),
    CHECK (
        (
            state = 'pending'
            AND phase != 'terminal'
            AND owner_generation IS NOT NULL
            AND lease_owner_id IS NOT NULL
            AND lease_generation IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND terminal_result_canonical IS NULL
            AND terminal_result_digest IS NULL
            AND quarantine_code IS NULL
            AND terminal_at IS NULL
            AND (phase != 'result_published' OR response_object_id IS NOT NULL)
        )
        OR
        (
            state = 'complete'
            AND phase = 'terminal'
            AND owner_generation IS NULL
            AND lease_owner_id IS NULL
            AND lease_generation IS NULL
            AND lease_expires_at IS NULL
            AND response_object_id IS NOT NULL
            AND terminal_result_canonical IS NOT NULL
            AND terminal_result_digest IS NOT NULL
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
            AND terminal_result_canonical IS NOT NULL
            AND terminal_result_digest IS NOT NULL
            AND quarantine_code IS NOT NULL
            AND terminal_at IS NOT NULL
        )
    )
) STRICT, WITHOUT ROWID;
```

### `resolve_route(session_id)`

Runs one indexed, bounded, query-only lookup on `task_routes.active_session_id`. Exact absence
returns `None`. Otherwise it validates task/session IDs and the generated
`tasks/<task_id>` relative route, requires a positive `route_generation`, maps the closed row
state to `TaskRouteState`, recomputes SHA-256 over canonical
`{"task_id": task_id, "bundle_relpath": bundle_relpath, "route_generation": route_generation}`,
constant-time compares it with `active_route_identity_digest`, and returns both as `TaskRoute`.
It never selects or exposes attachment commitments, timestamps, quarantine code, or any raw
reference. A duplicate/malformed row is
`STORAGE_CORRUPT`; busy/schema/path failures retain their classifications and are not absence.
No catalog ownership/operation lease is acquired by this read.


The complete catalog migration in `specs/migrations/catalog/0001.sql.md` additionally creates
`catalog_meta`, `maintenance_operations`, and `retained_task_routes`. This adapter does not advance
maintenance phases or mutate retained routes. It treats only the current `task_routes` row as
attachable, checks for a pending exclusive `restore`/`migration` operation before reserving or
completing a route mutation, and returns retryable `BUNDLE_BUSY` while that fenced operation owns
the task. A read-compatible pending backup does not by itself make the route non-attachable.

For a new route, `route_generation` is `1` and `active_route_identity_digest` is the canonical
digest of the structural map `{task_id, bundle_relpath, route_generation}`. The same values are
seeded into bundle metadata before activation. Attach preserves both fields. Only the restore CAS
defined by the catalog migration may increment the generation or replace the route identity.

### Commitments

```text
workspace_ref_commitment = lookup.mac(
    WORKSPACE_REF_DOMAIN, canonical_encode(workspace_ref))
external_ref_commitment = lookup.mac(
    EXTERNAL_REF_DOMAIN, canonical_encode(external_ref))
```

`lookup` is the purpose/domain/generation-bound `MacKeyHandle` over the sole installation-scoped
`K_lookup` from `KeyStorePort` (ADR-004). The adapter cannot inspect, serialize, clone, or rebind the
key and cannot use the handle for logging, privacy audit, or bundle commitments.
Raw `workspace_ref`/`external_ref` strings never enter the catalog, logs, or errors. The pair is
the exact attachment key, scoped implicitly by the server installation. `bundle_relpath` is
generated from the validated `task_id` (`tasks/<task_id>`), never accepted from a request.

### Lease validity rule (applies to every lease decision in this file)

A lease is valid only when its `owner_generation` equals the **current catalog owner generation**
AND `lease_expires_at` is in the future. Current generation + unexpired ⇒ another owner is live ⇒
return `OPERATION_PENDING`. Expired lease OR stale owner generation ⇒ fenced compare-and-swap
reclaim: update `owner_generation` to the current generation, `lease_owner_id` to this runtime's
owner nonce, `lease_generation = lease_generation + 1`, and a fresh `lease_expires_at` — all in
one `BEGIN IMMEDIATE` transaction whose UPDATE's WHERE clause names the old values (CAS). Wall
clock expiry never revives a stale generation.

The v0.1 start-operation lease is exactly 60 seconds and uses the shared half-life renewal policy.

### The 7-step crash-safe start state machine

`reserve_or_resume` executes steps 1–3 and, on resume, re-enters at the recorded phase; the
application `start` use case drives steps 4–5 through the returned `StartAllocation`; `complete`
executes step 6; step 7 governs every retry path.

1. **Validate (no transaction).** Validate the request, compute `request_digest` over the
   canonical publication-request identity bytes (ADR-002 decision 4; excludes ledger-assigned
   fields), and derive the two commitments when task refs are supplied. Mode semantics:
   `create` conflicts with an existing scoped key (`SESSION_CONFLICT`); `attach` requires an
   exact `session_id` or scoped key that resolves (`SESSION_NOT_FOUND` otherwise);
   `create_or_attach` attaches on the one exact match or creates. When both a `session_id` and
   commitments are supplied, they must resolve to the same route (`SESSION_CONFLICT` otherwise).
   A keyless `create` (no refs) is allowed; later attachment then requires the returned
   session ID.
2. **`BEGIN IMMEDIATE`, idempotency lookup.** Look up `(installation_id, operation_id)`.
   - Row exists, different `request_digest` → ROLLBACK, `IDEMPOTENCY_CONFLICT`.
   - Row exists, terminal (`complete` or `quarantined`) → return its stored
     `terminal_result_canonical` envelope (complete) or stable quarantine envelope; COMMIT.
   - Row exists, `pending`, valid lease (rule above) → COMMIT, return `OPERATION_PENDING`.
   - Row exists, `pending`, expired or stale-generation lease → fenced CAS reclaim (rule above);
     COMMIT; resume from the recorded phase treated as a **lower bound** (step 7).
   - No row → continue to step 3 in the same transaction.
3. **Allocate once.** Resolve the mode against `task_routes` (respecting the unique scoped
   attachment index). For `created` routes: mint `task_id`, `session_id`, `writer_id`,
   `lifecycle_event_id` via `IdPort.new` (`tsk_`/`ses_`/`wri_`/`evt_`), insert the
   `task_routes` row as `initializing` with route generation `1` and its computed route identity,
   and insert `start_operations` as
   `pending/route_reserved` with a live lease. For `attached` routes: reuse the route's
   `task_id`, mint a fresh `session_id`/`writer_id`/`lifecycle_event_id`, record
   `route_action='attached'`. COMMIT. No retry ever invents replacement IDs; these four IDs are
   permanent for this operation key.
4. **Bundle ready (outside catalog transactions).** The application creates or validates the
   allocated bundle directory + `ledger.sqlite3` (via `migrations.md` for create; via
   `recovery.md` validation for attach), then CAS-advances phase `route_reserved →
   bundle_ready` (UPDATE … WHERE state='pending' AND phase='route_reserved' AND lease fields
   match). It then appends the allocated lifecycle event (`session_opened` or
   `session_resumed`, using the pre-allocated `lifecycle_event_id`) idempotently through the
   bundle `LedgerPort` and CAS-advances to `lifecycle_committed`. Finding an identical event
   already durable means resume; contradictory durable state (same event ID, different bytes)
   quarantines with `start_lifecycle_contradiction`.
5. **Result published.** Durably publish the encrypted start-result object (user-visible
   content: title, raw refs, compact state) through `ObjectStorePort`, set
   `response_object_id`, CAS-advance to `result_published`.
6. **`complete(allocation, EncryptedResultRef)` — one catalog transaction.** `BEGIN IMMEDIATE`;
   re-verify: route row exists and matches the allocation's task, generated relative path,
   route generation, and route identity; no pending exclusive maintenance operation owns the task;
   bundle metadata has the same route generation/identity and its owner generation is current (via
   `assert_active_bundle_generation`); the lifecycle event is durable; the result object ID and
   response digest match the allocation. Then store the safe terminal envelope
   (`terminal_result_canonical` = canonical structural result bytes: assigned IDs, sequences,
   digests, reason codes only — no user content) and `terminal_result_digest`
   (= `canonical_digest` of those bytes), NULL all four lease fields, set `route_action`'s
   route to `active` with the new `active_session_id`, set `state='complete'`,
   `phase='terminal'`, `terminal_at`; COMMIT. Only after COMMIT returns may the application
   acknowledge. Catalog completion without a validated bundle is forbidden.
7. **Crash/resume rule.** A recorded phase is a **lower bound**: on reclaim, re-validate the
   durable state for the recorded phase before advancing (e.g., at `bundle_ready`, re-verify the
   bundle actually validates; at `result_published`, re-verify the object exists and
   authenticates). Expected absence at `route_reserved` (no bundle yet) is recoverable — redo the
   work. Only contradiction, corruption, or unsafe ambiguity becomes `quarantined/terminal` via
   `quarantine(allocation, SafeReason)`, which stores a stable safe envelope plus a
   `StartQuarantineCode` (never an exception string) and clears the lease fields in one
   transaction.

### `quarantine(allocation, SafeReason)`

`BEGIN IMMEDIATE`; verify the caller still holds the lease (CAS on lease fields); write
`state='quarantined'`, `phase='terminal'`, `quarantine_code` from the bounded enum, a stable safe
`terminal_result_canonical` envelope + digest, `terminal_at`; NULL lease fields; if the route was
`initializing`, mark it `quarantined` with the same code; COMMIT. Subsequent retries of the same
operation key return this envelope forever (idempotency table row "quarantined | any → stable
quarantine reason").

## Errors and edge cases

- Public mappings: digest mismatch → `IDEMPOTENCY_CONFLICT`; live lease → `OPERATION_PENDING`
  (retryable, same request ID); mode conflicts → `SESSION_CONFLICT` / `SESSION_NOT_FOUND`;
  catalog busy → `BUNDLE_BUSY`; catalog integrity failure → `STORAGE_CORRUPT` (recovery owns
  quarantining the catalog file).
- The unique `task_routes_scoped_attachment` index makes a create/create race impossible: the
  second inserter fails the index inside its transaction and re-resolves as attach (mode
  permitting) or `SESSION_CONFLICT`.
- `active_session_id` UNIQUE means attach-by-session-ID resolves at most one route; a session ID
  that matches no route is `SESSION_NOT_FOUND`, never fuzzy-matched.
- The runtime-facing `resolve_route` uses that same unique index but returns `None` for exact
  absence so `BundleRuntimePort` owns the public `SESSION_NOT_FOUND` mapping.
- A crash between step 3's COMMIT and any later step leaves a `pending/route_reserved` row that
  any future holder reclaims; a crash after step 6's COMMIT but before acknowledgement is healed
  by the terminal-row branch of step 2.
- Nothing user-controlled (title, raw refs, paths, exception text) is ever written to the
  catalog, logs, or error envelopes.
- A missing/stale handle or a handle with any purpose/domain other than `catalog_lookup` and the
  exact registered lookup domains fails before SQL lookup/mutation, with no commitment output.
- A retained route identity, retained generated path, or stale route generation never resolves as
  an attachment target. If a restore switch wins before start completion, the old allocation CAS
  changes zero rows and the start attempt retries against the active route or returns `BUNDLE_BUSY`.

## Invariants

- One `(installation_id, operation_id)` ⇒ at most one task/session/writer/lifecycle-event
  allocation, ever.
- The catalog is not a second product ledger: structural IDs, digests, states, commitments, and
  the bounded terminal envelope only.
- Every state/phase/lease combination satisfies the reproduced CHECK constraints; the adapter
  additionally enforces the monotonic phase order `route_reserved < bundle_ready <
  lifecycle_committed < result_published < terminal` in application code (SQLite cannot).
- Every lease decision applies the exact generation+expiry rule; stale generations are fenced
  regardless of wall clock.
- Acknowledgement happens only after the step-6 COMMIT.
- Start never changes `maintenance_operations` or `retained_task_routes`, and only restore changes
  an existing route's generation, path, or identity digest.
- Raw `K_lookup` bytes never cross into this adapter; all identity commitments use its one injected
  purpose-scoped `MacKeyHandle`.

## Tests

- `specs/tests/integration.md`: idempotency 5-row table for start; digest conflict; keyless create;
  attach-by-session vs attach-by-key agreement; scoped-key uniqueness race; create seeds matching
  route metadata; retained routes cannot attach; pending restore/migration and route-switch races
  fail with no stale completion.
- `specs/tests/subprocess.md`: kill matrix rows 1–11 applied to each phase boundary; reclaim at each
  recorded phase with valid/contradictory durable state; quarantine envelope stability.
- `specs/tests/conformance.md`: parity with `adapters/memory/start_catalog.md` on the same
  Hypothesis state-machine model.
- `specs/tests/conformance.md`: active/initializing/quarantined/absent route point reads are
  byte-equivalent to memory and reveal no attachment commitment.
- `specs/tests/integration.md`: raw-byte construction is unrepresentable; stale and wrong-purpose/
  domain handles fail before SQL and leave catalog bytes unchanged.
- Known-answer commitment vectors include the trailing `\x00` in every domain and compare the
  memory/SQLite result byte-for-byte.

## Open questions

None.

E-004 is the sole central lease-calibration gate; in-place route repair is deferred to v0.2.
