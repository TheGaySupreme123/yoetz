# schemas/events/plan-revised-1.0.0.schema.json — plan-revised payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`
**Imported by:** replay and revision tests

## Purpose

Describe the payload that revises an existing plan in place.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/plan-revised-1.0.0.schema.json`.
- Owning model: `PlanRevisedPayload`.

## Behavior

Closed payload object with:

- `plan_version`;
- `supersedes_plan_version`;
- `reason`;
- `summary`;
- `obligation_changes`.

The schema enforces version continuity and explicit change records. Nested
`replacement_obligation_ids` is allowed only for `superseded` and, when present, contains `1..8`
unique obligation IDs.

## Errors and edge cases

- Version mismatch fails.
- Missing reason on supersede/waive changes fails.
- An explicit empty `replacement_obligation_ids` array fails; absence represents no replacements.
- `replacement_obligation_ids` on waived or carried changes fails even when the array is empty.

## Invariants

1. Revision is explicit.
2. Version continuity is enforced.
3. Extra keys are forbidden.
4. Replacement-list presence always carries at least one replacement and cannot collapse to
   absence during normalization.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
