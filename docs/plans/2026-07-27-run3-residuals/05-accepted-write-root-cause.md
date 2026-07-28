# 05 — Find why the four-event publish response could not be projected

**Severity:** critical (cause), non-blocking (schedule) **PR boundary:** the specific projection defect + its regression test

**Pairs with:** [01 — safety net](01-accepted-write-safety-net.md). Plan 01 removes the harm. This
plan removes the cause. It is scheduled last **only because its duration is unknown** — it is a
research task, and it must never block the three bounded PRs. Start the reproduction early and let
it run alongside them.

## What is known

A valid four-event `publish_work` batch committed durably and then failed to project a response.
The batch:

- `request_id: req_7b8c9d0e-1f2a-4b34-8567-8d9e0f1a2b34`, recorded verbatim in
  `docs/dogfood/2026-07-27-grok-easy-linking/codex-events.jsonl`;
- four drafts — `action_recorded`, `result_recorded`, `evidence_recorded`, `claim_recorded`, all
  `1.0.0`;
- `causal_parents` chained across all four;
- the claim carries `supporting_refs` and `evidence_refs` referencing both an `evd_` and a `res_`
  id;
- three drafts have empty `artifact_refs` and `evidence_refs`; the fourth is populated.

The single-event `plan_published` batch earlier in the same session projected fine. Whatever fails
is content- or shape-dependent, not a blanket publish failure.

## Two constraints on the search

1. It fires in `_project_completed_response`, `src/yoetz/service/daemon.py:762-860`. Bounded errors
   pass through untouched; only an unexpected exception is reclassified.
2. **It does not reproduce in-process.** `tests/integration/application/test_full_workflow.py`
   already publishes multi-event batches successfully. The failure needs the daemon, the control
   protocol, the client disclosure sink, and the MCP bridge together.

## Ruled out

- `MAX_EVENTS_PER_BATCH` is 100 (`src/yoetz/protocol/models.py:116`). A four-event batch is far
  under both the request and the response limit.
- All four event schemas are registered in `_PUBLISH_SUMMARY_CATEGORY`
  (`src/yoetz/protocol/models.py:1284-1297`), so `_publish_event_selector` should not reject them.

## Live hypotheses, in order

1. **Disclosure pointer resolution.** `_replace_pointer`,
   `src/yoetz/application/service.py:437-464`, raises `ValueError("projection_pointer_unresolved")`
   when a static disclosure pointer does not resolve against the actual document — and its own
   comment names this exact consequence: *"turns a durable success into an apparent failure the
   caller cannot replay away."* The publish pointer set includes `/accepted_events/*/summary`
   (`src/yoetz/protocol/models.py:2547-2551`) while `_accepted_model`
   (`src/yoetz/application/publish_work.py:607-618`) never sets `summary`. Check how the wildcard
   expands over a four-element array and whether an absent or null `summary` resolves.
2. **Success-body validation.** `_validate_success_body` runs against the projected document; a
   closed model rejecting an omission or a null in a position it does not admit would raise here.
3. **Stored-response persistence.** `store_publish_response` / `load_publish_response` around
   `daemon.py:797-836`. Note `store_publish_response` already has a `PublicOperationError` escape
   hatch but nothing for an unexpected exception.

## Procedure

Do not write a fix first. The previous attempt (`0eee854`) improved the message without finding the
cause, which is why it recurred.

1. Build a subprocess-level reproduction from the exact recorded payload, in the style of
   `tests/subprocess/test_mcp_service_bridge.py`. That file is the vehicle — the failure needs the
   real bridge.
2. If it does not reproduce immediately, bisect the payload: drop the cross-event refs, then the
   `causal_parents`, then reduce four events to three to two, then vary the schema mix. The first
   variant that stops failing names the cause.
3. If it still will not reproduce, wait for run 4 with plan 01's diagnostic sink in place. The
   `correlation_id` and bounded reason token will then be recoverable instead of lost to stderr —
   which is precisely the gap that made this a research task rather than a lookup.
4. Only once it reproduces deterministically, fix it and pin the reproduction as a regression test.

## Files

Unknown until the cause is found. Expected surface: `src/yoetz/application/service.py`,
`src/yoetz/protocol/models.py` pointer sets, `src/yoetz/service/daemon.py`.

## Tests

- The exact recorded four-event batch replays through the bridge to a full, normally projected
  success — not merely to plan 01's reduced envelope.
- Whatever shape triggered it becomes a permanent case in the multi-event publish matrix.

## Done

Green CI, and the recorded batch produces a complete response.

## Dogfood observable

Run 4 shows a multi-event `publish_work` returning a **normal, complete** success — not the reduced
`accepted_projection_unavailable` envelope. If the reduced envelope appears, plan 01 is working and
this plan is not yet finished.

## Out of scope

Everything plan 01 already covers. If this hunt runs long, that is acceptable — plan 01 means the
product is no longer lying to agents in the meantime.
