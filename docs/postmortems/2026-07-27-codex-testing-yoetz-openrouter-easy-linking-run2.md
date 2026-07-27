# Codex-testing + Yoetz OpenRouter easy-linking dogfood, run 2

**Date:** 2026-07-27

**Disposition:** implementation and raw evidence retained only on the experiment branch; this
postmortem retained on `main`

**Experiment branch:** `codex/openrouter-easy-linking-dogfood-20260727-run2`

**Experiment commit:** `7805355` (`dogfood: preserve OpenRouter easy-linking run 2`)

**Repository baseline:** `04ca1cc5736f58ff7c3296b81e0362d989952ad3` (`main` and `origin/main`)

**Codex-testing:** `0.146.0-alpha.2`

**Codex session:** `019fa343-a85e-7d72-a4cb-72579f41a72a`

**Yoetz task:** `tsk_02f6b5a5-f246-4c35-b56d-40c4106aad57`

**Yoetz session:** `ses_746e3630-4983-4497-afa4-dc3bc45847ca`

**Final Yoetz receipt:** `rcp_034e7870-dd78-4741-a819-8c8c07e02133`

**GitHub disposition:** no issue, PR, push, or merge

## 1. Executive verdict

This was a strong Codex implementation run and a mixed Yoetz product run.

Codex found the real gap, implemented the smallest compatible change, preserved the security and
privacy boundaries, fixed every local regression it encountered, and ended with strong local
verification. The practical agent-quality observer scored Codex **8/10**.

Yoetz materially improved the trustworthiness of the handoff. It forced Codex to separate:

- the existing structural OpenRouter runtime path;
- the newly added convenience shortcut;
- a live Fireworks semantic review of the published claim; and
- the still-unverified live OpenRouter path.

It also made ambiguous durable writes recoverable through authoritative frontier reads and kept the
final wording coverage-bounded. The observer scored Yoetz's contribution **6/10: positive, but
expensive**.

Yoetz did not materially improve the code itself. The decisive diagnosis and implementation came
from ordinary repository reasoning: ADRs, interfaces, code, and tests showed that the OpenRouter
preset, secure credential binding, privacy gateway, runtime factory, request shape, response
normalization, and provenance already existed. Only the root shortcut parallel to `--fireworks` was
missing.

Yoetz also introduced substantial friction and failed at its most important closure promise:

- one `start` request used an unsupported field;
- completion publication hit `unsorted_set_field` and `ref_mirror_mismatch`;
- two accepted `publish_work` operations committed durably but returned
  `INTERNAL_ERROR/response_projection_failed`;
- neither stored-result recovery attempt produced the promised successful response;
- a successful `semantic_required` check with Fireworks provenance was followed by a receipt that
  dropped semantic coverage and reported `check_not_applicable`;
- the final status view dropped cooperative publication and check coverage again; and
- formatting and verification after receipt generation meant the receipt did not cover the final
  worktree anyway.

### Bottom line

**Did Yoetz make Codex better?** Yes, in evidence discipline, provider attribution, ambiguity
recovery, and honest reporting.

**Did Yoetz make Codex produce better code?** There is no evidence that it did. A competent agent
following the repository authority chain likely would have produced the same patch faster without
Yoetz.

**Did Yoetz make the run slower or less fluent?** Clearly yes. Event authoring, schema inspection,
publication repair, and response-recovery work consumed a large fraction of the run.

**Did Yoetz give correct guidance?** Its high-level guidance was mostly correct. Its operational
surface was not reliable enough: write-response recovery did not work cleanly, and receipt coverage
contradicted the successful check it should have represented.

**Is OpenRouter now live-proven?** No. The branch contains a locally verified easy-linking shortcut.
The only external semantic request in this run used the already-bound Fireworks provider.

## 2. What was tested

The assignment asked isolated `codex-testing`, in automatic workspace-write mode, to add:

```text
yoetz --set --openrouter --model <model-id>
```

alongside the existing Fireworks shortcut.

