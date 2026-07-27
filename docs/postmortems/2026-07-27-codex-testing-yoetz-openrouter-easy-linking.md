# Codex-testing + Yoetz OpenRouter easy-linking follow-up dogfood

**Date:** 2026-07-27

**Disposition:** implementation blocked at repository intake; evidence retained on the experiment
branch; this comparative postmortem promoted separately

**Experiment branch:** `codex/openrouter-easy-linking-dogfood-20260727`

**Repository baseline:** `25bf8c22db69845c114e71970662dffc5a5e27f1` (`main` and `origin/main`
at launch)

**Relevant baseline change:** `25bf8c2` includes PR #31, the remediation prompted by the
2026-07-26 dogfood

**Codex-testing:** `0.146.0-alpha.2`

**Codex session:** `019fa2a8-3706-7522-96d4-e19d69b26c56`

**Yoetz task:** `tsk_284f80f4-3e78-4cb3-9734-d73bef5083b0`

**Yoetz session:** `ses_c74f9e78-6b54-4bb5-b4c6-5af433488221`

**Final Yoetz receipt:** `rcp_4a21df6c-69fc-4663-bf62-1ffd52dc9d4e`

**Product-code disposition:** no product code changed

**OpenRouter disposition:** no live OpenRouter request or provenance

**Comparison baseline:** [`2026-07-26-codex-testing-yoetz-openrouter-easy-linking.md`](2026-07-26-codex-testing-yoetz-openrouter-easy-linking.md)

## 1. Executive verdict

The July 27 follow-up was **more process-correct and more precise in its final claims than the July
26 run, but it did not complete the requested implementation and did not show that Yoetz's
operational defects were fixed**.

Codex correctly discovered, again, that OpenRouter was already structurally supported. It narrowed
the remaining likely user-experience gap further than the July 26 run: the generic secure setup
path already accepts `--provider openrouter`, while Fireworks additionally has a dedicated
top-level shortcut. The likely missing parity feature is therefore a named `--set --openrouter`
shortcut, not a new provider adapter, generic endpoint mechanism, or privacy bypass.

Unlike the July 26 evaluation, the July 27 assignment authorized implementation. Repository rules
still required an issue before coding. Codex searched for duplicates and attempted to create the
issue, but the GitHub connector cancelled that state-changing action under auto mode. No issue or
maintainer acknowledgement existed, so Codex stopped before editing product files. That was the
correct repository-process outcome.

Yoetz again made the result more trustworthy:

- it established a real MCP-local task and durable ledger;
- it preserved the process blocker as open obligations rather than allowing completion;
- it kept Fireworks semantic evidence separate from OpenRouter interoperability;
- its frontier exposed that publications had committed despite failed public responses;
- its final receipt honestly concluded `insufficient_coverage`;
- Codex's final response listed the unverified claims instead of converting a blocked run into
  success.

Yoetz was still not operationally clean:

- two `start` attempts failed before activation;
- installed guidance referenced files that were not present;
- MCP resource discovery was unusable;
- compact status repeatedly returned `response_projection_failed`;
- a valid publication committed but returned `response_projection_failed`;
- exact same-request-ID replay repeated the error instead of returning the stored success;
- the final receipt exposed `check_not_applicable` immediately after a successful
  `semantic_required` check without explaining the relationship.

### Bottom line

**Did Yoetz function?** Yes, materially. Task creation, atomic persistence, fail-closed disclosure,
authorized Fireworks semantic dispatch, external provenance, frontier recovery, receipt creation,
and receipt replay all worked.

**Did Yoetz function well?** Only partially. Its durable core was stronger than its agent-facing
control surface. The ledger remained authoritative, but ordinary requests were hard to author and
successful operations could still appear to fail.

**Was Yoetz better than on July 26?** Mixed:

- final claim discipline and lifecycle explanation improved;
- validation errors were somewhat more specific;
- the first plan/obligation publication was authored correctly;
- recovery avoided duplicate events;
- but startup discoverability regressed in this run;
- compact status became a repeated failure point;
- and the post-commit response-integrity defect persisted in a more specifically named form.

