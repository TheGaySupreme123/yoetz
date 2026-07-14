# tests/property/test_ledger_state_machine_memory.py — in-memory ledger state machine

**Wave:** B–C | **ADRs:** ADR-001 through ADR-004 | **Imports (spec-tree):**
`src/yoetz_core/adapters/memory/ledger.md`, `src/yoetz_core/adapters/memory/start_catalog.md`,
`src/yoetz_core/adapters/memory/objects.md`
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

The model is the contract oracle. It does not copy SQLite code or depend on filesystem behavior.

## Errors and edge cases

- A rule that silently skips a durability transition fails.

## Invariants

1. The memory model is pure and deterministic.
2. Stateful transitions stay explicit.
3. Recovery paths remain bounded.

## Tests

- `tests/property/test_ledger_state_machine_memory.py`

## Open questions

None.
