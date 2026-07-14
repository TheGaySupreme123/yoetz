# tests/integration/application/test_publish_work.py — publish-work end-to-end

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/publish_work.md`, `src/yoetz_core/adapters/sqlite/repository.md`,
`src/yoetz_core/adapters/objects/encrypted_files.md`
**Imported by:** integration application tests

## Purpose

Prove `publish_work` persists a whole batch atomically and updates the projection and receipt inputs
exactly once.

## Public surface

- `test_atomic_batch_commit_and_replay` — the whole batch lands or none does.
- `test_known_and_unknown_event_paths` — known events commit, unknown events preserve gaps.
- `test_expected_frontier_and_identity_conflicts_fail_closed` — conflicts are explicit.

## Behavior

The test feeds the public operation with a reviewed batch and checks:

- event IDs, frontiers, and logical request identity are exact;
- publication is atomic across the full batch;
- accepted events replay to the same projection and receipt inputs;
- unknown events are preserved as opaque data, not interpreted as known facts.

## Errors and edge cases

- A partially committed batch fails.
- A changed logical request under the same request ID fails.

## Invariants

1. Batch publication is atomic.
2. Retry identity is stable.
3. Unknown events remain opaque.

## Tests

- `tests/integration/application/test_publish_work.py`

## Open questions

None.