**Did Codex improve because of Yoetz?** Yoetz again improved auditability and resistance to
overclaiming, while degrading speed and tool fluency. Codex's stronger final wording also reflects
the explicit July 27 assignment and the July 26 postmortem, so it cannot be attributed to Yoetz
alone.

**Was OpenRouter added or live-proven?** No. The implementation was blocked before product edits,
and all external semantic provenance was Fireworks.

## 2. What was being tested

The assignment asked isolated `codex-testing`, in automatic mode and on a new branch, to add
OpenRouter easy linking alongside OpenAI and Fireworks. Two independent observers ran in parallel:

- an agent-quality monitor assessed repository reasoning, process compliance, practical use of
  Yoetz, error recovery, verification, and final-answer honesty;
- a Yoetz-health monitor assessed activation, guidance, publication, persistence, status, semantic
  dispatch, privacy authorization, provenance, and receipts.

All raw events, launcher diagnostics, the agent's final response, both monitor reports, and a root
synthesis were written under:

`docs/dogfood/2026-07-27-openrouter-easy-linking/`

This differs materially from July 26. The July 26 maintainer instruction explicitly made that run
evaluation-only and declined GitHub issue creation. July 27 asked for implementation, but the
repository's issue/design process remained binding. The same no-code outcome therefore has
different causes:

- **July 26:** no code by explicit experiment design;
- **July 27:** no code because the required issue action was cancelled before the gate could be
  satisfied.

That distinction is essential when comparing agent and Yoetz performance.

## 3. Evaluation limits

This was not a controlled Yoetz-versus-no-Yoetz A/B experiment. The comparison is longitudinal:

- different repository baselines;
- different task authorization;
- different prompts;
- one day of intervening product remediation;
- the July 27 prompt explicitly included lessons learned on July 26.

The report can directly compare observed workflow behavior and runtime results. It cannot isolate
Yoetz as the sole cause of improved or degraded agent behavior.

The live semantic checks in both runs used Fireworks. Neither run performed an OpenRouter request.
Therefore neither report is evidence of OpenRouter E-007 interoperability.

The July 27 verification ran against unchanged product code. It validates the existing structural
path at `25bf8c2`; it does not validate the proposed shortcut.

## 4. Timeline

### 4.1 Branch and launcher preflight

The run started from clean `main` and `origin/main` at `25bf8c2`, then created
`codex/openrouter-easy-linking-dogfood-20260727`.

The isolated launcher remained:

- `/Users/shayb/.local/bin/codex-testing`;
- version `0.146.0-alpha.2`;
- isolated state under `/Users/shayb/.codex-testing`;
- Yoetz MCP registration enabled.

The first parent launch used an unsupported `-a` placement and exited before the Codex agent
started. The second launch could not write the isolated state database from the host sandbox. The
third launch used the correct configuration and host permission for `~/.codex-testing`, while the
implementation agent itself remained workspace-write scoped.

These launch failures did not exercise Yoetz and are not counted as Yoetz runtime failures.

### 4.2 Yoetz activation

Once Codex started, its first `yoetz.start` call sent an empty object. Yoetz returned
`INVALID_REQUEST` with the eight missing field locations.

The second call guessed:

- a free-form request ID instead of the required request-ID shape;
- `mode: start` instead of an admitted start mode.

It failed with field locations for `/request_id` and `/mode`, but without a canonical example.

Codex then attempted MCP resource and template discovery:

- `resources/list` returned an unexpected response type;
- `resources/templates/list` returned method-not-found.

The installed `skills/codex/yoetz/SKILL.md` also referenced adjacent workflow, publication-policy,
and coverage-and-receipts files that were not present at the expected paths. Codex recovered by
reading repository descriptors, protocol models, conformance tests, and schemas.

The third start succeeded:

- task `tsk_284f80f4-3e78-4cb3-9734-d73bef5083b0`;
- session `ses_c74f9e78-6b54-4bb5-b4c6-5af433488221`;
- writer `wri_2e223205-b1f1-4887-9680-c15466d74ea3`;
- frontier sequence `1`;
- local-disclosure receipt `egr_75060ec9-1a8b-4349-a4d7-e1e37da0428e`.

