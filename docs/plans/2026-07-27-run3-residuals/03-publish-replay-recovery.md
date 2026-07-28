# 02 — Recovery must not require reconstructing the original request body

**Severity:** critical **PR boundary:** replay resolution order + a request-id-keyed read surface

## The defect

Yoetz's documented recovery contract is "retry with the same `request_id` to load the stored
result." In practice an agent cannot execute it, because the stored result is reachable only by
re-sending a byte-identical copy of a large nested body — and the same product weakness that made
the body hard to author the first time makes it impossible to reproduce the second time.

Run 3 proves the loop. After the accepted-write failure on
`req_7b8c9d0e-1f2a-4b34-8567-8d9e0f1a2b34`, the agent read the remedy, followed it correctly, and
still got nowhere:

| Attempt | Result |
| --- | --- |
| 1 | `INTERNAL_ERROR` / `response_projection_failed`, write durable at sequence 6 |
| 2 | `INVALID_REQUEST`, `fields: ["/event_drafts/2"]` |
| 3 | `INVALID_REQUEST`, `fields: ["/event_drafts/3"]` |

Both replays died in **argument schema validation at the MCP bridge, before replay lookup ever
ran**. PR #37 shipped a replay fix that has now gone three dogfoods without a single valid exercise.
It is not known to be broken; it is known to be unreachable.

## Evidence

- `docs/dogfood/2026-07-27-grok-easy-linking/codex-events.jsonl`, the four `publish_work` calls
  sharing `req_7b8c9d0e…`.
- `_preflight_replay` is called from `execute_publish_work`
  (`src/yoetz/application/publish_work.py`) only *after* `prepare_publication` and after
  `request_digest(...)` has been computed over the full submitted body. Everything upstream —
  including bridge-level schema validation — must succeed before recovery is even attempted.

## Design

### 1. Resolve the operation before the body

Replay lookup keys on `(task_id, writer_id, request_id)`, which is fully determined by the request
envelope. It must not sit behind validation of `event_drafts`.

- At the bridge, validate the envelope fields first. If `request_id` names a known operation for
  this writer, route to recovery without validating the event payload.
- In `execute_publish_work`, resolve the operation record before `prepare_publication`.

Then three outcomes, all honest:

| Case | Result |
| --- | --- |
| Operation exists, complete, body digest matches | Stored result. No re-append. Today's behaviour, now reachable. |
| Operation exists, body digest differs | `REQUEST_IDENTITY_CONFLICT` — a distinct, non-destructive error carrying `sequence`, `head_digest`, `count`, and the accepted event ids. Never `INVALID_REQUEST`, never a re-append. |
| No operation | Normal publish path. |

The conflict case matters: it is exactly what run 3 hit, and the agent still needs to learn what
landed. Returning the frontier and the accepted ids inside the conflict lets it continue truthfully
even when it has lost the original body.

### 2. A read surface keyed by request id

Add `status view=operation`, taking a `request_id` and returning the stored result of that
operation — outcome, frontiers, accepted event ids and digests, or "no such operation."

`status` is the right home: recovery is a read, it needs no writer semantics or write capability,
and it keeps the six operations intact. Contract freedom is open pre-1.0, so this lands as a real
view with schema, vectors, and `docs/INTERFACES.md` updated, not as an undocumented extra.

This is the surface an agent should reach for after *any* ambiguous write, and it replaces "compose
a duplicate write and hope" with a single safe read.

### 3. Do not let frontier checks pre-empt recovery

An expected-frontier conflict must be evaluated *after* replay resolution. A replay of an already
accepted operation carries the pre-append frontier by definition; rejecting it as stale before
looking up the operation would fail exactly the caller who is doing the right thing. Pin this with a
test — it is the kind of ordering that silently regresses.

## Files

- `src/yoetz/application/publish_work.py` — `_preflight_replay` call site, ordering versus
  `prepare_publication`, `request_digest`, and the expected-frontier check
- `src/yoetz/mcp/server.py` — envelope-first validation, recovery routing
- `src/yoetz/application/status.py`, `src/yoetz/protocol/models.py` — the `operation` view
- `src/yoetz/protocol/errors.py` — `REQUEST_IDENTITY_CONFLICT`
- `schemas/`, `fixtures/`, `docs/INTERFACES.md`, `docs/usage/six-operations.md`,
  `guidance/workflow.md` — the recovery wording currently promises the unreachable path

## Tests

- Force a post-commit projection failure, then replay with the exact original body: stored result
  returned, no duplicate append, `status` shows exactly one copy of each event.
- Replay the same `request_id` with a mutated `event_drafts` entry — the literal run-3 sequence:
  `REQUEST_IDENTITY_CONFLICT` carrying the frontier, no duplicate append, no `INVALID_REQUEST`.
- Replay with a stale `expected_frontier`: recovery still resolves.
- `status view=operation` returns the stored result for a completed operation, a bounded
  not-found for an unknown `request_id`, and refuses a `request_id` belonging to another writer.
- An operation that is durably underway but not yet complete is reported as pending, not as absent.

## Done

Green CI, and the recorded run-3 replay sequence produces a usable recovery instead of two
validation errors.

## Dogfood observable

Run 4 must show either no replay needed at all (plan 01 doing its job), or a replay that resolves —
and never a replay that fails validation. If an ambiguous write occurs, the agent should recover with
`status view=operation` rather than by re-sending events.

## Out of scope

Making the body easier to author (plan 04). This PR makes recovery independent of the body; plan 04
reduces how often recovery is needed.
