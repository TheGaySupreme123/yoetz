# tests/integration/storage/test_migration_0004_observation.py — schema-4 continuity

**Wave:** D | **ADRs:** ADR-003, ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/migrations/bundle/0004.sql.md` |
**Imported by:** storage verification suite

## Purpose

Prove schema-3 upgrade to migration `0004`, restart/integrity, and atomic failed-followup rollback.

## Public surface

Pytest integration cases over on-disk APSW databases.

## Behavior

Build an authentic schema-3 bundle, apply registry migration `0004`, reopen it, and verify identity,
integrity, metadata, and all migration-4 observation tables. Inject a failing migration `0005` and
prove rollback leaves schema 4 intact.

## Errors and edge cases

The failure test expects SQLite error and unchanged user version/schema.

## Invariants

1. Schema-3 observation rows remain readable after `0004`.
2. A failed follow-up migration leaves no partial table.
3. Migration `0003` DDL is never edited by this suite.

## Tests

This file.

## Open questions

None.
