# tests/property/test_ledger_state_machine_memory.py — in-memory ledger state machine

**Wave:** B–C | **ADRs:** ADR-001 through ADR-004 | **Imports (spec-tree):**
`src/yoetz/adapters/memory/ledger.md`, `src/yoetz/adapters/memory/start_catalog.md`,
`src/yoetz/adapters/memory/objects.md`
**Imported by:** property-based conformance tests

## Purpose

Exercise the pure in-memory reference model as the state-machine oracle for ledger behavior.

## Public surface

- `test_memory_state_machine_start_paths` — reserve/attach/restore paths obey the model.
- `test_memory_state_machine_append_and_freeze` — event append and case freeze stay contiguous.
- `test_memory_state_machine_check_and_receipt` — check/receipt operations honor frontier rules.
- `test_memory_state_machine_recovery_paths` — crash/reclaim/quarantine paths are bounded.

## Behavior

The in-memory state machine must model:

- start lifecycle transitions;
- batch append, replay, and projection updates;
- case freezing and check commitment;
- receipt persistence and replay;
- recovery behavior after failed or interrupted operations.
- every successful phase/renew/reclaim spends the prior `FrozenCase.lease` and returns the one
  replacement authority; and
- generated mutations between prepare and final reservation prove that head, projection identity,
  dependencies, import state, idempotency, and owner generation are all revalidated in the one
  locked state swap; a mismatch stores no operation pointer;
- instrumentation proves case construction and object finalization occur in that order outside the
  shared lock, and reclaim reads the stored object with builder/publisher calls forbidden; and
- a final frontier/dependency mismatch appends nothing and terminally replays the same
  `FRONTIER_CONFLICT`, never a memory-only stale success.

The model is the contract oracle. It does not copy SQLite code or depend on filesystem behavior.

## Errors and edge cases

- A rule that silently skips a durability transition fails.

## Invariants

1. The memory model is pure and deterministic.
2. Stateful transitions stay explicit.
3. Recovery paths remain bounded.
4. The state machine never holds a frozen case with absent or spent authority.
5. The state swap can reference only an already-durable object built from the prepared exact case.

## Tests

- `tests/property/test_ledger_state_machine_memory.py`

## Open questions

None.
