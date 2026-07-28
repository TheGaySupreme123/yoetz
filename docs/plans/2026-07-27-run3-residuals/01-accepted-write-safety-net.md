# 01 — An accepted, durable write must never surface as a failure

**Severity:** critical **PR boundary:** publish response envelope + daemon projection window + diagnostics

**Pairs with:** [05 — root cause](05-accepted-write-root-cause.md). This plan removes the *harm*
without knowing the *cause*, and is deliberately independent of it. Do 01 first; 05 can run for as
long as it takes without blocking anything.

## The defect

A valid four-event `publish_work` batch committed atomically, advanced the frontier from 2 to 6, and
returned:

```json
{"code": "INTERNAL_ERROR",
 "message": "The write was accepted and is durable at the frontier in safe_details …",
 "retryable": true,
 "correlation_id": "err_aa2423b0-6808-4e28-bea4-d04286307841",
 "safe_details": {"count": 4, "head_digest": "sha256:4335…", "reason_code": "response_projection_failed", "sequence": 6}}
```

The same defect appeared in all three of today's dogfoods. It is the single most damaging Yoetz
behaviour: the product's core promise is an honest record, and its most common write operation
reports a durable success as an internal error.

## Evidence

- `docs/dogfood/2026-07-27-grok-easy-linking/codex-events.jsonl`, the `publish_work` call with
  `request_id: req_7b8c9d0e-1f2a-4b34-8567-8d9e0f1a2b34`.
- The single-event `plan_published` batch earlier in the same session projected fine.
- `status` immediately afterwards returned `sequence: 6`, `projection_lag: 0`,
  `rebuild_state: current`. The ledger was never in doubt.
- It fires in `_project_completed_response`, `src/yoetz/service/daemon.py:762-860` — the post-commit
  projection window, where only an unexpected exception is reclassified into
  `ControlError("response_projection_failed")`.

## Why this half ships first

The prior attempt at this defect (`0eee854`) improved the error *message* without changing the
*outcome*, and the defect recurred in the very next dogfood. The lesson is that the post-commit
window must stop being able to convert a durable write into a reported failure at all — regardless
of which specific exception fires inside it.

This plan also owns the publish result contract change, so it should land before plans 03 and 04
touch the same models. Both rebase cheaply onto it; the reverse costs a second round of schema,
vector, and manifest regeneration.

## Design

### 1. Return a total acceptance envelope instead of an error

When full response projection fails after the append succeeded, do not raise. Return a smaller
result that is constructible by construction — built only from what the ledger already handed back
in `AppendResult`, never from privacy projection or the full closed model:

- `ok: true`
- `request_id`, `task_id`, `session_id`, `writer_id`
- `subject_frontier`, `result_frontier`
- accepted event ids, entry digests, and ingestion sequences
- `response_completeness: "accepted_projection_unavailable"` with the bounded reason token
- the `correlation_id` for operator follow-up

The caller gets a true, smaller answer rather than a false failure, and needs no second `status`
call and no replay. Contract freedom is open pre-1.0, so this lands as a real reduced branch in the
publish result schema — not an error code bent into a success.

Keep `response_projection_failed` in the protocol reason inventory for reads
(`read_projection_failed` is unaffected) and for the case where even the minimal envelope cannot be
built. That case must now be genuinely impossible for a committed publish, and a test must assert
it.

Delete the `INTERNAL_ERROR` mapping for accepted writes at `src/yoetz/mcp/server.py:317-330`. An
accepted write no longer reaches it.

### 2. Recover the diagnostic trail

The run-3 error carried `correlation_id: err_aa2423b0-…` and there is no way to find out what it
meant. `record_unexpected_exception_without_raising`
(`src/yoetz/observability/logging.py:354-379`) writes a bounded reason token to **stderr only**, and
the MCP-spawned service's stderr is swallowed by the harness.
`~/Library/Logs/yoetz/service.stderr.jsonl` held 51 stale bytes from two days before the run. An
operator hitting this in production cannot diagnose it — and neither can we, which is exactly why
plan 05 has to start from a reproduction rather than from a log.

Add a durable, bounded, owner-only diagnostic record alongside the existing stderr emission:

- append-only, size-capped ring under `log_dir()`, `0o600`, one JSON object per line;
- fields limited to what is already caller-safe: `correlation_id`, `component`, `operation`,
  `reason` token, `request_id`, timestamp. No exception text, no payload, no paths;
- a read surface — `yoetz service diagnostics --correlation-id err_…` — so the id in an agent-facing
  error resolves to something.

This is what makes the next occurrence diagnosable in minutes instead of unrecoverable, and it is
why it belongs in the *first* PR rather than the last: it instruments run 4.

## Files

- `src/yoetz/application/publish_work.py` — `_internal_result`, `execute_publish_work` fallback
- `src/yoetz/service/daemon.py` — `_project_completed_response` reclassification block
- `src/yoetz/protocol/models.py` — publish result branch
- `src/yoetz/mcp/server.py` — remove the accepted-write `INTERNAL_ERROR` path
- `src/yoetz/observability/logging.py` + a new durable diagnostic sink
- `src/yoetz/cli/` — the `service diagnostics` read surface
- `schemas/`, `fixtures/`, `docs/INTERFACES.md`, ADR-002 — contract ripple

## Tests

- Fault injection: force projection to fail after a successful append and assert the total
  acceptance envelope, the durable frontier, exactly one copy of every event in `status`, and no
  `INTERNAL_ERROR`.
- The minimal envelope is constructible for every event-schema mix in `_PUBLISH_SUMMARY_CATEGORY`
  and `_PUBLISH_FIXED_SUMMARY`.
- Diagnostic sink: bounded size, `0o600`, no payload leakage, and a correlation id written by the
  daemon resolves through the read surface.
- Read-only methods still return `read_projection_failed` with no replay advice.

## Done

Green CI. No test needs to know why projection failed — only that a committed write can no longer
be reported as one that failed.

## Dogfood observable

Run 4 must show no `publish_work` returning `INTERNAL_ERROR`. If projection still fails internally,
the agent must still see `ok: true` and must not need a `status` call to learn what landed — and the
diagnostic sink must contain the matching `correlation_id` for plan 05 to work from.

## Out of scope

The root cause (plan 05). Replay ergonomics (plan 03) — this PR removes the *need* to replay after
an accepted write; it does not fix replay itself.
