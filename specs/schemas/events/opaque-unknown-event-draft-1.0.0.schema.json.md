# schemas/events/opaque-unknown-event-draft-1.0.0.schema.json — opaque unknown event schema

**Wave:** A/B/C | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/domain/events.md`
**Imported by:** unknown-event preservation tests

## Purpose

Describe the opaque branch used to preserve unrecognized event families without interpretation.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/events/opaque-unknown-event-draft/1.0.0`.
- Owning model: unknown-event payload branch.

## Behavior

Closed object with schema name/version identity and an opaque payload candidate. The schema preserves
the unknown family identity while forbidding collision with a known family/version. It does not
accept arbitrary extra keys or silently coerce the payload into a known event.

## Errors and edge cases

- Known-family collision fails.
- Extra keys fail.

## Invariants

1. Unknown stays opaque.
2. Collision with known families is forbidden.
3. Extra keys are forbidden.

## Tests

- `tests/conformance/protocol/test_unknown_events.py`

## Open questions

None.
