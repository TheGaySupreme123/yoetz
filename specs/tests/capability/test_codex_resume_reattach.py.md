# tests/capability/test_codex_resume_reattach.py — Codex interruption/resume continuity

**Wave:** D/F | **ADRs:** ADR-001, ADR-002, ADR-005 | **Imports (spec-tree):** capability evidence,
start/status/idempotency specs | **Imported by:** resume support claim

## Purpose

Verify real Codex interrupt, compaction, exit, and resume mechanisms reattach to durable Yoetz state
without allocating replacement task/session/writer identities or duplicating prior publication.

## Public surface

Scenarios: interrupt before/after publish commit, context compaction, clean exit/reopen, client crash,
same supported resume token, missing/invalid resume token, and resume after server process restart.

## Behavior

Run a synthetic workflow to a recorded frontier, interrupt through the exact Codex public mechanism,
discard client/process memory, and resume. First action queries/reattaches status; compare task,
session/writer policy, frontier, request sequence, findings and coverage. Reissuing same operation ID
returns stored result; next new request advances once.

Seed a post-commit/pre-response loss before interruption and prove resumed retry resolves one event.
Compaction cannot turn unrecorded text into ledger evidence. Invalid/missing resume identity creates
no attachment and reports bounded limitation.

## Errors and edge cases

- Codex versions with different resume semantics have independent evidence cells.
- Hidden conversation restoration is not the oracle; durable ledger and public JSONL/tool calls are.
- Stale server owner recovery must pass generation fencing.
- No real user conversation/config is accessed.

## Invariants

1. Durable IDs/frontier survive client and server lifetime.
2. Resume cannot duplicate an idempotent operation.
3. Unrecorded/compacted content remains absent or an explicit coverage gap.
4. Attachment failure never fuzzy-matches another task.

## Tests

Each case records before/after structural snapshots and exact Codex resume identity/version, with
private transcript digest and redacted result.

## Open questions

None.
