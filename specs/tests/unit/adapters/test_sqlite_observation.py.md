# tests/unit/adapters/test_sqlite_observation.py — SQLite ObservationPort unit coverage

**Wave:** D | **ADRs:** ADR-005, ADR-010 | **Imports (spec-tree):**
`adapters/sqlite/observation.md` | **Imported by:** none

## Purpose

Lock consent, ingest, dedup, pause/resume, and revoke/evidence-retention for
`SqliteObservationStore` against an in-memory bundle initialized through migration `0002`.

## Public surface

Pytest module; no production exports.

## Behavior

Grant consent, bind session, accept then duplicate an envelope, pause/resume fencing, and revoke
while retaining stored envelopes.

## Errors and edge cases

Resume without consent raises `PublicOperationError`.

## Invariants

Hermetic in-memory SQLite only; no filesystem paths in assertions.

## Tests

This file is the test.

## Open questions

None.
