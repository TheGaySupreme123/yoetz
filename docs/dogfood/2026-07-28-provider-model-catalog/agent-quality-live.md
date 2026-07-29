# Agent quality live log

Observer 1 appends evidence-backed chronological notes here while independently assessing the
practical quality of the Codex implementation and how Yoetz guidance affects the work.

## Observation protocol

- Driver thread: `019fa9fc-2111-7bb1-a52c-2e5339433c52`
- Raw event stream: `/private/tmp/yoetz-model-catalog-dogfood.BQQyQb/codex-events.jsonl`
- Baseline named by the task prompt: `eda66239210584486528e7de60d0715b0d8cc285`
- Branch: `codex/provider-model-catalog-dogfood-20260728`
- This log distinguishes **direct evidence** from **observer inference**. Event timestamps are not
  present in the JSONL payloads, so local observation times are used only as coarse bounds and event
  ordering is cited by `item_*` identifiers.

## Chronology

### 2026-07-28 21:27-21:29 +03:00 — activation attempt and protocol recovery

**Direct evidence**

1. `item_2` states that the driver would begin with repository authority and Yoetz operating
   guidance, trace the provider/model surfaces, remain local, and avoid issue/PR/commit/external
   publication. This accurately reflects the explicit one-run process exemption and most task
   boundaries.
2. `item_3` is a spontaneous `yoetz.start` invocation, so the integration was activated rather than
   merely listed. The request failed `INVALID_REQUEST` because
   `request_id=start-provider-model-catalog-20260728-01` did not match the required UUID-v4 shape.
   The error safely identified `/request_id`, provided a concrete regex, marked the failure
   non-retryable, and pointed to `yoetz://guidance/workflow.md`.
3. `item_4` correctly explained that the rejected request created no session and narrowed recovery
   to the request-ID shape. The explanation is appropriately honest about the absence of a durable
   task record.
4. `item_5` retried with a valid-looking UUID-v4 request ID but again received
   `INVALID_REQUEST`. Unlike the first error, this response contained no field-level reason. Direct
   schema inspection shows the likely cause: the request supplied `workspace_ref` without the
   dependent `external_ref`.
5. `item_6` then searched source schemas/tests for `task_title`, `requested_view`, and guidance
   references. The output exposed the exact `dependentRequired` rule and examples, but the search
   was broad and emitted a very large status-schema line.

**Observer inference**

- Positive: the driver recognized the first protocol failure precisely, did not falsely claim a
  session, and used the safe correction path. Yoetz's field-level diagnostic materially improved
  that recovery.
- Concern: the task explicitly required reading relevant `yoetz://guidance/*.md` resources, yet the
  first evidence is a tool call followed by repository text search, not a resource read. The second
  failure was avoidable if the tool's complete example/schema or workflow resource had been read
  first. This is an early, low-severity efficiency/discipline issue, not yet an implementation
  defect.
- Yoetz effect so far is mixed: activation and the first error are useful; the second generic
  validation error hindered recovery by omitting the dependency violation, while the repository
  schema remained sufficient to self-recover.

## Open findings (live)

| ID | Severity | Finding | Evidence | Current disposition |
|---|---|---|---|---|
| AQ-001 | Low | Driver attempted `start` before demonstrating that it had read the required workflow resource or complete schema example, causing two avoidable protocol failures. | `item_3`, `item_5`, task requirement 1 | Open; observe whether the driver reads and follows guidance before material publication. |
| AQ-002 | Low | Second Yoetz `INVALID_REQUEST` lacked field-level diagnostics for the `workspace_ref`/`external_ref` dependency, increasing recovery cost. This is a Yoetz usability observation that affects agent quality, not a driver-code defect. | `item_5`; `schemas/operations/start-request-1.0.0.schema.json` | Open; cross-reference health observer and observe recovery. |

### 2026-07-28 21:29-21:32 +03:00 — guidance read, successful activation, and work framing

**Direct evidence**

1. `item_7` inspected the exact `start` example and relevant actor/client schemas, then read all
   three required guidance documents: workflow, publication policy, and coverage/receipts.
