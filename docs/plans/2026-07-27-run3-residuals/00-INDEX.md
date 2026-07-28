# Run-3 residuals: making Yoetz ready

**Date:** 2026-07-27

**Source:** [`docs/postmortems/2026-07-27-codex-testing-yoetz-grok-easy-linking-run3.md`](../../postmortems/2026-07-27-codex-testing-yoetz-grok-easy-linking-run3.md)
and the raw trace under `docs/dogfood/2026-07-27-grok-easy-linking/`.

Four defects, five PRs — the first defect splits into a bounded fix and an unbounded hunt. **The
numbering is execution order.** Work them 1, 2, 3, 4, then 5. Each plan is self-contained: its own
PR boundary, its own tests, and what it deliberately leaves alone.

All five are open against `main` at `f3d1e62` (PR #40 merged, PR CI green).

| # | Plan | Defect | Why here in the order |
| --- | --- | --- | --- |
| 01 | [Accepted-write safety net](01-accepted-write-safety-net.md) | Durable write reported as `INTERNAL_ERROR` | Owns the publish result contract change, so it lands before 03 and 04 touch the same models. Its diagnostic sink also instruments everything after it. |
| 02 | [Grok interactive selector](02-grok-interactive-selector.md) | `--grok` ignored on an interactive TTY | Touches only `cli/app.py` — zero conflict with anything else, small, and a clean green after the big one. Genuinely parallel if you prefer. |
| 03 | [Publish replay recovery](03-publish-replay-recovery.md) | Documented recovery path unreachable | Rebases cheaply onto 01. Adds an error code and a status view. |
| 04 | [Event authoring](04-event-authoring.md) | 9 of 17 MCP calls surfaced as failures | Largest measured friction reduction, and the cheapest of the three to rebase, so it goes last of the bounded work. |
| 05 | [Accepted-write root cause](05-accepted-write-root-cause.md) | Why projection failed | Duration unknown. Start the reproduction early, land it whenever it resolves — it must never block 01-04. |

### Ordering rationale

Two things drive the sequence, and neither is severity.

**File conflicts.** Plans 01, 03, and 04 all touch `src/yoetz/application/publish_work.py` and
`src/yoetz/protocol/models.py`, so they are serial. Plan 02 touches only `src/yoetz/cli/app.py` and
its tests and can be slotted anywhere. Plan 01 goes first among the three because it changes the
publish result *shape*; the others add to it. Reversing that costs a second round of schema, vector,
and manifest regeneration.

**Unknown duration.** Plan 05 is research, not implementation. Splitting it out of 01 is what keeps
one unknown from stalling four bounded PRs — and plan 01's diagnostic sink is what makes 05
tractable, since the run-3 `correlation_id` currently resolves to nothing at all.

## Standing decisions

Decided for the whole set; not re-litigated inside each plan.

- **Contract freedom: fully open, pre-1.0.** v0.1 is unreleased. Where the right design requires it,
  these PRs may change wire shapes, add fields, add operations or views, and regenerate schemas,
  golden vectors, and the resource manifest. Breaking the "frozen contract" framing is permitted;
  silently breaking it is not — a contract change must update `docs/INTERFACES.md`, the affected
  ADR, the JSON Schemas under `schemas/`, the vectors under `fixtures/`, and the manifest digests in
  the same PR. `generate_schemas` does not own every schema — some are hand-authored, and their
  manifest digests must be refreshed by hand.
- **One PR per plan.** No plan depends on another plan's code landing first; the order above is
  about rebase cost, not correctness.
- **Done bar: code + regression tests.** Each PR lands green with tests at the layer the defect
  actually lives in. No installed-wheel gate inside the PRs.
- **Proof is dogfood.** Once these land, a run-4 dogfood is the acceptance evidence for the set.
  Plans 01, 03, and 05 have never reproduced in-process — they only ever fired on the live daemon
  plus MCP bridge path — so each names the observable a dogfood must show.

## What the set does not address

- No live xAI credential, egress, request, or receipt. Grok remains structurally implemented and
  live-unproven.
- No controlled Yoetz-enabled versus Yoetz-disabled A/B.
- The `respond` finding-disposition path stays untested by these PRs. Three dogfoods in a row have
  failed to exercise it because no check produced a finding. Fixing that is a run-4 design problem,
  not a code defect — the run must be built so a finding is expected.
