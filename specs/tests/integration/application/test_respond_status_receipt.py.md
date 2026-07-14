# tests/integration/application/test_respond_status_receipt.py — response, status, and receipt flow

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/respond.md`, `src/yoetz_core/application/status.md`,
`src/yoetz_core/application/receipt.md`
**Imported by:** integration application tests

## Purpose

Prove the response, status, and receipt operations compose over the same frozen frontier and remain
idempotent.

## Public surface

- `test_response_disposition_and_waiver_scope` — acknowledgements, rejections, and waivers are exact.
- `test_status_is_read_only_and_paginated` — status returns the correct frontier and page shape.
- `test_receipt_matches_check_and_response_state` — the receipt reflects the same frozen state.

## Behavior

The test asserts:

- responses preserve waiver scope and expiry;
- status never mutates state and discloses lag honestly;
- receipt text/JSON match the frozen frontier and the current findings/obligations;
- repeated requests return the same durable results.

## Errors and edge cases

- A status result that changes state fails.
- A receipt that outruns response or finding state fails.

## Invariants

1. Status is read-only.
2. Response scope stays bounded.
3. Receipt matches the frozen frontier.

## Tests

- `tests/integration/application/test_respond_status_receipt.py`

## Open questions

None.
