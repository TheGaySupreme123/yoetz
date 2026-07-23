# tests/integration/ — durable bundle and application workflow suite

**Wave:** C–E | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-006, ADR-008, ADR-009 |
**Imports (spec-tree):** application and local-service/control/privacy services,
SQLite/object/key/provider adapters, config/paths, fixtures | **Imported by:** PR, nightly, and
release gates

## Purpose

Prove that real adapters compose through the application service without changing domain truth.
These tests use temporary catalogs/task bundles, real APSW/SQLite and filesystem durability calls,
scripted key/provider backends, and the same six-operation facade owned by the trusted local service.
CLI/MCP are clients of that facade, never direct `Application` owners. Physical process/transport
behavior remains in `subprocess.md`.

## Public surface

```text
tests/integration/
  storage/
    test_build_and_pragma_gate.py
    test_migration_0001.py
    test_migration_0002_observation.py
    test_append_and_replay.py
    test_projection_rebuild.py
    test_start_catalog_state_machine.py
    test_owner_generation.py
    test_checkpoint_and_wal_bounds.py
    test_quarantine_and_recovery.py
    test_backup_restore.py
    test_migration_rollback.py
  objects/
    test_envelope_and_encrypted_files.py
    test_key_backends.py
    test_portable_recovery.py
    test_redaction_and_gc.py
  application/
    test_start.py
    test_publish_work.py
    test_check.py
    test_respond_status_receipt.py
    test_import_review.py
    test_maintenance.py
    test_full_workflow.py
  privacy/
    test_egress_gateway.py
    test_plaintext_canary_sweep.py
  providers/
    test_fake_provider_coordinator.py
  observation/
    test_acceptance_scenarios.py
  service/
    test_daemon_clients.py
    test_encrypted_vault.py
    test_human_control.py
    test_local_control_channel.py
    test_locked_ready_transitions.py
    test_multi_client_single_writer.py
    test_secret_ingress.py
```