The maintainer explicitly authorized a one-run exemption from opening an issue, waiting for
acknowledgement, and preparing a full PR. The prompt preserved all substantive authority, privacy,
security, durability, testing, and honesty rules. It prohibited pushes, external publication,
credential exposure, and unauthorized privacy or provider changes.

Two independent observers ran alongside Codex:

- the **agent-quality observer** evaluated practical reasoning, Yoetz use, payload mistakes,
  verification, efficiency, and final-answer honesty;
- the **Yoetz-health observer** evaluated service lifecycle, MCP activation, durability,
  validation, semantic provenance, privacy authorization, receipts, and frontier consistency.

All product changes, the prompt, the 139-event/3 MB Codex JSONL stream, Codex's final report, and
both observer reports are preserved at experiment commit `7805355`. Only this synthesis is on
`main`.

## 3. Evaluation limits

This was not a controlled Yoetz-versus-no-Yoetz A/B test. There was no second run using the same
model, prompt, repository state, and time budget without Yoetz.

Good behavior may have come from any combination of:

- the explicit dogfood prompt;
- `AGENTS.md` and `CONTRIBUTING.md`;
- the repository's ADR and interface authority chain;
- Codex's own engineering ability; and
- Yoetz instructions, checks, and receipts.

The causal assessment therefore distinguishes behavior Yoetz directly required or exposed from
behavior that ordinary repository work produced independently.

The run also did not:

- build and reinstall a wheel into the isolated harness;
- enter or rotate an OpenRouter credential;
- change the active personal provider binding;
- authorize new OpenRouter egress;
- send a live OpenRouter request; or
- produce OpenRouter provider provenance or receipt replay.

The `uv run` and raw MCP checks exercised the development worktree. They are not installed-wheel
parity evidence.

## 4. What Codex built

Codex determined that generic OpenRouter support was already present. The branch adds only the
missing convenience surface:

- root `--openrouter` option;
- mutual exclusion between provider shortcuts and `--provider`;
- routing through the existing `provider_preset("openrouter")`;
- continued use of the hidden-TTY credential ceremony;
- ADR-012 and provider-usage documentation; and
- focused shortcut, exclusion, and preset-selection tests.

The final product diff was 102 insertions and 12 deletions across:

- `src/yoetz/cli/app.py`;
- `src/yoetz/cli/setup.py`;
- `tests/subprocess/test_setup_wizard_cli.py`;
- `docs/adr/ADR-012-first-run-setup-wizard.md`; and
- `docs/usage/providers.md`.

No vault, credential storage, privacy policy, egress, provider adapter, request-shape,
normalization, or provenance code changed.

## 5. Timeline

### 5.1 Baseline and activation

The run started from clean `main`/`origin/main` at `04ca1cc`. The branch was
`codex/openrouter-easy-linking-dogfood-20260727-run2`.

Codex inspected the authority chain and the `start` schema, then sent a `start` request containing
unsupported `workspace_ref`. Yoetz rejected it with:

```text
INVALID_REQUEST
correlation=err_652a50ff-2485-4981-b3da-c1035114b9d5
```

The public hint pointed to the schema example but did not identify the invalid field. Codex removed
the property and successfully created the task at frontier 1.

This established real MCP-local activation. It did not establish ordinary service readiness or
provider activation.

### 5.2 Repository diagnosis

Codex initially searched too broadly and allowed the growing `codex-events.jsonl` to pollute later
search output. It corrected the search scope and found:

- reviewed OpenRouter profile and endpoint identity;
- nonsecret binding support;
- credential-vault binding;
- privacy and authorization path;
- runtime factory dispatch;
- OpenAI-compatible Chat Completions request and normalization;
- semantic provenance; and
- existing focused tests.

The diagnosis was correct: the missing parity was the named root shortcut, not a provider
implementation.

This was the most important engineering decision in the run, and it came from ordinary source
inspection. Yoetz recorded the decision but did not originate it.

### 5.3 First ambiguous durable publication

Codex published the diagnosis as a claim plus evidence at expected frontier 2. Yoetz returned:

```text
INTERNAL_ERROR
reason_code=response_projection_failed
count=2
sequence=4
head_digest=sha256:96a2934ceef1cdc41c66f54c00464295eae654f3623033dc40af67ee3fe6449f
correlation=err_56b63cce-5f90-4aa8-b86a-5e06721fd40c
```

