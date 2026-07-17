# schemas/events/assignment-recorded-1.0.0.schema.json — assignment-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`
**Imported by:** replay and obligation assignment tests

## Purpose

Describe the payload that records an obligation assignment.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/assignment-recorded-1.0.0.schema.json`.
- Owning model: `AssignmentRecordedPayload`.

## Behavior

Closed payload object with:

- `assignee_actor_id`;
- `obligation_ids`;
- `scope_description`;
- optional `write_policy`;
- optional `handoff_of`.

The schema keeps the assignment bounded and exact. Extra keys are forbidden.

## Errors and edge cases

- Empty obligation list fails.
- Wrong write-policy token fails.

## Invariants

1. Assignment is explicit.
2. Obligation refs are bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
