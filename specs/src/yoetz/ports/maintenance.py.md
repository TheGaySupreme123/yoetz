# src/yoetz/ports/maintenance.py — backup, restore, and migration authority boundary

**Wave:** C/D | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`protocol/errors.md`, `protocol/ids.md`, `domain/values.md` (`Frontier`), `ports/keys.md`,
`ports/objects.md`, `ports/privacy.md`,
`version.md` | **Imported by:** `application/maintenance.md`,
`adapters/sqlite/maintenance.md`, CLI composition and maintenance tests

## Purpose

Define the only application-facing authority for durable maintenance. Backup, restore, and schema
migration are not ordinary ledger writes: they pin a frontier, use bounded maintenance ownership,
move encrypted data between explicitly selected locations, and may change the catalog's active route.
This port keeps SQLite, paths, online-backup handles, key backends, and catalog transactions out of
the application layer while making every safety proof and terminal result testable.

The port does not expose a generic copy/export API. Callers can request only the three named
procedures and cannot skip planning, verification, generation fencing, or the no-overwrite rules.

## Public surface

### Protocol

`class MaintenancePort(Protocol)` exposes six async methods:

- `preview_backup(command: BackupCommand) -> BackupPlan`;
- `backup(command: BackupCommand, *, confirmed_plan_digest: str,
  recovery_secret: RecoverySecret | None) -> BackupResult`;
- `preview_restore(command: RestoreCommand) -> RestorePlan`;
- `restore(command: RestoreCommand, *, confirmed_plan_digest: str,
  recovery_secret: RecoverySecret | None) -> RestoreResult`;
- `preview_migration(command: MigrationCommand) -> MigrationPlan`;
- `migrate(command: MigrationCommand, *, confirmed_plan_digest: str) -> MigrationResult`.

Preview methods are read-only and never acquire a long-lived maintenance generation. Execution
methods recompute the plan under authority and require an exact digest match. Every method returns
Yoetz values and raises typed bounded failures; no SQLite/APSW/`Path` type crosses this boundary.

### Shared values

- `MaintenanceLocation(value: str)` — opaque, strict local-file location supplied by the support
  boundary. It is non-serializable in diagnostics and has a redacted representation. The adapter
  validates/normalizes it; no code infers it from cwd.
- `MaintenanceKind` — `backup|restore|migration`.
- `BackupMode` — `machine_bound|portable_recovery`.
- `MaintenanceReason` — closed reasons:
  `plan_stale`, `confirmation_required`, `target_exists`, `target_unsafe`, `source_invalid`,
  `manifest_invalid`, `manifest_tampered`, `backup_incomplete`, `key_unavailable`,
  `recovery_secret_wrong`, `recovery_artifact_invalid`, `object_missing`, `object_tampered`,
  `database_invalid`, `replay_mismatch`, `catalog_route_changed`, `maintenance_busy`,
  `migration_unsupported`, `migration_failed`, `rollback_required`, `generation_lost`.
- `MaintenanceError(reason: MaintenanceReason, retryable: bool, safe_details: Mapping[str,
  JsonValue])` — contains only IDs, digests, versions, counts, and bounded enums.
- `MaintenancePin(pin_id, task_id, frontier, owner_generation, privacy_root_generation,
  privacy_root_digest, expires_at)` — structural proof that a backup/rebuild frontier and exact
  catalog privacy root set are protected from object collection and generation removal.
- `BackupObjectEntry(object_id, kind, envelope_digest, envelope_size)` — one referenced
  ciphertext member; no plaintext-derived name or metadata.
- `BackupManifest` — canonical structural manifest described below.
- `PrivacyAuditBackupSnapshot` — canonical path-free structural sidecar containing origin
  installation/task IDs, catalog/audit-store versions, privacy-root generation/digest, sorted
  structural audit rows/terminal receipts and their `privacy_audit` ObjectRefs; no prepared bytes.
- `BackupCommand(request_id, session_id, destination, mode, expected_frontier)` — structural and
  secret-free in both modes.
- `BackupPlan(request_digest, task_id, frontier, mode, destination_commitment, object_count,
  estimated_ciphertext_bytes, privacy_audit_object_count, privacy_audit_snapshot_digest,
  version_manifest, warnings, plan_digest)`.
- `BackupResult(request_id, task_id, frontier, mode, backup_manifest_digest, backup_set_digest,
  object_count, privacy_audit_object_count, privacy_audit_snapshot_digest, database_digest,
  recovery_artifact_digest, completed_at)`.
- `RestoreCommand(request_id, source, destination_policy, recovery_mode, expected_task_id,
  expected_active_frontier)`.
- `RestorePlan(request_digest, source_manifest_digest, task_id, backup_frontier,
  active_frontier, new_route_identity_digest, key_classification, migration_needed,
  warnings, plan_digest)`.
