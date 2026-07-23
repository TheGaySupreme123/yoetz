# src/yoetz/adapters/sqlite/observation.py — durable ObservationPort over migration 0002

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`ports/observation.py.md`, `domain/observation.py.md`, `adapters/sqlite/migrations.md` |
**Imported by:** observation control handlers, migration/adapter tests

## Purpose

Provide a SQLite-backed `ObservationPort` against the observation tables created by bundle
migration `0002`, so consent, cursors, dedup, structural envelopes, and advice snapshots survive
process restart without plaintext transcript spool.

## Public surface

- `SqliteObservationStore` — `grant_consent`, `bind_session`, the five async `ObservationPort`
  methods, `set_advice_snapshot`, `load_advice_snapshot`, `list_envelopes`

## Behavior

Fail closed without active consent. Pause/resume/revoke match the memory adapter. Identical
envelopes are idempotent duplicates. Gaps from rejected stale cursors are persisted as bounded
`observation_gap` events. Envelope wire JSON is stored losslessly in `structural_json`; sensitive
prose never appears in plaintext columns.

## Errors and edge cases

Missing/revoked consent and unbound multi-workspace sessions reject ingest with bounded reasons.
Oversized retention trims oldest events/dedup rows.

## Invariants

1. No transcript/stdout/stderr/path columns.
2. Revoke retains evidence rows.
3. Tables are exactly those owned by `migrations/bundle/0002.sql`.

## Tests

`tests/unit/adapters/test_sqlite_observation.py`,
`tests/integration/storage/test_migration_0002_observation.py`.

## Open questions

None.
