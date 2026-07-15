# tests/integration/application/test_respond_status_receipt.py — response, status, and receipt flow

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/respond.md`, `src/yoetz/application/status.md`,
`src/yoetz/application/receipt.md`
**Imported by:** integration application tests

## Purpose

Prove the response, status, and receipt operations compose over the same frozen frontier and remain
idempotent.

## Public surface

- `test_response_disposition_and_waiver_scope` — acknowledgements, rejections, and waivers are exact.
- `test_status_is_read_only_and_paginated` — status returns the correct frontier and page shape.
- `test_receipt_matches_check_and_response_state` — the receipt reflects the same frozen state.
- `test_reviewer_challenge_response_paths_use_existing_protocol` — every model-requested next step
  maps to attributable respond/publish/recheck history without a new reply type.

## Behavior

The test asserts:

- responses preserve waiver scope and expiry;
- status never mutates state and discloses lag honestly;
- receipt text/JSON match the frozen frontier and the current findings/obligations;
- accepted reviewer challenges can be acknowledged and acted on, answered with evidence, followed
  by a superseding claim, rejected with matching evidence, or retained as an unresolved limitation;
- the reviewer cannot submit a response or waiver, and every material agent branch is visible to a
  later check at the new frontier;
- repeated requests return the same durable results.

## Errors and edge cases

- A status result that changes state fails.
- A receipt that outruns response or finding state fails.
- A model-authored waiver, unsupported dispute, or “acknowledged means fixed” shortcut fails.

## Invariants

1. Status is read-only.
2. Response scope stays bounded.
3. Receipt matches the frozen frontier.
4. Reviewer-to-agent dialogue remains ordinary finding/response/work history, not a hidden chat.

## Tests

- `tests/integration/application/test_respond_status_receipt.py`

## Open questions

None.
