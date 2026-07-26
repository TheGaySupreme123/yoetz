# Codex-testing + Yoetz OpenRouter easy-linking dogfood

**Date:** 2026-07-26

**Disposition:** evaluation only; evidence retained on the experiment branch

**Experiment branch:** `codex/openrouter-easy-linking-dogfood-20260726`

**Repository baseline:** `b081f56` (`main` and `origin/main` at launch)

**Codex-testing:** `0.146.0-alpha.2`

**Codex session:** `019f9e66-003e-7350-9b05-9243b6c505db`

**Yoetz task:** `tsk_4b138fec-36bd-4df0-b0bf-448486453074`

**Final Yoetz receipt:** `rcp_077a3825-c85b-4696-a5ba-360842918695`

**Product-code disposition:** no product code changed

**GitHub disposition:** no issue or PR opened, per maintainer instruction

## 1. Executive verdict

The run was useful and materially better than the preceding OpenRouter dogfood, but it
was not a clean success.

Codex produced a strong repository and design assessment. It correctly discovered that
OpenRouter support was already structurally present, avoided inventing duplicate
provider plumbing, respected the no-code constraint, and clearly separated source
wiring from live interoperability. Its final answer correctly said that no OpenRouter
request occurred and that Yoetz's authoritative receipt remained
`insufficient_coverage`.

Yoetz helped Codex most with **epistemic discipline**:

- it required explicit activation rather than treating MCP registration as proof;
- it steered qualitative interoperability work to `semantic_required`;
- it returned external Fireworks provenance instead of an ungrounded semantic-success
  label;
- it repeatedly refused to upgrade weak evidence into a completion claim;
- its frontier/status model exposed that an operation reported as failed had actually
  committed.

Yoetz did not help Codex operate efficiently. Its event-writing contract was difficult
to use, validation responses were too generic, CLI and MCP readiness surfaces appeared
in conflict, and one committed publication returned `INTERNAL_ERROR`. Codex made
several payload mistakes, failed to close four obligations, and made one inaccurate
final claim that no runtime state changed.

### Bottom line

**Did Codex improve because of Yoetz?** Probably yes, modestly, in honesty, evidence
boundaries, and recovery from an ambiguous write. No controlled no-Yoetz run was
performed, so strict causal attribution is not possible. Relative to the 2026-07-25
dogfood, Yoetz's influence moved from **none** to **material and net positive**.

**Did Codex degrade?** Its repository reasoning did not degrade. Its operational
efficiency did: a large portion of the run was spent authoring and repairing Yoetz
protocol payloads. That is integration friction, not better reasoning.

**Did Yoetz give correct help?** Mostly yes at the policy and evidence-boundary level.
Its guidance and final receipt were correct. The successful semantic checks were valid
evidence that Fireworks and the Yoetz semantic pipeline worked, but were not evidence
that OpenRouter worked or that the overall task was complete. Yoetz correctly retained
that distinction. Its runtime and error surfaces were not fully healthy.

## 2. What was being tested

The initial assignment asked Codex-testing to add OpenRouter easy linking alongside
OpenAI and Fireworks. The actual purpose was to test how well Yoetz guided the agent,
not to improve the feature during the run.

The maintainer explicitly declined the repository's issue-intake step. Because provider
and privacy work is design-gated, the run was constrained to:

1. inspect current authority, implementation, and tests;
2. use Yoetz materially in automatic mode;
3. produce a no-code implementation or verification proposal;
4. preserve raw events and independent quality/health observations;
5. make no credential, privacy-policy, provider-binding, or product-code change.

Two independent monitors ran alongside Codex:

- the agent-quality monitor assessed practical reasoning, instruction following,
  Yoetz influence, verification, and final-answer honesty;
- the health monitor assessed actual MCP activation, service state, semantic dispatch,
  provenance, persistence, and receipt behavior.

## 3. Evaluation limits

This run does **not** establish a controlled causal effect because it did not run the
same model, prompt, repository state, and time budget once with Yoetz and once without
Yoetz.

The comparative judgment uses:

