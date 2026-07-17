# schemas/events/action-recorded-1.0.0.schema.json — action-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/values.md`
**Imported by:** replay and action/result tests

## Purpose

Describe the payload that records an attempted action.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/action-recorded-1.0.0.schema.json`.
- Owning model: `ActionRecordedPayload`.

## Behavior

Closed payload object with:

- `action_id`;
- `action_kind`;
- `description`;
- optional `command`;
- optional `subject_state`;
- optional `obligation_refs`;
- optional `attempted_items`.

The schema ensures edit/command/research/review/other kinds remain closed and exact.

## Errors and edge cases

- Command missing for command actions fails where required by the contract.
- Extra keys fail.

## Invariants

1. Action kind is closed.
2. Subject state is bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
