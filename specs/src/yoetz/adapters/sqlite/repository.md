# src/yoetz/adapters/sqlite/repository.py — LedgerPort over the task bundle database

**Wave:** C | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/sqlite/connection.md`,
`specs/src/yoetz/adapters/sqlite/migrations.md`,
`specs/src/yoetz/protocol/canonical.md`, `specs/src/yoetz/protocol/errors.md`,
`specs/src/yoetz/domain/events.md`, `specs/src/yoetz/kernel/reducers.md`,
`specs/src/yoetz/ports/ledger.md` | **Imported by:**
`specs/src/yoetz/application/service.md`, `specs/src/yoetz/application/check.md`,
`specs/src/yoetz/adapters/sqlite/recovery.md`

## Purpose

The single durable implementation of `LedgerPort`. Everything the product promises about
acknowledgement, idempotency, ordering, and replay is ultimately this file executing the ledger
append and check/semantic lease lifecycle owned by `specs/src/yoetz/ports/ledger.md` against
the schema owned by canonical root `specs/migrations/bundle/0001.sql.md` and executed from its
verified byte-identical installed resource.
It is deliberately not a second semantic kernel: canonicalization, validation, and reducers are
imported from `protocol/` and `kernel/`; this file only decides *when* they run relative to
SQLite transaction boundaries and turns their outputs into durable rows.

## Public surface

- `SqliteLedger` — implements the exact contract in
  `specs/src/yoetz/ports/ledger.md`:
  - `append_batch(AppendCommand) -> AppendResult`
  - `load_events(session_id, after=0, through=None) -> AsyncIterator[AcceptedEvent]`
  - `load_projection(session_id, view) -> StoredProjection | None`
  - `query_projection(query: ProjectionQuery) -> ProjectionPage`
  - `freeze_case(session_id, writer_id, expected_frontier, request_id, request_digest) -> FrozenCase`
  - `commit_check_if_current(frozen, findings, semantic_status, semantic_reason, semantic_provenance, request_id) -> CheckResult`
  - `lookup_operation(writer_id, operation_id) -> OperationRecord | None`
- Check/semantic orchestration methods in the shared `LedgerPort` contract:
  - `advance_check_phase(lease: OperationLease, expected_phase: CheckPhase, next_phase: CheckPhase, durable_object_ref: ObjectRef | None = None) -> OperationLease`
  - `enqueue_semantic_job(lease: OperationLease, case_digest: str, case_object_ref: ObjectRef) -> SemanticJobRecord`
  - `claim_semantic_job(lease: OperationLease, job_id: str) -> SemanticAttemptHandle`
  - `record_attempt_outcome(handle: SemanticAttemptHandle, outcome: AttemptOutcome, result_object_ref: ObjectRef | None = None, terminal_code: SemanticReason | None = None) -> None`
  - `select_attempt(lease: OperationLease, handle: SemanticAttemptHandle, selected_result_object_ref: ObjectRef) -> SelectedAttempt`
  - `renew_leases(lease: OperationLease) -> OperationLease`
  - `reclaim_operation(writer_id: str, operation_id: str, request_digest: str) -> OperationLease | PendingVerdict`
- `run_passive_checkpoint(wal_page_threshold) -> CheckpointReport` (registered shared result).
- `rebuild_projection(projection_name) -> None` — generation-replay trigger owned with
  `specs/src/yoetz/kernel/projections.md`.

All methods execute their SQLite work as jobs on the bundle's `SqliteWriterThread` (reads may use
the reviewed bounded read-only connection policy).

## Behavior

### Idempotency decision table (the `LedgerPort` contract applied by every mutating method)

Key: `(writer_id, operation_id)` (physically equivalent to `(task_id, writer_id, operation_id)`
because one bundle holds exactly one task; a future multi-task database MUST add `task_id`).

| Existing operation | New request digest | Result |
|---|---|---|
| none | any valid digest | Validate and accept all events atomically. |
| complete | same | Return the stored original response (assigned sequences/digests). Append nothing. |
| complete | different | Return public `IDEMPOTENCY_CONFLICT`; append nothing. |
| pending | same | Resume under the valid owner lease or return `OPERATION_PENDING`; never guess that an external side effect completed. |
| quarantined | any | Return the stable quarantine reason envelope; operator repair is explicit. |

`pending` rows exist only for `operation_kind='check'` in v0.1; `publish_work`, `respond`, and
`receipt` commit absent-to-complete atomically in their single bounded event transaction.

Lease validity (used everywhere below): valid iff `owner_generation` equals the current
`bundle_meta.owner_generation` AND `lease_expires_at` is in the future. Current generation +
unexpired ⇒ `OPERATION_PENDING`. Expired OR stale generation ⇒ fenced CAS reclaim
(`lease_generation += 1`, current generation and owner nonce written, new expiry), always via
UPDATE … WHERE naming the old lease values.

The v0.1 operation lease is exactly 60 seconds. A job expiry may not exceed its parent operation's
remaining lifetime, and live operation/job leases renew at half-life. These are versioned runtime
policy values and never weaken owner-generation fencing.

### `append_batch(AppendCommand)` — the `LedgerPort` append transaction

**Phase 1 — bounded preflight transaction.** A read of `operations` for
`(writer_id, operation_id)`. Same digest + complete → return stored response. Different digest →
`IDEMPOTENCY_CONFLICT`. Pending/quarantined → apply the table above. This preflight only avoids
wasted object publication on ordinary retries; it is **never trusted for correctness** — the
write transaction repeats the lookup.

**Phase 2 — outside any transaction.** Validate the request; canonicalize each payload; run
secret scan / policy filtering; encrypt each payload object and publish it durably through
`ObjectStorePort` (`stage` → `finalize`: temp file, fsync, atomic rename, dir fsync); compute the
canonical request digest over the publication-request identity bytes (caller headers + keyed
payload commitments; no plaintext, nonces, object IDs, or ledger-assigned fields — ADR-002
decision 4, so retry re-encryption cannot change identity). A crash here leaves only
unreferenced encrypted objects for delayed orphan GC.

**Phase 3 — `BEGIN IMMEDIATE` (writer thread).** Every step is bounded indexed reads/writes; no
hashing of payloads, no I/O beyond SQLite:

1. Verify the runtime/storage generation is writable: `bundle_meta.owner_generation` and owner
   nonce equal this process's handle (stale ⇒ abort, `StorageUnsafeError("bundle_generation_
   lost")`); no exclusive maintenance generation active.
2. Recheck `(writer_id, operation_id)`; apply the idempotency table exactly (same+complete →
   load stored `result_canonical`, COMMIT, return; same+pending → only valid for `check`
   (this method never creates pending rows); same+quarantined → load stable safe envelope,
   COMMIT, return; different digest → ROLLBACK, conflict).
3. For a new `operation_kind='receipt'`, run the indexed predicate
   `NOT EXISTS (SELECT 1 FROM import_jobs WHERE session_id=? AND state='pending')`. If false,
   ROLLBACK and return retryable `OPERATION_PENDING`. This occurs after terminal replay and before
   any sequence allocation, in this same authoritative transaction.
4. Verify the writer: row exists in `writers`, `state='active'`, and the command's claimed
   predecessor equals `writers.head_entry_digest`; the batch's first event will take
   `writer_seq = writers.next_writer_seq`. A conflicting predecessor or reused sequence fails
   (`EVENT_INVALID`, or quarantine for a durable contradiction, under
   `specs/src/yoetz/ports/ledger.md`), never last-write-wins.
5. Verify `expected_frontier` when the command carries one: it must equal
   `bundle_meta` head sequence, else ROLLBACK with `FRONTIER_CONFLICT`.
6. Allocate N consecutive ledger sequences from the transactional `counters` row
   `ingestion_sequence` (`UPDATE counters SET next_value = next_value + N … RETURNING`), so a
   rollback returns the reservation.
7. Build each accepted envelope: assign `writer.sequence`, `writer.previous_entry_digest`,
   `ledger.ingestion_sequence`, `ledger.previous_entry_digest` (previous accepted entry's digest
   or the literal `"genesis"`), `ledger.accepted_at` (from `ClockPort`, three fractional
   digits); serialize with `canonical_encode`; compute `entry_digest = canonical_digest` of
   those exact bytes. Within the batch, event k's predecessors are event k−1's digests
   (consecutive chaining).
8. Insert `objects` inventory rows (state `present`), `events` rows (including the exact
   `canonical_entry` bytes), `event_parents` (each parent must already exist in this task and
   precede the child — enforced by the FK plus an ordered existence check), and `event_refs`.
9. Advance `writers.next_writer_seq`/`head_entry_digest` and `bundle_meta` global head
   sequence/digest.
10. Apply the incremental pure reducers (`kernel/reducers.md`) to each accepted event in order
   and write the resulting change set to the active generation's projection tables and
   `projection_state.applied_through_seq`/`state_digest` — in this same transaction, so
   projections never lag acknowledged writes.
11. Build the stable canonical structural operation result (assigned IDs, sequences, digests,
    reason codes only — never user content), store `result_canonical` + `result_digest`, and
    insert the `operations` row as `complete/terminal` with `first_ingestion_seq`/
    `last_ingestion_seq` and **no lease fields**.
12. `COMMIT`.

**Phase 4 — only after COMMIT returns successfully** does the method return `AppendResult`
(which the application acknowledges). `SQLITE_BUSY`, `SQLITE_FULL`, `SQLITE_IOERR`, failed sync,
failed commit, connection loss, and ambiguous termination never emit success; the client retries
the identical operation and the durable row decides.

### Check-operation lifecycle (`specs/src/yoetz/application/check.md`)

`freeze_case(session_id, expected_frontier, request_id)`:
in `BEGIN IMMEDIATE` — idempotency lookup (table above; a terminal row returns its envelope, a
live-leased pending row returns `OPERATION_PENDING`, an expired/stale one is CAS-reclaimed and
resumed at its recorded phase); for every new/resumed nonterminal check require the indexed
`NOT EXISTS` pending-import predicate for the session; catch projections up if needed; capture frontier `F` (current
head sequence + digest), dependency digest `D`, and policy/config/engine versions. The encrypted
resume case object is durably published **first** (outside the transaction, before this
transaction runs); then insert `operations` as `pending/reserved`, `operation_kind='check'`,
`resume_object_id` set, with the current bundle owner generation, this runtime's lease owner
nonce, `lease_generation=1`, and expiry. COMMIT before any expensive work. Returns `FrozenCase`
carrying `F`, `D`, versions, and the `OperationLease`.

Phase advancement (all via `advance_check_phase`, each a one-row CAS in a short
`BEGIN IMMEDIATE`): `reserved → local_ready` after deterministic checks' immutable result object
is durable; `local_ready → semantic_wait` together with `enqueue_semantic_job` when semantic
evaluation is configured and necessary, otherwise `local_ready → ready_to_finalize`;
`semantic_wait → ready_to_finalize` once all required jobs are terminal and no job lease is
live. Every CAS's WHERE clause verifies state `pending`, the expected phase, and all four lease
fields; zero rows updated ⇒ the lease was lost ⇒ raise the fencing error (never write anyway).
Phases only move forward; on reclaim the recorded phase is a lower bound — the successor
re-validates durable state for that phase before advancing and never assumes an external call
completed.

`commit_check_if_current(frozen, findings, semantic_status, semantic_reason,
semantic_provenance, request_id)` — the final
`BEGIN IMMEDIATE`: verify the operation lease (all four fields + current owner generation) and
that material dependency revisions still match `D`; repeat the indexed no-pending-import
predicate for the frozen session; append the check/finding events (reusing
steps 5–9 of the append transaction — checks append through the same machinery); store
`result_canonical`/`result_digest`; set `state='complete'`, `phase='terminal'`, NULL lease
fields, `terminal_at`; COMMIT; only then acknowledge. If `F`/`D` no longer match, the semantic
result is labeled stale and cannot steer: the check still completes deterministically with
weakened coverage or returns `FRONTIER_CONFLICT` per the application's policy — this adapter
only enforces that a stale-fenced write never happens.

### Semantic jobs and attempts

The durable rows are owned by
`specs/src/yoetz/resources/migrations/bundle/0001.sql.md`; the orchestration lifecycle is
owned by `specs/src/yoetz/application/check.md`.

`enqueue_semantic_job` inserts a `queued` job (deduplicated by
`UNIQUE(writer_id, operation_id, case_digest)`; an existing row is returned, never duplicated).

`claim_semantic_job` (one `BEGIN IMMEDIATE`): verify the parent operation is still
`pending/semantic_wait` and its lease is live under the current owner generation. A
current-generation unexpired **job** lease is left alone (`OPERATION_PENDING` semantics). A
queued job, expired lease, or stale-generation lease is fenced and CAS-claimed. Reclaim is
state-dependent on the previously active attempt: `started` → `expired` (no bytes);
`response_durable` → `late` (result object retained); already-terminal stays terminal. Then
insert a new `semantic_attempts` row capturing its own `attempt_id`, `attempt_ordinal`,
`provider_request_id`, and — permanently — the `owner_generation`, `lease_owner_id`, and
incremented `lease_generation` that authorized it; increment `attempt_count` and the job's
lease generation; set job `state='leased'` with `active_attempt_id`. The new job expiry is no
later than the operation expiry. Provider calls happen with no SQLite transaction held.

`record_attempt_outcome`: CAS the attempt through the closed monotonic transition table, which
this adapter enforces in full (rows are never deleted or reused):

| Current | Allowed next | Meaning |
|---|---|---|
| `started` | `response_durable`, `failed`, `expired`, `late` | Call began; it then produced bytes, failed, lost its live lease without bytes, or returned after losing authority |
| `response_durable` | `selected`, `failed`, `late` | Bytes exist; validation/current-generation selection decides their effect |
| `selected` | none | This is the one job result allowed to steer |
| `failed` | none | Terminal provider/validation failure |
| `expired` | none | Terminal lease loss with no durable response bytes |
| `late` | none | Terminal non-selected response with retained bytes |

Every other transition is rejected, and mutation of `attempt_id`, `job_id`, `attempt_ordinal`,
`provider_request_id`, captured owner/lease generations, `started_at`, or a previously stored
`result_object_id` is forbidden (UPDATEs never name those columns; a verification read asserts
they are unchanged in the same transaction).

`select_attempt` (one short `BEGIN IMMEDIATE`): verify operation and job owner generation, lease
owner, lease generation, active attempt identity, and that frontier `F` and dependency `D` still
match. Mark the attempt `selected`, set the job `state='succeeded'` with `selected_attempt_id`
and the **identical** `selected_result_object_id` (the adapter verifies this cross-row equality
because SQLite cannot express it as a row-local CHECK), clear the job lease — all in the same
transaction. The partial unique index `semantic_attempts_one_selected` makes a second selection
impossible. A late result from an expired generation remains linked to its encrypted
non-selected attempt but can never steer or complete the operation. Failed/quarantined jobs name
no selected attempt and no result object; provider refusal, timeout, or invalid output records a
terminal job failure and weakens semantic coverage — it does not quarantine the public check.

`renew_leases`: in one transaction, renew the parent operation lease first, then its live job
leases. A new bundle owner generation invalidates every older operation/job lease immediately,
regardless of wall clock.

### `reclaim_operation`

One `BEGIN IMMEDIATE` transaction reads `(writer_id, operation_id)` and applies the port's exact
reclaim table. Absence returns `PendingVerdict(absent)` and creates nothing. A different digest
rolls back with `IDEMPOTENCY_CONFLICT`. Complete/quarantined rows return their structural
`terminal`/`quarantined` verdict and stored `OperationRecord`. A current-generation unexpired
lease returns `live` with a bounded remaining-milliseconds hint. An expired or stale-generation
pending check is reclaimed by one `UPDATE` whose `WHERE` names the old state, phase, owner
generation, lease owner, lease generation, and expiry; it writes the current owner generation/
nonce, increments lease generation exactly once, assigns the fresh bounded expiry, and returns
the new `OperationLease`. Zero updated rows means the transaction rereads and returns the now-
authoritative verdict; it never returns the speculative lease. A pending non-check row, illegal
phase, invalid resume object, contradictory event range, or terminal result/digest disagreement
is closed with the matching registered `OperationQuarantineCode`, not repaired in place.

### Reads

`lookup_operation`: single indexed read; returns the `OperationRecord` (state, phase, digests,
stored envelope) or `None`. On-read verification applies (below).

`load_events`: paginates by stable `ingestion_seq` ranges in pages of exactly 500 on a read-only
connection, releasing the read transaction between pages — no long readers on hot paths, as
required by `specs/src/yoetz/ports/ledger.md`. For every row returned it re-verifies that the indexed columns
(`event_id`, `writer_id`, `writer_seq`, `schema_name`, digests, `payload_object_id`, …) and the
normalized `event_parents`/`event_refs` rows agree with the stored `canonical_entry` bytes, and
that `entry_digest == canonical_digest(canonical_entry)`. Disagreement is canonical corruption:
raise the internal corruption error mapped to `STORAGE_CORRUPT` and hand off to `recovery.md`
(bundle quarantined for writes). Yields `AcceptedEvent` values decoded from the canonical bytes,
never from the index columns.

`load_projection`: reads the active generation's projection tables plus `projection_state`;
returns `StoredProjection` including `applied_through_seq` and its lag versus the head, so
results can state exactly which `Frontier` the cache represents. If
`projection_state.projection_version` ≠ the engine's active version, or `state_digest` fails its
consistency check, return `None` after scheduling `rebuild_projection` — projection corruption
never blocks canonical reads and is repaired by the generation replay owned by
`specs/src/yoetz/kernel/projections.md`: verify identity,
load version V into empty tables, bounded pages ordered by `ingestion_seq <= F` with
per-page digest/predecessor/payload verification and pure reducer application, persist
`applied_through_seq` per page in one transaction, atomically switch the generation. A failed
rebuild leaves the previous generation intact.

`query_projection`: validates the typed filter/position and exact requested frontier, then uses
the view's registered covering index and stable sort key to select at most `limit + 1` rows. It
returns no more than `limit`, with an exclusive typed next position only when the extra row exists,
plus requested/head/effective frontiers, lag, projection version, rebuild state, coverage, and
sorted gaps. Each page is one bounded read transaction released before return. A cursor/query/
version mismatch or future frontier is `INVALID_REQUEST`; absence is `SESSION_NOT_FOUND`; an exact
historical frontier that cannot be represented fails or is served by bounded verified replay,
never silently substituted. `candidate_findings` remains the registered `load_projection` + pure
kernel exception and is rejected as a repository row-query view.

### `run_passive_checkpoint` (ADR-003 checkpoint ownership)

Only the authoritative owner runs checkpoints. On the writer thread, when the WAL exceeds the
size threshold: verify the owner generation, then `PRAGMA wal_checkpoint(PASSIVE)` and record
the `(busy, log, checkpointed)` result. PASSIVE never blocks readers/writers; an incomplete
checkpoint is retried on a later trigger, never awaited unboundedly. Shutdown attempts one final
PASSIVE checkpoint but does not treat failure as data loss — committed data never depends on
checkpoint success.

## Errors and edge cases

- Public mappings: digest conflict → `IDEMPOTENCY_CONFLICT`; live lease → `OPERATION_PENDING`;
  stale `expected_frontier` → `FRONTIER_CONFLICT`; invalid event/writer-chain violation →
  `EVENT_INVALID`; busy beyond timeout → `BUNDLE_BUSY`; lost generation →
  `STORAGE_UNSAFE`; canonical corruption → `STORAGE_CORRUPT`.
- Quarantine (`state='quarantined'`) is reserved for corruption, invariant violation, or
  irreconcilable durable-state ambiguity; the row stores a stable safe envelope plus an
  allowlisted `quarantine_code`, never an exception message.
- Duplicate `event_id`, duplicate/skipped `writer_seq`, wrong writer or global predecessor,
  future/cross-task parent: all fail closed under the `LedgerPort` error contract before any row
  is visible.
- A crash between COMMIT and response (kill points 10–11) is healed by retry hitting the
  complete row; a crash before COMMIT leaves at most orphan objects.
- No SQL text, path, or user content ever appears in errors; `result_canonical` and quarantine
  envelopes contain only opaque IDs, sequences, digests, and reason codes.

## Invariants

- Acknowledgement strictly follows a successful COMMIT that wrote the complete/terminal row.
- One `BEGIN IMMEDIATE` per write path; only bounded indexed work inside; all expensive work
  outside (before or after).
- `ingestion_seq`, writer sequences, and head digests advance only together and only in the
  append/check-commit transactions.
- `canonical_entry` bytes are written once and never updated; reads verify them and derive from
  them.
- At most one `selected` attempt per job; a `succeeded` job names that attempt and its identical
  result object; attempt provenance columns are immutable.
- Leases obey the generation+expiry rule; a stale generation can never write, select, finalize,
  or checkpoint.
- Check freeze/finalization and new receipt append test pending importer rows inside their own
  transaction; a prior status read is never trusted for this gate.
- Projection tables carry no truth that cannot be rebuilt from events.

## Tests

- `specs/tests/integration.md`: append idempotency (all five table rows), batch atomicity, sequence
  allocation/rollback, frontier conflicts, writer-chain negatives, on-read verification
  detects each mutated index column, result_canonical stability across retries.
- `specs/tests/subprocess.md`: kill matrix points 5–12, 15–16; lease reclaim at every phase; two
  generations racing job selection — exactly one selected attempt survives.
- `specs/tests/conformance.md`: byte parity with `adapters/memory/ledger.md` on canonical bytes,
  outcomes, projections, findings, coverage, receipts; incremental/full replay equivalence
  across the five projection paths; Hypothesis state-machine model of leases/idempotency; import reservation/
  completion races at both check boundaries and receipt append.
- `specs/tests/capability.md`: 10K/100K/1M bounded replay; WAL growth under disabled/slow
  checkpointing; no long readers.

## Open questions

None.

E-004 and E-005 are the sole central runtime/storage calibration gates.
