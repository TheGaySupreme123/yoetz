# src/yoetz/adapters/sqlite/maintenance.py — SQLite bundle backup, restore, and migration

**Wave:** C | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`ports/maintenance.py.md`, `adapters/sqlite/connection.md`, `adapters/sqlite/recovery.md`,
`adapters/sqlite/migrations.md`, `adapters/sqlite/start_catalog.md`,
`adapters/sqlite/repository.md`, `adapters/objects/encrypted_files.md`, `ports/keys.md`,
`adapters/privacy/catalog.md`, `config/paths.md`, `protocol/canonical.md`,
`observability/privacy.md` | **Imported by:**
runtime composition and SQLite maintenance integration/fault tests

## Purpose

Implement `MaintenancePort` for the durable SQLite/object bundle. This module owns maintenance
generation acquisition, frontier pins, online database snapshots, ciphertext inventory copy,
manifest finalization, quarantine verification, migration composition, and the only atomic catalog
route switch used by restore.

It does not own ledger meaning, key cryptography, SQL DDL, public prompting, or arbitrary file copy.
`recovery.py` classifies/reopens a bundle; `migrations.py` owns frozen DDL; this file orders them into
operator-safe procedures.

## Public surface

- `class SqliteMaintenance(MaintenancePort)` implementing all six preview/execute methods.
- `acquire_maintenance(task_id, kind, request_id) -> MaintenanceHandle` — generation-fenced,
  non-serializable authority used by migration/recovery/route-switch internals.
- `create_frontier_pin(handle, frontier, expires_at) -> MaintenancePin` and
  `release_frontier_pin(handle, pin) -> None`.
- `build_backup_manifest(...) -> BackupManifest` — canonical structural builder.
- `verify_backup_set(source, expected_task_id=None) -> VerifiedBackupSet` — read-only complete proof.
- `verify_restored_target(staged, manifest, keys, handle) -> RestoredTargetEvidence`.
- Internal records `MaintenanceHandle`, `VerifiedBackupSet`, and `RestoredTargetEvidence` are opaque
  adapter values; only `MaintenanceHandle` crosses to `migrations.py` and should be registered as a
  shared internal type.

## Behavior

### Authority and operation rows

Resolve exact task through catalog and validate locations/path safety before acquisition. Backup
acquires a read-compatible maintenance lease plus pin under the current authoritative writer; it
does not open a second writer. Restore/migration acquire the exclusive `maintenance` generation,
stop new operations, wait boundedly for admitted shielded commits, invalidate older leases, and own
the single writer/checkpointer. A live owner that cannot hand off returns `maintenance_busy`.

Persist a catalog-scoped structural maintenance operation keyed by `(installation_id, request_id)`
with request digest, kind, phase, generation/lease, safe plan/result bytes/digests, staging route
identity and terminal code. This table must be introduced by the owning migration before the adapter
exists; it contains no locations or user content. Phases are:

- backup: `reserved → pinned → database_ready → objects_ready → manifest_ready → terminal`;
- restore: `reserved → source_verified → target_ready → target_verified → route_switched → terminal`;
- migration: `reserved → backup_ready → schema_applied → replay_verified → terminal`.

On retry, phases are lower bounds: revalidate every durable artifact before advancing. Same request/
digest terminal returns stored result; different digest conflicts. A contradictory phase/artifact is
quarantined, never overwritten.

### Backup preview and execution

Preview opens catalog/bundle read-only, validates exact session/task and destination syntax/newness,
captures head frontier/version/key mode plus the current privacy-root generation/digest, counts the
union of task-database/importer/privacy-root ciphertext, and then builds the plan digest. It creates
no target/pin.

Execution order:

1. Recompute confirmed plan and reserve/reclaim operation under current generation.
2. Create a `maintenance_pins` row at exact frontier `F` and bind the exact
   `PrivacyAuditObjectRoots` generation/digest in one short catalog `BEGIN IMMEDIATE`; pin ID and
   expiry are generation-bound. Snapshot the sorted set of object IDs referenced by events through
   `F` plus required operation/result/receipt/semantic/importer objects whose durable state belongs
   to `F`, union every privacy-catalog ObjectRef, and serialize the matching canonical
   `privacy-audit-snapshot.json` structural sidecar. A privacy ref needs no task-ledger inventory row.