This proved activation. Registration alone had not.

### 4.3 Repository and duplicate assessment

Codex read:

- `AGENTS.md`;
- `CONTRIBUTING.md`;
- `docs/architecture.md`;
- `docs/INTERFACES.md`;
- relevant ADRs;
- provider usage documentation;
- setup CLI code;
- provider-binding and factory code;
- focused tests;
- the July 26 postmortem;
- related Git history and PRs.

It correctly established:

- OpenRouter already has an exact approved preset;
- the credential/vault and privacy boundaries already exist;
- runtime dispatch and normalization already exist;
- generic secure setup accepts `--provider openrouter`;
- Fireworks has a dedicated top-level shortcut;
- OpenRouter lacks the equivalent named shortcut;
- live OpenRouter evidence remains separate and absent.

Available issue and PR searches found no exact OpenRouter easy-linking issue. PR #31 was adjacent and
explicitly left live OpenRouter E-007 outside its scope. The agent's final wording could have been
slightly more cautious because connector issue search did not surface adjacent issue #30 directly;
the strongest wording is that no exact duplicate was found in the available search results.

### 4.4 Repository intake blocker

Codex attempted to create the required GitHub issue before editing product code. The connector
cancelled the action under auto mode.

Codex did not:

- edit product code first;
- invent an issue number;
- fabricate maintainer acknowledgement;
- reinterpret the dogfood branch as an exemption;
- open a PR or push.

It converted the run into the strongest permitted blocked handoff. That is good agent behavior and
the correct application of repository authority.

### 4.5 Plan and obligation publication

After reading canonical event schemas, Codex's first publication succeeded:

- request `req_00000000-0000-4000-8000-000000000002`;
- one plan plus three obligations;
- resulting frontier sequence `5`;
- disclosure receipt `egr_ff84bc52-ae41-4244-8725-e35851171e15`.

The plan accurately encoded:

- the identified shortcut gap;
- the issue/design gate;
- verification requirements;
- the separate live OpenRouter proof obligation.

Privacy projection failed closed: blocked summaries were omitted rather than leaked into structural
output.

### 4.6 Status and ambiguous publications

Compact status was repeatedly unhealthy. Requests returned retryable `INTERNAL_ERROR` with safe
detail `response_projection_failed`.

Codex preserved the same request ID and replayed as directed. The replay failed again with a new
correlation ID. A later valid four-event publication also returned
`response_projection_failed`; exact replay again returned the same failure class.

The operation had committed. A `status` request using the `versions` view established:

- authoritative frontier sequence `9`;
- projection lag `0`;
- rebuild state `current`;
- no duplicate events from replay.

This is the central runtime result:

> Yoetz's ledger and idempotency remained durable, but its public response path still made a
> successful operation appear to fail, and the documented same-ID recovery did not restore the
> successful response.

### 4.7 Semantic check

At frontier `9`, Codex requested `semantic_required`. The check succeeded:

- verdict `no_issue_detected`;
- result frontier sequence `10`;
- semantic attempt `att_dacf344e-243b-4fa4-a2e4-9d36c5e2147b`;
- authorization `aut_6d694e73-35b4-4e51-b35f-520144183d0a`;
- egress receipt `egr_229768a1-0a6f-4fda-9305-9f63c21a071b`;
- provider request `resp_08c1972afa234c0c92a6e6da52cdb5d9`;
- profile `fireworks-responses`;
- model `accounts/fireworks/models/minimax-m3`.

This is valid evidence for the Yoetz privacy gateway and Fireworks semantic path over the published
subject. It is not:

- an OpenRouter request;
- verification of a new shortcut;
- source-code review of a diff;
- evidence that the task was complete.

Codex preserved that boundary in its final answer.

### 4.8 Final receipt

Receipt request `req_...0009` succeeded and was replayed idempotently. Both reads returned:

