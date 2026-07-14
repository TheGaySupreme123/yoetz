# tests/conformance/adapters/test_ledger_port.py — ledger port contract parity

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):**
`src/yoetz_core/ports/ledger.md`, `tests/conformance/protocol/test_idempotency_and_frontiers.py.md`
**Imported by:** conformance adapter tests

## Purpose

Prove the reference and durable ledger ports expose the same public behavior for append, freeze,
commit, and lookup operations.

## Public surface

- `test_append_batch_contract` — append behavior, conflicts, and idempotency match across backends.
- `test_load_and_freeze_contract` — event loading and frozen-case construction match across backends.
- `test_commit_check_if_current_contract` — check commit semantics match across backends.
- `test_lookup_operation_contract` — operation lookup and replay visibility match across backends.

## Behavior

The test runs each port scenario against memory and SQLite backends with the same injected clocks,
IDs, and policy/provider scripts. It asserts:

- equal logical input yields equal public result;
- canonical bytes, digests, and frontier transitions are stable;
- adapter diagnostics may differ, but public behavior does not;
- unsupported/private row details never become part of the comparison oracle.

## Errors and edge cases

- A port-specific shortcut that changes public output fails.
- A comparison that includes private row IDs instead of public artifacts fails.

## Invariants

1. Ledger port behavior is adapter-neutral.
2. Public canonical artifacts are the comparison oracle.
3. Private adapter details stay private.

## Tests

- `tests/conformance/adapters/test_ledger_port.py`

## Open questions

None.
