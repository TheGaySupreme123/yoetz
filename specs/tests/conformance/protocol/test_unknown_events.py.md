# tests/conformance/protocol/test_unknown_events.py — unknown event preservation contract

**Wave:** A/B/C | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/kernel/reducers.md`
**Imported by:** conformance protocol tests

## Purpose

Prove opaque unknown events are preserved, not interpreted, and that their presence only weakens
coverage.

## Public surface

- `test_unknown_event_round_trip_preserves_opaqueness` — raw unknown payloads stay opaque.
- `test_unknown_event_adds_projection_gap_only` — the projection records a gap, not fake facts.
- `test_unknown_version_or_type_batch_rejects_or_preserves_as_specified` — known/unknown branches
  behave exactly as the contract says.

## Behavior

The test sends opaque event records through the full ingest/replay path and asserts:

- the event is preserved as opaque data;
- the projection gains gap metadata and weaker coverage only;
- known-event behavior is unchanged;
- no hidden attempt is made to parse the unknown payload as a known family.

## Errors and edge cases

- An unknown event that becomes a known fact fails.

## Invariants

1. Unknown means opaque.
2. Gaps weaken only.
3. Known event handling stays intact.

## Tests

- `tests/conformance/protocol/test_unknown_events.py`

## Open questions

None.
