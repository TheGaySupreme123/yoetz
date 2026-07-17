# src/yoetz/adapters/sqlite/recovery.py — bundle recovery, tail validation, and quarantine

**Wave:** C | **ADRs:** ADR-001, ADR-003, ADR-004 | **Imports (spec-tree):**
`adapters/sqlite/connection.md`, `adapters/sqlite/migrations.md`, `adapters/sqlite/repository.md`,
`adapters/objects/encrypted_files.md`, `adapters/keys/os_keyring.md`,
`adapters/keys/passphrase.md`, `adapters/privacy/catalog.md`, `config/paths.md`,
`domain/values.md`, `ports/runtime.md`, `ports/maintenance.py.md` |
**Imported by:** startup/lifecycle code, restore tooling, and recovery tests

## Purpose

This file handles the part of startup that decides whether an existing bundle can be trusted,
reopened, repaired, or quarantined. It is not a second database layer. It is the recovery gate that
checks the bundle identity, the ledger tail, the object store state, and the key/material
availability before any write path is allowed to proceed.

The recovery gate exists to answer one question before the application starts: can this bundle be
made writable without inventing state or endangering data? If the answer is not provable, the
bundle stays read-only, quarantined, or explicitly failed.

## Public surface

- `RecoveryState` — frozen, internal, constant-redacted dataclass with fields `bundle_root: Path`,
  `catalog_path: Path`, `task_id: str`, positive `route_generation: int`,
  `route_identity_digest: str`, `storage_schema_version: int`, non-negative
  `owner_generation: int`, `owner_nonce: str`, `last_verified_frontier: Frontier`,
  `tail_state: RecoveryTailState`, `object_state: RecoveryObjectState`,
  `key_state: RecoveryKeyState`, `marker_state: RecoveryMarkerState`,
  `projection_state: RecoveryProjectionState`, non-negative `privacy_root_generation: int`, and
  `privacy_root_digest: str`. Paths/nonces never cross this package or enter repr/diagnostics.
- `RecoveryTailVerdict` — frozen dataclass with `state: RecoveryTailState`,
  `frontier: Frontier`, `reason: RecoveryReason`, and `ownership_admissible: bool`.
- `RecoveryResult` — frozen dataclass with `outcome: RecoveryOutcome`, `task_id`, `frontier`,
  `reason: RecoveryReason`, `fence: OwnershipFence | None`, `projection_rebuilt: bool`, and
  `privacy_audit_degraded: bool`.
- `RecoveryTailState` — `clean`, `interrupted_recoverable`, `corrupt_ambiguous`,
  `generation_conflict`.
- `RecoveryObjectState` — `verified`, `missing`, `authentication_failed`.
- `RecoveryKeyState` — `ready`, `locked`, `missing`, `backend_unavailable`.
- `RecoveryMarkerState` — `absent`, `complete`, `incomplete`, `malformed`.
- `RecoveryProjectionState` — `current`, `rebuild_required`, `unreadable`.
- `RecoveryOutcome` — `writable`, `read_only`, `quarantined`, `restore_required`,
  `manual_intervention`.
- `RecoveryReason` — `clean`, `interrupted_write`, `projection_rebuild`, `key_locked`,
  `key_missing`, `key_backend_unavailable`, `schema_unsupported`, `ledger_tail_corrupt`,
  `ledger_tail_ambiguous`, `generation_conflict`, `object_missing`,
  `object_authentication_failed`, `privacy_root_invalid`, `recovery_marker_malformed`,
  `restore_provenance_invalid`, `catalog_route_contradiction`.
- Exact functions:
  - `inspect_recovery_state(bundle_root: Path, *, catalog_path: Path, task_id: str, route_generation: int, route_identity_digest: str) -> RecoveryState`
  - `validate_recovery_tail(state: RecoveryState) -> RecoveryTailVerdict`
  - `acquire_bundle_ownership(state: RecoveryState, verdict: RecoveryTailVerdict, *, service_instance_id: str, service_generation: int, owner_nonce: str, now: datetime) -> OwnershipFence`
  - `recover_bundle(state: RecoveryState, verdict: RecoveryTailVerdict, fence: OwnershipFence, *, now: datetime) -> RecoveryResult`
  - `quarantine_bundle(state: RecoveryState, reason: RecoveryReason, fence: OwnershipFence, *, now: datetime) -> RecoveryResult`
  - `restore_bundle(state: RecoveryState, manifest: BackupManifest, fence: OwnershipFence, *, now: datetime) -> RecoveryResult`

## Behavior

