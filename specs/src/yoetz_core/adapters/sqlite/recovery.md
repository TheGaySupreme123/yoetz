# src/yoetz_core/adapters/sqlite/recovery.py — bundle recovery, tail validation, and quarantine

**Wave:** C | **ADRs:** ADR-001, ADR-003, ADR-004 | **Imports (spec-tree):**
`adapters/sqlite/connection.md`, `adapters/sqlite/migrations.md`, `adapters/sqlite/repository.md`,
`adapters/objects/encrypted_files.md`, `adapters/keys/os_keyring.md`,
`adapters/keys/passphrase.md`, `adapters/privacy/catalog.md`, `config/paths.md`
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

| Name | Signature (natural language) |
|---|---|
| `RecoveryState` | frozen dataclass with bundle path, generation, last verified frontier, privacy-root generation/digest, and safety flags |
| `RecoveryResult` | frozen dataclass describing open/recover/quarantine/restore outcomes |
| `inspect_recovery_state(...)` | read-only startup probe over bundle metadata, ledger tail, and object markers |
| `validate_recovery_tail(...)` | verify the latest durable state and detect incomplete commits |
| `recover_bundle(...)` | reopen a recoverable bundle after crash/exit and re-establish the writer generation |
| `quarantine_bundle(...)` | mark the bundle unsafe and make writes unavailable until explicit operator recovery |
| `restore_bundle(...)` | activate a restored bundle and validate its provenance before use |

## Behavior

`inspect_recovery_state(...)` is the first recovery step after path safety and version checks. It
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

`validate_recovery_tail(...)` verifies the bundle tail is internally consistent without mutating it.
It checks the ledger tail against the last committed frontier, confirms the current generation fence,
validates that any pending operation or checkpoint has a stable terminal envelope, and verifies the
catalog privacy-root set against the active route. It does not
attempt to "fix" a partially written commit by guessing intent.

Tail validation must distinguish these cases:

- clean tail with stable frontier;
- interrupted write with recoverable evidence;
- corrupted or ambiguous tail that requires quarantine;
- generation mismatch that indicates another writer or a stale process.

`recover_bundle(...)` is used after an unclean exit or a successful external restore. It may:

- reopen the bundle under a new generation after proving the prior owner is dead;
- replay or revalidate migrations and projections;
- reopen object references if the required key backend is available;
- reconcile privacy audit state: expire restored/stale nonterminal authority, complete
  `decision_receipt_pending`, repair `receipt_pending` with the real bounded/unknown outcome, and
  preserve every verified catalog content root;
- refuse recovery if tail validation fails or if the object/key state is inconsistent.

Recovery may repair metadata that was already expected to be durable, but it must not fabricate a
missing commit. If the evidence says the bundle never reached a stable checkpoint, recovery stops
and the operator gets a quarantine or failure result instead.

`quarantine_bundle(...)` records a stable unsafe state when the tail, objects, or migration markers
are not safe to trust. It preserves the evidence and blocks writes; it does not delete the bundle.

Quarantine writes the smallest amount of persistent evidence needed to make the unsafe state
observable on the next startup. It should be obvious to the operator why the bundle was blocked.

`restore_bundle(...)` activates a restored bundle on a clean profile or machine. It checks that
the restore provenance, key story, and bundle identity match the documented recovery path before the
bundle is made writable.

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
