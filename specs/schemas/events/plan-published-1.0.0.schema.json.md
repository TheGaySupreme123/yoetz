# schemas/events/plan-published-1.0.0.schema.json — plan-published payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`
**Imported by:** publication and replay tests

## Purpose

Describe the initial plan publication payload.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/plan-published/1.0.0`.
- Owning model: `PlanPublishedPayload`.

## Behavior

Closed payload object with:

- `plan_version`;
- `summary`;
- `obligation_refs`;
- `scope_exclusions` optional.

Plan version continuity and closed arrays are enforced. Extra keys are forbidden.

## Errors and edge cases

- Noncanonical plan version fails.
- Duplicate obligation refs fail.

## Invariants

1. Plan version is explicit.
2. Obligation refs are bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/application/test_publish_work.py`

## Open questions

None.