`inspect_recovery_state(bundle_root, catalog_path, task_id, route_generation,
route_identity_digest)` is the first recovery step after path safety and version checks. It
collects the minimal safe facts needed to decide whether the bundle can proceed:

- the bundle root and catalog location;
- the bundle generation fence and recorded owner state;
- ledger tail integrity and migration status;
- object-store availability for required immutable objects;
- the active route-bound `PrivacyAuditObjectRoots` generation/digest and verification of every
  catalog-held privacy `ObjectRef`, independent of task-ledger inventory;
- key availability classification, but not key material;
- any incomplete recovery marker left by a prior crash.

The inspection phase is read-only and should be cheap enough to run on every startup. It may look
at several files, but it must not mutate bundle state or assume that the current process owns the
bundle.

`validate_recovery_tail(state)` verifies the bundle tail is internally consistent without mutating it.
It checks the ledger tail against the last committed frontier, confirms the current generation fence,
validates that every pending operation/checkpoint has its complete phase-specific resume evidence
and every terminal operation has a stable canonical envelope/digest, and verifies the
catalog privacy-root set against the active route. It does not
attempt to "fix" a partially written commit by guessing intent.

Tail validation must distinguish these cases:

- clean tail with stable frontier;
- interrupted write with recoverable evidence;
- corrupted or ambiguous tail that requires quarantine;
- generation mismatch that indicates another writer or a stale process.

`acquire_bundle_ownership(state, verdict, service_instance_id, service_generation, owner_nonce,
now)`
requires `verdict.ownership_admissible`, the service singleton's already-durable positive
generation, and an exact current catalog route-identity read. `ownership_admissible` says only that
the task bundle's prior writer can be fenced safely; it does not classify ledger/object state as
writable. After revalidating that the catalog still names this exact route, one `BEGIN IMMEDIATE`
compare-and-swap against the task bundle's `bundle_meta` advances/writes the bundle owner generation
and nonce only when their old values still equal `state`; it then returns the matching
`OwnershipFence`. This acquisition does not mutate or CAS the catalog route. Catalog route CAS is
reserved for start/restore routing transitions and cannot substitute for the bundle ownership row.
A bundle CAS miss, generation rollback, live-generation contradiction, route drift, or inadmissible
verdict fails closed. PID, heartbeat age, filesystem time, and endpoint presence are never inputs to
this authorization. `now` only supplies the canonical diagnostic heartbeat written after the bundle
CAS predicate succeeds.

`recover_bundle(state, verdict, fence, now)` has an exact effect table:

| Verified input | Outcome and mutation |
|---|---|
| clean tail, objects/privacy roots verified, keys ready, current projection, no incomplete marker | `writable`; verify the fence and clear no state |
| same, projection `rebuild_required` or `unreadable` while canonical ledger/objects verify | build and verify a new projection generation, atomically select it, return `writable` with `projection_rebuilt=true` |
| `interrupted_recoverable` with a complete proof for the recorded operation/checkpoint | complete only the already-evidenced metadata transition, revalidate the tail, then apply one of the two writable rows above |
| key `locked`, `missing`, or `backend_unavailable`, with otherwise verified structure | `read_only` with the matching key reason and no fence-authorized payload/write surface |
| unsupported/newer schema identity | `manual_intervention`/`schema_unsupported`; no migration or write is attempted here |
| invalid catalog privacy root with otherwise verified task state | quarantine only the audit row, preserve its ref, and return `writable` with `privacy_audit_degraded=true`; deterministic no-egress work remains available while disclosure/backup/restore are fenced |
| route already has a stable quarantine envelope and no staged verified target | `restore_required` with the stored reason; preserve the route and perform no bundle mutation |
| corrupt/ambiguous tail, malformed marker, or missing/authentication-failed bundle object | call `quarantine_bundle(state, reason, fence, now=now)`; never rebuild canonical state |
| generation conflict for which ownership is not admissible | `manual_intervention`/`generation_conflict`; perform no write |

The only recoverable privacy-audit transitions are those already evidenced by durable catalog
rows: expire restored/stale nonterminal authority, complete `decision_receipt_pending`, repair
`receipt_pending` with the real bounded/unknown outcome, and preserve every verified catalog
content root. No branch fabricates a missing commit.

Recovery may repair metadata that was already expected to be durable, but it must not fabricate a
missing commit. If the evidence says the bundle never reached a stable checkpoint, recovery stops
and the operator gets a quarantine or failure result instead.

