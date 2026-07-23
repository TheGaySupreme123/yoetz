# src/yoetz/adapters/sqlite/observation.py — durable ObservationPort over migrations 0002/0003

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`ports/observation.py.md`, `domain/observation.py.md`, `adapters/sqlite/migrations.md` |
**Imported by:** observation control handlers, migration/adapter tests

## Purpose

Provide a SQLite-backed `TaskObservationPort` against migrations `0002` and `0003`, so consent,
cursors, dedup, structural envelopes, encrypted-content metadata, logical identity, exact policy
trust, verification, and advice state survive restart without plaintext transcript spool.

## Public surface

- `SqliteObservationStore` — the five async `ObservationPort` methods plus repository-owned
  content-manifest, workspace-binding, logical-identity, trust, verification-fact, advice-history,
  and advice-snapshot operations.

## Behavior

Fail closed without active consent. Revoke deactivates the locator, revokes trust, and retains
encrypted evidence. Identical envelopes are idempotent duplicates. A logical-identity claim binds
workspace, normalized identity, canonical materialization digest, stable operation ID, source mask,
and mapping version; a second source ORs coverage only when the other fields match exactly.
Content rows store encrypted object inventory/commitments/classification/relations only. The
verification repository owns all job/result SQL, lease recovery, coalescing, and fact projection.

## Errors and edge cases

Missing/revoked consent and unbound multi-workspace sessions reject ingest with bounded reasons.
Oversized retention trims oldest events/dedup rows.

## Invariants

1. No transcript/stdout/stderr/path columns.
2. Revoke retains evidence rows.
3. Tables are exactly those owned by `migrations/bundle/0002.sql` and `0003.sql`.
4. Coordinators never access `_db` or issue observation-table SQL.

## Tests

`tests/unit/adapters/test_sqlite_observation.py`,
`tests/integration/storage/test_migration_0002_observation.py`.

## Open questions

None.
