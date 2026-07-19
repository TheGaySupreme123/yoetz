# src/yoetz/adapters/memory/importer.py — in-memory reference ImporterPort state machine

**Wave:** B/C/D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-005 |
**Imports (spec-tree):** `protocol/canonical.md`, `protocol/errors.md`, `protocol/ids.md`,
`ports/importer.md`, `ports/ledger.md`, `ports/objects.md`, `ports/clock.md`, `ports/ids.md`,
`adapters/importers/codex_jsonl.md`, `adapters/memory/objects.md` | **Imported by:** importer
conformance/property tests and injected `adapters/runtime.md` factories

## Purpose

`MemoryImporter` is the reference implementation of the complete `ImporterPort` state machine.
It gives conformance tests an implementation whose authority is explicit Python state rather than
SQLite, while preserving the same source identity, request aliases, leases/generation fences,
phase checkpoints, stable plan/batch/report IDs, report-evidence crash seam, status gate, and
review snapshot semantics as the durable adapter.

It is not a simplified convenience importer. It uses the real pure Codex parser/mapper and the
real `ObjectStorePort`, publishes no ledger event itself, and cannot relax a validation merely
because process exit discards its state. The application still sends every returned batch/report
draft through normal `publish_work` and reports the `AppendResult` back.

## Public surface

- `class MemoryImporter(ImporterPort)` implementing all methods registered for `ImporterPort`:
  `capture`, `reserve_or_resume`, `prepare_plan`, `publish_plan`, `next_batch`, `record_batch`,
  `prepare_report`, `publish_report`, `status`, `complete`, `quarantine`, and
  `load_review_source`.
- `@dataclass(slots=True) class MemoryImportState` — process-local structural state shared with
  the task's `MemoryLedgerAdapter` under one injected async transaction lock; contains request
  aliases, source jobs, ordered batch rows, and a monotonic revision, never plaintext.
- `@dataclass(frozen=True, slots=True) class MemoryImportPolicy` with v0.1 constants:
  - exact source `4 * 1024 * 1024` bytes;
  - stderr retained-prefix/commitment input `64 * 1024` bytes (raw bytes then discarded);
  - physical line `1 * 1024 * 1024` bytes and 20,000 lines;
  - at most 1,024 planned batches;
  - 60-second job lease and 30-second minimum renewed publication window;
  - 64 active-job entries in one status snapshot.
- `enum MemoryImportFaultPoint` — transaction/object/response-loss points used only by tests; its
  names mirror the durable kill matrix.

Constructor contract:

`MemoryImporter(*, task_id, admitted_session_id, ownership_fence, state, transaction_lock,
objects: ObjectStorePort, ledger: LedgerPort, clock: ClockPort, ids: IdPort,
policy: MemoryImportPolicy, fault_hook=None)`.

The ledger is retained only for accepted-event/frontier verification in `record_batch`, report
publication, and review. It is never called while `transaction_lock` is held. `state` and the lock
are shared with the memory ledger so its check/receipt pending-import guard can be atomic with
freeze/final append; two independent locks are forbidden.

## Behavior

### Reference storage model and transactions

`MemoryImportState` contains only frozen/copy-on-write internal records equivalent to the SQLite
tables:

- aliases keyed by `(requesting_writer_id, request_id)` with request digest, source-identity
  digest, and creation metadata;
- jobs keyed uniquely by `ImportSourceIdentity.identity_digest`, carrying the immutable source/
  session/profile/mapping/publishing-writer identity and the exact mutable state/phase/lease/
  plan/report/terminal fields in `ImportAllocation`;
- batch rows keyed by `(identity_digest, batch_index)` with stable request/event IDs, plan object
  ref/digest, state `planned|complete`, and optional canonical `AppendResult`/digest/frontiers.

No source line, argv/cwd/stderr text, candidate payload, raw SHA audit digest, exception string,
or key material exists in this state. Source metadata, candidates/line outcomes, and reports live
in finalized objects exactly as they do for SQLite.