This is an improvement over the prior dogfood: the error clearly stated that the write was accepted
and durable and included the exact authoritative frontier.

The response instructed same-request replay. Codex reused the request ID but changed both
causal-parent values, so Yoetz correctly returned `IDEMPOTENCY_CONFLICT`. This attempt does not test
exact replay; it was an agent error.

Codex then followed the right recovery path: compact status and history proved both events were
projected at frontier 4.

### 5.4 Implementation and debugging

Codex applied the narrow shortcut patch and documentation.

The first focused test run produced:

```text
23 passed, 1 failed
```

Adding another provider name made Rich wrap an existing public error message, breaking the stable
assertion for `require --set`. Codex reproduced the exact CLI output, simplified the message to:

```text
provider flags require --set
```

and reran the tests successfully. It then added a deeper test proving the new shortcut selects the
existing OpenRouter preset rather than merely forwarding a flag.

The formatter later found the modified test file needed formatting. Codex formatted it and reran
the relevant verification instead of omitting the failure.

Final local evidence:

- 71 focused setup/config/factory/request-shape tests passed;
- 7 CLI/MCP surface-conformance tests passed;
- targeted Ruff lint passed;
- Ruff format check passed;
- pinned Pyright reported zero errors, warnings, or information;
- `git diff --check` passed.

### 5.5 Bounded runtime checks

The development worktree reported Yoetz `0.1.0`.

The new command failed closed without a TTY:

```text
invalid_request: --set requires a local terminal for hidden credential input
```

That is useful security evidence: no credential entered arguments, config, logs, or agent context.

A literal MCP initialize/tools-list exchange succeeded and advertised the six expected tools. This
proved worktree MCP operability, not external-provider activation.

Ordinary service and provider status remained:

- user service unavailable;
- current personal binding Fireworks;
- credential state unknown;
- privacy/inference state unknown;
- semantic readiness false or indeterminate.

A nonsecret environment overlay selected the exact OpenRouter provider/profile/model while
readiness stayed false. That proved configuration routing only.

### 5.6 Completion publication failures

Codex attempted to publish its action, local evidence, partial result, and completion claim.

The first batch failed with:

```text
EVENT_INVALID
field=/event_drafts/3
reason_code=unsorted_set_field
correlation=err_d4dc8818-511b-4c78-a72c-0345e3ef32cc
```

After sorting, the next failed with:

```text
EVENT_INVALID
field=/event_drafts/3
reason_code=ref_mirror_mismatch
correlation=err_af728db8-51ea-402c-a17c-9c2b082f07ab
```

The validation was safely fail-closed and identified the event index, but not the exact mirrored
field or expected value. Codex inspected validation source and simplified the event batch.

The corrected four-event batch committed but again returned:

```text
INTERNAL_ERROR
reason_code=response_projection_failed
count=4
sequence=8
head_digest=sha256:c1037d45fa453b4f17f7dec0a8ccaff06bef3fa5864d86965b11eaacb8a2a404
correlation=err_6062e45a-4e96-434a-817b-ef4e10c373d9
```

This time Codex replayed the same request ID and payload. The stale expected frontier was still 4,
and Yoetz returned `FRONTIER_CONFLICT` at frontier 8 rather than loading the promised stored
result. Status again proved the original operation committed.

Durability held. Stored-response recovery did not work as advertised.

### 5.7 Successful Fireworks semantic check

Codex requested `semantic_required` for the bounded completion claim at subject frontier 8.

The check succeeded with zero findings and advanced to frontier 9. Its external provenance was:

- provider: `fireworks`;
- endpoint: `fireworks-responses/1.0.0`;
- model: `accounts/fireworks/models/minimax-m3`;
- semantic attempt: `att_6cb1a244-52f5-4a76-86e9-a9ff0aebd51c`;
- provider request: `resp_ec654f98ba3d4afaba4ee2c0fa10a996`;
- egress authorization: `aut_c3879d1a-ea92-41b3-9933-5b219b8a8ea0`;
- privacy receipt: `egr_b6cf3f0c-f7ba-47b3-917c-f368a741895b`;
- latency: 2,831 ms; and
- status/reason: `succeeded/semantic_completed`.

