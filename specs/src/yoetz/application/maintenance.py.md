# src/yoetz/application/maintenance.py — maintenance use-case orchestration and consent gate

**Wave:** D | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`protocol/errors.md`, `ports/maintenance.py.md`, `ports/clock.md`, `ports/diagnostics.md` |
**Imported by:** CLI support-command composition and maintenance tests

## Purpose

Provide the application service used by `backup`, `restore`, and `migrate` support commands. It
separates user-facing preview/confirmation from storage mechanics, applies one exception/cancellation/
privacy policy, and ensures support commands cannot call SQLite recovery helpers directly.

This module is outside the six MCP workflow operations. It never registers extra MCP tools and does
not make Yoetz a general file-copy or deployment utility.

## Public surface

- `MaintenanceService(maintenance: MaintenancePort, clock: ClockPort,
  diagnostics: MaintenanceDiagnosticSink, recovery_secrets: RecoverySecretAcquirer,
  service_generation: int)` — generation-bound injected facade. `service_generation` is the exact
  positive generation of the daemon instance that owns the facade; callers never supply it. The
  recovery injection has only the typed acquisition operation owned by
  `ports/maintenance.py`; it is not a confidential client, secret-memory allocator, or generic
  human-control facade.
- Async methods:
  - `preview_backup(BackupRequest) -> BackupPlan`;
  - `backup(BackupRequest, Confirmation) -> BackupResult`;
  - `preview_restore(RestoreRequest) -> RestorePlan`;
  - `restore(RestoreRequest, Confirmation) -> RestoreResult`;
  - `preview_migration(MigrationRequest) -> MigrationPlan`;
  - `migrate(MigrationRequest, Confirmation) -> MigrationResult`;
  - `close() -> None` (idempotent service/reference release; it does not close shared runtime twice).
- `Confirmation(plan_digest: str, explicitly_accepted: bool, channel: ConfirmationChannel)` where
  channel is `interactive|noninteractive_flag|release_automation`.
- Strict boundary values `BackupRequest`, `RestoreRequest`, and `MigrationRequest`, whose
  `to_command()` methods convert to the corresponding port commands without retaining locations or
  secrets in repr/logs.

`Confirmation`, `ConfirmationChannel`, and the request names are shared application types
registered in `specs/INTERFACES.md`. CLI parsing/prompts remain in `cli/app.py`.

## Behavior

### Request normalization

For every request, validate IDs, mode/version/frontier, local location syntax, mutually exclusive
options, and size/length bounds before calling the port. Requests and previews are always
secret-free; they carry only the selected recovery mode. Never accept recovery input in a command-
line argument, environment, config file, JSON report, persistent request object, or preview. The
application does not resolve paths, inspect databases, or read manifests itself.

Preview calls are read-only. They return the port plan unchanged except for mapping internal
`MaintenanceError` to `PublicOperationError`/CLI support errors with bounded reason codes. The human
renderer may display the user-supplied source/destination because the user is already operating
locally, but diagnostics/evidence receive only a location commitment.

### Confirmation flow

Execution requires `explicitly_accepted=True` and exact `plan_digest`. Interactive CLI displays the
complete plan first and asks one clear question. Noninteractive automation supplies the plan digest
plus an explicit acceptance flag; a bare `--yes` without a plan digest is insufficient for restore/
migration. Backup to a new target also requires confirmation because it can expose encrypted data
and create a portable recovery artifact.

Only after exact confirmation, a portable plan constructs one secret-free
`RecoverySecretAcquisition` from the original request ID, the preview/confirmation's equal plan
digest, the still-current service generation, and `create|restore`, then invokes the injected
`RecoverySecretAcquirer`. It receives one service-internal `RecoverySecret` bound to exactly that
acquisition. No application constructor or method accepts raw secret `str`, `bytes`, `bytearray`,
`memoryview`, generic `SecretHandle`, secret callback, or confidential socket/client. Portable
backup/create requires two exact local entries and one
transmitted/captured handle; portable restore requires one entry and one handle. Machine-bound plans
never call the acquirer. The application then calls the matching
port execution with the original strict request, confirmed digest, and exactly that handle or
`None`. The port recomputes the plan; application never treats confirmation or possession of a
handle as bypass for changed target/frontier/version/key/route facts. Decline/stale preview prompts
for no secret; stale execution consumes/overwrites the old handle unused before requesting a new
preview.

### Backup orchestration

1. Preview and disclose task, pinned-current/expected frontier, mode, estimated object count/bytes,
   privacy-audit object/structural-row counts and snapshot digest, target-is-new rule, recovery
   classification, and limitations.
2. Obtain confirmation; only then does portable mode obtain a double-entered/one-send recovery
   secret bound to the confirmed request+plan digest and `create` through confidential ingress.
