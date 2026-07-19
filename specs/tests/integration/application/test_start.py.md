# tests/integration/application/test_start.py — start operation end-to-end

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/adapters/sqlite/start_catalog.md`,
`src/yoetz/adapters/objects/encrypted_files.md`
**Imported by:** integration application tests

## Purpose

Prove the public `start` operation can reserve or attach a workspace, publish a durable start
result, and remain idempotent on retry.

## Public surface

- `test_create_replays_exact_result_without_reopening_runtime` — terminal same-ID retry returns the
  exact stored unprojected result without reopening the task runtime.
- `test_matching_refs_attach_and_same_title_without_refs_stays_distinct` — committed reference
  identity attaches, title-only equality does not, and lifecycle events are opened/resumed once.
- `test_result_published_crash_resumes_pinned_object_and_releases_each_runtime` — a crash after
  publication reclaims the lease, resolves the exact ID+envelope, and never republishes the result.
- `test_sqlite_and_encrypted_files_resume_exact_catalog_pinned_object` — the production-shaped
  SQLite catalog and encrypted-files store persist/reopen the same pinned bytes and frontier.

## Behavior

The test exercises the full start catalog and result publication path, then asserts:

- allocation, route, and session IDs are stable and durable;
- retries return the stored result rather than re-allocating state;
- the after-publication crash window remains recoverable from the four pinned response values;
- the persisted result is the exact bounded `StartInternalResult`, without privacy projection;
- every admitted task runtime usage reference is released.

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