Every logical transaction acquires the shared lock, checks the current runtime generation and
lease against one captured `ClockPort.now()`, clones only affected records, validates every CHECK-
equivalent invariant, and swaps the new records/revision as one non-awaiting commit. Object/
ledger/parser/clock/ID calls never occur inside the lock. A fault before the swap leaves the old
state; a fault after it simulates committed response loss. Returned values are reconstructed from
the committed record, not from a mutable object held across the lock.

Lease validity is exactly `owner_generation == current fence generation` **and**
`lease_expires_at > captured_now`. Reclaim increments `lease_generation`; stale owner generation
is immediately reclaimable regardless of time. Every mutator constant-time compares source/job/
publisher/lease identity. There is no background lease reaper.

### Exact source capture

`capture` consumes the two one-shot `ImportByteSource` values outside task-state transactions:

1. Resolve the exact Codex capability profile before reading. Unsupported profile/metadata fails
   without any job state.
2. Read source once with declared-size precheck and cap-plus-one detection. Always call the source's
   idempotent close in `finally`. Preserve byte order/line endings/final partial line exactly;
   compute line/final-newline counts without decoding.
3. Compute the ordinary source SHA-256 only for encrypted audit metadata. Stage/finalize one
   `ObjectKind.import_source`; its returned kind-domain keyed commitment is the sole structural
   source equality value.
4. Run `sanitize_codex_argv`. Convert cwd identity input through the bundle-keyed commitment
   helper; never normalize/open it. Build bounded safe metadata fields.
5. If stderr exists, read at most 64 KiB plus one byte, record present/captured count/truncated,
   compute `ObjectKind.import_stderr` through `objects.commitment_for` over the retained prefix,
   compute its ordinary digest only for audit metadata, then discard all stderr bytes. Never call
   `stage` for this commitment-only kind and never drain an unbounded remainder.
6. Encode source/stderr audit digests, sanitized argv/cwd audit material, exact object ref, and
   safe capture fields into one encrypted `ObjectKind.import_source_manifest` object; finalize it.
7. Return `CapturedImportSource` only after both objects are finalized/authentic. Failures may
   leave finalized unreferenced memory objects, matching durable orphan semantics, but create no
   alias/job.

The capture value's structural `metadata_digest` covers only the safe allowlisted metadata fields,
not encrypted audit text. Its redacted representation and all faults expose counts/codes only.

### Reservation, aliases, and frozen publisher

`reserve_or_resume` first recomputes `ImportSourceIdentity` from command/task/profile/mapping and
the captured source commitment. In one transaction:

1. An existing alias with different request digest/source identity raises
   `IDEMPOTENCY_CONFLICT`; same alias follows its job.
2. A terminal complete job returns `replayed` and its original report. A quarantined job replays
   its stable quarantine outcome.
3. A pending job reached by another requesting writer raises `OPERATION_PENDING` unless it is
   already terminal. The job's first `publishing_writer_id` never changes.
4. A live current lease raises `OPERATION_PENDING`; expired/stale-generation lease is reclaimed
   by generation-fenced CAS and returns `resumed` at the recorded phase.
5. No job inserts the alias and a new `pending/source_reserved` job atomically with source refs,
   publishing writer, generation, lease generation 1, and expiry; returns `reserved`.

Same source under a new request/writer resolves the same job because job uniqueness is the source-
identity digest. Different safe nonmapping metadata records only the bounded
`source_already_imported_metadata_differs` replay warning; it does not replace capture metadata or
produce a second plan.

### Pure parsing, materialization, and plan publication

`prepare_plan` requires a current `source_reserved` allocation. It snapshots/verifies job identity
under the lock, then outside it:

1. opens/authenticates the exact source and capture-metadata objects and rechecks size/commitment;
2. calls the exact profile's `parse_codex_jsonl` and `plan_codex_mapping` with the registered line/
   aggregate limits;
3. allocates fresh UUIDv4-backed event/action/result IDs for every template local key plus the
   final report request/event/evidence IDs via `IdPort`;
