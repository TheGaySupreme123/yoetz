# schemas/events/redaction-recorded-1.0.0.schema.json — redaction-recorded payload schema

**Wave:** C/D | **ADRs:** ADR-003, ADR-004, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/receipts.md`
**Imported by:** redaction and recovery tests

## Purpose

Describe the payload that records logical redaction or object deletion.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/redaction-recorded/1.0.0`.
- Owning model: `RedactionRecordedPayload`.

## Behavior

Closed payload object with:

- `target_event_ids`;
- `target_object_ids`;
- `method`;
- `reason_category`;
- `authority`;
- `remaining_gap`.

At least one target list must be non-empty. The schema preserves structural gaps rather than claiming
forensic erasure.

## Errors and edge cases

- Empty target lists fail.
- Invalid reason/method values fail.

## Invariants

1. Redaction is recorded as history.
2. Structural gaps remain explicit.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/integration/objects/test_redaction_and_gc.py`

## Open questions

None.