2. `item_8` successfully created task `tsk_6b464777-1eb2-4a08-b6c0-243842e2b9c1`, session
   `ses_dd188a25-a617-473a-b63a-f97107c7d79d`, and writer
   `wri_cac64f83-8c51-42c2-b437-29c53a91bda4` at frontier sequence 1. Returned coverage explicitly
   said `self_asserted`, `published_only`, `metadata_only`, and `check_types: ["none"]`.
3. `item_9` accurately disclosed Yoetz's local-ledger role and its weak observation/authorship
   boundary. It did not upgrade activation into verification.
4. `item_10` created a six-step task list covering authority/surface discovery, Yoetz publication,
   implementation, tests/docs, static and boundary verification, then semantic-required review,
   finding disposition, receipt replay, and final reporting.
5. `item_11` and `item_12` inspected complete `publish_work` examples and schemas before attempting
   publication. This is a clear behavioral improvement over the initial guessed `start` payload.

**Observer inference**

- AQ-001 is now mitigated: the driver responded to the failed calls by reading the complete examples
  and all relevant guidance before material publication. The initial failures remain real
  efficiency evidence, but they did not propagate.
- The task list is appropriately aligned with both the user's job and the Yoetz ten-step workflow.
  In particular, it preserves semantic review as a late, evidence-bound check rather than treating
  semantic output as an implementation oracle.
- Guidance materially helped the agent communicate coverage limitations and adopt dry-run/schema
  discipline. No evidence yet shows whether it improves the actual product design or catches a
  defect; that requires later diff/finding evidence.

## Startup-recovery finding update

- **AQ-001 — mitigated, retain in final audit.** The agent read all relevant guidance and exact
  examples at `item_7`, then inspected publication examples before composing the next write
  (`item_11`-`item_12`).

### 2026-07-28 21:30-21:34 +03:00 — bounded plan publication

**Direct evidence**

1. `item_13` used `dry_run: true` to preview four coherent obligations plus one plan event. The
   obligation set covers implementation/compatibility, tests/docs, proportional verification, and
   semantic/receipt/report closure without creating one event per file. This closely follows the
   publication policy's batching guidance.
2. The preview failed `EVENT_INVALID` at `/event_drafts/4/payload` with
   `reason_code=unsorted_set_field`. `item_14` correctly stated that nothing was appended and that
   only canonical ordering needed correction.
3. `item_15` reused request `req_cdd4928f-77ad-4d74-b75d-45a3b16dd908`, sorted the obligation
   references, and received a non-evidential successful dry-run.
4. `item_16` reused the same operation identity and event IDs for the real publish. Five events were
   accepted and projected, advancing frontier sequence 1 to 6 with head
   `sha256:19a1e37ca692d35ab5b0a6d291f7f1cc4b693d6dac30ca496ea945c63a3bff75`.
5. Every published event asserted `occurred_at: 2026-07-28T09:00:00.000Z`, while Yoetz recorded
   `accepted_at: 2026-07-28T18:30:49.719Z`. The observer's local stream was active around 21:30
   +03:00 (18:30Z), so the asserted occurrence time predates the actual planning activity by about
   9.5 hours.

**Observer inference**

- Yoetz guidance materially improved publication quality: the driver used dry-run, corrected
  canonical ordering without speculative retries, preserved identity, batched at useful work-package
  granularity, and published explicit acceptance evidence.
- The hard-coded event time is a material record-quality problem. Although accepted timestamps
  preserve ledger ingestion chronology and the coverage remains self-asserted, the event's own
  occurrence claim is false or at minimum unsupported. A durable integrity tool should not be fed a
  knowingly synthetic time during a live run. The final audit must check whether the driver
  discloses or corrects this.

## Plan-publication finding update

| ID | Severity | Finding | Evidence | Current disposition |
|---|---|---|---|---|
| AQ-003 | Medium | Driver published a fabricated/stale `occurred_at` (`09:00Z`) for plan/obligation events created around `18:30Z`, weakening chronological integrity of the evidence record. | `item_13`-`item_16`; authoritative `accepted_at=18:30:49.719Z` | Open; observe later timestamps and final disclosure. |

### Events item_17-item_36 — authority and surface discovery

**Direct evidence**

1. `item_17` read `docs/architecture.md`, `docs/INTERFACES.md`, and `CONTRIBUTING.md`; subsequent
   searches and reads covered the provider/privacy/setup ADRs, provider configuration, CLI setup,
   existing tests, and user documentation (`item_18`-`item_36`).
