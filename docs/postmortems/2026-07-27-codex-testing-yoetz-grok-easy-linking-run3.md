# Codex-testing + Yoetz easy-linking dogfood, attempt 3

**Date:** 2026-07-27

**Experiment branch:** `codex/grok-easy-linking-dogfood-20260727`

**Repository baseline and current `HEAD`:**
`3da640a9d4999d38149b2e996dc84ae87edc0295` (`main` and `origin/main` at launch)

**Codex-testing:** `0.146.0-alpha.2`

**Model:** `gpt-5.6-luna`, high effort

**Codex session:** `019fa475-e0f9-7640-a742-6a0828962146`

**Yoetz task:** `tsk_861ccfd3-2781-4d92-91c9-96e4215b28cb`

**Yoetz session:** `ses_52384bf1-22c4-48eb-8a8a-9ac71379e874`

**Final Yoetz receipt:** `rcp_4b302cb2-a9f1-41a2-99fb-aa74b60372c6`

**Disposition:** all product and dogfood changes remain uncommitted; no issue, PR, push, or merge

## 1. Executive verdict

This was the strongest of today's three attempts, but it was not a full end-to-end Grok/xAI
success.

The requested implementation mostly worked well:

- Codex found the real gap and added the smallest coherent Grok/xAI path rather than inventing a
  second credential, privacy, transport, or receipt system.
- The uncommitted change adds an exact `xai-openai-chat-completions` profile pinned to
  `https://api.x.ai/v1`, CLI shorthands, aliases, factory selection, tests, ADRs, and operator
  documentation.
- The current working tree passes 72 focused tests, Ruff lint and format, Pyright, and the
  711-file public-boundary scan.