Each test creates an isolated owner-only app-data root outside any source/sync/network path, injects
clock/IDs/provider behavior, and cleans only paths it created.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/integration/application/test_check.py
tests/integration/application/test_full_workflow.py
tests/integration/application/test_import_review.py
tests/integration/application/test_maintenance.py
tests/integration/application/test_publish_work.py
tests/integration/application/test_respond_status_receipt.py
tests/integration/application/test_start.py
tests/integration/objects/test_envelope_and_encrypted_files.py
tests/integration/objects/test_key_backends.py
tests/integration/objects/test_portable_recovery.py
tests/integration/objects/test_redaction_and_gc.py
tests/integration/observation/test_acceptance_scenarios.py
tests/integration/privacy/test_egress_gateway.py
tests/integration/privacy/test_plaintext_canary_sweep.py
tests/integration/providers/test_fake_provider_coordinator.py
tests/integration/service/test_daemon_clients.py
tests/integration/service/test_encrypted_vault.py
tests/integration/service/test_human_control.py
tests/integration/service/test_local_control_channel.py
tests/integration/service/test_locked_ready_transitions.py
tests/integration/service/test_multi_client_single_writer.py
tests/integration/service/test_secret_ingress.py
tests/integration/storage/test_append_and_replay.py
tests/integration/storage/test_backup_restore.py
tests/integration/storage/test_build_and_pragma_gate.py
tests/integration/storage/test_checkpoint_and_wal_bounds.py
tests/integration/storage/test_migration_0001.py
tests/integration/storage/test_migration_0002_observation.py
tests/integration/storage/test_migration_rollback.py
tests/integration/storage/test_owner_generation.py
tests/integration/storage/test_projection_rebuild.py
tests/integration/storage/test_quarantine_and_recovery.py
tests/integration/storage/test_start_catalog_state_machine.py
```

## Behavior

### Storage identity and migrations

- Verify exact certified APSW/SQLite/source ID/amalgamation/compile options and every PRAGMA by
  reading returned state, not trusting setter success.
- Fresh catalog/bundle migration produces exact application/user version, tables/indexes/CHECKs,
  STRICT/WITHOUT ROWID choices, and canonical resource digest.
- Reopen current, older supported, newer unknown, wrong application ID, truncated/corrupt, and
  projection-only-corrupt fixtures. Writes fail closed where required; canonical data is never
  rewritten merely to inspect.
- Failed migration at every transactional/finalization boundary leaves original usable/quarantined
  as specified; retry is idempotent.

### Catalog, ownership, append, replay

Exercise the complete start phase machine: reserve, each durable advance, bundle creation/validation,
encrypted result finalization, complete; crash/retry at each phase; current lease pending; expired or
stale-generation reclaim; commitment attachment conflict; quarantine.

Race owner generation through controlled connections: exactly one current generation; stale owner
cannot append/checkpoint even with unexpired wall-clock lease. All CLI/MCP/UI requests observe the
same port semantics through one service owner; clients never acquire a generation themselves.

Append tests cover 1/100 events and 1 MiB boundaries, two writer chains, expected frontier,
same/canonical-equivalent retry, changed reuse conflict, duplicate event/writer sequence, skipped
sequence, wrong predecessor/global head, invalid known vs preserved unknown, and atomic rejection.
Reopen and replay from zero after every case; projection cache deletion/corruption rebuilds to the
same digest.

Long-reader/checkpoint tests verify owner-only PASSIVE checkpoint, WAL thresholds/degraded
diagnostics, busy timeout, disk-full/quota/permission/read-only and supported injectable I/O failures
without unbounded growth or acknowledged loss.

### Objects, keys, recovery, redaction

Use reviewed object known-answer vectors and fresh encrypted objects to test stage → fsync → rename →
dir fsync → finalize, verified open, size cap, header/ciphertext digest/commitment, and references
only after durable publication.

Inject failure at partial write, pre/post-fsync, pre/post-rename, and finalization. Temp/orphan
objects are not referenced; GC honors 24-hour safety, live refs, and maintenance pins. Symlink,
hardlink, traversal, swapped object, tamper/truncation/appended bytes/wrong key/slot/algorithm fail
with bounded reasons.

Scripted key backends cover create/load, locked/missing/unsupported, backend identity mismatch,
distinct derived domains, passphrase Argon2id artifact, wrong/tampered/unsupported/key-ID-mismatch,
machine-bound vs portable backup, and clean-profile restore that decrypts every required object.
Fixed-IVK vectors additionally prove opaque installation `catalog_lookup|log_correlation|privacy_audit`
handle derivation, cross-installation separation, no independent MAC-key records, wrong-purpose/
domain rejection, and relock invalidation without exposing raw key bytes.

Redaction appends history, changes projection coverage immediately, deletes only eligible encrypted
payloads, preserves structural gap, and weakens later checks/receipts. No forensic-erasure claim is
asserted.

### Application workflows

Invoke the trusted service composition's `Application` facade, not adapter helpers. The integration
harness may call that facade in-process to isolate durable composition behavior; it must not model
CLI/MCP as owners of `Application`, keys, storage, or provider state:

- start modes and idempotent resume;
- publish known/unknown atomic batches and ambiguous retry;
- deterministic-only, semantic-if-configured, semantic-required check, with optional semantic as
  the ordinary configuration default;
- exact deterministic finding bases; rich selected review packets and typed omissions; fake
  provider success/refusal/timeout/invalid/late/invented-ID/coverage-upgrade/stale-frontier;
- assisted `check → reviewer challenge → respond/publish_work → fresh check → receipt`, using only
  existing finding and response surfaces;
- response dispositions, waiver authority/scope/expiry, recheck;
- status views/frontier/pagination/cache-lag disclosure;
- receipt JSON/Markdown, own-event-excluded frontier, idempotent return;
- Codex JSONL import source retention/quarantine and cooperative-vs-import review.

The full vertical slice uses one task, two writers, two-event publication, encrypted payload,
projection, deterministic finding, malformed/stale semantic fake, response, current recheck,
receipt, close/reopen/replay.

### Service, vault, and privacy gateway

The seven service integration modules prove one service/many clients and one writer generation;
`starting → ready|locked`, explicit lock/relock, and restart transitions; encrypted-vault and
best-effort secret-memory cleanup; separately typed confidential secret ingress; and local-human
privacy decisions that ordinary control/MCP cannot submit. The egress integration path compiles the
exact review selector, prepares the canonical rich packet, minimizes/redacts/scans it, binds exact
approval when required, dispatches only through the gateway, and commits structural receipts for
success, denial, timeout, invalid, and ambiguous outcomes. It proves the explicit current-data-use
guard fences only when enabled, standing assisted policy requires no routine human prompt, and
agent-context projection either carries the accepted challenge through the existing finding fields
or emits a typed omission. Provider failure preserves deterministic `incomplete_check` results.

### Backup, restore, privacy

Backup pins a frontier, uses destination-side online Backup API, copies referenced objects, finalizes
a canonical manifest, then releases pin. Restore verifies manifest/keys/objects/replay in quarantine
and atomically switches catalog route only after full success. Missing/tampered/nonportable cases
leave current route untouched.

Seed unique canaries in every user-controlled category. After normal workflows and fault paths,
scan database, WAL, SHM, temp/orphan names/content, logs, backup/export/manifests, migration/recovery
artifacts, and configured diagnostic output. Ciphertext matches are not plaintext leaks; any
application-controlled plaintext occurrence is a release failure.

## Errors and edge cases

- Tests skip only when a platform is outside the declared matrix; advertised platforms may not skip
  required storage/durability/key cases.
- Real OS key stores are tested only in isolated capability jobs; normal integration uses a faithful
  scripted backend so no developer prompts/account state occur.
- Filesystem fault injection is explicit and scoped; unsupported OS fault cases are recorded as
  missing release evidence, not passing.
- No live semantic provider call exists here.
- Temp roots and background threads/tasks must be closed before cleanup; leaked WAL/handles fail.

## Invariants

1. All public workflows pass through application + ports.
2. Reopen/retry/replay never loses or duplicates an acknowledged logical effect.
3. Reference and durable adapters remain logically equivalent; SQLite is not a second kernel.
4. Network/model work and object encryption/fsync occur outside SQLite write transactions.
5. Recovery is into a new quarantined target; originals are preserved until verified switch.
6. Structural plaintext surfaces remain canary-free.

## Tests

```bash
uv run --locked pytest tests/integration -m "not fault and not live_keyring" -q --timeout=180
uv run --locked pytest tests/integration -m fault -q --timeout=300
```

Release runs on each advertised OS/CPU/ABI with exact artifact installs and preserves normalized
evidence: versions, source ID, test IDs, outcome, duration, and artifact digests.

## Open questions

None.

E-005 is the sole central storage-performance gate.