- within-run evidence of behavior directly prompted by Yoetz guidance and responses;
- the independent monitors' observations;
- the preceding 2026-07-25 OpenRouter dogfood, where Yoetz never established a session
  and its tooling influence was assessed as none.

Some good behavior also came from the assignment, `AGENTS.md`, ADRs, and Codex's own
repository inspection. Those effects cannot be cleanly separated from Yoetz. Claims
below therefore use “helped,” “likely influenced,” and “net effect” rather than claiming
scientific causality.

## 4. Timeline

### 4.1 Preflight

The experiment branch was created from clean `main`/`origin/main` at `b081f56`.
`codex-testing` was confirmed as the isolated alpha launcher, and its Yoetz MCP
registration was enabled.

Standalone readiness was poor:

- `yoetz provider status` reported `service_unavailable`;
- semantic readiness was indeterminate;
- Fireworks was the configured provider;
- credential and privacy readiness could not be read;
- `yoetz service status` recommended starting the user service.

This proved registration but not activation.

### 4.2 Activation

Codex discovered the six Yoetz tools and relevant guidance. It then called `start`
instead of treating discovery as proof. `start` succeeded and created:

- session `ses_db8200a5-c72f-4f05-add1-e1be32040673`;
- writer `wri_fd466f74-3495-4f32-b6d9-4f51ce02e0de`;
- initial frontier sequence `1`.

This was already an improvement over the prior dogfood: Yoetz had a real session and
could influence the work.

Separate CLI readiness still reported the user service unavailable. The later MCP
success shows that the stdio path had a usable local composition even though the CLI
surface did not communicate it.

### 4.3 Repository assessment

Codex inspected the authority chain, provider profiles, CLI setup, vault binding,
privacy gateway, runtime provider factory, Chat Completions adapter, ready composition,
tests, and prior postmortems.

It correctly changed the premise of the assignment:

- OpenRouter profile and exact endpoint already existed;
- CLI setup already accepted OpenRouter;
- credential binding and encrypted storage were already defined;
- privacy authorization remained independent;
- the runtime factory already selected the Chat Completions adapter;
- request construction, normalization, provenance, and focused tests existed;
- live capability evidence remained explicitly absent.

The correct remaining scope was therefore UX simplification and authorized live
verification—not a new generic endpoint abstraction or duplicate runtime factory.

### 4.4 First Yoetz publication

Codex tried to publish its plan and obligations. The first request used a malformed
event UUID and was rejected as `INVALID_REQUEST`. The error did not identify the
failing field. Codex diagnosed the payload, corrected it, and advanced the frontier
from `1` to `6`.

This demonstrates both sides of the run:

- Codex was willing to use Yoetz materially and recover;
- routine ledger authoring imposed substantial protocol overhead.

### 4.5 Semantic checks and receipts

Codex requested `semantic_required`. Three checks ultimately succeeded through the
configured Fireworks profile, with distinct:

- provider request IDs;
- egress authorization IDs;
- semantic attempt IDs;
- request commitments;
- privacy receipt IDs.

This is real live evidence for the Yoetz → privacy gateway → Fireworks → normalization
→ provenance → durable-check path.

It is **not** OpenRouter evidence. Codex preserved that boundary in its final report.

Early receipts returned `insufficient_coverage`. This was correct: the ledger primarily
contained a plan and open obligations, with self-asserted, published-only,
metadata-only evidence. Repeating semantic checks could not repair those evidence
limitations.

### 4.6 Final publication and ambiguous commit

Codex attempted to publish final action/result/evidence/claim events:

1. it used `digest` instead of `head_digest` in the frontier;
2. after correcting the frontier, it used invalid event enum values;
3. after reading the exact schemas and correcting the events, the four-event batch
   returned `INTERNAL_ERROR`.

The third request had actually committed. A subsequent write against the old frontier
returned `FRONTIER_CONFLICT` showing sequence `14`, and status confirmed the new
records.

This is the most important Yoetz health defect in the run. The durable ledger remained
truthful and recoverable, but the public response created an unknown outcome after
commit. Codex's first retry was not an idempotent replay of the original operation, so
a less careful agent could have duplicated work.

