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

The external/workspace refs must be both present or both absent. This is the atomic attachment
identity for the start catalog.

## Errors and edge cases

- One of the attachment refs without the other fails.
- Invalid profile/integration values fail.

## Invariants

1. Start identity is explicit.
2. Both-or-neither attachment refs are required.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/application/test_start.py`

## Open questions

None.
