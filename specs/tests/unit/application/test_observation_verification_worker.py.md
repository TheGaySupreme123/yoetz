# tests/unit/application/test_observation_verification_worker.py — durable worker state

**Wave:** D | **ADRs:** ADR-003, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/sqlite/observation_verification.md` |
**Imported by:** unit suite

## Purpose

Prove rapid-state coalescing, identical-state caching, generation recovery, and immutable result
completion.

## Public surface

Pytest unit cases over a migration-3 in-memory task database.

## Behavior

Enqueue two states and inspect stale/latest statuses; claim under one generation, recover under the
next, complete once, and prove no remaining work.

## Errors and edge cases

Lease recovery is tested before expiry when service generation changes.

## Invariants

No real command or network operation runs.

## Tests

This file.

## Open questions

None.