This is real proof that the Yoetz MCP-local composition, privacy gateway, authorization,
Fireworks adapter, semantic normalization, and provenance path worked for the published claim.

It is not OpenRouter proof. There was no fallback: Fireworks was the active provider throughout.

The provenance also reported all-zero `policy_digest` and `privacy_policy_digest`, while the
agent-context privacy projection reported the nonzero effective policy digest. The authorization,
commitment, and privacy receipt make the dispatch audit-linked, but the zero provenance digests are
a material provenance-quality discrepancy.

### 5.8 Receipt and final-status contradiction

Codex requested a final receipt for frontier 9. Yoetz persisted:

```text
receipt_id=rcp_034e7870-dd78-4741-a819-8c8c07e02133
receipt_object_id=obj_328d3bbd-9737-4cce-8a49-d14d44fbd729
receipt_digest=sha256:040001fe3a456bbfddcd5b6db8cc5b9458d7360c3a2635e6d6cede1c822a5f8b
result_frontier=10
```

The conclusion was honestly weak:

```text
insufficient_coverage
ledger_freshness=partial
check_types=["deterministic"]
known_gaps=["check_not_applicable"]
```

That coverage contradicts the immediately preceding successful check at frontier 9, which reported
current freshness and both deterministic and semantic-model-derived check types. The receipt did
not carry forward the successful semantic coverage or provenance.

The final compact status at frontier 10 weakened the view further:

```text
publication_channels=["engine_derived"]
authorship_assurance="service_authenticated"
check_types=["none"]
```

This lost the cooperative publication and successful check coverage visible at earlier frontiers.
The ledger remained durable and current, but its final reader-facing coverage was not a trustworthy
summary of the run.

Codex correctly accepted the weakest conclusion and refused to claim receipt-backed completion.

After generating the receipt, Codex formatted the test file and ran additional verification. Those
are material final-state changes not covered by the frontier-9 subject receipt. Even if the receipt
coverage bug did not exist, a new publish/check/receipt cycle would have been required for terminal
worktree closure.

## 6. What went well

### 6.1 Codex engineering quality

Codex:

- found the exact missing UX surface;
- avoided duplicate provider plumbing;
- preserved hidden credential input and independent egress authorization;
- updated the owning ADR and usage docs;
- added behavior-level tests rather than only argument-forwarding tests;
- fixed the CLI wording regression;
- formatted and reran verification;
- distinguished worktree, structural, Fireworks, and OpenRouter evidence; and
- left the work on the requested branch without issue, PR, push, or merge.

### 6.2 Yoetz's integrity effect

Yoetz:

- required real activation before claiming use;
- recorded plan, claim, and evidence frontiers;
- made durable-write ambiguity visible rather than silent;
- gave Codex an authoritative status recovery path;
- directed qualitative provider/privacy correctness to `semantic_required`;
- returned exact Fireworks provenance and privacy authorization identifiers; and
- prevented the final answer from presenting local or Fireworks evidence as OpenRouter success.

The strongest Yoetz benefit was not finding a better patch. It was making an overclaim much harder.

### 6.3 Privacy and security

No credential was exposed or copied. The OpenRouter setup command failed closed outside a local
TTY. No personal provider binding or privacy policy was changed. The Fireworks request used a
separate authorization, request commitment, and privacy receipt.

## 7. What did not go well

### 7.1 Agent-to-Yoetz authoring remained too difficult

Codex made avoidable payload mistakes despite reading schemas:

- unsupported start field;
- unsorted set-valued references;
- envelope/payload mirror mismatch; and
- changed payload during the first same-ID recovery attempt.

The JSON schema is exact, but exactness alone is not usable guidance. The agent had to inspect
implementation source to understand a routine event correction.

### 7.2 Durable write response integrity is still broken

Two accepted publications returned `response_projection_failed`. The new safe details are a real
improvement because they remove outcome ambiguity, but the normal successful response was still
unavailable.