2. `item_20` confirmed exact HEAD/baseline
   `eda66239210584486528e7de60d0715b0d8cc285` on
   `codex/provider-model-catalog-dogfood-20260728`, matching the launch prompt.
3. The driver inspected both `src/yoetz/cli/provider_binding.py` and the secure setup path in
   `src/yoetz/cli/setup.py`, plus configuration writing, provider endpoint CLI behavior, setup
   subprocess tests, provider ADRs, and usage docs. It did not assume the request referred to only
   one picker.
4. `item_33` identified seven reviewed provider presets shared by two interactive paths and
   separated owner-declared origins as necessarily manual. It rejected runtime model discovery
   because CLI/setup code is not authorized to open provider network channels and credentials are
   service-owned, then proposed a repository-owned deterministic catalog shared across both
   interactive paths while leaving explicit `--model` behavior intact.
5. As of this observation, no product diff existed; the only untracked tree was the dogfood
   evidence directory created by the harness/observers.

**Observer inference**

- The driver's task understanding and authority use are strong. Its conclusion is traceable to the
  privacy/provider authority and directly addresses the assignment's explicit warning against
  inventing an unauthorized discovery channel.
- The design direction is appropriately conservative and consistent: one reviewed catalog serving
  both interactive entry points avoids duplicated behavior, while owner-declared endpoints and
  scripted `--model` remain manual.
- Yoetz did not itself produce this design; the evidence shows repository authority and source
  inspection did. Yoetz's contribution at this stage is maintaining explicit obligations and
  boundaries. Any claim that semantics or the ledger caused the design would be unsupported.

### Events item_45-item_52 — decision publication projection failure

**Direct evidence**

1. The driver used a real clock read (`item_45`: `2026-07-28T18:34:01.000Z`) for its next decision
   event, correcting its timestamp-generation practice for future events.
2. The decision event in `item_46` was bounded and well framed: repository-owned, per-preset,
   maximum 10, default-first, explicit custom option, shared interactive picker, byte-preserved
   explicit `--model`, and no catalog claim for owner-declared endpoints. It explicitly labeled
   suggestions as convenience metadata rather than availability/interoperability proof.
3. `item_46` dry-run failed `INTERNAL_ERROR/read_projection_failed` and explicitly claimed no
   durable state change. The driver accurately reported that ambiguity and called authoritative
   compact status (`item_47`-`item_48`), which confirmed frontier 6, zero projection lag, and four
   open obligations.
4. A second fresh dry-run (`item_49`) reproduced the same projection failure. The driver then made
   one bounded non-dry-run attempt (`item_51`), which returned the same explicit no-state-change
   result. It immediately queried `status view=operation` for that request (`item_52`), but the
   recovery read itself also failed `read_projection_failed`.
5. No decision event was durably appended in these attempts; frontier 6 remained the last confirmed
   state.

**Observer inference**

- The driver's recovery behavior is strong: it did not claim publication, checked authoritative
  state, reproduced the failure once with a fresh identity, bounded the actual attempt, and used the
  newly repaired `view=operation` path exactly as guidance directs.
- Yoetz is now materially hindering the agent. Roughly seven tool interactions/messages were spent
  trying to publish one valid, bounded design decision, and both the write response and targeted
  recovery projection failed. The ledger therefore lost a material design transition even though
  the agent composed it correctly.
- Proceeding with one real write after two failed previews was a reasonable bounded deviation from
  the publication-policy preference because both failures were response shaping, not content
  validation, and compact status proved no append. The agent must still retain this as an unresolved
  Yoetz limitation rather than silently treating the decision as published.
- AQ-003 is only partially mitigated: later events use real time, but the already accepted false
  occurrence timestamps cannot be rewritten and have not yet been explicitly corrected in the
  ledger or final report.

## Projection-recovery finding update

| ID | Severity | Finding | Evidence | Current disposition |
|---|---|---|---|---|
| AQ-004 | Medium | Yoetz projection failures consumed substantial agent effort and prevented durable publication of a well-formed material design decision; `status view=operation` failed too. | `item_46`-`item_52`, correlations `err_1a0812d9...`, `err_484d6884...`, `err_39f37475...`, `err_1c8df158...` | Open Yoetz-caused quality/efficiency limitation; driver recovery was appropriate. |

