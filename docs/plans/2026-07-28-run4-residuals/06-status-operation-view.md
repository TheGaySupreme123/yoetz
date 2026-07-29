# 06 — `status view=operation` must work

**Severity:** medium **PR boundary:** the `operation` branch of `execute_status`

## The defect

`status view=operation` — the recovery surface shipped by PR #47 in the previous residual set —
raises an `AttributeError` inside the daemon and surfaces as a **non-retryable**
`INTERNAL_ERROR: "The bridge could not complete the operation."`

It failed on its first real use, at the exact moment it was designed for: the agent had an
ambiguous `check` and reached for the operation view to recover the stored result.

## Evidence

Run 4 `item_40`. The agent asked for the operation record of the failed check
(`req_a8b4c004-…`) and received:

```
INTERNAL_ERROR  "The bridge could not complete the operation."
correlation_id: err_7e5c31e9-aaf4-41a9-8d7a-cec3fa119f44
retryable: false
```

The durable sink holds both halves of that failure, 44 ms apart:

```
09:56:16.902Z | service.daemon | status_internal_error | exception_attribute_error | req_f5f30b50-…
09:56:16.946Z | mcp.bridge     | status_internal_error | exception_control_error   | req_f5f30b50-…
```

The daemon-side reason is `exception_attribute_error` — an `AttributeError`, not a bounded error.
It escaped `_project_completed_response` and was caught by the generic dispatch handler at
`src/yoetz/service/daemon.py:700-706`, which means it was raised during `application.status(...)`
execution, not during response projection.

The `operation` branch is `src/yoetz/application/status.py:746-793`. The exact attribute has not
been localized; candidates visible on inspection include the `except PublicOperationError` recovery
branch at `:777-784`, which reads `head.sequence` and reassigns `head` — `head` is bound only in
the `else` arm of the cursor decode at `:737-738`, so a request with no cursor may reach it
unbound or stale.

**Do not fix from that guess.** Reproduce first — the previous set's plan 05 established that
guessing at a projection failure produces a better message and not a fix.

## Design

### 1. Reproduce before fixing

Drive `status view=operation` against a task with:

- a completed `publish_work` operation (the populated path),
- a completed non-`publish_work` operation — a `check` is what run 4 asked for, and is the case
  that actually failed,
- an unknown `request_id`,
- a `request_id` belonging to a different writer,
- an operation that is durably underway but not complete.

The run-4 case is the second one. If it does not reproduce in-process, drive it through the daemon
— the failure was observed there, and the generic dispatch handler is what reclassified it.

### 2. Fix the cause, and make the branch total

Whatever the attribute turns out to be, the branch must be total over its inputs: every path that
reaches the projection assignment must have `head`, `effective`, `lag`, `projection_version`, and
`rebuild_state` bound. An unbound-or-stale local in a recovery path is the shape of this bug
regardless of which name it is.

### 3. An unexpected error here must not be non-retryable

The agent was told `retryable: false` on a read that changed nothing and would very likely succeed
on a retry with a fresh `request_id`. A read that fails unexpectedly should present as
`read_projection_failed` — retryable, with the "repeat the request" remedy — not as a terminal
bridge error. Align this branch with how other read failures are classified.

## Files

- `src/yoetz/application/status.py` — the `operation` branch
- `src/yoetz/service/daemon.py` — only if the reclassification is wrong for this case
- tests under `tests/integration/` covering all five operation-view cases

## Tests

- The exact run-4 sequence: a completed `check` operation looked up by `request_id` returns a
  bounded, honest operation page rather than any internal error.
- A completed `publish_work` operation returns its stored accepted-event detail — PR #47's
  intended behaviour, now actually reachable.
- An unknown `request_id` returns the bounded absent page.
- A `request_id` belonging to another writer returns the same absent page, disclosing nothing.
- An operation durably underway but not complete reports pending, not absent.
- A cursor-less request and a cursor-bearing request both resolve; the recovery branch is covered.
- No path through the `operation` branch can raise an unbounded exception.

## Done

Green CI, and the recorded run-4 lookup returns a usable answer.

## Dogfood observable

Run 5: if any ambiguous write or check occurs, `status view=operation` resolves it. It must never
return `INTERNAL_ERROR`, and never `retryable: false` on a read.

## Out of scope

Replay resolution for `check` — the previous set's plan 03 made replay reachable for
`publish_work` only, and extending it is a separate question. Once plan 01 lands, a `check` that
produces findings returns normally and the need for replay largely disappears.