The advertised stored-result replay was not demonstrated:

- the first attempt changed the payload and correctly conflicted;
- the second kept the request but returned stale-frontier conflict rather than the stored result.

Frontier/status recovery protected durability, but it is a fallback, not a healthy idempotent
response path.

### 7.3 Receipt coverage did not represent the successful check

This is the highest-severity product finding from the run.

The semantic check succeeded and carried full Fireworks provenance. The immediately following
receipt said the check was not applicable and dropped semantic coverage. Final compact status then
dropped check coverage entirely.

A receipt that cannot faithfully summarize the preceding successful check cannot serve as the
authoritative closure artifact, even when its conservative conclusion avoids a false success.

### 7.4 Final-state closure was not maintained

Codex performed formatting and verification after receipt generation. Yoetz guidance says to
recheck after material change, but the workflow did not cause the agent to publish and close again.

The final answer honestly disclosed the weak receipt, but the run still ended without a receipt for
the terminal worktree.

### 7.5 Ordinary service readiness remained confusing

The independently probed user service remained unavailable while MCP-local start, writes, check,
and receipt all worked. Provider status does name `mcp_local_composition=starts_on_demand`, which is
better than an unexplained contradiction, but the overall operator story still requires manual
interpretation.

### 7.6 Efficiency was poor

Codex performed noisy broad searches and accidentally searched the growing JSONL log. It spent
substantial time reading event schemas and validation source, repairing publications, and
recovering committed frontiers.

The harness reported 8,292,096 input tokens, of which 8,060,416 were cached, and 32,312 output
tokens. Those counters are not a clean billing measure, but they reflect an unusually large
interaction footprint for a five-file convenience change.

## 8. Comparative assessment

Compared with the 2026-07-26 OpenRouter dogfood:

### Improved

- Codex was authorized to implement, so practical code quality could be measured.
- The remaining product gap was resolved rather than only described.
- Local verification was substantially stronger.
- Post-commit error responses now disclosed that the write was durable and returned exact
  `sequence`, `head_digest`, and `count`.
- Validation errors exposed event indexes and reason codes.
- Codex recovered ambiguous writes without duplicating committed events.
- Fireworks semantic provenance remained explicit and correctly separated from OpenRouter.

### Unchanged or still weak

- OpenRouter was structurally present but not live-proven.
- Ordinary service readiness and MCP-local operation still required explanation.
- Event authoring remained error-prone.
- Accepted publications still returned response-projection errors.
- Yoetz improved trustworthiness more than code quality.
- The final receipt still concluded `insufficient_coverage`.

### Worse or newly exposed

- The receipt/check applicability mismatch is now clearer and more serious: a successful
  semantic-required check was immediately omitted from receipt coverage.
- Final compact status also lost the earlier cooperative/check coverage.
- Semantic provenance carried suspicious all-zero policy digests.
- Post-receipt changes showed that the workflow still does not reliably close over the final
  worktree.

## 9. Scorecard

| Dimension | Assessment | Evidence |
|---|---|---|
| Codex scoping | Strong | Found the shortcut-only gap and avoided duplicate provider work |
| Codex implementation | Strong | Minimal five-file change aligned with existing authority |
| Codex verification | Strong | 71 focused + 7 surface tests; Ruff, format, Pyright, diff clean |
| Codex security/privacy | Strong | Hidden-TTY boundary preserved; no credential or policy mutation |
| Codex Yoetz fluency | Weak to moderate | Repeated schema errors and one changed replay payload |
| Codex final honesty | Strong | No OpenRouter or receipt-backed completion overclaim |
| Yoetz MCP/durability | Good | Operations reached durable frontiers with zero projection lag |
| Yoetz validation | Mixed | Fail-closed and indexed, but insufficiently actionable |
| Yoetz write responses | Poor | Two accepted writes surfaced as internal errors |
| Yoetz recovery UX | Poor | Stored-result replay did not produce a successful response |
| Yoetz semantic dispatch | Good for Fireworks | Real authorization, privacy receipt, request ID, and provenance |
| Yoetz OpenRouter evidence | Absent | No live OpenRouter request occurred |
| Yoetz receipt integrity | Poor | Successful semantic check became `check_not_applicable` |
| Yoetz net agent effect | Positive but expensive | Better honesty/auditability, worse efficiency |

