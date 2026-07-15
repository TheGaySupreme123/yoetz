# tests/integration/application/test_full_workflow.py — full vertical slice workflow

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/application/publish_work.md`,
`src/yoetz/application/check.md`, `src/yoetz/application/respond.md`,
`src/yoetz/application/receipt.md`
**Imported by:** integration application tests

## Purpose

Prove the main public workflow can run as one coherent slice across start, publish, check, respond,
recheck, and receipt.

## Public surface

- `test_end_to_end_workflow_path` — a complete public slice stays coherent.
- `test_malformed_semantic_result_does_not_break_followup` — semantic failures do not corrupt the
  rest of the slice.
- `test_reopen_and_replay_remain_stable` — follow-up runs preserve identity and history.
- `test_assisted_reviewer_challenge_to_agent_recheck` — a bounded model challenge reaches the main
  agent as an ordinary finding and is resolved or preserved only through existing operations.

## Behavior

The test walks one realistic task through the full public operation chain and asserts:

- start creates stable IDs and a durable route;
- publish records the work batch;
- check returns deterministic and/or semantic findings as configured;
- an assisted semantic packet supplies deterministic bases and linked recorded excerpts without a
  live repository handle, and its accepted challenge uses the existing finding summary/detail;
- respond plus published work/evidence or a superseding claim updates attributable state;
- recheck and receipt reflect the updated frozen state;
- replay remains stable across the same request identity.

## Errors and edge cases

- A later step that depends on a mutated earlier identity fails.
- A broken semantic step that poisons the workflow fails.
- A reviewer that invents source facts, waives its own finding, or bypasses recheck fails.

## Invariants

1. Public operations compose.
2. Identity stays stable across the slice.
3. Failures remain bounded.

## Tests

- `tests/integration/application/test_full_workflow.py`

## Open questions

None.