3. Call `MaintenancePort.backup` once; on cancellation/timeout report outcome unknown and instruct
   retry with the same request ID/digest.
4. Return the structural result and wording: a machine-bound result is never called portable;
   portability is conditioned on the recorded clean-profile drill evidence.

No event is appended merely because a backup was made; maintenance evidence lives in its manifest
and operation record. A future audit event would require a protocol addition, not an ad-hoc event.

### Restore orchestration

1. Preview source manifest/task/frontier/key classification, current route frontier, new-target
   identity, privacy-audit snapshot/root counts, required migration, retained prior route, and every
   warning. The preview states that restore invalidates all nonterminal disclosure authority and
   resolves any ambiguous `receipt_pending` attempt as outcome unknown; it never promises pending
   approvals will resume.
2. Reject restore into the currently active target or a caller-selected arbitrary bundle directory.
3. Require exact confirmation, then and only then collect one protected recovery entry bound to
   that request+plan digest and `restore` when applicable.
4. Call the port. Success is acknowledged only after catalog switch commits; response loss is
   resolved by retrying the same request.
5. Render explicit result: restored frontier, active-route digest, prior-route retained, limitations.

The service never offers “merge backup into current ledger.” Restore is replacement routing to a
new target that passed the complete structural, cryptographic, replay, and route-switch checks;
later work continues normally from that frontier.

### Migration orchestration

1. Preview exact from/to versions, contiguous migration IDs, frontier, backup mode and warnings.
2. Require confirmation. A target equal to current version is rejected as an invalid/no-migration
   request rather than fabricating backup/replay digests for a no-op; downgrade and skipped versions
   are also rejected.
3. Call the port and return only after backup, migration, replay, reopen and verification complete.
4. On `rollback_required`, surface the backup manifest digest and direct to the public rollback
   runbook. Never automatically apply reverse SQL or copy the backup over the active database.

### Error, cancellation, and diagnostics

Expected maintenance reasons map to bounded public/support error codes: conflict/stale plan,
confirmation/invalid request, busy/pending, storage unsafe/corrupt, key/recovery failure, migration
required/unsupported, and internal fenced error. Cancellation is re-raised; if execution may have
crossed a durable terminal boundary, the CLI states outcome unknown and same-request retry resolves.

Diagnostics use only the separate `MaintenanceDiagnosticSink.record_maintenance` contract. A
frozen `MaintenanceDiagnostic` records operation kind, preview/execute phase, success/failure/
cancellation, request/task ID, plan/result digests, migration versions, count, duration, bounded
reason, and observed time. It excludes locations, manifest contents, object IDs, key locator/
secret, paths, SQL, and exception text. Maintenance diagnostics never enter `DiagnosticsPort.record`
and never participate in `evaluate_startup_gate`.

## Errors and edge cases

- Non-TTY invocation needing confirmation fails immediately; it never reads implicitly or hangs.
- Preview staleness is normal: execution returns a new-preview requirement without mutation.
- After a confirmed portable plan, absent/malformed recovery input prevents execution; wrong secret
  is a port outcome. Preview and decline never inspect recovery material.
- Closing the CLI/service during shielded switch does not cancel the underlying terminal transaction.
- Findings/check/receipt state is neither silently recomputed nor strengthened by maintenance.
- Missing/dangling privacy-audit roots or a changed root generation make the plan stale/corrupt;
  application cannot waive, omit, or repair them through confirmation.

## Invariants

1. Every mutating maintenance action has a displayed/digest-bound confirmed plan.
2. Application imports no SQLite/APSW/filesystem/keyring implementation type.
3. Secrets and locations do not cross diagnostics or durable structural results.
4. Same request identity resolves timeout/response ambiguity.
5. Maintenance remains a support surface, not a seventh MCP workflow operation.
6. Portable recovery is ordered preview -> confirmation -> confidential one-shot secret -> execute;
   no earlier phase can capture a secret.
7. Portable create is double-entry/one-send; portable restore is single-entry/one-send, and neither
   confirmation shape can be substituted for the other.
8. Maintenance plans expose bounded privacy-root counts/digests and cannot authorize dropping
   catalog-held task-bundle objects.
9. The recovery-secret injection can mint only an opaque one-use handle for one exact confirmed
   request/plan/service-generation/operation binding; it has no raw-secret or general
   human-control surface.

## Tests

- `specs/tests/unit.md`: request conversion, confirmation channels, no-TTY failure, exception/privacy
  mapping, cancellation and same-ID retry guidance.
- `specs/tests/integration.md`: service-to-port sequencing for backup/restore/migrate; plan drift and
  route/frontier changes; protected secret handling.
- `specs/tests/subprocess.md`: CLI preview/decline/confirm, response loss, exact stdout/stderr/exit.
- `specs/tests/packaging.md`: clean installed upgrade/rollback and portable recovery drill.

## Open questions

None.
