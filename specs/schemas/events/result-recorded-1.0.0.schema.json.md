# schemas/events/result-recorded-1.0.0.schema.json — result-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`
**Imported by:** replay and action/result tests

## Purpose

Describe the payload that records an action outcome.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/result-recorded-1.0.0.schema.json`.
- Owning model: `ResultRecordedPayload`.

## Behavior

Closed payload object with:

- `result_id`;
- `action_id`;
- `outcome`;
- optional `exit_status`;
- optional `summary`;
- optional `subject_state`;
- optional `evidence_refs`.

The schema keeps the action/result linkage explicit and bounded.

## Errors and edge cases

- Missing action linkage fails.
- Invalid outcome token fails.

## Invariants

1. Result links back to an action.
2. Subject state is bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
