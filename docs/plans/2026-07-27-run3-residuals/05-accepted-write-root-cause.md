# 05 — Find why the four-event publish response could not be projected

**Severity:** critical (cause), non-blocking (schedule) **PR boundary:** the specific projection defect + its regression test

**Status:** completed by PR #50.

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
- **Missing accepted-event summaries.** On this branch, `_publish_work_result_as_json` preserves
  total omission for an unset accepted-event `summary`, `_public_model` keeps that omission through
  the public-model round trip, and `_ACCEPTED_SUMMARY_DISCLOSURE_RULES` applies only when a summary
  leaf actually exists. An absent summary is neither materialized as `null` nor reported as
  policy-omitted, so `/accepted_events/*/summary` is no longer a live explanation. Any future
  `projection_pointer_unresolved` failure must identify a different pointer or a distinct
  failure-specific shape.
- **Stored-response persistence.** The recorded response can be stored and replayed byte-for-byte
  after successful projection; persistence was downstream of the failure.

## Confirmed cause

The internal publish result materialized an unset accepted-event summary as explicit JSON `null`.
For the recorded `claim_recorded` event, the shipped policy included the summary's
`finding_summary` category, so projection preserved the phantom null. Success-body validation then
rejected it because the closed wire model permits summary text, an omission marker, or total
absence — never null. PR #50 omits unset summaries at internal serialization and preserves that
omission through `_public_model`; the exact four-event batch now projects, stores, and replays as a
complete success.

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
