# docs/runbooks/migration-rollback.md — forward migration and verified rollback-by-restore

**Wave:** C/F | **ADRs:** ADR-001, ADR-003, ADR-007 | **Imports (spec-tree):** maintenance/migration/
recovery specs, backup/restore and compatibility docs | **Imported by:** migrate CLI help and release
upgrade guidance

## Purpose

Document a safe schema migration and recovery procedure. “Rollback” means return routing to a fully
verified restored pre-migration backup in a new target where compatibility is proven—not reverse SQL,
binary downgrade, or copying a database over the active bundle.

## Public surface

Future sections:

1. Scope and compatibility decision
2. Preconditions
3. Preview a migration
4. Backup-first execution
5. Verify success
6. Interrupted/failed migration classification
7. Rollback by verified restore
8. Package downgrade warning
9. Prohibited actions
10. Evidence/exit criteria

## Behavior

### Preconditions and decision

Require exact current/candidate package, platform/SQLite/resource/migration identities and public
compatibility matrix; adequate local disk; current supported key/object state; no quarantine or
other owner/maintenance; known task/session/current frontier. Read release notes/limitations and
confirm contiguous immutable migration path. Unknown newer schema, missing migration, downgrade,
unsafe filesystem or no ability to create verified backup stops.

Migration may change physical schema/projection generation but cannot rewrite canonical event bytes,
digests or meaning. Old binaries may become unable to write/read after new events/schema; package
downgrade is never presumed rollback.

### Preview and execute

1. Prepare stable migration request ID/session/target version/expected frontier.
2. Run JSON preview; review from/to versions, ordered migration IDs, current frontier/head, backup
   mode/destination policy, capacity and warnings/plan digest.
3. Stop or re-preview on any mismatch. Explicitly confirm exact plan; noninteractive use requires
   plan digest plus acceptance.
4. Runtime acquires exclusive maintenance generation and creates/verifies backup through the full
   frontier-pin/online-Backup/object-manifest procedure. No schema step begins first.
5. Runner applies each forward migration transactionally where supported, advances `user_version`
   and metadata together, preserves canonical bytes, creates/replays new projection generation, and
   reopens through normal safety gate.
6. Same request ID resolves timeout/response loss; do not launch another migration or edit metadata.

### Success verification

Compare result: from/to, backup manifest digest, frontier before/after (no migration event means same
canonical frontier), canonical count/byte/head samples/full chain policy, object inventory, new schema,
projection replay digest, current status/check/receipt on synthetic/known task state. Close/reopen and
verify again. Retain pre-migration backup/prior evidence through release retention window.

Do not say data was “verified” beyond the executed integrity/replay/coverage checks. Migration does
not refresh repository evidence or semantic results.

### Failure classification

- Before backup terminal: no schema mutation; retry same request after cause.
- Backup terminal, before schema: safe to stop; backup remains.
- Transactional migration fails/rolls back and normal gate proves old schema/state: keep old active,
  preserve backup/error, resolve cause before retry.
- Ambiguous DDL/metadata mismatch/replay/integrity/generation failure: writes quarantine and result
  `rollback_required`; do not rerun/downgrade/edit.
- Response lost after success: lookup same request/status/version determines outcome.

### Rollback-by-restore

1. Preserve failed/current target and pre-migration backup; stop writers.
2. Install a package that supports reading the backup format and desired target schema according to
   compatibility matrix—often the migration candidate can restore the old snapshot. Do not merely
   reinstall old package over current data.
3. Run restore preview on backup; verify manifest/task/frontier/key/object/database, generated new
   route and whether any migration would be applied. For a true pre-migration rollback, do not accept
   a plan that upgrades it back unexpectedly.
4. Confirm exact plan. Restore into new quarantined target, full replay/reopen, then catalog CAS
   switch. Current failed route retained.
5. Verify restored frontier/head/replay/receipt and explicitly identify acknowledged work after backup
   frontier that is absent. Decide manually whether/how to republish; never merge databases.

### Prohibited actions

No reverse/down migration SQL; no edit `user_version`/metadata; no copy/replace live DB/WAL/SHM; no
delete projection/event rows to satisfy old schema; no force-open unknown version; no run two writers;
no remove backup/failed target before verification; no claim binary uninstall restored data.

## Errors and edge cases

- A migration backup can be machine-bound; key must remain available for rollback target.
- Restore may require a supported forward reader even when desired data schema is old.
- Work committed after pre-migration backup is not in rollback target and must be disclosed.
- Projection cache difference alone is rebuildable; canonical difference is not ignored.
- Examples use synthetic versions; current paths are generated from release manifest/help.

## Invariants

1. Verified backup completes before schema change.
2. Only forward contiguous immutable migrations run.
3. Canonical event bytes/frontier are unchanged by migration.
4. Rollback uses verified new target and route switch, never overwrite/reverse SQL.
5. Old/failed/backup sources remain preserved until exit criteria.

## Tests

- Run command examples against every released old fixture and exact current candidate.
- Fault at backup, each migration step, replay, reopen and route switch; documented branch must match.
- Docs lint rejects SQL/file-copy/version-edit/downgrade-as-rollback guidance.
- Packaging upgrade/rollback and subprocess kill matrices are linked executable evidence.

## Open questions

None.