- receipt `rcp_4a21df6c-69fc-4663-bf62-1ffd52dc9d4e`;
- object `obj_95f2672b-b5a4-48d9-a5a0-2b7fdf65927f`;
- result frontier sequence `11`;
- conclusion `insufficient_coverage`.

Coverage remained:

- published-only observation;
- metadata-only immutability;
- partial freshness;
- `check_not_applicable`;
- open obligations.

The conclusion was correct. The implementation did not exist, the issue gate was open, and
OpenRouter had not run.

The presentation is confusing, however: the immediately preceding `semantic_required` check
succeeded with external Fireworks provenance, while the final receipt exposed
`check_not_applicable`. The receipt may be conservatively describing completion applicability, but
the reader-facing result does not explain why a successful semantic check and
`check_not_applicable` coexist.

### 4.9 Verification and final response

With no product diff, Codex ran a proportional existing-path verification:

- 54 focused tests passed;
- Ruff check passed;
- Ruff format check passed;
- pinned `npx --no-install pyright` returned zero errors, warnings, or information messages.

Initial `uv` runs could not access the existing cache from the workspace sandbox. Codex retried
with a task-local cache under `/private/tmp`, which is an appropriate environment-only recovery.

The final response accurately stated:

- exact branch and baseline;
- no tracked product changes;
- the issue/design-gate blocker;
- the narrow likely shortcut gap;
- successful existing-path verification;
- Fireworks rather than OpenRouter provenance;
- `insufficient_coverage`;
- every major unverified claim.

Unlike July 26, it did not falsely claim that no runtime state changed.

## 5. What went well

### 5.1 The actual product gap became more precise

July 26 concluded that the remaining work was UX simplification plus authorized live verification.
July 27 narrowed the UX item to a concrete likely surface:

`yoetz --set --openrouter --model ... --api-key ...`

That is a materially better handoff than “add OpenRouter support.” It avoids duplicating:

- endpoint profiles;
- factory registration;
- request normalization;
- secret storage;
- privacy authorization;
- generic provider setup.

### 5.2 Repository authority controlled the implementation

Codex followed the required intake order even though it prevented visible code progress. It searched
before creating, attempted the issue before coding, and stopped when it could not satisfy the gate.

This is stronger process behavior than silently treating an experiment branch as permission to
bypass maintainer review.

### 5.3 Final claim calibration improved

The final answer was better than July 26's in one important respect. July 26 incorrectly said that
no runtime state changed even though Yoetz had created durable task/check/receipt state. July 27
used the narrower and accurate claim that no tracked product files changed, while naming the Yoetz
task, session, check, and receipt.

### 5.4 Fireworks and OpenRouter stayed distinct

Both the agent and Yoetz-health monitor treated the provider identity as evidence:

- active profile: Fireworks;
- semantic provider request: Fireworks;
- OpenRouter live evidence: none.

No structural catalog entry, generic setup path, or Fireworks success was relabeled as OpenRouter
interoperability.

### 5.5 Durable persistence remained recoverable

Despite failed public responses, the ledger:

- committed the valid batch atomically;
- retained an authoritative frontier;
- showed projection lag `0`;
- did not duplicate events on same-ID replay.

The `versions` status view provided a recovery oracle when compact status was broken.

### 5.6 Privacy remained fail closed

Local disclosure was receipt-bound, blocked summaries were omitted from structural output, and
external semantics used an independently authorized Fireworks path. No credential value or private
vault content was copied into the dogfood artifacts.

### 5.7 Verification was honest and proportional

The existing structural path received a meaningful test slice plus Ruff and full pinned Pyright.
The agent did not claim that green existing tests validated a nonexistent implementation.

## 6. What did not go well

### 6.1 The requested implementation did not start

The issue action was cancelled, so the run ended before product edits. This is not a code-quality
failure, but it is a practical failure to complete the user's requested feature.

Auto mode and repository intake were not pre-coordinated. A state-changing GitHub action was
predictably capable of requiring a separate authorization path.

### 6.2 Start was not agent-authorable from the installed guidance