4. materializes once and deterministically partitions ordered candidates under both
   `MAX_EVENTS_PER_BATCH` and `MAX_CANONICAL_REQUEST_BYTES` by measuring the exact future
   `PublishWorkRequest` canonical bytes; more than 1,024 batches is `LIMIT_EXCEEDED`;
5. stage/finalize one `import_plan` object per batch containing candidate drafts, line outcomes,
   gaps, local-to-Yoetz ID map, mapping/profile identities, and source ranges; build the bounded
   structural manifest and `plan_digest`.

It returns a provisional `PreparedImportPlan`; it does not mutate state. Repeated calls before a
successful publication may allocate different IDs/objects, which remain harmless orphans.

`publish_plan` uses one transaction to reverify phase/lease/source, validate every object ref/
digest/count/ID/cap, and atomically install all batch rows plus final report IDs and
`plan_digest`, advancing to `plan_ready`. If an identical plan is already recorded, return it
idempotently. Any different plan at or after `plan_ready` is a contradiction suitable for
quarantine, never replacement.

### Batch selection and recording

`next_batch` accepts `plan_ready|publishing`. In one transaction it selects the lowest planned
index, verifies the fixed publisher and lease, renews the lease so at least 30 seconds remain,
and returns a refreshed allocation plus an immutable row snapshot. If no row remains, it returns
the refreshed allocation with `batch=None`.

Outside the lock, it opens/verifies the snapshot's `import_plan` object, reconstructs exact
`EventDraft` values, remeasures caps, and returns `ImportBatch`. A verification failure is storage
corruption; no later batch is skipped. A lease that cannot be safely renewed returns
`OPERATION_PENDING` before payload exposure.

`record_batch` receives only the corresponding terminal/replayed `AppendResult`. Before locking it
verifies canonical shape and ordered event IDs. In one transaction it verifies the same row/job/
lease, then:

- identical already-complete result returns idempotently;
- different result/event/frontier for a completed row is contradiction;
- otherwise stores canonical result/digest/frontiers, marks complete, increments the exact count,
  and advances first completion to `publishing`.

A fault after ledger commit but before this state commit leaves the row planned. Retry selects the
same stable plan IDs; normal `publish_work` replay yields the same `AppendResult`, then this method
heals the state.

### Report-ready checkpoint and terminal completion

`prepare_report` requires all batches complete and phase `plan_ready|publishing`. Outside the lock
it authenticates the proposed `import_report`, decodes its safe structural terminal result, and
recomputes source/plan/batch counts/digests/frontiers. It builds the one exact importer-authored
`evidence_recorded(import_report)` draft using preallocated IDs. In one transaction it pins the
exact report ref/digest, safe result, canonical evidence draft/bytes/digest, renews the lease, and
advances to `report_ready`.

Identical retry returns the pinned allocation. A different object/draft at `report_ready` is
contradiction. Thus a crash after report evidence commits cannot rebuild a randomized object under
the same event ID.

`publish_report` verifies outside the lock that the `AppendResult` accepted/replayed exactly the
pinned report event and object/digest. One transaction stores its canonical result/evidence
locator and advances `report_ready -> report_published`; identical retry is a no-op.

`complete` authenticates the pinned report outside the lock, then one transaction rechecks all
batch/report/evidence facts, sets `complete/terminal`, records the stable terminal envelope/digest,
clears lease fields, and returns the terminal allocation. Only then may application code
acknowledge. Same-source aliases always replay this original report.

`quarantine` is one transaction from a current leased pending job. It accepts only the seven
frozen importer contradiction/commit-ambiguity codes owned by the bundle migration, writes the
stable safe terminal envelope/digest, clears leases, and sets
`quarantined/terminal`. Parse gaps, unsupported source categories, cancellation, key unavailability,
or ordinary partial progress are never quarantine reasons.

### Status, atomic operation gate, and review