`quarantine_bundle(state, reason, fence, now)` accepts exactly `ledger_tail_corrupt`,
`ledger_tail_ambiguous`, `object_missing`, `object_authentication_failed`,
`recovery_marker_malformed`, or `restore_provenance_invalid`,
requires that `fence` names the newly acquired current service/bundle generation, records a
canonical structural quarantine envelope under the current fenced catalog/bundle transaction, and
returns `RecoveryResult(outcome=quarantined, fence=None, projection_rebuilt=false, ...)`. It records
a stable unsafe state when the tail, objects, or migration markers
are not safe to trust. It preserves the evidence and blocks writes; it does not delete the bundle.

Quarantine writes the smallest amount of persistent evidence needed to make the unsafe state
observable on the next startup. It should be obvious to the operator why the bundle was blocked.

`restore_bundle(state, manifest, fence, now)` activates only the already staged, quarantined restore
target created by `MaintenancePort.restore`. It verifies the manifest's task/frontier/database/
object/privacy-root/version digests, decryptability, canonical replay digest, route generation,
and the supplied current fence; then it performs the catalog route compare-and-swap and returns
`writable`. Any manifest/provenance/replay mismatch returns a quarantined target with
`restore_provenance_invalid`; an active-route CAS miss returns `manual_intervention` with
`catalog_route_contradiction`. It never edits or replaces the source/original route.

Restore has a narrower trust story than recovery:

- recovery is for the same bundle after interruption;
- restore is for a copied bundle whose provenance must be re-validated;
- both require the same ledger and object invariants before write access returns.

The recovery flow is deliberately conservative:

1. inspect;
2. validate tail;
3. if safe, recover or restore;
4. otherwise quarantine;
5. only then allow the normal writer startup path to continue.

A recoverable interruption that passes every validation proceeds directly through the ordinary
generation-acquisition/write-open gate; it does not add a mandatory read-only intermediate state.
Read-only remains a terminal capability classification when write safety cannot be proven.

Decision outcomes should be explicit enough that the startup code can surface a stable operator
message:

- open/write allowed;
- open/read-only only;
- quarantine required;
- restore required;
- manual intervention required.

## Errors and edge cases

- A missing or locked key is a recovery classification, not a successful empty bundle.
- A stale generation fence is not recovered by guessing; it must be invalidated and reacquired.
- A corrupt tail, malformed recovery marker, or mismatched object digest forces quarantine.
- A missing, wrong-task/kind, digest-mismatched, or undecryptable catalog-rooted privacy object
  quarantines that privacy audit row without clearing its ref, fences task content disclosure and
  resume, and returns bounded audit degradation. Deterministic no-egress work may continue, but
  backup/restore and all content disclosure remain unavailable until verified repair.
- Restore never silently rewrites identity or provenance.
- If the ledger and object store disagree on required durable state, the bundle is unsafe until the
  discrepancy is resolved explicitly.
- Absence of a privacy object from task-ledger inventory is expected and is not corruption; the
  installation privacy catalog is its owning root. Conversely, catalog/object disagreement cannot be
  repaired by inserting a fabricated ledger row or letting GC clear the catalog ref.
- If a prior crash marker exists but the ledger tail is already known-good, the marker must still be
  cleared or rewritten explicitly as part of the recovery flow.
- Recovery must not depend on wall-clock truth if the filesystem timestamps are missing or skewed.

## Invariants

1. Recovery never invents state.
2. Quarantine preserves evidence and blocks writes.
3. A clean startup path and a crash-recovery path must converge to the same durable state before
   the application starts accepting operations.
4. Recovery decisions are based on stable on-disk evidence, not on Python process memory.
5. Restore proof requires the same bundle identity and recovery classification that the docs claim.
6. The same on-disk bundle, if recovered twice without new writes, should land in the same
   classification.
7. A read-only recovery decision must not accidentally re-enable the write path.
8. Recovery verifies both bundle-owned and catalog-owned object roots and never revives stale privacy
   authorization after restore/route-generation change.
9. `RecoveryResult.fence` is present exactly when `outcome=writable`; every other outcome clears it
   before returning and cannot be passed to `open_writer`.

## Tests

- `tests/subprocess/test_reopen_retry_replay.py` — crash before/after commit, replay, and quarantine
  behavior.
- `tests/integration/storage/test_backup_restore.py` — restored bundle activation and provenance
  checks.
- `tests/integration/storage/test_quarantine_and_recovery.py` — recovery-state classification and
  bounded safe facts, privacy-root verification, dangling-ref quarantine, and no-ledger-root parity.
- `tests/integration/storage/test_owner_generation.py` — stale owner/concurrent writer detection.
- `tests/property/test_ledger_state_machine_sqlite.py` — repeated classification stays stable across
  identical evidence.

## Open questions

None.