Yoetz nevertheless helped recovery: the frontier conflict and status read exposed
authoritative persisted state instead of allowing Codex to assume the write had failed.

### 4.7 Final check and receipt

At frontier `14`, a final `semantic_required` check succeeded via Fireworks and
advanced to `15`. Both requested policy packs ran, no findings were returned, and full
external provenance was present.

The final durable receipt advanced the ledger to `16` and concluded:

- `insufficient_coverage`;
- self-asserted authorship;
- published-only observation;
- metadata-only immutability;
- partial freshness;
- `check_not_applicable`;
- four obligations still open before the final check.

That receipt is the authoritative ceiling. The run demonstrated useful assessment and
a working semantic pipeline, not verified OpenRouter interoperability or complete
Yoetz closure.

## 5. What went well

### 5.1 Codex reasoning and scoping

Codex did not blindly implement the wording of the prompt. It discovered the current
product truth and reframed the work. This avoided:

- duplicate OpenRouter provider wiring;
- a dangerous generic `base_url` escape hatch;
- inheritance of Official OpenAI data-use claims;
- credentials or privacy authorization being treated as implied by provider binding.

The final proposal was source-rich and technically aligned with ADR-006, ADR-009, and
ADR-014.

### 5.2 Evidence honesty

The strongest final-answer behavior was the explicit separation of:

- structural source support;
- successful live Fireworks semantic dispatch;
- historical postmortem evidence;
- unverified live OpenRouter behavior;
- the insufficient final receipt.

Codex did not hallucinate OpenRouter success and did not use catalog visibility or
provider setup as a substitute for a physical request.

### 5.3 Yoetz guidance content

The core guidance was correct:

- activate before claiming Yoetz is active;
- use `semantic_required` for interoperability, privacy, and design judgments;
- publish bounded work and evidence;
- inspect status and frontier state;
- obtain a receipt;
- word the final answer according to the weakest coverage.

Codex followed the most important parts, particularly activation, semantic mode, and
receipt-bounded wording.

### 5.4 Semantic and privacy pipeline health

The run proved more than MCP registration. Fireworks checks carried external
provenance through the independently authorized privacy path and durable receipt
records. This was genuine operational evidence.

### 5.5 Fail-closed receipt behavior

Yoetz did not let successful model calls become a blanket completion claim. Every
receipt remained insufficient while the evidence and obligations were insufficient.
This was correct help, even though it was not the result the agent wanted.

### 5.6 Durable-state recovery

After the false `INTERNAL_ERROR`, frontier conflict and status exposed the committed
truth. The recovery model worked better than relying on the failed response alone.

## 6. What did not go well

### 6.1 No controlled A/B baseline

The experiment can compare against history and trace direct interactions, but it cannot
quantify how the same Codex would have performed without Yoetz. Future causal
evaluations should run paired, equivalent tasks with fixed budgets.

### 6.2 Excessive discovery and trace noise

Codex started with broad repository searches that pulled very large generated schemas
into the event stream. It also guessed a nonexistent guidance path before locating the
correct resources. This increased token use and reduced audit readability.

### 6.3 Protocol authoring friction

Codex repeatedly failed to produce valid routine requests:

- malformed event UUID;
- wrong frontier key;
- invalid event enums;
- missing `writer_id` on status.

Strict rejection was correct, but the public errors were too generic to enable quick
recovery. Yoetz guidance explained the workflow better than the interface supported
executing it.

### 6.4 Checks were spent before evidence was ready

Codex repeated semantic checks and receipt requests while the ledger still contained
open planning obligations and weak evidence. The results were predictably insufficient.
Guidance should make the preconditions for a useful check/receipt more operational and
machine-actionable.

### 6.5 The accepted-write `INTERNAL_ERROR`

This is the most serious defect. Returning failure after a durable commit violates the
caller's ability to know whether retry is safe. The precise post-commit stage was not
identified during the run.

### 6.6 CLI/MCP readiness ambiguity