`status(session_id)` snapshots under the shared task lock and returns bounded counts plus up to 64
active identities/phases/batch counts and terminal report evidence locators. Sorting is active
first by identity digest, then terminal reports by terminal sequence/digest. It contains no object
plaintext/path/capture text.

The same `MemoryImportState` and lock are visible to `MemoryLedgerAdapter` only through a private
`has_pending_import(session_id)` predicate. Ledger `freeze_case`, final check commit, and receipt
append invoke that predicate inside their own atomic state transition; preflight `status` is an UX
optimization, not the correctness boundary.

`MemoryImportState` also owns both directions of the permanent publication reservation relation:
`(publishing_writer_id, request_id) -> (source_identity_digest, ordinal)` and
`(source_identity_digest, ordinal) -> (publishing_writer_id, request_id)`. Plan publication swaps
all batch reservations plus the final-report ordinal atomically with the plan. Ledger append checks
the same maps together with its operation map under the shared lock. Completion and quarantine do
not remove reservations.

`load_review_source(identity, through)` snapshots the exact job/batch rows under the lock; absent
returns `None`, quarantined/contradictory state raises `STORAGE_CORRUPT`. It verifies source/plan/
report objects and accepted batch/report events outside the lock, rejects any selected event past
`through`, then returns the bounded structural `ImportReviewSource`. Pending jobs include only
complete batches and are marked `import_incomplete`; planned candidates never appear observed.
It acquires no lease and mutates nothing.

## Errors and edge cases

- It produces the same public codes as `ports/importer.md`: `INVALID_REQUEST`, `LIMIT_EXCEEDED`,
  `IDEMPOTENCY_CONFLICT`, `OPERATION_PENDING`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`,
  `STORAGE_CORRUPT`, and `MIGRATION_REQUIRED` where applicable.
- Awaiting a memory object/ledger call while holding `transaction_lock` is a test failure; this
  prevents the reference model from hiding transaction/IO mistakes unavailable to SQLite.
- Empty valid stream creates a zero-candidate plan, opaque/report gaps, and final report evidence.
- Cancellation before a state swap changes nothing; cancellation after a swap is committed
  response loss and same-call retry resolves it.
- State/fault diagnostics use only identities, phases, indexes, counts, and fault-point enums.
  Source/argv/cwd/stderr/payload/ordinary audit digest never appears.

## Invariants

1. Memory and SQLite adapters return byte-equivalent structural values for identical fixtures,
   clocks, IDs, keys, and injected fault sequence.
2. One source identity owns one immutable publishing writer, plan/event set, and terminal report.
3. Every phase change is one atomic copy-on-write state swap under the shared task lock.
4. Parser/object/ledger/clock/ID work never occurs while that lock is held.
5. The adapter never appends events directly; batches and report evidence use normal publication.
6. Pending import gating is atomic with memory check freeze/finalization and receipt append.
7. Process-local storage weakens durability only across process exit, never observable protocol
   semantics within a conformance run.

## Tests

- `specs/tests/unit.md`: every method/state/phase guard, source cap/stderr discard, alias/source
  dedupe, publisher freeze, lease/reclaim, batching, report-ready identity, review/status bounds,
  no-await-under-lock assertion, redacted state/reprs.
- `specs/tests/conformance.md`: run the shared importer state-machine suite against
  `MemoryImporter` and `SqliteImporter` with byte-equivalent allocations/plans/selections/results/
  reports/status/review snapshots.
- `specs/tests/property.md`: generated source lines, aliases, lease times, generation changes,
  fault points, and replay sequences preserve one event set and monotonic phases.
- `specs/tests/integration/application/test_import_review.py.md`: memory application publishes
  every batch/report through `MemoryLedgerAdapter` and compares the frozen review result.
- `specs/tests/integration.md`: plaintext/digest canaries appear only inside approved memory object
  envelopes and never in structural state, errors, fault traces, or reprs.

## Open questions

None.

The numeric policy values above are the v0.1 conformance defaults and the durable adapter
must use the same release constants.