- Official xAI documentation currently supports the selected
  [base URL](https://docs.x.ai/developers/rest-api-reference/inference),
  [`/v1/chat/completions`](https://docs.x.ai/developers/rest-api-reference/inference/chat),
  [`response_format.type=json_schema` and strict schemas](https://docs.x.ai/developers/model-capabilities/text/structured-outputs),
  and the [`grok-4.5` model ID](https://docs.x.ai/developers/models/grok-4.5).
- The agent correctly did not claim a live Grok result. No xAI credential, authorized xAI egress,
  provider request ID, installed-wheel exercise, or xAI receipt exists.

Yoetz also mostly worked:

- it was activated, not merely registered;
- the task reached a durable plan, claim, evidence, semantic check, receipt, receipt replay, and
  final status;
- status recovered the authoritative frontier after an ambiguous write;
- the live semantic check carried real Fireworks provenance and non-placeholder policy digests;
- the receipt retained both deterministic and semantic-model-derived coverage; and
- the final receipt remained explicitly coverage-bounded.

Yoetz did not work fluently:

- two of three `start` calls failed before activation;
- only one of seven `publish_work` calls returned a normal success;
- the four-event completion publication committed but returned
  `INTERNAL_ERROR/response_projection_failed`;
- the agent corrupted both attempted publish replays, so stored-result recovery remains unproven;
- the agent spent avoidable calls learning envelopes, enum values, UUID shape, and replay behavior;
  and
- the `respond`/finding-disposition path was not exercised because the check returned no findings.

### Bottom line

| Question | Verdict |
| --- | --- |
| Did attempt 3 complete the requested local implementation? | **Mostly yes** |
| Did the uncommitted implementation look good? | **Yes, with one small CLI edge-case and missing live/installed proof** |
| Did Yoetz materially function? | **Yes** |
| Did Yoetz function fully and cleanly? | **No** |
| Was Yoetz better than attempts 1 and 2? | **Clearly better overall** |
| Is Grok/xAI live-proven? | **No** |
| Is Yoetz production-clean for ordinary agent use? | **Not yet** |

The fairest summary is:

> **Attempt 3 shows that Yoetz can now close a useful cooperative integrity loop and preserve
> semantic receipt truth. It is mostly working, materially better than earlier today, and still too
> error-prone at the publication boundary to call fully working.**

## 2. What changed

The product diff contains 172 insertions and 37 deletions across 14 tracked files:

- configuration preset and aliases in `src/yoetz/config/write.py`;
- exact xAI factory facts in `src/yoetz/adapters/providers/factory.py`;
- CLI and interactive setup routing in `src/yoetz/cli/app.py`,
  `src/yoetz/cli/provider_binding.py`, and `src/yoetz/cli/setup.py`;
- four focused test files; and
- `INTERFACES`, `OPEN_QUESTIONS`, ADR-006, ADR-012, and provider usage documentation.

The implementation uses the existing Chat Completions, confidential credential, privacy gateway,
semantic normalization, provenance, and receipt machinery. That is the right scope. A new provider
adapter or a generic free-form base URL would have widened the trust boundary without need.

The raw dogfood artifacts are preserved under
`docs/dogfood/2026-07-27-grok-easy-linking/`.

## 3. Evidence levels

This report separates three evidence classes.

### Independently rechecked for this postmortem

- local `HEAD` and `origin/main` both resolve to `3da640a`;
- the current diff is 14 tracked product files, +172/-37;
- `git diff --check` passes;
- focused pytest: **72 passed in 6.90 seconds**;
- Ruff lint: passed;
- Ruff format: all 9 changed Python/test files formatted;
- Pyright: **0 errors, 0 warnings, 0 informations**;
- public-boundary scan: **PASS, 711 files scanned**;
- raw Codex event order, arguments, structured results, and final token counters;
- receipt coverage contains `deterministic` and `semantic_model_derived`;
- semantic provenance uses the nonzero policy digest
  `sha256:17d5bed3bcce064f2c104b7070d7c071fef0ce7181e28832bd2aab0d890016d1`,
  equal to the recorded privacy-policy digest; and
- current official xAI documentation for the endpoint, structured output, and model ID.

### Recorded by the attempt and consistent with the raw trace

- temporary-directory nonsecret binding/factory exercise;
- the personal Fireworks binding remained unchanged;
- no credential appeared in the config or report;
- the semantic dispatch used Fireworks `minimax-m3`;
- receipt replay returned the same receipt ID and digest; and
- no issue, commit, push, or PR was created.

### Not established

- a built wheel containing the Grok changes;
- reinstall into isolated `codex-testing`;
- live xAI credential storage or service readiness;
- authorized xAI egress;
- a physical xAI request;
- xAI provider provenance or receipt;
- remote CI or code review; or
- a controlled Yoetz-enabled versus Yoetz-disabled A/B result.

## 4. Comparison with today's earlier attempts

The comparison is longitudinal, not controlled. The prompts, repository baselines, target features,
and intervening remediation differ.

| Dimension | Attempt 1: OpenRouter follow-up | Attempt 2: OpenRouter run 2 | Attempt 3: Grok/xAI |
| --- | --- | --- | --- |
| Product outcome | Blocked correctly at issue-intake gate; no product edits | Strong five-file shortcut implementation | Stronger 14-file preset, factory, CLI, tests, and authority update |
| Main-agent quality | Process-correct and honest; implementation unmeasurable | Strong, observer score 8/10 | Strongest implementation and honesty; this review 8.5/10 |
| Yoetz net effect | Honest blocker record, but operationally immature | Positive but expensive, observer score 6/10 | Positive and materially healthier, this review 7.5/10 |
| Compact status | Repeated projection failures | Coverage later became inconsistent | Healthy after ambiguous write and at final frontier |
| Accepted-write response | Projection failure | Projection failure | **Still projection failure** for four-event batch |
| Publish replay | Failed | Failed | Unproven because agent changed both replay bodies |
| Semantic provenance | Fireworks, not OpenRouter | Fireworks; policy digests were placeholders | Fireworks; policy digests are real and match privacy authority |
| Receipt coverage | Honest but hard to interpret | Dropped successful semantic coverage | Preserved deterministic + semantic-model-derived coverage |
| Terminal closure | Blocked/insufficient | Receipt did not cover the final worktree cleanly | Product files stable before receipt; final status current |
| Target-provider live proof | None | None | None |

### What demonstrably improved

1. **A more substantial task completed.** Attempt 3 added a new exact provider preset and runtime
   selection path, not only a root shortcut.
2. **Receipt/check applicability improved.** The semantic check survived into receipt coverage,
   directly closing the central attempt-2 defect.
3. **Semantic provenance improved.** Policy fields are no longer all-zero placeholders and agree
   with the privacy-policy digest used for dispatch.
4. **Frontier recovery improved.** Compact and evidence status both recovered the exact durable
   frontier after publication response failure.
5. **Terminal discipline improved.** No material product edit occurred after check/receipt.
6. **Provider attribution stayed honest.** Fireworks semantic proof was never relabeled as Grok
   proof.

### What did not improve enough

1. **The accepted-write response defect remains.** A valid four-event batch advanced the frontier
   from 2 to 6 but surfaced as an internal error.
2. **Replay ergonomics remain fragile.** The recovery contract required the same request ID and
   exact body; the agent twice changed identifiers inside the body.
3. **Envelope authorability remains weak.** Nine of 17 Yoetz MCP calls surfaced as failed calls:
   two `start`, six `publish_work`, and one receipt typo. One failed publish was durably accepted,
   but the user-facing success rate was still poor.
4. **No live target-provider proof exists.** Like both OpenRouter attempts, the external check used
   Fireworks.
5. **No installed-artifact proof exists.** The changed source tree was tested with `uv run`; the
   isolated MCP service was the pre-existing installed Yoetz.

## 5. How well Yoetz did

### 5.1 Durable and security-sensitive core: strong

The strongest Yoetz behavior was below the agent-facing response layer:

- atomic four-event append;
- exact safe durable frontier after projection failure;
- zero projection lag in authoritative status;
- fail-closed validation;
- local-disclosure omission of protected excerpts;
- independently authorized Fireworks dispatch;
- provider request ID, semantic attempt ID, prompt/schema digests, policy digests, and privacy
  receipt;
- stable receipt ID/digest on replay; and
- bounded conclusion rather than a correctness guarantee.

This core merits approximately **9/10** for the exercised path.

### 5.2 Agent-facing control surface: mixed

The control surface still imposed too much protocol work:

- empty and guessed startup envelopes;
- empty publication probing;
- an undiscoverable nested `action_kind` enum;
- manually invented UUIDs;
- a successful durable write presented as `INTERNAL_ERROR`;
- recovery requiring byte-identical semantic content that the agent failed to preserve; and
- no typed event builder or recovery operation that avoids resending a complex body.

This surface merits approximately **5.5/10**.

### 5.3 Guidance and practical effect

Yoetz materially improved:

- durable claim/evidence publication;
- provider attribution;
- explicit structural-versus-live wording;
- recovery through status rather than blind duplicate writes;
- semantic-required review; and
- receipt-backed, coverage-bounded closure.

Yoetz did not materially design the Grok change. The repository authority chain, prior OpenRouter
postmortems, existing provider patterns, and official xAI documentation explain the implementation
choices. The semantic check returned no findings that changed the patch.

### 5.4 Overall Yoetz score

**7.5/10 — mostly working and meaningfully better, not fully fluent or production-clean.**

The score is higher than attempt 2 because receipt integrity, policy provenance, status recovery,
and terminal closure all improved. It is capped by the persistent publication response failure,
authoring friction, unproven publish replay, and absence of live Grok proof.

## 6. How well the agents did

### 6.1 Main implementation agent

| Dimension | Score | Assessment |
| --- | --- | --- |
| Scoping and authority | 9/10 | Found the exact missing preset/operator surface and avoided duplicate trust machinery |
| Implementation | 9/10 | Coherent 14-file change; exact host/path; existing factory and privacy boundaries reused |
| Verification | 9/10 | Focused tests, lint, format, typecheck, temp runtime exercise, help, diff, and boundary scan |
| Security/privacy | 10/10 | No credential or personal binding mutation; unknown data-use preserved |
| Honesty | 10/10 | Explicit Fireworks-versus-Grok fence and no live/installed overclaim |
| Yoetz fluency | 5/10 | Multiple envelope mistakes, two corrupted publish replays, one receipt writer typo |
| Efficiency | 7/10 | Actual launcher metadata indicates about 14 minutes, but 36,848 output tokens and repeated protocol repair were still expensive |
| **Overall** | **8.5/10** | Excellent engineering and honesty; protocol fluency and one missed CLI edge-case keep it below outstanding |

The raw completion counter reports 10,046,813 input tokens, but 9,672,448 were cached
(96.27%). It is misleading to describe that as ten million unique input tokens. The exact launcher
metadata records `16:43:34Z` to `16:57:47Z`, approximately 14 minutes 13 seconds.

### 6.2 Observer agents

The two observer agents added real value:

- they kept practical agent quality separate from Yoetz runtime health;
- they caught the distinction between durable acceptance and failed response projection;
- they identified the two mutated replay bodies;
- they discounted the main agent's slight causal over-attribution to Yoetz;
- they verified Fireworks provenance rather than trusting the target-provider label; and
- they preserved scores, IDs, frontiers, and limitations in durable artifacts.

Their reports are not perfect:

- `agent-quality-live.md` labels observations as `18:05Z` through roughly `18:35Z`, while launcher
  metadata says the run ended at `16:57:47Z`; raw JSONL has item order but no timestamps, so those
  wall-clock labels are not reliable;
- the agent-quality efficiency score of 5/10 relies partly on an apparent 90-minute chronology
  contradicted by launcher metadata;
- the Yoetz-health header says the “six-ops path” was exercised even though `respond` was not
  called; and
- neither observer found the CLI predicate omission described below.

**Observer-agent score: 7.5/10.** The factual trace analysis is valuable, but timing and completeness
claims need tighter reconciliation against primary metadata.

## 7. Uncommitted-change review finding

### P2 — `provider endpoint --grok` can ignore the selector on an interactive TTY

In `src/yoetz/cli/app.py`, the generic interactive-picker predicate excludes `official` and
`fireworks` but not `grok`. With an interactive terminal, this command:

```text
yoetz provider endpoint --grok
```

can enter the generic provider picker instead of treating `--grok` as the selected provider and
enforcing or prompting for its model consistently. The equivalent Fireworks selector is excluded
from that branch.

The predicate should account for `not grok`, and a focused test should cover `--grok` with no model
on an interactive TTY. This is a small operator-path defect, not a credential/privacy failure and
not a blocker for the tested `--grok --model grok-4.5` path.

Additional test gaps:

- interactive menu choice 6 (`grok`) is not directly exercised;
- `--official`, `--fireworks`, and `--grok` mutual-exclusion combinations are not all covered; and
- no installed-wheel CLI/MCP exercise covers the new preset.

## 8. Prioritized residuals

### P1 — Make accepted multi-event publications return a usable result

Reproduce the exact four-event action/result/evidence/claim batch and require:

1. durable append;
2. successful response shaping; or
3. an explicit `accepted_with_response_unavailable` result that is not presented as a generic
   internal failure.

### P1 — Prove stored-result publish replay

Use the exact original request ID and body after forced response projection/cache failure. Require:

- no duplicate append;
- no stale-frontier rejection before replay lookup;
- the stored accepted result returned; and
- status/history showing one copy of every event.

Attempt 3 did not invalidate the replay fix; the agent never made a valid exact replay.

### P2 — Improve event authorability

Provide canonical builders or schema examples for each event family, admitted enum values, and
cross-event reference construction. Prefer a recovery operation keyed by request ID that does not
require an agent to reconstruct the full original event batch.

### P2 — Fix and test the Grok interactive selector

Add `not grok` to the generic interactive predicate and cover the no-model TTY path, menu choice,
aliases, and selector conflicts.

### P2 — Run the real installed Grok chain

After review and explicit authorization:

1. build the exact commit;
2. reinstall the wheel with semantic extras into isolated `codex-testing`;
3. verify CLI help and MCP handshake from that installation;
4. bind a separately authorized xAI credential;
5. prove service/provider readiness;
6. execute `semantic_required`;
7. capture xAI request ID, endpoint/model provenance, privacy authorization, and policy digests;
8. obtain and replay the final receipt; and
9. make no material change after receipt.

## 9. Final answer

Yes: Yoetz now **mostly worked well and worked clearly better than the previous two attempts today**.

The strongest proof is not that the MCP was visible. It is that attempt 3 reached:

```text
start
→ durable plan
→ durable evidence/claim
→ authoritative status recovery
→ live Fireworks semantic_required check
→ receipt retaining semantic coverage
→ idempotent receipt replay
→ current final frontier
```

The strongest remaining counter-evidence is:

```text
valid multi-event publish
→ durable append
→ response_projection_failed
→ two invalid agent replays
→ manual status recovery
```

So the correct product judgment is **mostly working, approximately 7.5/10, and not yet fully
working**.

The main implementation agent did better than Yoetz itself: approximately **8.5/10**, with excellent
engineering, verification, security, and honesty. The observer agents were useful but less precise,
approximately **7.5/10**, because their timing narrative conflicts with the launcher metadata and
they missed a small CLI edge-case.

The uncommitted Grok implementation is strong enough to preserve for review. It is not strong enough
to advertise as live Grok interoperability until the CLI edge-case is fixed, the installed artifact
is exercised, and an independently authorized xAI semantic/receipt chain succeeds.