3. Create an owner-only no-follow staging directory beside the absent destination, with a fixed
   structural marker. Destination/staging must pass export-target safety policy; never place backup
   in bundle root, repository, shared temp, sync/network/unsafe location.
4. Open a new empty destination database and run APSW's destination-side online Backup API against
   the source connection. Step in bounded pages with cancellation/resource checks, then close and
   fsync. Never copy live DB/WAL/SHM bytes and never include WAL/SHM in backup.
5. Reopen the snapshot read-only with approved flags; verify application/user/schema IDs, task,
   exact frontier/head through `F`, `quick_check`, canonical event chains/index agreement, and no
   event beyond the intended snapshot contract. Compute raw database SHA-256 after close.
6. For each sorted referenced object in that complete union, open source/destination with no-follow, stream ciphertext in
   bounded chunks while computing digest/size, fsync file, rename from temp, fsync shard directory.
   Compare source inventory before/after and manifest digest; do not decrypt/copy by user filename.
7. In portable mode, ask `KeyStorePort.wrap_recovery` for the separate versioned artifact, write/
   fsync it without logging bytes, and record only its digest/classification. Machine-bound mode
   records nonsecret locator fingerprint only.
8. Write/fsync the canonical privacy-audit sidecar, then build the canonical manifest last with its
   digest/count/root generation. Rescan structural plaintext, write/fsync it and staging root.
   Verify the whole set from its own manifest.
9. Atomically rename staging to the still-absent destination and fsync its parent. Mark operation
   terminal, then release pin. Return only after commit/fsync succeeds.

If execution fails, keep an owner-only `.incomplete-<request-id>` staging directory or remove it
only after proving it is unreferenced; never present it as a backup. Pin is released when no future
cleanup needs its object set, or expires under a recovery-audited rule. Ordinary object GC respects
all live pins.

### Restore verification and new target

Preview/`verify_backup_set` safely reads manifest first with caps and canonical validation, recomputes
self/set/database/object/recovery digests, verifies source path no-follow/owner policy, task/version/
mode, privacy sidecar/root digest, and classifies keys. It never opens the current target for mutation.

Execution:

1. Acquire exclusive maintenance and reverify source/current route/confirmed plan.
2. Allocate a new generated relative route beneath `tasks/restore-<request-derived-id>/`; it must be
   absent and not caller-selected. Create owner-only quarantined staging with durable marker.
3. Create a new destination SQLite file and restore from the immutable backup database through the
   supported SQLite Backup API. Do not copy database bytes over the active target. Copy ciphertext
   objects from the backup with digest/size/no-follow/fsync/atomic rename checks. On a
   same-installation route move, also query the current authoritative privacy-root set under the
   exclusive maintenance generation and copy/verify any live refs newer than the backup from the old
   active route into the new target.
4. Resolve machine-bound key only when the recorded key slot is available; portable mode unwraps
   into the verified backend/new target through `KeyStorePort`. Wrong/tampered/mismatched recovery
   fails and erases only transient handles/new target, never current key.
5. Open the new target in quarantine. Verify application/schema/build identities, database integrity,
   every canonical event/writer/global chain through manifest frontier, object reference/inventory/
   authentication, every privacy sidecar/current-catalog root without requiring ledger inventory,
   and deterministic full replay from empty projection. If a supported migration is
   required, it runs here under the same confirmed plan and never alters manifest canonical bytes.
6. Build `RestoredTargetEvidence` binding task/frontier/head/replay/object/key/version/new route
   identity/current generation. Close/reopen the new target through normal recovery/startup and
   reproduce the evidence.