## 10. Prioritized findings

### P0 — Receipt/check coverage integrity

The receipt builder or applicability projection did not carry the successful frontier-8
`semantic_required` check into the frontier-9 receipt. Final status then lost it again.

Required evidence for a fix:

- a regression that records a scoped successful semantic check;
- a receipt at the exact result frontier;
- preserved deterministic and semantic check types;
- preserved applicable check provenance;
- no false `check_not_applicable`; and
- stable coverage in later compact status.

### P1 — Accepted-write response projection and replay

`publish_work` committed at frontiers 4 and 8 but returned internal errors. Exact stored-response
replay did not succeed in the one valid replay attempt.

Required evidence for a fix:

- the original accepted write forced into response-projection failure;
- safe durable frontier details;
- byte-identical replay with the same request ID;
- replay lookup before stale-frontier rejection;
- the stored successful result returned without reappending events; and
- status/history showing one copy of each event.

### P1 — Semantic provenance policy digests

The Fireworks semantic provenance used all-zero policy digests while the privacy projection exposed
the real nonzero policy digest.

Required evidence for a fix:

- provenance fields bound to the exact effective policy and privacy-policy digests used for the
  physical dispatch;
- agreement with the egress receipt; and
- a negative test preventing zero placeholders on successful external dispatch.

### P2 — Agent-facing event construction

Add canonical examples or typed builders for every admitted event family, sorted-set handling, and
envelope mirror rules. Validation should identify the exact offending field and expected mirror,
not only the event index.

### P2 — Terminal-state closure

The integration should detect material work after the last check/receipt and explicitly require a
new publication cycle before the agent calls the task closed.

### P2 — Lifecycle/readiness explanation

CLI readiness should present user-service state and MCP-local on-demand state in one coherent view,
without requiring the operator to reconcile separate surfaces manually.

### P3 — Harness log isolation

Dogfood instructions or default search exclusions should keep generated JSONL artifacts out of
repository searches while the run is active.

## 11. Recommended next experiment

Do not repeat another broad implementation dogfood first. Run a bounded verification matrix:

1. reproduce `response_projection_failed` and prove byte-identical stored-result replay;
2. reproduce successful `semantic_required` followed by receipt generation and prove coverage
   retention;
3. verify semantic provenance policy digests against the egress receipt;
4. rerun the same small shortcut task with client-side event builders/examples; and
5. only after those pass, perform an authorized OpenRouter live smoke with:
   - exact installed wheel;
   - service ready and unlocked;
   - OpenRouter-bound credential;
   - independently enabled `llm_inference`;
   - literal MCP handshake;
   - `semantic_required`;
   - OpenRouter provider request/provenance;
   - privacy authorization and receipt;
   - final receipt replay; and
   - no post-receipt material work.

A future causal evaluation should also run the same bounded task once with Yoetz and once without it
under the same model and budget. That would distinguish integrity gains from prompt and repository
discipline more reliably.

## 12. Final conclusion

Yoetz is doing real, useful work for Codex now. It is no longer merely registered or decorative.
It influenced the workflow, captured durable state, enabled a real semantic review, exposed provider
provenance, and constrained the final claim.

But it is not yet a fluent or reliable completion layer.

For this run, Yoetz made Codex **more trustworthy, not more capable**. The patch quality came from
Codex and the repository. Yoetz's value was auditability and epistemic restraint. That value was
material, but it came with too much protocol ceremony, repeated post-commit response failures, and
a receipt that failed to represent the successful check it followed.

The honest disposition is:

- the experiment branch contains a strong local OpenRouter easy-linking implementation;
- local code and worktree surfaces passed proportionate verification;
- Fireworks-backed Yoetz semantic review succeeded for the published claim;
- OpenRouter live interoperability remains unverified; and
- Yoetz did not achieve trustworthy receipt-backed closure for the terminal worktree.
