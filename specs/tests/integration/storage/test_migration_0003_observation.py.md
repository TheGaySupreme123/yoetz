# tests/integration/storage/test_migration_0003_observation.py — schema-3 continuity

**Wave:** D | **ADRs:** ADR-003, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/migrations/bundle/0003.sql.md` |
**Imported by:** storage verification suite

## Purpose

Prove schema-2 upgrade through schema-4 (migrations `0003`+`0004`), restart/copy restore, and
atomic failed-followup rollback on a candidate `0004` applied after schema-3 only.

## Public surface

Pytest integration cases over on-disk APSW databases.

## Behavior

Build an authentic schema-2 bundle, apply the full registry, reopen/copy it, and verify identity,
integrity, metadata, and all migration-3/4 observation tables. Separately apply through `0003`,
then inject a failing candidate migration `0004` and prove rollback.

## Errors and edge cases

The failure test expects SQLite error and unchanged user version/schema.

## Invariants

1. Schema-2 data is never rewritten or lost.
2. A failed migration leaves no partial table.

## Tests

This file.

## Open questions

None.