The standalone CLI said the service was unavailable while MCP-local work and external
semantic dispatch succeeded. These may represent different lifecycles, but the product
did not explain that distinction clearly.

### 6.7 Incomplete closure

Four obligations remained open and the receipt was insufficient. Codex nonetheless
marked its internal “receipt closure” todo complete. Operational completion and
successful receipt issuance were conflated with a sufficient completion conclusion.

### 6.8 One inaccurate final statement

Codex claimed that no runtime state changed. This is false: it created and advanced a
Yoetz task, checks, semantic/privacy receipt state, and durable ledger events.

The intended and supportable statement was:

> No product source, provider configuration, credential binding, or privacy
> authorization was changed.

Yoetz did not prevent this wording error. Its final semantic check assessed the
published ledger at that point, not necessarily the exact final prose subsequently
emitted.

## 7. Did Yoetz improve Codex?

### 7.1 Comparative scorecard

| Dimension | Prior OpenRouter dogfood | This run | Yoetz effect |
| --- | --- | --- | --- |
| Activation | No Yoetz session | Real MCP session | Clear improvement |
| Authority-chain reasoning | Strong without Yoetz | Strong | No demonstrated change |
| Structural vs live distinction | Honest | Honest and receipt-bounded | Small improvement / reinforcement |
| Semantic evidence | None | Three live Fireworks checks with provenance | Clear improvement |
| OpenRouter live proof | None | None | No change |
| Completion honesty | Prompt/repo-derived | Receipt explicitly prevented overclaim | Improvement |
| Ledger closure | No ledger | Ledger present but four obligations open | Better observability, incomplete result |
| Operational efficiency | No Yoetz workflow cost | Many payload failures and retries | Degradation |
| Unknown-outcome recovery | Not exercised | Frontier/status exposed committed truth | Improvement |
| Final prose accuracy | Honest | One false runtime-state sentence | Small degradation |

### 7.2 Net judgment

The net effect was **positive but modest**.

Yoetz materially improved the run's auditability, live semantic evidence, evidence
ceilings, and recovery from ambiguous persistence. It likely reinforced Codex's correct
decision not to claim OpenRouter success.

It did not improve the underlying repository analysis, which was already strong. It
did not produce closure. Its interface substantially degraded efficiency and introduced
new failure opportunities. One final false statement also escaped the process.

A fair rating is:

- **Codex repository reasoning:** strong, essentially unchanged;
- **Codex epistemic honesty:** improved;
- **Codex Yoetz execution:** moderate;
- **Codex efficiency:** degraded;
- **overall Codex output quality:** modestly improved;
- **causal confidence:** medium-low without an A/B control.

## 8. How well did Yoetz do?

### 8.1 Correctness of help

**Mostly correct.**

Correct help included:

- requiring a real start;
- selecting `semantic_required`;
- warning against completion claims with weak coverage;
- emitting real external provenance;
- refusing sufficient coverage;
- exposing authoritative frontier state after the ambiguous response.

The semantic result `no_issue_detected` should be read narrowly: the policy checks did
not find an issue in the published subject at that frontier. It did not certify source
inspection, OpenRouter behavior, or task completion. Yoetz's receipt correctly kept
those broader claims out of scope.

### 8.2 Usefulness

**Useful for governance and evidence; cumbersome for execution.**

The high-level workflow and coverage concepts were valuable. The low-level event API
was too easy to misuse, and the validation surface did not provide enough safe,
actionable structure.

### 8.3 Runtime health

**Functional but not clean.**

- MCP activation: healthy.
- Fireworks semantic/provider/privacy path: healthy.
- Receipt durability and honesty: healthy.
- OpenRouter path: untested.
- Standalone readiness communication: confusing.
- Validation ergonomics: weak.
- Post-commit response integrity: unhealthy and high priority.

### 8.4 Overall Yoetz rating

For this run:

- guidance correctness: **good**;
- evidence honesty: **very good**;
- practical ergonomics: **poor to moderate**;
- runtime reliability: **mixed**;
- overall: **materially useful, not production-clean**.

## 9. Root causes

