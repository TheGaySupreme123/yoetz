# tests/integration/application/test_start.py — start operation end-to-end

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/adapters/sqlite/start_catalog.md`,
`src/yoetz/adapters/objects/encrypted_files.md`
**Imported by:** integration application tests

## Purpose

Prove the public `start` operation can reserve or attach a workspace, publish a durable start
result, and remain idempotent on retry.

## Public surface

- `test_create_attach_and_restore_paths` — the supported start modes work end to end.
- `test_idempotent_resume_returns_same_result` — same request identity returns same result.
- `test_crash_before_and_after_publish_is_recoverable` — failures are durable and replayable.

## Behavior

The test exercises the full start catalog and result publication path, then asserts:

- allocation, route, and session IDs are stable and durable;
- retries return the stored result rather than re-allocating state;
- crash windows before and after result publication remain recoverable;
- the published start result is exact and bounded.

## Errors and edge cases

- A start operation that reallocates on identical identity fails.
- A result that changes after retry fails.

## Invariants

1. Start is idempotent.
2. Route and result publication are durable.
3. Recovery preserves identity.

## Tests

- `tests/integration/application/test_start.py`

## Open questions

None.
