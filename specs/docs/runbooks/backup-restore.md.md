# docs/runbooks/backup-restore.md — safe bundle backup and verified restore procedure

**Wave:** C/F | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):** maintenance
port/application/adapter specs, key and packaging specs | **Imported by:** CLI help, migration/key
runbooks and operator support

## Purpose

Specify the public operator procedure for creating a frontier-pinned encrypted bundle backup and
restoring it into a new target that passed the complete structural, cryptographic, replay, and
route-switch checks before an atomic catalog switch. It must prevent the
common unsafe shortcuts: copying a live database/WAL, overwriting the active bundle, calling a
machine-bound backup portable, or treating a manifest checksum as a signature.

## Public surface

Future headings:

1. Scope, data sensitivity and guarantees
2. Choose machine-bound or portable recovery
3. Preconditions and capacity
4. Preview and create a backup
5. Verify/retain a backup
6. Restore preview
7. Restore into a new target
8. Post-restore verification
9. Failure/cancellation/outcome-unknown handling
10. What never to do
11. Evidence/support checklist

Examples use support CLI `--input`/`--json`/preview/confirm-plan flow and synthetic IDs/paths. No
example places a recovery secret in argv, environment, JSON or shell history.

## Behavior

### Guarantees and limitations

State: backup is an encrypted snapshot of one task at one `Frontier`, plus exactly referenced
ciphertext objects and structural manifest. Online Backup API gives database consistency; object pin
protects set until finalization. It does not capture uncommitted work, prove correctness, sign data,
or guarantee portability without correct recovery mode/key and clean-profile drill.

Backups are sensitive even when encrypted because structural metadata, sizes, IDs/times/versions and
ciphertext remain. Store owner-only and outside repositories, cloud-sync/network/shared temp unless a
separately supported policy exists (v0.1 rejects unsafe targets).

### Mode choice

Table:

- `machine_bound`: manifest holds nonsecret key locator/fingerprint; restore only where original
  verified backend entry remains available. Copying backup alone to another machine is insufficient.
- `portable_recovery`: separate authenticated recovery artifact wraps bundle key with a user-held
  recovery secret. Keep backup/artifact/secret according to custody policy; portability claim only
  after clean-profile drill. Wrong/tampered secret/artifact is distinct failure.

Warn neither mode protects against compromised active account/root/memory and deletion is not
forensic erasure.

### Backup procedure

1. Install/verify supported package/platform; run `version --json`; identify exact session/task and
   stable new destination with capacity/owner-only policy. Ensure no competing maintenance.
2. Prepare strict request file with request ID/session/destination/mode/optional expected frontier;
   no secret. For portable mode, enter the secret only through the foreground no-echo confidential
   helper after reviewing the exact plan; v0.1 has no inherited-descriptor or password-FD channel.
3. Run backup preview JSON. Review task, actual frontier/head, mode, target-is-new, object count/bytes,
   versions, limitations and plan digest. If work/frontier/target changes, preview again.
4. Confirm exact plan digest explicitly. Never use new request ID after timeout; same ID resolves.
5. Wait for terminal result. Success records manifest/set/database/recovery digests and frontier. Do
   not rename `.incomplete-*` or treat it as backup.
6. Run verification command/read-only preview against finalized set; compare result/manifest digests.
   Store checksums/evidence separate as policy requires.

Explain internally but plainly: current process pins F, snapshots SQLite through online Backup API,
copies verified ciphertext, writes recovery artifact if requested, writes manifest last and atomically
publishes absent destination. It never copies live `ledger.sqlite3`, `-wal` or `-shm`.

### Restore procedure

1. Preserve current installation/bundle; do not move/delete/edit it. Verify package/platform and
   source backup ownership; obtain recovery secret safely if needed.
2. Run restore preview. Review source manifest/task/frontier/database/object/key/version integrity,
   current active route/frontier, required supported migration, generated new-target identity, retained
   prior route, warnings and plan digest. Task mismatch/newer unsupported/tamper/missing member stops.
3. Confirm exact plan. Restore creates a generated quarantined target, uses SQLite Backup API into a
   new database, copies/authenticates ciphertext, resolves key, verifies chains/integrity, full replay
   and normal reopen. It never merges histories or overwrites active files.
4. Catalog switches route in one generation-fenced transaction only after verification. Response
   loss after switch is outcome unknown: rerun same request/status; never start another restore.
5. Verify active route structurally via status, expected restored frontier/head, replay/current check
   and a deterministic receipt appropriate to captured data. Keep prior route/backup until a reviewed
   retention decision and at least one successful reopen/use cycle.

### Failures and stop rules

For each bounded reason give action: plan stale → preview again; busy → stop other maintenance/retry
same request; target exists/unsafe → choose new safe destination; key locked/missing/wrong → resolve
backend/secret without resetting; manifest/object/database/replay mismatch → stop, preserve all;
route changed → preview current state; migration needed → use supported candidate or stop.

Cancellation/timeout never proves failure. Inspect structural result/status and retry exact request.
No public log/support bundle should include paths, manifests with user extension, DB/object bytes,
secret/key, payload or traceback.

### Prohibited actions

Explicit red box list: no `cp`/Finder/rsync of live database/WAL/SHM; no overwrite of active bundle;
no delete/move prior route during restore; no direct SQLite/manifest/version edit; no disable fsync/
integrity/key checks; no secret in shell/env/file; no calling machine-bound portable; no downgrade
binary as rollback; no manual catalog path change.

## Errors and edge cases

- Runbook examples must match implemented CLI help and support non-TTY fail-fast confirmation.
- Paths are synthetic placeholders; do not imply repository/cloud folder is acceptable.
- If no verified backup exists, the document cannot promise recovery from corrupt/missing key data.
- Backup may be valid yet not current; state exact frontier and work performed afterward.
- Restore of imported/unknown/redacted history retains coverage gaps.

## Invariants

1. Backup uses online snapshot plus frontier pin and manifest-last finalization.
2. Restore uses a new quarantined target and verified catalog switch.
3. Current/prior source remains untouched until success and retained afterward.
4. Portability/key/checksum wording remains honest.
5. Every ambiguous outcome uses same-request lookup/retry.

## Tests

- Execute every command example in installed CLI subprocess tests with synthetic bundle/modes.
- Fault tests kill during snapshot/object/manifest and route switch; runbook action remains correct.
- Clean-profile portable and same-profile machine-bound drills validate mode table.
- Docs lint rejects live-copy commands, overwrite/downgrade advice, secrets in examples and missing
  preview/confirmation/verification steps.

## Open questions

None.