### Events item_53-item_56 — first implementation and red tests

**Direct evidence**

1. `item_53` re-established frontier 6 after the failed operation lookup. `item_54` retained the
   authority-compatible design and explicitly said suggestions are not availability proof.
2. `item_55` changed four product files:
   `src/yoetz/config/write.py`, `src/yoetz/cli/provider_binding.py`,
   `src/yoetz/cli/setup.py`, and `src/yoetz/cli/app.py` (initial net diff: +119/-54).
   The design centralizes `suggested_models` in `ProviderPreset`, validates nonempty/unique/default
   first/max-10 invariants, introduces a shared numbered/custom picker, integrates it into both
   interactive provider paths, keeps owner-declared model entry manual, and preserves noninteractive
   missing-model failure.
3. The first focused test run (`item_56`) ran 42 existing subprocess tests and honestly reported
   `3 failed, 39 passed`. Two failures reflect expected prompt-contract changes; one exposed a
   compatibility problem in a mocked preset (`provider_id` was newly assumed in a code path whose
   existing test stub only promised `choice`).
4. The catalog inserted these Official OpenAI suggestions:
   `gpt-4.1-mini`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`,
   `gpt-5.4-mini`, and `gpt-5.4-nano`; it also inserted
   `openai/gpt-5.6-terra` for OpenRouter.
5. The raw research trail contains provider-targeted web queries for OpenAI GPT-5.4/5.2, OpenRouter,
   Vercel, Fireworks, and xAI, but no preserved source/result supporting `gpt-5.6-luna` or showing
   that Codex execution labels `5.6-sol`/`5.6-terra` are public OpenAI API model IDs. The launch
   prompt separately selected “5.6 sol medium” as the **coding agent** model.

**Observer inference**

- The structural implementation is compact and consistent, and running the existing focused suite
  immediately was good test discipline.
- The initial catalog appears to conflate the run's Codex model selection with provider API model
  availability and even adds `gpt-5.6-luna`, for which no provenance is visible. This directly
  threatens the core assignment: a deterministic reviewed catalog is worse than manual entry if it
  recommends nonexistent or unverified IDs. The driver's own “not availability proof” disclaimer
  does not excuse invented identifiers.
- This is the clearest test of Yoetz semantics: a useful semantic review should challenge catalog
  provenance and unsupported “current/recommended” claims before closure. If it does not, the agent
  should still catch the issue through source verification and review.

## Catalog-provenance finding update

| ID | Severity | Finding | Evidence | Current disposition |
|---|---|---|---|---|
| AQ-005 | High | The central catalog includes apparently invented/unproven API IDs, especially `gpt-5.6-luna` and Codex execution labels `gpt-5.6-sol`/`gpt-5.6-terra`, without preserved provider-owned evidence. | `item_55` diff in `src/yoetz/config/write.py`; launch prompt model choice; raw web queries | Open; must be removed or supported by exact authoritative provenance before closure. Semantic review should catch this. |

### Events item_58-item_64 — focused tests green and public behavior documented

**Direct evidence**

1. `item_58`-`item_59` updated the implementation/tests after the initial red run. `item_60` then
   ran three focused files and passed `93 passed in 1.59s`.
2. New tests cover:
   - exact scripted custom model preservation for every reviewed preset;
   - shared interactive picker dispatch for explicit provider selection;
   - suggestions/default/custom disclosure for all seven presets;
   - custom, empty, invalid, TTY, and `--no-interactive` behavior;
   - shared picker use across all secure `--set` provider choices;
   - catalog default-first, uniqueness, nonempty, and max-10 invariants.
3. `item_63`-`item_64` updated the owning first-run ADR and provider usage documentation in the same
   change. The docs accurately describe a static, no-network, default-first capped picker; custom
   entry; exact explicit `--model`; owner-declared manual behavior; aging/account/compatibility
   limitations; and no popularity claim.
4. `docs/usage/providers.md` asserts that the catalog was reviewed on 2026-07-28 against seven
   provider-owned URLs. However, it does not map individual model IDs to source evidence, and the
   raw event stream contains no captured source excerpts/results supporting the questionable OpenAI
   5.6 labels. The linked OpenAI query trail searched GPT-5.4/5.2, not `gpt-5.6-luna`.
5. The driver also made a tangential wording change about prior Fireworks dogfood provenance. It is
   consistent with repository dogfood history but not necessary to implement the picker.

**Observer inference**

- Test discipline is strong and proportionate so far: the driver used the first red run to update
  behavior contracts, expanded coverage across every provider/path, and preserved security-relevant
  noninteractive behavior.
- The tests validate catalog shape but cannot validate truth. This is appropriate for deterministic
  unit tests, but it increases the importance of reviewable per-ID provenance. The current
  documentation's broad list of provider home/guidance pages does not substantiate every literal ID.
- The ADR/doc update satisfies the repository's same-change public-behavior rule, but the Fireworks
  historical edit modestly broadens scope without contributing to the requested UX.
- AQ-005 remains high severity: polished documentation now amplifies the unsupported “reviewed”
  claim rather than resolving it.

### Events item_67-item_80 — expanded verification and static-analysis repair

**Direct evidence**

1. `item_67` applied Ruff formatting. `item_70` passed `git diff --check`; `item_71` passed focused
   Ruff; `item_72` passed an expanded CLI/config slice with `127 passed in 2.10s`; and `item_73`
   passed repository-wide `ruff check .`.
2. In parallel, `item_75` passed conformance and packaging/public-boundary coverage with
   `59 passed, 4 xfailed in 16.14s`.
3. The first pinned Pyright run (`item_74`) failed with 20 errors, including a real tuple-index
   narrowing problem in production code and unknown lambda types in new tests. The driver inspected
   the exact sites (`item_76`), changed them (`item_77`), then reran formatting/lint, focused tests,
   and Pyright.
4. The repaired checks passed: `item_78` “All checks passed!”, `item_79`
   `0 errors, 0 warnings, 0 informations`, and `item_80` `93 passed in 1.85s`.

**Observer inference**

- Verification discipline is strong: failures are surfaced rather than omitted, production and
  test typing are repaired, and the test surface expands from focused behavior to adjacent CLI,
  conformance, packaging, and secret/public-boundary checks.
- Parallel Pyright and boundary execution was efficient and did not obscure results.
- These checks materially support implementation mechanics and compatibility, but none can resolve
  AQ-005's per-model factual provenance. A green suite must not be treated as proof that suggested
  provider model IDs exist.

### Events item_81-item_90 — lossy Yoetz workaround and evidence publication

**Direct evidence**

1. `item_81` accurately summarized all verification and explicitly stated that the earlier design
   decision was not durable. `item_82` re-grounded against `view=versions` at frontier 6.
2. Another full decision dry-run (`item_83`) failed the same `read_projection_failed` boundary.
   The driver isolated the trigger by reducing the event to:
   “Use a repository-owned setup catalog,” authority `ADR-006`, and one short rationale. That
   minimal event successfully dry-ran (`item_84`) and published (`item_85`) as
   `evt_9015bac8-1c3d-4db8-b95e-591507dfb4d0`, advancing frontier to 7.
3. The successful decision omits most material properties of the real design: max-10/default-first,
   custom entry, both interactive paths, scripted preservation, owner-declared exclusion, and
   limitations. Those details remain in product docs and the unpublished failed request, but not in
   the durable decision event.
4. `item_87` computed an exact nine-file diff digest
   `sha256:7b8f776416afac98ffebca92b8c2480d1aeb1cd92326a7df6f9c37476d43093b`;
   `item_88` separately digested a bounded test summary.
5. `item_89` successfully dry-ran a seven-event action/evidence/result/claim batch and `item_90`
   published it, advancing frontier 7 to 14. It retained published-only/self-asserted limits and
   explicitly stated that live provider availability/interoperability remains unproven.
6. The material claim nevertheless says “seven reviewed presets share suggestions,” relying on the
   unsupported catalog contents behind AQ-005.

**Observer inference**

- The driver's evidence construction is careful: exact diff identity, separately digest-bound test
  summary, linked action/result/evidence/claim, current real timestamps, and explicit proof limits.
- The response-projection workaround made Yoetz usable but materially degraded the durable decision
  record. This is a direct example of Yoetz harming record quality: the agent had to choose between
  no decision and an oversimplified decision.
- The semantic review now has a bounded changed-symbol summary and test evidence, but the published
  evidence does not include literal catalog entries or per-ID source evidence. A semantic reviewer
  may therefore be blind to AQ-005 even if the agent follows the tool contract. This is partly an
  agent evidence-selection weakness and partly a consequence of the projection failures that
  punished richer content.

### Events item_92-item_101 — semantic-required review succeeds but adds no practical value

**Direct evidence**

1. After one invalid obligation-resolution preview (`item_92`), the driver inspected the event
   model/reducer and corrected the payload rather than guessing (`item_93`-`item_97`).
   `item_98` dry-ran and `item_99` published three resolved obligations, advancing frontier to 17.
2. `item_100` invoked `check` with `mode=semantic_required`, both policy packs, the material claim,
   and the three implementation/test/docs obligations at exact frontier 17.
3. The check completed successfully:
   - verdict `no_issue_detected`;
   - `semantic_status=succeeded`, `semantic_reason=semantic_completed`;
   - external Fireworks dispatch via `accounts/fireworks/models/minimax-m3`;
   - provider request `resp_33043a3edd5143488b8ea3e86806a7c0`;
   - attempt `att_8a327d87-cb78-498d-a000-72ea341e6530`;
   - latency 1854 ms;
   - zero findings and zero suppressed findings;
   - frontier advanced 17 to 18.
4. `item_101` accurately said the semantic pass caused no code changes and explicitly limited it to
   review of the bounded published record, not live proof of catalog provider/model suggestions.
5. The check did **not** challenge:
   - the unsubstantiated literal catalog entries in AQ-005;
   - the claim that models were sourced from provider-owned documentation without per-ID evidence;
   - the earlier false event occurrence times;
   - the lossy decision event.

**Observer inference**

- Semantic dispatch is technically real and healthy: external provider/model provenance, privacy
  authorization, policy execution, current frontier, and semantic-derived coverage are all directly
  evidenced.
- Semantic review did not improve the implementation. It produced no actionable finding and missed
  the central correctness/provenance risk. The driver is honest about “no changes,” which is good,
  but the practical value is zero for this run.
- The primary cause is inadequate semantic visibility: only a digest and high-level changed-symbol
  description were published, not the literal model list or per-ID sources. The agent selected an
  evidence scope too weak for the task's most qualitative question. Yoetz's repeated projection
  failures likely encouraged this minimal payload, but the final choice still belongs to the driver.
- A `no_issue_detected` verdict is therefore not credible support for catalog correctness. It
  supports only that the visible, self-asserted record triggered no challenge.

## Semantic-review finding update

| ID | Severity | Finding | Evidence | Current disposition |
|---|---|---|---|---|
| AQ-006 | High | Semantic-required review was blind to the literal model catalog and per-ID provenance, then returned no findings; it did not help and missed AQ-005. | `item_89` published evidence scope; `item_100` result; `item_101` | Confirmed. Technical semantic dispatch works; practical semantic contribution is none. |

### Events item_102-item_141 — report closure, semantic instability, and final handoff

**Direct evidence**

1. The driver produced a detailed 520-line report, repeatedly rebound it after adding closure
   evidence, and ended with report digest
   `sha256:3d89aa59a8e3ccaf8acf1d54ff63ce1299b1ed9dabf65cf692fe62b015781bbc`.
   This avoided falsely claiming that an earlier receipt covered later report bytes.
2. Across the complete run, eight `semantic_required` attempts were made. Three succeeded
   (`item_100`, `item_126`, `item_136`), four failed closed with
   `response_schema_invalid` (`item_120`, `item_123`, `item_133`, `item_135`), and one failed
   closed with `provider_timeout` (`item_122`). The final successful check used Fireworks
   `accounts/fireworks/models/minimax-m3`, attempt
   `att_62d59cb7-2a83-4ba6-8a36-7602c1a4119c`, provider request
   `resp_ea7a7bdd5e96430b837d6bb58005dcbe`, and latency 2430 ms.
3. All three successful semantic checks emitted zero findings and caused zero implementation
   changes. The two post-report successes were narrowed to the completion claim rather than a
   full-workspace or literal-catalog review.
4. Final receipt `rcp_4d427dd2-d53d-4cf9-a1b6-79724fbaee6a`, object
   `obj_3879a1e9-2f35-4e45-af38-6dec72a6a6a2`, and digest
   `sha256:0668927dc52e30d629617801907279b2de1757fe140cb3f9f77057e8da1c7465`
   were replayed idempotently. The receipt covered subject frontier 36 and advanced the ledger to
   frontier 37. Its human text explicitly said the result was not proof of correctness.
5. Final compact status was current at frontier 37, with zero open obligations, zero unresolved
   findings, zero projection lag, and no reported blockers.
6. Actor assertions drifted during the closure phase despite one stable writer ID. Earlier calls
   used actor ID `codex-provider-model-catalog-20260728`, actor type `model_backed_worker`, and
   client version `2026-07-28`; some later calls used actor ID `codex`, actor type
   `logical_agent`, and client version `1.0`; final calls restored the original actor ID but retained
   the changed actor type/client version and changed display name.
7. `item_141` honestly disclosed all verification results, semantic completion provenance,
   final receipt/frontier, no issue/PR/commit/push, and the absence of live account availability or
   interoperability proof.

**Observer inference**

- The report/receipt recursion was handled unusually carefully and is a genuine strength. The
  driver distinguished interim from final evidence and did not claim bytes were covered before
  their digest was published.
- Semantic health was operational but unreliable: only 3 of 8 attempts succeeded. More
  importantly for agent quality, the semantic path added no practical value. It never saw or
  challenged the literal unsupported catalog and produced no code or documentation improvement.
- The final report's statement that semantic guidance “helped materially” is too strong. Guidance
  caused real, provenance-bearing dispatch and honest coverage wording; semantics itself did not
  materially improve the work. Repeated schema-invalid/timeout failures consumed substantial
  effort, and final closure required narrowing the review subject.
- Actor assertion drift does not invalidate the service-authenticated stable writer identity, but
  it weakens the clarity of the self-asserted participant record and should be avoided in a run
  whose purpose includes auditability.
- The final handoff is honest about live interoperability limits. Independent official-document
  verification after completion corroborated the questioned OpenAI, Anthropic, Gemini, and xAI
  identifiers. The remaining AQ-005 concern is reproducibility of the driver's source-review
  record, not invented catalog entries.

### Post-run correction to the live AQ-005 inference

The raw driver stream did not preserve per-ID source results, so the live observer initially
inferred that the future-looking OpenAI 5.6 labels might be invented or copied from the Codex
execution-model name. That inference is superseded. Independent official-document verification
confirmed exact OpenAI API IDs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`, plus the
questioned Anthropic, Gemini, and xAI families. Anthropic's current catalog also includes newer
models omitted from this capped sample, but the picker explicitly documents non-exhaustiveness and
retains custom entry.

