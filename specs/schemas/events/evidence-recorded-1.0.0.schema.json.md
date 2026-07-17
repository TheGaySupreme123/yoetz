# schemas/events/evidence-recorded-1.0.0.schema.json — evidence-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** replay, evidence, and claim tests

## Purpose

Describe the payload that records observed evidence and its immutability strength.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/evidence-recorded-1.0.0.schema.json`.
- Owning model: `EvidenceRecordedPayload`.

## Behavior

Closed payload object with:

- `evidence_id`;
- `evidence_kind`;
- `strength`;
- one or more of `reference`, `captured_object_id`, `content_digest` depending on strength;
- optional `description`;
- `observed_at`;
- optional `subject_state`.

Strength-gated field requirements are strict and closed. Extra keys are forbidden.

## Errors and edge cases

- Declared strength without required fields fails.
- Invalid enum values fail.

## Invariants

1. Strength is gated by substance.
2. Subject state is optional but bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
