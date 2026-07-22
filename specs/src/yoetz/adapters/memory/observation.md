# src/yoetz/adapters/memory/observation.py — in-memory ObservationPort

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):**
`ports/observation.py.md`, `domain/observation.py.md` | **Imported by:** observation unit tests,
composition fixtures

## Purpose

Provide a reference in-memory `ObservationPort` that exercises consent, generation-fenced cursors,
dedup, pause/resume/revoke, and structural envelope retention without durable SQLite.

## Public surface

- `MemoryObservationConsent`, `MemoryObservationState`, `MemoryObservationStore`
- `MemoryObservationStore.grant_consent`, `bind_session`, and the five `ObservationPort` methods

## Behavior

Without active consent, `ingest` and `resume` fail closed. `pause` keeps consent but stops ingest.
`revoke` stops ingest and retains envelopes/dedup/cursors. Identical envelopes are idempotent
duplicates. Session commitments bind to a workspace commitment for consent lookup.

## Errors and edge cases

Missing/revoked consent, stale cursors, and unbound multi-workspace sessions fail closed with
bounded public or ingest-result reasons.

## Invariants

1. No transcript prose or raw paths are retained.
2. Revoke never deletes retained evidence in this adapter.
3. Dedup keys are canonical digests over path-free structural identity.

## Tests

`tests/unit/adapters/test_memory_observation.py`.

## Open questions

None.