The first call was empty. The second guessed invalid values. Canonical examples were not directly
available through MCP resources, and referenced guidance files were missing.

An agent had to inspect product source and conformance tests just to start the product whose purpose
is to guide the agent. This is a serious onboarding and packaging defect.

### 6.3 Search and evidence capture were noisy

An early broad `rg` traversed large fixture content and inflated the raw event stream to roughly
five megabytes across only 123 JSONL events. The run remained auditable, but not pleasantly so.

The installed guidance should route agents to bounded authoritative paths before broad repository
search.

### 6.4 Compact status was repeatedly unusable

July 26 emphasized CLI/MCP lifecycle ambiguity and one ambiguous publication. July 27 added a new
practical failure shape: compact status itself repeatedly failed response projection.

The alternate `versions` view worked, which protected recoverability, but ordinary guidance should
not depend on knowing a less obvious projection as an emergency path.

### 6.5 Accepted-write response integrity was still broken

PR #31 was motivated in part by the July 26 rule that an accepted write must never be reported as
failure. The July 27 publication still:

1. committed;
2. advanced the frontier;
3. returned a retryable public error;
4. failed again on exact same-request-ID replay.

The error is now more diagnosable—`response_projection_failed` rather than only a generic
`INTERNAL_ERROR`—but the caller still cannot obtain the original successful response through the
prescribed replay.

This is partial remediation, not closure.

### 6.6 Receipt/check semantics were not self-explanatory

A successful external semantic check immediately preceded a receipt that exposed
`check_not_applicable`. The final insufficient conclusion was right, but the coverage explanation
was not clear enough for an operator to understand whether:

- the semantic check was excluded because no completion claim was applicable;
- only deterministic policy coverage was admitted into the receipt;
- open obligations prevented the check from contributing;
- or the receipt projection omitted relevant semantic coverage.

### 6.7 Yoetz did not improve implementation quality

No implementation occurred. Any claim that Yoetz improved code design, patch correctness, or test
quality in this run would be unsupported.

Its demonstrated value was governance, auditability, evidence boundaries, and recovery.

## 7. Direct comparison with the 2026-07-26 dogfood

### 7.1 Comparative scorecard

| Dimension | 2026-07-26 | 2026-07-27 | Assessment |
| --- | --- | --- | --- |
| Baseline | `b081f56` | `25bf8c2`, including PR #31 remediation | July 27 tests the remediated main |
| Task authorization | Evaluation-only, issue explicitly declined | Implementation requested; issue required | Same no-code outcome, different cause |
| OpenRouter structural diagnosis | Already present | Already present; gap narrowed to named shortcut | Improved specificity |
| OpenRouter live proof | None | None | No improvement |
| Yoetz activation | Successful real MCP session | Two failed starts, then successful session | Worse startup fluency |
| Guidance availability | Sufficient to proceed, though path discovery was noisy | Skill referenced missing files; MCP resources failed | Packaging/discoverability regression or newly exposed defect |
| First plan publication | One malformed event UUID, then success | Correct first plan/obligation batch after schema inspection | Improved authoring after recovery |
| Final/second publication | Wrong frontier, invalid enums, then commit + generic `INTERNAL_ERROR` | Valid publication committed + `response_projection_failed`; replay also failed | Better diagnosis, integrity defect persists |
| Status recovery | Frontier conflict/status exposed commit | Compact status failed; `versions` status exposed commit and no duplicates | Durable core stable; ordinary status less healthy |
| Semantic checks | Three Fireworks checks | One Fireworks check | Both prove Fireworks only |
| Final receipt | `insufficient_coverage`; four open obligations | `insufficient_coverage`; three open obligations and process blocker | Honest in both; neither closes task |
| Receipt/check clarity | `check_not_applicable` after semantic work | Same unexplained coexistence more explicitly observed | Unresolved |
| Final response | Strong, but falsely said no runtime state changed | Exact product-state wording and explicit unverified list | Clear improvement |
| Existing-path verification | Repository assessment and tests, no OpenRouter live proof | 54 tests + Ruff + format + full Pyright | Stronger bounded local verification |
| Operational efficiency | Many payload repairs and repeated checks | Startup/schema archaeology plus projection failures | Still poor; failure mix changed |
| Yoetz net effect | Positive but modest | Positive for trust, negative for fluency, neutral for code | Broadly unchanged |

