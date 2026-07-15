# tests/property/test_ledger_state_machine_sqlite.py — SQLite-backed ledger state machine

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/sqlite/repository.md`, `src/yoetz/adapters/sqlite/start_catalog.md`,
`src/yoetz/adapters/sqlite/migrations.md`
**Imported by:** property-based conformance tests

## Purpose

Exercise the SQLite implementation against the same state-machine rules as the memory oracle.

## Public surface

- `test_sqlite_matches_memory_start_and_append_rules` — lifecycle and append rules agree with the
  pure model.
- `test_sqlite_projection_and_recovery_rules_match_model` — freeze, replay, and recovery match the
  oracle.
- `test_sqlite_owner_generation_and_crash_rules_match` — lease/generation rules remain explicit.
- `test_recovery_classification_is_stable_for_identical_evidence` — repeated inspection cannot
  oscillate among recovery states or widen the exposed safe facts.

## Behavior

The SQLite state machine must satisfy the same public transitions as the memory model while also
proving the durable behaviors that only SQLite can enforce, such as transaction boundaries and
rebuild/recovery behavior. Generated recovery evidence covers clean, rebuildable projection,
quarantined canonical data, incomplete migration, stale generation, and corrupt object-reference
states; inspecting an unchanged state repeatedly must return the same classification and bounded
reason facts without mutation.

## Errors and edge cases

- A SQLite-only shortcut that violates the memory oracle fails.
- A recovery classifier that changes state without changed durable evidence fails.

## Invariants

1. SQLite matches the memory oracle on public behavior.
2. Durability rules remain explicit.
3. Recovery is replayable.
4. Recovery classification is a pure function of the inspected durable evidence.

## Tests

- `tests/property/test_ledger_state_machine_sqlite.py`

## Open questions

None.
