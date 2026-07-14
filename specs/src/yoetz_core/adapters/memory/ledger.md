# src/yoetz_core/adapters/memory/ledger.py — in-memory reference ledger adapter

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

| Name | Signature (natural language) |
|---|---|
| `MemoryLedgerAdapter` | in-memory implementation of `LedgerPort` |
| `MemoryLedgerState` | injected copy-on-write ledger/projection/operation state |
| `append_batch(...)` | append accepted events and update projections in memory |
| `load_events(...)` | stream accepted events for a session from memory |
| `load_projection(...)` | return the current projection snapshot |
| `freeze_case(...)` | capture a durable case/frontier snapshot for deterministic check work |
| `commit_check_if_current(...)` | atomically publish a check result if the frozen frontier is still current |
| `lookup_operation(...)` | read the recorded operation status for idempotency and recovery tests |

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
- any stored receipt or result metadata needed by the conformance suite.

Every mutating method is a non-awaiting copy-on-write state swap under the shared task transaction
lock. Ledger/import state is observed from one lock acquisition; reducers, object work, clock/ID
calls, and canonicalization happen outside it and are revalidated before swap.

`append_batch(...)` validates that the batch is structurally acceptable, rejects conflicts
atomically, and updates the in-memory projections exactly as the reducer would. It must preserve
duplicate-request, frontier-conflict, and invalid-event behavior just like the durable adapter.
After terminal idempotency replay but before a new receipt append, the same locked section checks
that `import_state` has no `pending` job for the command session. A hit returns retryable
`OPERATION_PENDING` and swaps nothing; non-receipt publication, including import batches/report
evidence, is unaffected.

`load_events(...)` yields the accepted events for the given session in canonical order. It may page
internally, but it never mutates the state while reading.

`load_projection(...)` returns a snapshot object compatible with the port contract, or `None` if
the projection has never been built.

`freeze_case(...)` captures the current frontier and the admissible evidence set for a check. It is
the reference model’s version of the durable case freeze. For a new/resumed nonterminal check it
tests the same session's pending-import set under the shared lock before storing the freeze.

`commit_check_if_current(...)` publishes findings only if the current frontier still matches the
frozen one. If the frontier moved, the check result must weaken to a stale/conflict response rather
than silently applying to the wrong state. It repeats the pending-import predicate in the same
locked state swap that appends the check/finding events and terminal result.

`lookup_operation(...)` returns the remembered operation record for idempotency and retry tests. It
does not invent a durable history beyond the current process lifetime.

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
