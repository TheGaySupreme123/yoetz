# docs/runbooks/quarantine-recovery.md — preserve-first response to unsafe bundle state

**Wave:** C/F | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`specs/src/yoetz_core/adapters/sqlite/recovery.md`,
`specs/src/yoetz_core/adapters/sqlite/maintenance.py.md`,
`specs/src/yoetz_core/ports/diagnostics.md`, `specs/src/yoetz_core/ports/objects.md`,
`specs/src/yoetz_core/ports/ledger.md` | **Imported by:** storage errors, support and migration
runbooks

## Purpose

Give operators a safe procedure when Yoetz classifies catalog/bundle/object/tail/migration state as
corrupt, unsafe, or quarantined. The runbook prevents speculative repair, direct database edits,
deletion of evidence, key reset, and projection repair being confused with canonical-data recovery.

## Public surface

Stable headings:

1. What quarantine means
2. Immediate stop/preserve steps
3. Record safe structural facts
4. Classify the failure
5. Projection-only recovery
6. Interrupted-operation/startup recovery
7. Canonical/object/catalog corruption
8. Restore and verify
9. Escalation evidence and privacy
10. Prohibited actions
11. Exit criteria

## Behavior

### Meaning and first response

Explain quarantine disables writes because the runtime cannot prove invariants. It does not mean
data is empty/deleted, prove malicious corruption, or automatically make every backup bad. Read-only
structural/version inspection may remain available; payload read requires valid key and safe state.

Immediate ordered steps:

1. Stop retrying with new IDs and stop all Yoetz CLI/MCP processes for this installation. Do not
   kill unrelated processes; let shielded commit finish when current process reports it.
2. Preserve the original bundle/catalog/quarantine records exactly where runtime placed them. Do
   not copy live DB/WAL/SHM, rename, chmod, edit, vacuum, checkpoint with sqlite tools, delete temp/
   object files, reset key, or run a newer/older binary speculatively.
3. Record installed artifact/version/resource/SQLite identity, public error/reason/correlation ID,
   task/session IDs if already known, last verified `Frontier`, and whether failure followed crash,
   disk-full, key, migration or filesystem change. Do not record paths/payloads/secrets/raw errors.
4. Verify no competing owner/maintenance process and filesystem remains local/owner-only/supported.

### Classification decision tree

Use bounded reasons/evidence to distinguish:

- projection cache digest/generation/lag failure while canonical ledger/object chains verify;
- incomplete operation/backup/restore/migration marker after process death;
- live/stale generation or busy state;
- key locked/missing/backend mismatch;
- missing/tampered ciphertext object or key-slot/authentication failure;
- canonical entry/index/global/writer-chain/database integrity mismatch;
- catalog route/operation contradiction;
- unsupported/newer storage/resource/runtime identity.

Never infer category from one exception string or file timestamp. Normal startup/recovery gate owns
tail/marker classification; operator uses only public status/recovery/restore surfaces.

### Recoverable cases

Projection-only: normal supported recovery discards/rebuilds a new projection generation from
canonical events, verifies reference digest/frontier, then reopens. Operator must not drop tables
manually. Success requires canonical database/object/key verification and reference-equal replay;
unknown/redacted events remain explicit gaps.

Interrupted operation/start/maintenance: retry same request ID when known. Recorded phase is lower
bound; runtime revalidates object/database/manifest/route before resuming. Outcome unknown after
commit/switch resolves through durable operation/status. Stale generation recovery uses CAS after
proving prior owner absent; PID/time alone is insufficient.

Busy/live owner: stop duplicate client or wait boundedly; never delete lock/heartbeat/catalog row.
Key states route to key-recovery runbook and are not “corrupt empty payload.” Unsupported identity
requires a known compatible package/migration path, never force metadata.

### Non-repairable-in-place cases

Canonical event/database/object/catalog contradiction remains quarantined. Use a previously verified
backup and the backup/restore runbook: verify source, build new quarantined target, replay, atomic
catalog switch, retain original and prior route. If no verified backup/key exists, state recovery is
not proven; preserve evidence and seek expert support. Never fabricate missing event/object or delete
it to make checks pass.

After restore, compare expected frontier/receipt/coverage and explicitly disclose lost work after the
backup frontier. Quarantined original remains until retention/support decision; not ordinary GC.

### Safe evidence

Allowlist: package/protocol/storage/resource/SQLite identities/digests, bounded reason/correlation,
counts, frontiers/head hashes, operation/migration phase enum, manifest digest, key classification
without locator/secret. Prohibit database/WAL/SHM/object/recovery artifact, manifest paths, raw logs,
tracebacks, config/env, repository/workspace refs, prompts/payloads, key material. Any diagnostic
bundle must use documented profile and pass canary scan.

### Exit criteria

Writes resume only when normal startup gate returns current supported identity, keys available,
database/tail/chains/objects verify, projection replay matches, no unresolved recovery marker, and
current owner generation is acquired. Record restored/recovered frontier and remaining gaps. Do not
say “data repaired” when procedure restored an older backup.

## Errors and edge cases

- Disk/media failure may worsen on reads; stop and use qualified storage recovery rather than repeat
  scans. Runbook does not prescribe forensic imaging.
- Catalog-wide corruption can affect routing to multiple tasks; do not guess paths from filesystem.
- An older backup may restore correctly yet omit later acknowledged work; disclose exact frontier.
- Key loss and canonical corruption can coexist; resolving one does not validate other.
- Human support direction cannot authorize bypass of product checks without a reviewed recovery tool.

## Invariants

1. Preserve originals and stop writes before diagnosis.
2. Projection-only state is the only automatically rebuildable corruption class.
3. Canonical/object/catalog ambiguity is never edited in place.
4. Restore uses verified new target and retains old route/evidence.
5. Safe evidence contains no user content/key/path/raw internal text.

## Tests

- Table-driven docs tests map every public storage/key/maintenance reason to one safe branch/action.
- Fault/corruption fixtures execute projection rebuild, pending retry, stale generation, object/
  canonical/catalog corruption and verified restore.
- Docs lint rejects sqlite shell/copy/delete/lock-removal/key-reset instructions and overclaim wording.
- Privacy fixtures seed canaries into every prohibited evidence surface.

## Open questions

None.

F-006 is the sole central public-contact gate.
