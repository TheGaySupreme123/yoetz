# tests/conformance/protocol/test_idempotency_and_frontiers.py — idempotency and frontier contracts

**Wave:** A/B/C/D | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `src/yoetz/kernel/projections.md`
**Imported by:** conformance protocol tests

## Purpose

Prove logical request identity, replay identity, and frontier handling stay consistent across all
public operations.

## Public surface

- `test_retry_same_identity_returns_same_public_result` — same identity, same result.
- `test_changed_identity_conflicts_under_same_request_id` — changed logical intent conflicts.
- `test_frontier_handling_matches_operation_contract` — current/expected/stale frontiers behave as
  specified.

## Behavior

The test varies request IDs, request digests, and frontier values and asserts:

- idempotent retries return the stored result;
- logical identity changes under the same request ID fail closed;
- frontier handling is exact and operation-specific;
- no operation claims a newer frontier than it actually observed.

## Errors and edge cases

- A stale frontier accepted as current fails.

## Invariants

1. Request identity is stable.
2. Frontiers are exact.
3. Replay returns the stored result.

## Tests

- `tests/conformance/protocol/test_idempotency_and_frontiers.py`

## Open questions

None.
