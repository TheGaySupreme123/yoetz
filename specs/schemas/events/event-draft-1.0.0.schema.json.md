# schemas/events/event-draft-1.0.0.schema.json — event draft schema

**Wave:** A/B/C | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/protocol/models.md`
**Imported by:** publish-work validation fixtures and conformance tests

## Purpose

Describe the client-shaped event draft wrapper used before ledger acceptance.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/event-draft-1.0.0.schema.json`.
- Owning model: `EventDraft`.

## Behavior

Closed object with:

- `event_id`;
- `schema` (`name`, `version`);
- `occurred_at`;
- `causal_parents`;
- `payload` as known family payload or opaque JSON for unknown families;
- `artifact_refs`;
- `evidence_refs`, a sorted-unique `evidence_id|result_id` union preserving the payload mirror.

Known and unknown branches are disjoint; unknown-family payloads remain opaque and do not widen the
known family schema. Extra properties are forbidden.

## Errors and edge cases

- Duplicate parents fail.
- An evidence ref outside the exact evidence/result ID union fails.
- Unknown family collisions fail.
- Extra keys fail.

## Invariants

1. Draft is pre-acceptance only.
2. Known and unknown branches are disjoint.
3. Opaque payloads stay opaque.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_unknown_events.py`

## Open questions

None.
