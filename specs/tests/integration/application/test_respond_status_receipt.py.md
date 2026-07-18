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
- `test_status_is_task_read_only_paginated_and_projection_receipted` — status returns the correct
  frontier/page, writes no task state, and ordinary output carries its local-disclosure receipt.
- `test_receipt_matches_check_and_response_state` — the receipt reflects the same frozen state.
- `test_reviewer_challenge_response_paths_use_existing_protocol` — every model-requested next step
  maps to attributable respond/publish/recheck history without a new reply type.
- `test_response_and_waiver_never_resolve_finding` — all three dispositions and waiver expiry leave
  resolution unchanged until a qualifying later check.
- `test_scoped_check_applicability_is_durable` — normalized scope and policy executions in the
  check event distinguish a resolving recheck from skipped/non-overlapping work.

## Behavior

The test asserts:

- responses preserve waiver scope and expiry;
- recorded disposition remains exact across waiver expiry; neither time nor any response resolves
  the finding or removes it from compact/receipt unresolved accounting;
- a later check resolves only with matching `run/completed`, zero suppression, current gap-free
  coverage, and whole-case/direct claim-or-obligation scope overlap; semantic resolution also
  requires `succeeded/semantic_completed`. Every negative guardrail leaves the old row unresolved;
- a same-issue successor resolves only the older row and itself begins unresolved;
- status never mutates task state and discloses lag honestly; its common service projection writes
  or replays exactly one privacy-audit receipt without changing the task frontier;
- receipt text/JSON match the frozen frontier and the current findings/obligations;
- accepted reviewer challenges can be acknowledged and acted on, answered with evidence, followed
  by a superseding claim, rejected with matching evidence, or retained as an unresolved limitation;
- the reviewer cannot submit a response or waiver, and every material agent branch is visible to a
  later check at the new frontier;
- repeated requests return the same durable results.

## Errors and edge cases

- A status result that changes task state or lacks its required `privacy_projection` receipt fails.
- A receipt that outruns response or finding state fails.
- A model-authored waiver, unsupported dispute, or “acknowledged means fixed” shortcut fails.

## Invariants

1. Status is task-ledger read-only; ordinary client disclosure remains durably receipted.
2. Response scope stays bounded.
3. Receipt matches the frozen frontier.
4. Reviewer-to-agent dialogue remains ordinary finding/response/work history, not a hidden chat.
5. Finding resolution is backed by visible durable check applicability, never inferred from
   acknowledgement, disagreement, waiver, or wall clock; only explicit redaction may remove proof.

## Tests

- `tests/integration/application/test_respond_status_receipt.py`

## Open questions

None.
