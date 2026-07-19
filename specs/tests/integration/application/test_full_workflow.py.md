# tests/integration/application/test_full_workflow.py — full vertical slice workflow

**Wave:** B6/D/E | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/start.md`, `src/yoetz/application/publish_work.md`,
`src/yoetz/application/check.md`, `src/yoetz/application/respond.md`,
`src/yoetz/application/receipt.md`
**Imported by:** integration application tests

## Purpose

Prove the ready application's sink-independent internal workflow can run as one coherent offline
slice across start, publish, check, respond, recheck, and receipt, with disclosure performed only at
the final authenticated client boundary.

## Public surface

- `test_full_workflow_uses_one_final_client_projection` — the complete provider-free internal slice
  stays frontier-coherent and performs exactly one final per-client privacy projection.

## Behavior

The test walks one realistic memory-backed task through the ready facade and asserts:

- start creates stable IDs and a durable route;
- publish records the work batch;
- deterministic-only check records an actionable finding without any provider or live-agent call;
- a simulated crash after deterministic-result finalization but before `reserved → local_ready`
  leaves only an orphan candidate; after lease expiry, the same request resumes from the real
  memory-ledger reservation and installs one durably pinned replacement result;
- a second simulated crash immediately after the `local_ready` CAS proves the operation row now
  names that deterministic-result object; after lease expiry, retry authenticates its bound prior
  resume object, reuses the exact finding IDs/executions, and completes without policy rerun or a
  third deterministic-result publication;
- respond records an attributable acknowledgment and recheck freezes the resulting frontier;
- receipt reflects the rechecked frozen state;
- start, publish, check, respond, recheck, and receipt return unprojected structural results;
- one final `project_result_for_client(...)` call adds the only client privacy projection.

## Errors and edge cases

- Every operation consumes the exact preceding result frontier.
- Retry after the pre-phase crash reuses the same logical CHECK request and frozen subject frontier.
- Retry from `local_ready` observes exactly two deterministic-result objects (one orphan and one
  authoritative checkpoint) before and after completion.
- The idle import status is explicit before receipt creation.
- The test is provider-free, live-agent-free, non-e2e, and uses the in-memory ledger oracle.

## Invariants

1. Internal operations compose without per-use-case disclosure.
2. Task, session, writer, and frontier identity stay stable across the slice.
3. Exactly one authenticated client projection occurs after the receipt is durable.

## Tests

- `tests/integration/application/test_full_workflow.py`

## Open questions

None.