7. Reconcile privacy audit before activation. Same-installation move preserves existing catalog rows,
   invalidates nonterminal authority under the new owner generation, repairs `receipt_pending`, and
   CASes the exact current privacy-root generation/digest. Clean-profile restore imports the verified
   sidecar with collision rejection; terminal evidence is preserved, pending/approved/authorized
   state expires `authorization_stale`, `decision_receipt_pending` completes its frozen decision,
   and `receipt_pending` becomes `transport_failed/outcome_unknown`. Then, in one catalog
   `BEGIN IMMEDIATE`, CAS on task ID, old route identity where present, active session/frontier
   expectation, owner generation, and privacy-root generation/digest; change `bundle_relpath` to the verified new route, retain the
   old route as a quarantined rollback entry/structural recovery record, store terminal result,
   commit. Only after COMMIT is restore acknowledged.
8. Re-resolve through catalog and open the active new route. Never delete the prior target in this
   operation.

Kill before step 7 leaves current route active and resumable verified staging. Kill after commit is
outcome-unknown; retry reads terminal operation/catalog route and returns the same result. Two
restore attempts cannot switch the same expected route concurrently.

### Migration

Preview verifies current schema/manifest/frontier, requested higher supported target and contiguous
registry. Execution acquires exclusive maintenance, invokes the same backup path first (a completed
manifest is persisted in operation), then calls `run_migrations` with `MaintenanceHandle`.

Before and after record count, total canonical-entry bytes, sampled/full chain digests as bounded by
release policy, head frontier/digest and object inventory. Each step updates `user_version` and
metadata atomically. Projection changes create a new generation and full replay; old projection
remains until verification. Close/reopen normal gate and require identical canonical truth plus
reference projection/receipt vectors. Only then terminal success.

On transactional failure, verify old schema/state remains and reopen it. On ambiguous/nontransactional
or post-validation failure, quarantine writes and return `rollback_required` with backup digest; the
operator follows verified new-target restore. Never execute reverse SQL or overwrite DB from a file.

## Errors and edge cases

- Destination/source symlink, hardlink, path escape, collision, unsafe permissions/filesystem, or
  changed stat during read is `target_unsafe`/`source_invalid`.
- Pin expiry/generation loss during backup aborts before manifest finalization; a backup cannot outlive
  its protected object set invisibly.
- `SQLITE_BUSY/FULL/IOERR`, failed fsync/rename/commit/backup, corrupt/tampered member, missing object,
  dangling privacy ref, privacy sidecar/root drift, key failure, replay mismatch, or route CAS
  conflict leaves no acknowledged success.
- Backup manifests and operation rows are structural plaintext and pass the privacy scanner. User
  location, key/passphrase, object plaintext, SQL and raw exception are excluded.
- A current route is never renamed, deleted, truncated, or overwritten by restore/migration rollback.

## Invariants

1. Live SQLite databases are snapshotted/restored through the online Backup API, never ad-hoc copy.
2. A backup manifest names exactly the pinned frontier/database/ciphertext set.
3. Restore always builds and verifies a new quarantined target before one CAS catalog switch.
4. Migration has a completed backup and never rewrites canonical event bytes.
5. Old route/data remain preserved through restore/rollback decisions.
6. Every write/checkpoint is owned by the current generation.
7. A backup object set includes every catalog privacy root at the pinned root generation even without
   ledger inventory; restore cannot switch routes while one is missing or the root set changes.
8. Clean restore preserves terminal audit evidence but never restores live disclosure authority.

## Tests

- `specs/tests/integration.md`: exact backup/manifest/object/pin path; machine/portable modes; new-
  target restore, privacy sidecar/root union, full replay, route/root CAS, retained prior route,
  clean-profile pending-state invalidation; migration success/failure.
- `specs/tests/subprocess.md`: kill at database/object/manifest phases, migration and route-switch
  point 14, post-commit response loss and same-ID replay.
- `specs/tests/property.md`: generated operation phase/retry/plan-stale/route-race model.
- `specs/tests/packaging.md`: every old release upgrade/rollback, clean-profile portable restore and
  SQLite platform identity.
- `specs/tests/conformance.md`: structural results/manifests match a reference maintenance model.

## Open questions

None.
