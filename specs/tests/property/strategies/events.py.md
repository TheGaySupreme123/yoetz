# tests/property/strategies/events.py — generated event and payload strategies

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/values.md`
**Imported by:** property-based replay and boundary tests

## Purpose

Generate valid family payloads and one-rule-at-a-time mutations for replay, validation, and
reducer equivalence tests.

## Public surface

- `strategy_valid_event_payloads` — one valid payload per family.
- `strategy_invalid_event_payloads` — one named defect per family.
- `strategy_unknown_event_drafts` — opaque unknown-event records with preserved metadata.
- `strategy_event_sequences` — causal event sequences with writer/frontier metadata.

## Behavior

The strategy module must be causal-aware:

- parent/frontier chains are generated consistently;
- invalid cases mutate a single named rule when possible;
- unknown-event cases preserve opaque bytes and metadata without pretending to be known;
- shrink paths preserve the defect label.

## Errors and edge cases

- Blindly filtering random events until one passes is not acceptable.
- A strategy that loses causal metadata fails the test purpose.

## Invariants

1. Payload validity is family-specific.
2. Unknown events stay opaque.
3. Event sequences respect causal parents where required.

## Tests

- `tests/property/strategies/events.py`

## Open questions

None.
