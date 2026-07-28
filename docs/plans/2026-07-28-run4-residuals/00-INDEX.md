# Run-4 residuals: making the reviewer speak

**Date:** 2026-07-28

**Source:** [`docs/dogfood/2026-07-28-chatgpt-oauth-signin/`](../../dogfood/2026-07-28-chatgpt-oauth-signin/)
and the live reproductions recorded in each plan.

Six defects, six sequential PRs. **The numbering is execution order.** Work them 1 through 6. Each
plan is self-contained: its own PR boundary, its own tests, and what it deliberately leaves alone.

Drafted against `main` at `d3274a7` (the run-4 baseline, PRs #43/#45/#47/#49/#50 merged).

## The thing this set is actually about

Semantic review is the second reviewer: a model that reads the frozen case and returns *explanation
and advice* the deterministic policies cannot produce. That value is delivered in exactly one place
— the `findings` array of a check response.

`check` cannot return any response containing a finding. It never could. So **semantic advice has
never once reached an agent.** The two semantic successes ever observed — run 3 `item_62`, and the
live Check A on 2026-07-28 — both returned `findings: []`, so they had nothing to carry and the
defect stayed hidden. The first time semantic has something to say, it fails exactly as run 4's
three deterministic checks did.

That is why plan 01 is first and plan 02 is second. Everything else follows.

## The order

| # | Plan | Defect | Why here |
| --- | --- | --- | --- |
| 01 | [Check response findings](01-check-response-findings.md) | `check` cannot return any response containing a finding | The reviewer's only delivery channel is broken. Nothing semantic can matter until this lands. Also builds the result-model sweep harness plan 04 reuses. |
| 02 | [Semantic advice delivery](02-semantic-advice-delivery.md) | A semantic finding has never been projected; semantic can decline to run silently | Proves the channel opened by 01 actually carries semantic advice with its provenance, and makes a non-dispatch always say why. |
| 03 | [Check projection and coverage](03-check-projection-and-coverage.md) | A check leaves no trace and resets coverage to `["none"]` | A successful semantic check currently erases the record that semantic verification happened. Honesty defect, and semantic-relevant. |
| 04 | [Null optional sweep](04-null-optional-sweep.md) | Unset optional projected as explicit `null` | Breaks the *default* status view. Third sighting of one class; this plan ends the class. |
| 05 | [Correlation id propagation](05-correlation-id-propagation.md) | The id in an agent-facing error resolves to nothing | Instruments everything after it. Small. |
| 06 | [Status operation view](06-status-operation-view.md) | `status view=operation` raises `AttributeError` | The recovery surface shipped in PR #47 is unusable. Isolated. |

## What run 4 established about semantic

Worth stating plainly, because it redirects effort.

**Semantic dispatch works.** Verified live twice on 2026-07-28 against the running service
(generation 28): `semantic_status: succeeded`, `provider_request_id: resp_357677ac6e1f470c…` and
`resp_a7ff518926644e7e…`, latency 8437 ms, full provenance chain (egress authorization id, privacy
receipt id, prompt digest, request commitment, schema digest, sampling params), and
`coverage.check_types: ["deterministic", "semantic_model_derived"]`.

**There is no evidence of a semantic defect.** Run 4's semantic outcome is *unknown*, not *failed*:
its check response was destroyed by plan 01's defect before `semantic_status` could be read, and
plan 03's defect meant the durable projection never stored a second copy.

**`semantic_jobs` and `semantic_attempts` are not the record of dispatch.** Both tables are empty
even for a check that demonstrably dispatched. Do not use them as dispatch evidence.

**The semantic *finding* path has never executed.** Dispatch succeeding and advice arriving are
different things. `validate_semantic_judgment` → `CheckProjectedFindingModel` with
`origin: "semantic_model_derived"` and non-null `provenance` has never been exercised against a
real provider response. Plan 02 owns that.

## Ordering rationale

**The delivery channel leads.** Plan 01 is the only defect that makes an operation unusable, and it
is the sole blocker on semantic advice. Plan 02 then proves the channel carries the payload.

**File conflicts are light.** Plans 01 and 04 both live around public result-model validation and
its tests, so 01's sweep harness is what 04 uses to prove its class is closed. Plan 02 touches
`service/ready_composition.py` and `application/check.py`; plan 03 is confined to
`adapters/sqlite/repository.py`; plan 06 to `application/status.py`; plan 05 touches
`ports/control.py`, `service/daemon.py`, and `mcp/server.py`. Plans 03-06 can be reordered among
themselves without cost.

## Standing decisions

Carried over from the run-3 residual set; not re-litigated inside each plan.

- **Contract freedom: fully open, pre-1.0.** Where the right design requires it, these PRs may
  change wire shapes, add fields, and regenerate schemas, golden vectors, and the resource
  manifest. A contract change must update `docs/INTERFACES.md`, the affected ADR, the JSON Schemas
  under `schemas/`, the vectors under `fixtures/`, and the manifest digests in the same PR.
  `generate_schemas` does not own every schema — some are hand-authored, and their manifest digests
  must be refreshed by hand.
- **One PR per plan, landed sequentially.** The order above is about severity and rebase cost.
- **Done bar: code + regression tests.** Each PR lands green with tests at the layer the defect
  actually lives in. No installed-wheel gate inside the PRs.
- **Proof is dogfood.** Once these land, a run-5 dogfood is the acceptance evidence for the set.
  Each plan names the observable a dogfood must show.

## What the set does not address

- The dead entity tables `p1_query_checks`, `p1_query_findings`, `p1_query_obligations` and their
  siblings. Plan 03 deliberately fixes only the aggregate and the coverage lie.
- Closure ergonomics. Run 4 ended with two acknowledged-but-unresolved findings and no receipt —
  honest, but with no route to closure. A design question, not a code defect.
- A controlled Yoetz-enabled versus Yoetz-disabled A/B.
