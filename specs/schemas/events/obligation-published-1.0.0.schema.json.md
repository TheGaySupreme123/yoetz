# schemas/events/obligation-published-1.0.0.schema.json — obligation-published payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/domain/events.md`
**Imported by:** publication, replay, and finding tests

## Purpose

Describe the immutable work obligation payload.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/events/obligation-published/1.0.0`.
- Owning model: `ObligationPublishedPayload`.

## Behavior

Closed payload object with:

- `obligation_id`;
- `description`;
- `evidence_expectation`;
- optional `acceptance_criteria`;
- optional `requested_items`;
- optional `source_refs`;
- `status`;
- `resolution_evidence_refs` only when resolved.

The schema enforces the publish-then-resolve rule and keeps resolution evidence gated by status.

## Errors and edge cases

- Missing resolution evidence on resolved obligations fails.
- Status/body mismatch fails.

## Invariants

1. Obligation identity is stable.
2. Resolution evidence is gated by status.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/application/test_publish_work.py`

## Open questions

None.