### Root cause A: task premise lagged current product state

The request assumed OpenRouter needed adding, while current source already contained
the structural path. Codex handled this well by reframing the task.

### Root cause B: evidence workflow began before a closure model was clear

The agent published planning obligations, then spent checks and receipts without first
deciding how those obligations would be satisfied or explicitly closed in a read-only
assessment.

### Root cause C: event APIs expose too much frozen wire complexity

Ordinary agent actions required manually correct UUIDs, frontier objects, enum values,
and event-specific payload schemas. Guidance alone did not make this fluent.

### Root cause D: safe errors lacked actionable field-level structure

The errors protected content but made recovery depend on manual schema archaeology.
Privacy-safe diagnostics and useful diagnostics were treated as more mutually exclusive
than necessary.

### Root cause E: response projection failed after durable commit

The accepted batch advanced the ledger, but the bridge returned `INTERNAL_ERROR`. The
exact failing projection or response stage remains to be reproduced.

### Root cause F: lifecycle states are not presented as distinct products

CLI user-service status and MCP-local composition behaved differently without a clear
operator-facing explanation.

## 10. Recommended actions

### P0 — Eliminate ambiguous post-commit failure

Reproduce the frontier `10` → `14` publication using the retained event stream.
Identify the failing post-commit stage, guarantee stable replay by request/event ID,
and add a regression proving that an accepted write cannot be surfaced as an
unqualified failure.

### P1 — Make publication agent-native

Provide canonical helper operations or typed builders for common plan, obligation,
action, result, evidence, claim, and obligation-resolution events. Agents should not
need to hand-author the full frozen wire protocol for routine work.

### P1 — Return safe structured validation locations

Return JSON-pointer-like field locations, expected enum/schema identifiers, and
content-free reason codes. Do not echo user content.

### P1 — Clarify service lifecycle

Make provider/service readiness explicitly distinguish:

- user-service unavailable;
- MCP-local composition available;
- vault/provider/privacy readiness for each path.

### P1 — Add a pre-receipt closure checklist

Before spending another check or receipt, guidance/status should state which open
obligations, evidence gaps, or claim types prevent a useful completion conclusion.

### P2 — Test guidance efficacy with an A/B harness

Run paired Codex-testing tasks with:

- identical model/version/effort;
- fixed repository commit and prompt;
- fixed time/token budget;
- Yoetz enabled versus disabled;
- blinded reviewers;
- measures for correctness, unsupported claims, tool failures, tokens, elapsed time,
  closure, and recovery quality.

### Separate authorized test — OpenRouter E-007

With explicit operator authority, provision an exact OpenRouter credential and
independent `llm_inference` privacy authorization, then verify:

- exact host/path/profile;
- one physical request;
- deterministic request commitment;
- actual `response_format` behavior;
- valid judgment normalization;
- refusal, timeout, malformed output, and provider-error mapping;
- final external provenance and durable privacy receipt.

This dogfood must not be cited as that proof.

## 11. Preserved evidence

All local evidence is under:

`docs/dogfood/2026-07-26-openrouter-easy-linking/`

- `README.md` — run index;
- `codex-testing-prompt.md` — exact assignment;
- `codex-testing-events.jsonl` — complete public JSONL event stream;
- `codex-testing-final.md` — Codex's final response;
- `agent-quality-monitor.md` — independent practical-quality audit;
- `yoetz-health-monitor.md` — independent runtime-health audit;
- `final-synthesis.md` — concise combined synthesis.

The raw isolated Codex session path is recorded in the health monitor. Secrets,
credential values, and private vault contents were not copied into these artifacts.

## 12. Final answer

Yoetz did give Codex correct and valuable help, chiefly by enforcing evidence
boundaries and retaining an honest insufficient-coverage conclusion. This moved its
influence from absent in the previous dogfood to material in this one.

Codex became more trustworthy, but not more fluent. Repository reasoning stayed
strong; honesty improved; execution efficiency degraded; closure remained incomplete.
Yoetz therefore earned a **net-positive but not yet production-clean** verdict.
