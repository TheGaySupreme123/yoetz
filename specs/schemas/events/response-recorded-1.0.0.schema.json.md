# schemas/events/response-recorded-1.0.0.schema.json — response-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/findings.md`
**Imported by:** respond and replay tests

## Purpose

Describe the response payload to a finding.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/response-recorded/1.0.0`.
- Owning model: `ResponseRecordedPayload`.

## Behavior

Closed payload object with:

- `finding_id`;
- `finding_frontier`;
- `disposition`;
- optional `reason`;
- optional `waiver_scope` only when waived;
- optional `waiver_expiry` only when waived;
- optional `evidence_refs`.

The schema keeps disposition-gated fields closed and exact.

## Errors and edge cases

- Waiver-only fields on other dispositions fail.
- Missing reason on reject/waive fails.

## Invariants

1. Disposition gates dependent fields.
2. Finding frontier is explicit.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/operations/test_respond_contract.py`

## Open questions

None.
