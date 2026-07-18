# schemas/events/session-opened-1.0.0.schema.json — session-opened payload schema

**Wave:** A/B/C | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/config/models.md`
**Imported by:** start-operation and conformance fixtures

## Purpose

Describe the payload for the initial `start` lifecycle event.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/session-opened-1.0.0.schema.json`.
- Owning model: `SessionOpenedPayload`.

## Behavior

Closed payload object with:

- `task_title`;
- `external_ref` optional;
- `workspace_ref` optional;
- `client_kind`;
- `client_version`;
- `integration`;
- `profile`.

`task_title`, `external_ref`, and `workspace_ref` use the same nonempty 8,192-code-point content
bound as the public start request so lifecycle publication is lossless. The two optional refs are
independent in this history schema. The Start request/application applies the stricter
both-or-neither attachment-key rule before it constructs a new lifecycle event; imported
schema-valid history is not rejected by an unstated event-only rule.

## Errors and edge cases

- Empty or over-8,192-code-point raw identity content fails.
- Invalid profile/integration values fail.

## Invariants

1. Start identity is explicit.
2. New Start operations enforce both-or-neither before this schema boundary; the event history
   schema preserves either optional ref independently.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/application/test_start.py`

## Open questions

None.
