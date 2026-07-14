# schemas/events/session-resumed-1.0.0.schema.json — session-resumed payload schema

**Wave:** A/B/C | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`src/yoetz_core/domain/events.md`
**Imported by:** start-operation and conformance fixtures

## Purpose

Describe the payload for reattaching to an existing session route.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/events/session-resumed/1.0.0`.
- Owning model: `SessionResumedPayload`.

## Behavior

Closed payload object with:

- `client_kind`;
- `client_version`;
- `integration`;
- `profile`;
- `resumed_frontier`.

The resumed frontier is the client-facing frontier at reattach time. Extra keys are forbidden.

## Errors and edge cases

- Missing resumed frontier fails.
- Invalid client/integration/profile values fail.

## Invariants

1. Reattach frontier is explicit.
2. Client metadata remains bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/application/test_start.py`

## Open questions

None.