### 7.2 What PR #31 appears to have improved

The July 27 evidence supports limited improvements:

- validation returned field locations and reason codes for invalid start fields;
- the agent could author the first publication correctly after consulting current schemas;
- lifecycle reporting explicitly described MCP-local composition as starting on demand, reducing the
  July 26 CLI/MCP contradiction;
- authoritative `versions` status showed current projection state and no duplicate replay;
- the public failure named `response_projection_failed`, making the failing layer more visible.

These are real ergonomics and diagnosability gains.

### 7.3 What PR #31 did not close

The main honesty invariant remained violated at the public operation boundary:

- a durable write committed;
- the caller received failure;
- same-ID replay did not return the stored success.

The internal ledger stayed correct, but a cooperative agent still had to infer success from a
separate status view. That is exactly the unknown-outcome burden the July 26 postmortem prioritized.

The follow-up also exposed residual authorability gaps:

- canonical start examples were not directly available;
- installed skill references were incomplete;
- compact status response projection was unreliable;
- receipt coverage did not explain its relationship to the semantic check.

### 7.4 Agent quality compared with July 26

Repository reasoning was strong in both runs.

July 27 was better at:

- obeying repository process under a real implementation request;
- narrowing the feature gap;
- using exact product-state wording;
- listing unverified claims;
- applying the prior ambiguous-write recovery lesson;
- running a clearly bounded verification suite.

July 27 was worse at:

- initial Yoetz activation;
- bounded discovery;
- avoiding unsupported MCP discovery calls.

The improved final answer likely reflects three influences:

1. Yoetz's receipt and provenance boundaries;
2. the explicit July 27 prompt, which encoded July 26 lessons;
3. direct access to the July 26 postmortem.

It should not be credited solely to Yoetz.

### 7.5 Yoetz health compared with July 26

The durable and privacy-sensitive layers were consistently good in both runs:

- local task/session creation;
- fail-closed disclosure;
- frontier truth;
- authorized Fireworks egress;
- external provenance;
- honest insufficient receipts.

The agent-facing layer remained the weak link:

- July 26: difficult event payloads, generic validation, CLI/MCP ambiguity, generic post-commit
  failure;
- July 27: invalid startup attempts, missing guidance resources, broken resource discovery,
  repeated projection failures, and unrecoverable successful responses.

The failure became easier to name but not easier enough to resolve. Yoetz was therefore
**functionally durable but still operationally immature**.

## 8. Did Yoetz improve Codex in this run?

### 8.1 Improvements attributable in part to Yoetz

Yoetz directly contributed:

- a durable record of the process blocker;
- explicit obligations that remained open;
- provider identity attached to semantic evidence;
- a frontier-based truth source after ambiguous responses;
- a final coverage ceiling;
- pressure to enumerate unverified claims.

These mechanisms made the result more trustworthy.

### 8.2 Costs introduced by Yoetz

Yoetz directly consumed work through:

- two invalid start attempts;
- failed resource discovery;
- schema and descriptor archaeology;
- repeated compact-status failures;
- failed publication replay;
- additional interpretation of check and receipt coverage.

These costs made the agent slower and increased the chance of workflow mistakes.

### 8.3 Net judgment

The net effect was **positive for epistemic quality, negative for operational efficiency, and
unproven for implementation quality**.

Relative to July 26, this is not a major shift. The strongest improvement was the agent's final
calibration and process compliance. Yoetz's runtime health remained mixed.

## 9. How well did Yoetz itself function?

### 9.1 Activation and lifecycle

**Result: functional after poor discovery.**

Activation eventually succeeded and created a durable local task. The separate CLI surface still
reported `service_unavailable`, but current provider status explained that MCP-local composition
starts on demand. This is clearer than July 26, though the operator still has to understand two
lifecycles.