- `RestoreResult(request_id, task_id, restored_frontier, prior_route_identity_digest,
  active_route_identity_digest, backup_manifest_digest, replay_digest, completed_at)`.
- `MigrationCommand(request_id, session_id, target_storage_version, expected_frontier)`.
- `MigrationPlan(request_digest, task_id, from_version, to_version, current_frontier,
  required_migration_ids, preflight_backup_mode, warnings, plan_digest)`.
- `MigrationResult(request_id, task_id, from_version, to_version, backup_manifest_digest,
  frontier_before, frontier_after, replay_digest, completed_at)`.
- `RecoverySecret` — opaque one-shot `SecretHandle(purpose=portable_recovery)` staged only after
  exact plan confirmation. It is service-internal, constant-redacted, bound to request ID + plan
  digest + service generation, supplied as the separate execution-only keyword above, and never
  participates in command repr/serialization/request digest/plan output.

These cross-module names are registered in `specs/INTERFACES.md`. The records are support-layer
interfaces, not public six-operation wire models.

## Behavior

### Common planning and idempotency

Commands are strict/frozen, secret-free, and identify one request. Request digest includes kind,
validated task/session identity, normalized location commitment, backup/migration/recovery mode,
version expectations, and expected frontier; it excludes secret bytes/handles and adapter-assigned
times/pin/route values. Reusing a
request ID with another digest is `IDEMPOTENCY_CONFLICT`; reusing it after terminal success returns
the stored structural result. A pending execution owned by a current generation is
`OPERATION_PENDING`; stale ownership is reclaimed only through the implementation's generation CAS.

Each preview:

1. validates command shape and location syntax without creating anything;
2. resolves the exact catalog route, never fuzzy path/workspace matching;
3. performs read-only source/target/version/key classification;
4. captures the current structural facts and warnings;
5. returns a canonical `plan_digest` over every fact that execution must recheck.

Preview never requests, receives, derives, validates, or stages `RecoverySecret`, even when the
selected plan is portable. After the exact plan is rendered and locally confirmed, confidential
ingress may mint one recovery handle bound to `(request_id, plan_digest, service_generation,
operation)`. Portable backup/create requires helper-side double entry and one send; portable
restore requires one entry and one send. The port receives exactly one handle in either case.
Execution requires it exactly when the recomputed confirmed plan says portable, forbids it for
machine-bound mode, consumes it once inside the recovery wrapper, and overwrites it on every
success/failure/cancellation/stale-plan path. A changed plan can never reuse the old handle.

Execution repeats validation after acquiring least maintenance authority. A changed route, frontier,
manifest, target existence, key classification, version, or source digest produces `plan_stale` and
no mutation. It consumes/discards any already staged recovery handle rather than asking for a new
secret against unpreviewed facts. `confirmed_plan_digest` is proof the caller approved that exact
preview, not a bearer token and not permission to weaken checks.

### `BackupManifest`

Canonical JSON schema `yoetz.backup-manifest/1` includes: manifest/backup format, request/task IDs,
pinned `Frontier`, database file logical name/size/SHA-256, ASCII-sorted `BackupObjectEntry` values,
object-set digest, protocol/storage/migration/projection/policy/object/encryption/resource/SQLite
identities, mode, key fingerprint/locator classification (never raw key), optional portable recovery
artifact logical name/digest/KDF policy, created/completed times supplied by `ClockPort`, and manifest
self-digest. It contains no title, workspace ref, payload, path, filename from a user object, prompt,
provider content, raw key, passphrase, or exception.

The manifest also requires logical member `privacy-audit-snapshot.json`, its size/SHA-256,
`privacy_root_generation`, `privacy_root_digest`, audit-store version, structural row/receipt count,
and privacy-audit object count. The sidecar is canonical structural plaintext; content remains only
inside the referenced encrypted bundle objects. It is not a task-ledger snapshot and does not add
privacy objects to task-ledger inventory.

The manifest digest is identity, not authenticity/signature. `portable_recovery` may be claimed only
when a matching recovery artifact is included and the release process has a clean-profile restore
drill for that format; otherwise the result is `machine_bound`.

### Backup contract

Execution must pin a concrete source frontier plus exact catalog privacy-root generation/digest, use
SQLite's online Backup API to produce the database snapshot, emit the matching canonical privacy
audit sidecar, and copy the union of ciphertext objects referenced at/before the pin and every
sidecar privacy `ObjectRef` while validating inventory/digests. It finalizes a canonical manifest last,
fsyncs files/directories, and publishes the backup directory
atomically to a previously absent target. It releases/expires the pin only after final success or a
recorded failed cleanup path. Copying a live `ledger.sqlite3`, WAL, or SHM with a generic file copy is
never a valid implementation.

### Restore contract

