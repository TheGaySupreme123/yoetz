# src/yoetz/adapters/memory/ledger.py — in-memory reference ledger adapter

**Wave:** C | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`ports/ledger.md`, `domain/events.md`, `domain/findings.md`, `domain/receipts.md`,
`domain/values.md`, `kernel/reducers.md`, `kernel/projections.md`,
`adapters/memory/importer.md` (shared task state/lock contract) |
**Imported by:** conformance tests and adapter-parity fixtures

## Purpose

This file defines the in-memory reference implementation of the ledger port. It exists so the
conformance suite can prove the ledger semantics independently of SQLite, file systems, and
checkpoint mechanics.

## Public surface

- `MemoryLedgerAdapter` implements every method of `LedgerPort` with the exact port annotations:
  - `append_batch(command: AppendCommand) -> AppendResult`
  - `load_events(session_id: SessionId, *, after: int = 0, through: int | None = None) -> AsyncIterator[AcceptedEvent]`
  - `load_projection(session_id: SessionId, view: ProjectionView) -> StoredProjection | None`
  - `query_projection(query: ProjectionQuery) -> ProjectionPage`
  - `freeze_case(session_id: SessionId, writer_id: str, expected_frontier: int | None, request_id: str, request_digest: str) -> FrozenCase`
  - `advance_check_phase(lease: OperationLease, expected_phase: CheckPhase, next_phase: CheckPhase, durable_object_ref: ObjectRef | None = None) -> OperationLease`
  - `enqueue_semantic_job(lease: OperationLease, case_digest: str, case_object_ref: ObjectRef) -> SemanticJobRecord`
  - `claim_semantic_job(lease: OperationLease, job_id: str) -> SemanticAttemptHandle`
  - `record_attempt_outcome(handle: SemanticAttemptHandle, outcome: AttemptOutcome, result_object_ref: ObjectRef | None = None, terminal_code: SemanticReason | None = None) -> None`
  - `select_attempt(lease: OperationLease, handle: SemanticAttemptHandle, selected_result_object_ref: ObjectRef) -> SelectedAttempt`
  - `renew_leases(lease: OperationLease) -> OperationLease`
  - `reclaim_operation(writer_id: str, operation_id: str, request_digest: str) -> OperationLease | PendingVerdict`
  - `commit_check_if_current(frozen: FrozenCase, findings: RankedFindings, semantic_status: SemanticStatus, semantic_reason: SemanticReason, semantic_provenance: SemanticProvenance | None, request_id: str) -> CheckResult`
  - `lookup_operation(writer_id: str, operation_id: str) -> OperationRecord | None`
- `MemoryLedgerState` is the injected copy-on-write ledger/projection/operation/job/attempt state.

Constructor contract: `MemoryLedgerAdapter(*, task_id, ownership_fence, state:
MemoryLedgerState, import_state: MemoryImportState, transaction_lock, clock, ids, objects)`. The
same `import_state` and `transaction_lock` are supplied to `MemoryImporter`; independently locked
instances are forbidden because they could not model the SQLite atomic pending-import gate.

## Behavior

The memory adapter mirrors the SQLite ledger semantics, not its implementation details. It holds:

- accepted events in append order;
- projections keyed by session and view;
- operation records and idempotency state;
- frozen-case snapshots for check paths;
- semantic job and immutable-attempt records plus lease/selection indexes;
- any stored receipt or result metadata needed by the conformance suite.

Every mutating method is a non-awaiting copy-on-write state swap under the shared task transaction
lock. Ledger/import state is observed from one lock acquisition; reducers, object work, clock/ID
calls, and canonicalization happen outside it and are revalidated before swap.

`append_batch(command)` validates that the batch is structurally acceptable, rejects conflicts
atomically, and updates the in-memory projections exactly as the reducer would. It must preserve
duplicate-request, frontier-conflict, and invalid-event behavior just like the durable adapter.
After terminal idempotency replay but before a new receipt append, the same locked section checks
that `import_state` has no `pending` job for the command session. A hit returns retryable
`OPERATION_PENDING` and swaps nothing; non-receipt publication, including import batches/report
evidence, is unaffected.

`load_events(session_id, after, through)` yields the accepted events for the given session in canonical order. It may page
internally, but it never mutates the state while reading.

`load_projection(session_id, view)` returns a snapshot object compatible with the port contract, or `None` if
the projection has never been built.

`query_projection(query)` freezes the requested/head/effective frontiers under the shared lock,
copies at most `query.limit + 1` matching typed rows in the view's registered stable order, and
returns the same `ProjectionPage` fields and exclusive next typed position as SQLite. It validates
the query-bound cursor position and projection version before reading. It never snapshots the
whole adapter merely to page one view, and it swaps no state.

`freeze_case(session_id, writer_id, expected_frontier, request_id, request_digest)` captures the current frontier and the admissible evidence set for a check. It is
the reference model’s version of the durable case freeze. For a new/resumed nonterminal check it
tests the same session's pending-import set under the shared lock before storing the freeze.

`commit_check_if_current(frozen, findings, semantic_status, semantic_reason,
semantic_provenance, request_id)` publishes findings only if the current frontier still matches the
frozen one. If the frontier moved, the check result must weaken to a stale/conflict response rather
than silently applying to the wrong state. It repeats the pending-import predicate in the same
locked state swap that appends the check/finding events and terminal result.

`lookup_operation(writer_id, operation_id)` returns the remembered operation record for idempotency and retry tests. It
does not invent a durable history beyond the current process lifetime.

The seven orchestration methods use the exact phase, job, attempt, selection, renewal, and reclaim
tables in `ports/ledger.md`. Each mutation prepares a replacement state outside the shared lock,
then under one lock revalidates the complete operation/job lease, owner generation, phase,
frontier, dependency digest, and object presence before one copy-on-write swap. In particular:

- `advance_check_phase` accepts only the registered forward edge and binds a required durable
  object reference on the same swap;
- enqueue is unique on `(writer_id, operation_id, case_digest)`; claim creates one immutable
  attempt ordinal and fences an expired active attempt exactly like SQLite;
- outcome recording applies the closed transition/argument-shape table; selection atomically
  changes the attempt and job and stores one identical result reference;
- renewal never extends a child job past its operation; and
- `reclaim_operation` returns the exact `absent/live/terminal/quarantined` `PendingVerdict`, or
  increments the lease generation and returns the new `OperationLease` after an expired/stale
  compare-and-swap. A different digest raises `IDEMPOTENCY_CONFLICT`; the method never creates a
  missing operation.

## Errors and edge cases

- The adapter must reject the same invalid inputs as SQLite would reject at the port boundary.
- A stale frozen case is not committed as current.
- Duplicate request IDs resolve to the same terminal record when the semantic result is stable.
- Terminal same-digest check/receipt replay precedes the pending-import predicate because replay
  commits no new observation.
- The adapter is not a persistence substitute; process exit discards state.

## Invariants

1. Same inputs, same outputs, same order as the durable adapter.
2. The memory adapter never changes contract semantics to make tests easier.
3. Projection output must match the reducer contract byte-for-byte in the fixed vectors.
4. It remains side-effect free outside its own object instance.
5. Check freeze/finalization and receipt append cannot commit while a pending import for the same
   session exists; the predicate and state mutation share one task lock.

## Tests

- `tests/conformance/adapters/test_ledger_port.py` — SQLite vs memory append/freeze/commit parity.
- `tests/property/test_ledger_state_machine_memory.py` — retry/conflict/replay sequences.
- `specs/tests/conformance.md` — deterministic import-start/finish races at check freeze,
  check final commit, and receipt append match SQLite exactly.

## Open questions

None.
