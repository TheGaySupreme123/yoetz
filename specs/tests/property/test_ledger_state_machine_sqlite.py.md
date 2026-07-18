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

Generated check transitions also assert the exact current `FrozenCase.lease` handoff and the one
terminal final-currentness rule: changed frontier/dependency stores and replays
`FRONTIER_CONFLICT`, clears the lease, and appends no check/finding event.

Freeze generation injects crashes and competing writes at prepare, after case build, after object
finalization, before `BEGIN IMMEDIATE`, and after ambiguous COMMIT. It asserts the case-build event
strictly precedes object finalization; accepted-record paging/replay/canonicalization/encryption/
fsync/object-open hooks are never called inside `BEGIN IMMEDIATE`; and the final reservation
rechecks idempotency, import state, head, projection identity, dependencies, expected frontier,
owner generation, and finalized object inventory before inserting one pointer. A failed check
leaves no object inventory or operation row. Once a pointer commits, expiry/stale-generation
reclaim opens and verifies that exact object while builder/publisher hooks are configured to fail
if called.

## Errors and edge cases

- A SQLite-only shortcut that violates the memory oracle fails.
- A recovery classifier that changes state without changed durable evidence fails.

## Invariants

1. SQLite matches the memory oracle on public behavior.
2. Durability rules remain explicit.
3. Recovery is replayable.
4. Recovery classification is a pure function of the inspected durable evidence.
5. SQLite has no adapter-specific stale-success branch.
6. SQLite never reserves a resume object before its exact frozen case exists.

## Tests

- `tests/property/test_ledger_state_machine_sqlite.py`

## Open questions

None.
