# tests/integration/storage/test_migration_0002_observation.py — observation schema migrate/read

**Wave:** D | **ADRs:** ADR-005, ADR-010 | **Imports (spec-tree):**
`migrations/bundle/0002.sql.md`, `adapters/sqlite/observation.md` | **Imported by:** none

## Purpose

Prove forward migration from bundle schema `0001` to `0002` installs observation tables without
rewriting prior ledger tables, and that a fresh initialize at version 2 can status-query empty
observation state.

## Public surface

Pytest module; no production exports.

## Behavior

Apply `0001` DDL, run migrations to apply `0002`, assert observation tables exist and `events`
remains readable, then ingest through `SqliteObservationStore`. Fresh `initialize_bundle` yields
`user_version = 2` with stopped empty observation status.

## Errors and edge cases

None beyond migration runner fail-closed already covered elsewhere.

## Invariants

No destructive rewrite of `0001` tables; observation DDL only adds tables/indexes.

## Tests

This file is the test.

## Open questions

None.
