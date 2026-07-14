# tests/integration/storage/test_start_catalog_state_machine.py — start catalog phase machine

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/start_catalog.md`, `src/yoetz_core/application/start.md`
**Imported by:** integration storage tests

## Purpose

Prove the durable start catalog obeys the reserve/resume/complete/quarantine phase machine.

## Public surface

- `test_reserve_resume_and_complete_paths` — the normal lifecycle is stable.
- `test_crash_and_reclaim_paths` — interrupted operations are reclaimable.
- `test_attachment_conflict_and_quarantine_paths` — conflicting attachments are quarantined.
- `test_expiry_and_stale_generation_reclaim` — expired or stale owners lose authority.

## Behavior

The test drives the catalog through every durable phase transition and checks:

- each phase writes the expected durable row state;
- retries return the same stable route or completion state;
- stale owner generations cannot advance state;
- quarantine records are final for the conflicted bundle.

## Errors and edge cases

- A phase machine that skips directly to completion fails.
- A stale generation that can still mutate the catalog fails.

## Invariants

1. Start state is durable and replayable.
2. Ownership is generation-bound.
3. Quarantine is final for the conflicting route.

## Tests

- `tests/integration/storage/test_start_catalog_state_machine.py`

## Open questions

None.
