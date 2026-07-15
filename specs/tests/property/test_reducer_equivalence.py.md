# tests/property/test_reducer_equivalence.py — replay equivalence under partitioning

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`tests/property/strategies/events.py`, `src/yoetz/kernel/reducers.py`,
`src/yoetz/kernel/projections.py`
**Imported by:** property-based reducer tests

## Purpose

Prove that reducer replay is partition-independent and matches the full reference model.

## Public surface

- `test_full_vs_partitioned_replay_match` — any partitioning yields the same projection.
- `test_incremental_replay_matches_reference_model` — replay matches the pure model.
- `test_unknown_event_and_redaction_paths_weaken_only` — gaps weaken state only.

## Behavior

The property suite generates event streams and splits them across arbitrary boundaries to verify:

- the same ordered accepted stream yields the same projection state;
- unknown and redaction events weaken coverage without inventing facts;
- the reducer remains pure under repeated application.

## Errors and edge cases

- A partition-sensitive projection fails the property.

## Invariants

1. Replay is order-sensitive but partition-insensitive.
2. Unknown events weaken only.
3. The pure model and SUT remain equivalent.

## Tests

- `tests/property/test_reducer_equivalence.py`

## Open questions

None.
