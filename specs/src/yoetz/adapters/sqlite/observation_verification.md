# src/yoetz/adapters/sqlite/observation_verification.py — verification job repository

**Wave:** D | **ADRs:** ADR-003, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/migrations/bundle/0003.sql.md`,
`specs/src/yoetz/application/observation_verification.md` |
**Imported by:** task observation store and ready verification worker

## Purpose

Own durable enqueue, coalescing, lease recovery, completion, and cache identity for observation
verification without coordinator SQL.

## Public surface

`SqliteObservationVerificationRepository.enqueue_latest`, `claim_next`, and `complete`.

## Behavior

Newer subject state stales older pending work. Exact workspace/policy/approval/state identity
deduplicates. Claim selects a latest eligible job while no workspace sibling runs and fences it to
service generation/owner/expiry. New generation or expired lease requeues abandoned work.
Completion verifies the exact lease, inserts an immutable result, and marks current or stale.

## Errors and edge cases

Stale completion raises retryable session conflict. Missing policy authority is handled by the
worker and never fabricates a result. Running results after state drift persist non-current.

## Invariants

1. At most one running job per workspace is selected.
2. Completed result rows are immutable.
3. Cache identity includes exact policy and subject state.

## Tests

`tests/unit/application/test_observation_verification_worker.py` and migration tests.

## Open questions

None.