Restore never overwrites the active bundle. It verifies source manifest, database, ciphertext set,
privacy-audit sidecar/root set, key/recovery material, versions and requested task identity before creating a new owner-only staging
target. It restores the database through the supported SQLite backup path, materializes verified
ciphertext, opens the new target in quarantine, runs supported migration if required/authorized,
replays from canonical events, validates chains/projections/receipt vectors/object decryption samples,
and only then performs one generation-fenced catalog route switch. The prior route is retained as a
rollback/quarantine target. Any failure leaves the active route unchanged.

For a same-installation route move, the existing privacy catalog remains authoritative. Under the
exclusive maintenance generation, restore obtains its current `PrivacyAuditObjectRoots`, copies and
verifies every live ref into the new route (including refs newer than the backup sidecar), and binds
the route-switch CAS to that root generation/digest. ObjectRefs remain path-free/unchanged. A root
change aborts the switch and leaves the old route active. Before the switch, the new owner/service
generation invalidates `reserved|awaiting_human|approved|authorized` state as
`approval_expired/authorization_stale`, completes any frozen `decision_receipt_pending`, and repairs
`receipt_pending` from durable attempt evidence or, when no stronger evidence exists, as
`transport_failed/outcome_unknown`; route movement never preserves dispatch authority.

For clean-profile restore, the verified sidecar imports its conflict-free historical rows before
route activation. Terminal rows/receipts remain byte-exact historical evidence. Nonterminal
`reserved|awaiting_human|approved|authorized` rows are invalidated to terminal
`approval_expired/authorization_stale`; `decision_receipt_pending` completes its already frozen
terminal decision; `receipt_pending` completes `transport_failed/outcome_unknown` with its stored
attempt identity/commitment. No restored approval or authorization can dispatch. The imported
content refs become roots in the new catalog transaction, and route activation occurs only after all
referenced ciphertext verifies in the new bundle.

### Migration contract

Migration requires an exclusive maintenance generation and a completed verified backup first. It
runs only a contiguous registered path, never rewrites canonical event bytes/digests, advances
`user_version` and schema metadata together, creates/replays a new projection generation when needed,
and reopens through the full safety gate. Success requires unchanged frontier/head/canonical-byte
inventory plus reference-equal replay. Unknown newer or missing migration path fails closed. Failure
returns a stable `rollback_required`/backup digest when safe recovery cannot be proven; it never
performs an ad-hoc reverse DDL or file overwrite.

## Errors and edge cases

- Cancellation before authority/target creation has no effect. During online backup/copy it leaves
  only an incomplete staging target; during final catalog switch the smallest transaction resolves
  before cancellation and request lookup determines outcome.
- Destination already exists even if empty/identical: `target_exists`; v0.1 never overwrites a
  backup or restore target.
- Missing/locked machine-bound key is distinct from wrong/tampered portable recovery material.
- Missing recovery material is checked only after portable plan confirmation; preview/decline never
  produce `recovery_secret_wrong` or touch confidential ingress.
- Insufficient disk, I/O/full/read-only, pin expiry, generation loss, active route change, manifest
  mismatch, privacy-root/sidecar drift, dangling privacy catalog ref, or object/database corruption
  cannot produce success.
- Location values, secrets, SQL, object payloads, and raw exceptions never enter errors/results/logs.

## Invariants

1. No backup succeeds without one pinned frontier and online SQLite snapshot.
2. No restore mutates the active route before the new target fully verifies and replays.
3. No migration begins without a verified backup and exclusive generation.
4. Canonical event bytes and chain digests are never rewritten by maintenance.
5. Preview confirmation cannot waive runtime safety checks or survive material plan drift.
6. Machine-bound and portable recovery claims are distinct and evidence-backed.
7. Portable recovery order is always secret-free preview, exact confirmation, bound one-shot
   ingress, then execution; a stale plan consumes the staged handle without use.
8. The recovery binding distinguishes `create` from `restore`; create confirmation is double-entry
   while restore is single-entry, with one transmitted handle for each.
9. Backup/restore roots are the union of task DB/importer, privacy catalog, and active pins; privacy
   audit reachability never depends on a task-ledger inventory row.
10. Route move preserves every current catalog-rooted privacy object, and clean restore never revives
    disclosure authority from backup.

## Tests

- `specs/tests/unit.md`: command/plan/result canonicalization, secret-redacted repr, plan drift,
  manifest schema/digest and error mappings.
- `specs/tests/integration.md`: frontier pin, online backup, object set, new-target restore/replay,
  privacy-audit sidecar/root union, atomic route switch, clean-profile nonterminal invalidation,
  migration backup-first and version gates.
- `specs/tests/subprocess.md`: kill points during backup finalization and restore/migration switch;
  same-request retry and unchanged-route failures.
- `specs/tests/packaging.md`: old-version upgrade/rollback, resource/migration identity and clean-
  profile portable restore.

## Open questions

None.

E-004, E-006, and R-001 are the sole central maintenance/recovery gates.