### 9.2 Guidance

**Result: conceptually correct, incompletely delivered.**

The intended guidance—start, publish bounded work, use semantic mode appropriately, inspect
frontier, obtain receipt, respect coverage—was correct.

The installed delivery was incomplete because referenced files were missing and MCP resource
discovery could not supply them. A product cannot rely on guidance files that the installed agent
cannot read.

### 9.3 Publication and durability

**Result: durable core healthy; response boundary unhealthy.**

Events committed atomically and replay did not duplicate them. That is strong.

The successful publication could not return a successful response, even on exact replay. That is a
high-priority operational defect.

### 9.4 Status

**Result: recovery surface available, primary compact surface unreliable.**

The `versions` view was authoritative and useful. Compact status repeatedly failed. Guidance should
not assume the primary view is healthy when closure depends on it.

### 9.5 Privacy and semantic dispatch

**Result: healthy for Fireworks, untested for OpenRouter.**

The semantic check carried:

- independent authorization;
- egress receipt;
- provider request ID;
- exact provider profile;
- exact model;
- durable result frontier.

That is good live evidence for Fireworks and the privacy gateway. It says nothing about OpenRouter.

### 9.6 Receipt

**Result: honest and stable, but insufficiently explanatory.**

Receipt creation and replay were stable. The conclusion correctly remained
`insufficient_coverage`.

The relationship between the successful semantic check and `check_not_applicable` was not clear
enough. Honest output must also be interpretable.

### 9.7 Overall rating

- durable ledger integrity: **good**;
- privacy boundary: **good**;
- Fireworks semantic path: **good**;
- completion honesty: **very good**;
- activation discoverability: **poor**;
- guidance packaging: **poor**;
- request authorability: **poor to moderate**;
- response integrity: **poor**;
- receipt explainability: **moderate**;
- OpenRouter interoperability: **not tested**;
- overall: **materially useful, more diagnosable than July 26 in places, still not
  production-clean**.

## 10. Root causes

### Root cause A: implementation authority and auto mode were not coordinated

The task required a GitHub issue before coding, while the selected automatic execution path
cancelled that external write. The run could not reach implementation without either a pre-existing
issue or separately authorized issue creation.

### Root cause B: installed guidance was incomplete

The skill referenced supporting workflow files that were not available where the installed skill
said they would be. MCP resource discovery also failed. Codex therefore fell back to source-code
archaeology.

### Root cause C: control responses depend on a failing projection layer

Compact status and publication both reached `response_projection_failed`. The durable operation and
the public response are insufficiently isolated.

### Root cause D: replay protects persistence but not response recovery

Same-request-ID replay prevented duplicate events, but did not reproduce the stored successful
response. Idempotency protected the ledger without fully protecting the caller.

### Root cause E: coverage concepts are accurate but not operationally explained

`insufficient_coverage`, published-only observation, partial freshness, external semantic
provenance, and `check_not_applicable` may all be internally consistent, but the product did not
explain their relationship.

### Root cause F: the prompt premise still lagged current source

The request again said “add OpenRouter,” while most OpenRouter support already existed. The agent
handled this well, but repeated premise drift wastes implementation time and complicates dogfood
comparisons.

## 11. Recommended actions

### P0 — Make accepted writes return replayable success

Reproduce request `req_...0004` from the retained event stream. The regression must prove:

1. a committed write never returns an unqualified failure;
2. if response projection fails after commit, the response clearly says the write was accepted and
   the projection is unavailable;
3. exact same-request-ID replay returns the durable accepted result;
4. replay never duplicates events;
5. compact status failure does not hide authoritative frontier recovery.

### P0 — Repair installed guidance completeness

Package and verify every file referenced by `skills/codex/yoetz/SKILL.md`. Add an offline installed
artifact test that:

- opens the skill;
- resolves every relative reference;
- validates a canonical `start`;
- validates one canonical publication;
- does not require repository source inspection.

### P1 — Make start self-describing

Invalid `start` responses should safely include:

- admitted mode identifiers;
- request-ID format identifier;
- a content-free canonical example or tool-schema example;
- exact client shape.