Accordingly, AQ-005 is downgraded to a low-severity evidence-preservation concern. Earlier
chronological statements are retained as an honest record of what the raw trace supported at the
time, not as the final factual conclusion.

## Final live finding state

| ID | Severity | Final disposition |
|---|---|---|
| AQ-001 | Low | Mitigated after the driver read all packaged guidance and exact examples. Retained as startup-efficiency evidence. |
| AQ-002 | Low | Confirmed Yoetz diagnostic weakness; driver recovered through repository schema inspection. |
| AQ-003 | Medium | Confirmed. Five durable plan/obligation events retain unsupported occurrence times about 9.5 hours before acceptance; final report omitted this. |
| AQ-004 | Medium | Confirmed. Projection failures blocked the full design event and forced a materially lossy durable decision. |
| AQ-005 | Low | Corrected. Official documents independently corroborated the questioned IDs; the driver did not preserve per-ID source results in its own evidence. |
| AQ-006 | Medium | Confirmed. Semantic review could not assess the literal catalog/provenance and produced no practical improvement. |
| AQ-007 | Low | Confirmed. Actor type/ID/display/client assertions drifted during closure while the writer ID stayed stable. |
| AQ-008 | Medium | Confirmed. Semantic-required completion was unstable: 3/8 attempts succeeded, with four schema-invalid failures and one timeout. |
