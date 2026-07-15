# schemas/events/decision-recorded-1.0.0.schema.json — decision-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`
**Imported by:** replay and decision-chain tests

## Purpose

Describe the payload that records a decision and optional supersession.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/decision-recorded/1.0.0`.
- Owning model: `DecisionRecordedPayload`.

## Behavior

Closed payload object with:

- `statement`;
- `rationale`;
- optional `alternatives`;
- `authority`;
- optional `affected_obligation_ids`;
- optional `supersedes_event_id`.

The schema keeps the bounded decision record exact and closed.

## Errors and edge cases

- Empty rationale fails.
- Invalid authority IDs fail.

## Invariants

1. Decision body is bounded.
2. Supersession is explicit.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