The agent should not need two failed calls and source archaeology.

### P1 — Make resource discovery either supported or absent

If Yoetz exposes MCP resources, make `resources/list` and template discovery conformant. If it does
not, the integration and guidance should not imply that resource discovery is a recovery path.

### P1 — Repair compact status projection

Add a regression using the retained frontier and coverage shape. Compact status should either
render or return a successful structurally bounded response with an explicit unavailable section.
It should not turn authoritative status into an operation failure.

### P1 — Explain semantic-check versus receipt applicability

Receipt output should state why a successful semantic check does or does not contribute to
completion coverage. In this case, explain why `check_not_applicable` coexists with external
semantic provenance.

### P1 — Preflight repository authority before auto execution

The dogfood harness should detect:

- whether an issue is required;
- whether an exact duplicate exists;
- whether external issue creation is authorized;
- whether maintainer acknowledgement is required.

If not authorized, it should pause before starting an implementation run or deliberately classify
the run as evaluation-only.

### P2 — Bound discovery and raw event size

Guidance should route to:

1. ADRs;
2. `docs/INTERFACES.md`;
3. owning modules;
4. focused tests and schemas.

Default searches should exclude large fixture payloads and generated artifacts. The evidence stream
should retain commands and bounded output without embedding multi-megabyte irrelevant fixtures.

### P2 — Run a controlled guidance A/B

Use the same repository commit, model, prompt, time budget, and permissions with Yoetz enabled and
disabled. Measure:

- correctness;
- unsupported claims;
- issue/process compliance;
- tool failures;
- event-authoring retries;
- elapsed time and tokens;
- completion coverage;
- recovery from injected ambiguous writes;
- final prose accuracy.

### Separate authorized test — OpenRouter E-007

After the shortcut issue is acknowledged and implemented, use an explicitly authorized OpenRouter
credential and independent `llm_inference` approval to prove:

- exact OpenRouter host, path, and endpoint profile;
- one physical request;
- canonical request commitment;
- normalized judgment;
- provider request ID;
- OpenRouter provenance;
- privacy authorization and egress receipt;
- refusal, timeout, malformed-output, and provider-error handling;
- final durable receipt.

Neither the July 26 nor July 27 dogfood can be cited as that proof.

## 12. Preserved evidence

The complete July 27 evidence remains on:

`codex/openrouter-easy-linking-dogfood-20260727`

under:

`docs/dogfood/2026-07-27-openrouter-easy-linking/`

Files:

- `README.md` — evidence index;
- `codex-testing-prompt.md` — exact assignment;
- `codex-testing-events.jsonl` — complete public JSONL event stream;
- `codex-testing-stderr.log` — launcher diagnostics;
- `codex-testing-final.md` — Codex's final response;
- `agent-quality-monitor.md` — independent practical-quality audit;
- `yoetz-health-monitor.md` — independent runtime-health audit;
- `synthesis.md` — combined root synthesis.

The postmortem is retained with those files on the experiment branch and promoted separately to
`main`. Raw evidence, observer reports, and experiment-only files are not promoted to `main`.

## 13. Final answer

Compared specifically with the July 26 dogfood, Yoetz on July 27 was **better at exposing the exact
failure layer and supporting accurate final claims, but not demonstrably better at completing
ordinary agent operations**.

Its strongest layers remained:

- durable local state;
- fail-closed privacy;
- Fireworks semantic provenance;
- frontier-based recovery;
- honest receipt conclusions.

Its weakest layers remained:

- request discoverability;
- guidance delivery;
- agent-facing authorability;
- public response integrity;
- coverage explainability.

Codex became more process-correct and precise, but no implementation occurred. Yoetz made the
blocked result trustworthy; it did not make the work fluent, and it did not prove OpenRouter.

The fairest overall verdict is:

> **Yoetz remained net positive for truthfulness and auditability, negative for execution
> efficiency, and still not production-clean. PR #31 improved diagnosis and lifecycle clarity but
> did not close the accepted-write response-integrity defect.**
