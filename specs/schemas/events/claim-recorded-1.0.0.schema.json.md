# schemas/events/claim-recorded-1.0.0.schema.json — claim-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/findings.md`
**Imported by:** replay, claim, and receipt tests

## Purpose

Describe the payload that records a claim and its support refs.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/claim-recorded/1.0.0`.
- Owning model: `ClaimRecordedPayload`.

## Behavior

Closed payload object with:

- `claim_id`;
- `claim_kind`;
- `statement`;
- `supporting_refs`;
- optional `subject_state`;
- optional `obligation_refs`;
- optional `disputes_refs`.

The schema keeps support refs explicit and bounded.

## Errors and edge cases

- Unknown claim kind fails.
- Empty/oversized refs fail.

## Invariants

1. Claim support is explicit.
2. Subject state is bounded.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
