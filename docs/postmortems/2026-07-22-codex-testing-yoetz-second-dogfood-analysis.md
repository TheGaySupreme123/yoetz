# Codex-testing + Yoetz second dogfood: evidence-based failure analysis

**Date:** 2026-07-22  
**Tested Codex session:** `019f8b27-b98e-7061-bbb5-d0b897594de6`  
**Repository baseline:** `a09f51b`  
**Preserved generated implementation:** branch `dogfood/codex-testing-multiprovider`, commit
`59afd70`  
**Related intake:** GitHub issue 6  
**Comparison report:**
[`2026-07-22-codex-testing-yoetz-activation.md`](2026-07-22-codex-testing-yoetz-activation.md)

## Purpose and limits

This document explains why the second Yoetz dogfood run did not provide useful implementation
assistance even though several previously broken setup layers now worked. It concentrates on the
small number of failures that materially determined the outcome and ties each conclusion to direct
runtime, durable-state, transcript, or source evidence.

This is not a remediation design. “Improvement surface” means a capability or boundary that the
evidence shows is missing, misleading, or difficult to use. It does not select an implementation,
protocol revision, or product policy.

The generated provider implementation is preserved as experimental evidence. It is not described
as release-ready or as an intended change to `main`.

## Executive finding

The second run improved Yoetz from **registered but unused** to **started, auto-unlocked, and
durably allocated**. It did not improve Yoetz to a functioning work ledger/checker for the task.

The decisive sequence was:

1. Codex was explicitly instructed to use Yoetz.
2. `start` succeeded and created one task/session/writer at frontier 1.
3. The first work publication was non-canonical and failed; Yoetz hid the useful reason behind
   `INTERNAL_ERROR`.
4. Codex could not read the complex MCP request type from its tool declaration and began guessing
   fields.
5. Every later `status`, `publish_work`, and `check` call was malformed.
6. The durable ledger therefore retained only the initial session-open event.
7. Codex continued in Yoetz's documented degraded mode, compacted using its own context summary,
   generated code, ran static tests, and completed without a Yoetz finding or receipt.
8. Independent verification found that the new provider paths fail before network dispatch and
   that semantic checking remains explicitly unconfigured.

Yoetz did not produce an incorrect code recommendation. It produced no code recommendation at all.
Its contribution was limited to an empty durable session, privacy/honesty instructions, and failed
workflow ceremony.

## Evidence set

The findings below use these primary artifacts:

- Complete isolated Codex rollout:
  `/Users/shayb/.codex-testing/sessions/2026/07/22/rollout-2026-07-22T21-47-44-019f8b27-b98e-7061-bbb5-d0b897594de6.jsonl`.
- Terminal transcript:
  `/tmp/codex-testing-provider-endpoints-20260722-214656.typescript`.
- Current task ledger:
  `/Users/shayb/Library/Application Support/Yoetz/tasks/tsk_24f56d5d-cdfc-4681-9fd4-a121147c9338/ledger.sqlite3`.
- Preserved generated diff: commit `59afd70`.
- The prior dogfood report, which records the earlier zero-call activation failure and provider
  metadata-only implementation.
- Read-only/pure diagnostic replays performed after the run: one valid `status` probe, two
  `publish_work` preparation replays, and local SDK transport probes.

No conclusion relies only on the Codex final answer. Where the transcript, database, and code have
different explanatory strength, this document states the narrower conclusion.

## Previous run versus second run

| Layer | Previous run | Second run | What changed |
| --- | --- | --- | --- |
| MCP registration | Six tools discoverable | Six tools discoverable | No material change. |
| Activation | Zero Yoetz calls | One successful `start` | Activation occurred, but the prompt explicitly required Yoetz use. This is not proof of spontaneous activation. |
| Service lifecycle | Not exercised | On-demand daemon start and auto-unlock succeeded | A real infrastructure improvement. |
| Durable task allocation | No task for the run | Task/session/writer/frontier persisted | A real infrastructure improvement. |
| Work publication | None | Two failed attempts; zero accepted publications | No usable workflow improvement. |
| Re-grounding | No `status` call | Seventeen invalid `status` calls | The operation exists, but the Codex-facing contract was not usable in this run. |
| Check/receipt | Not exercised | One invalid `check`; no receipt | Still no completion constraint or finding. |
| Semantic review | Production evaluator not configured | Production evaluator still not configured | No improvement. |
| Generated code | Plausible provider metadata; no runtime factories | Runtime factories and Chat adapter added | Broader implementation, but primary dispatch and semantic paths remain nonfunctional. |
| Final claim | “Implemented and verified” overclaimed the evidence | Final answer disclosed no live dispatch and no Yoetz receipt | Codex's claim honesty improved. |

The previous report's overall result was “registration passed; activation failed”
([line 14](2026-07-22-codex-testing-yoetz-activation.md#L14)). The second result is more specific:
activation and allocation passed, but cooperative publication and checking failed.

## Claim 1: activation improved, but the run does not prove automatic activation

### Proof

The exact user message in the rollout says:

> Use the Yoetz MCP actively for relevant repository/context guidance before and during
> implementation, and tell me when Yoetz helped or failed.

At `2026-07-22T18:49:58.849Z`, `start` returned:

- `ok: true`;
- `outcome: created`;
- task `tsk_24f56d5d-cdfc-4681-9fd4-a121147c9338`;
- session `ses_a5339000-b828-4a1f-9bd9-364fe0a54a96`;
- writer `wri_3ae1b2a1-e5f2-4287-ac95-43e44245df94`;
- frontier sequence `1`.

The daemon generation advanced, the vault auto-unlocked, and the catalog/task databases changed.
These facts establish that MCP dispatch, lazy service launch, ready activation, local privacy
projection, task allocation, and persistence all worked.

### Bounded conclusion

The result is stronger than the previous zero-call run, but it does not isolate the effect of the
activation remediation. The user prompt manually supplied the missing activation instruction. A
material task without that instruction was not tested here.

### Improvement surface shown by the evidence

The product still lacks evidence that a normal material request triggers Yoetz without
Yoetz-specific wording. This is a missing demonstrated capability, not proof that automatic
activation can never work.

## Claim 2: the first publication failure was caused by a non-canonical event set, then obscured

### Proof from the original request

The first `publish_work` request at `2026-07-22T18:53:31.596Z` used this plan order:

```json
"obligation_refs": [
  "obl_f0aff344-9df1-4484-9b92-91dcfc537a34",
  "obl_de3b7375-2d0d-44dd-a977-cef2be451154"
]
```

Yoetz requires set-like identifier tuples to be ASCII-sorted. The domain validator calls
`_validate_ascii_sorted_unique` in
[`domain/events.py`](../../src/yoetz/domain/events.py#L319) and raises
`ProtocolValueError("unsorted_set_field")` when ordering is non-canonical.

A pure replay of the recorded request through `PublishWorkRequest.model_validate` followed by
`prepare_publication` reproduced exactly:

```text
ProtocolValueError: unsorted_set_field
PublicOperationError: The event batch is invalid.
```

The live MCP result did not expose `EVENT_INVALID` or `unsorted_set_field`. It returned:

```text
Error INTERNAL_ERROR; retryable: no;
correlation: err_94d951a8-33e0-45dc-869f-d63d570adf87
```

The MCP bridge's generic exception branch converts unhandled failures into “The bridge could not
complete the operation” in [`mcp/server.py`](../../src/yoetz/mcp/server.py#L285).

### Bounded conclusion

Codex authored an invalid publication. Yoetz detected the invalidity, but its public error path
removed the only fact that would have let Codex correct the request. Both sides contributed:
Codex violated the canonical ordering contract; Yoetz represented a correct validation failure as
an opaque internal failure.

### Improvement surface shown by the evidence

The agent-facing contract does not make canonical set ordering discoverable enough, and the public
failure does not preserve a safe, actionable classification for this known input error.

## Claim 3: the MCP tool shape was valid JSON Schema but unusable in Codex's rendered declaration

### Proof

Yoetz serves bundled Draft 2020-12 schemas through `list_tools` in
[`mcp/server.py`](../../src/yoetz/mcp/server.py#L396). The `status` request itself explicitly
requires protocol/schema versions, request ID, actor, client, session, writer, view, and a
string-valued limit in [`protocol/models.py`](../../src/yoetz/protocol/models.py#L1144).

Codex's discovered declaration did not show those fields. It rendered:

```text
mcp__yoetz__status(args: unknown & unknown & unknown & unknown & unknown & unknown & unknown)
mcp__yoetz__start(args: unknown)
mcp__yoetz__publish_work(args: unknown & unknown & unknown)
```

The rollout then records Codex building `status` one field at a time. Errors progressed through:

- `/protocol_version`;
- `/request_id`;
- `/actor`;
- `/client` and `/client/kind`;
- `/client/integration`;
- `/session_id`;
- `/writer_id`;
- `/view`;
- `/limit`.

This was not ordinary semantic uncertainty. Codex was reconstructing a hidden function signature
from validation failures.

### Bounded conclusion

The underlying schema may be protocol-valid while still failing the actual agent-usability
requirement. The evidence does not identify whether Codex's schema-to-TypeScript converter or the
complexity/composition of Yoetz's schemas is the sole cause. It establishes an interoperability
failure between the exact versions tested.

### Improvement surface shown by the evidence

Basic operation arguments are not visible to the target agent in a directly usable form. Schema
conformance and agent-call usability are currently different capability levels.

## Claim 4: `status` is not universally broken; Codex never sent the correct fresh request

### Proof from the failed calls

The first status sequence used a new request ID but included unsupported `task_id`, supplied
`limit` as an integer, and was abandoned before reaching a valid shape.

The later schema-walking sequence added unsupported `client.id`. Once all required fields appeared,
that extra field remained. Several calls also reused the original `start` request ID rather than a
fresh read correlation identity.

The same `client.id` defect appears in the recorded `check` request and the second
`publish_work` request. A pure validation replay of the second publication returned:

```text
client.id: Extra inputs are not permitted
```

After the run, a read-only `status` probe with:

- a fresh UUIDv4 request ID;
- `actor` containing only `actor_id` and `actor_type`;
- `client` containing only `kind`, `version`, and `integration`;
- the recorded session and writer IDs;
- `view: "compact"`;
- `limit: "50"`;

returned `ok: true`, frontier 1, zero open obligations, and zero findings.

### Bounded conclusion

The 17 failed status calls are strong evidence of unusable agent ergonomics and poor Codex request
construction. They are not evidence that a correctly shaped status request fails. Describing the
status implementation itself as completely broken would overstate the run.

### Improvement surface shown by the evidence

The target agent needs a request surface it can call correctly without iterative schema discovery,
and its call behavior needs to preserve exact fields rather than importing generic client concepts.

## Claim 5: Yoetz did not track the implementation after `start`

### Proof from durable state

A read-only query of the task ledger after completion returned:

```text
events|1
operations|1
plans|0
obligations|0
checks|0
findings|0
semantic_attempts|0
```

The single event is the session-open event created by `start`. The ledger has no accepted plan,
obligation, action, result, evidence, completion claim, check, or semantic attempt.

This matches Yoetz's own guidance: it “records only what participants publish” and “is not an
observer” in
[`agent-instructions.md`](../../src/yoetz/resources/guidance/agent-instructions.md).

### Bounded conclusion

Yoetz did not lose an accepted work record. No work record was accepted. After the first operation,
the implementation proceeded outside Yoetz's knowledge.

### Improvement surface shown by the evidence

The tested integration has no independent observation channel. Its knowledge is contingent on the
same agent successfully translating its work into strict publication events.

## Claim 6: compaction continuity did not improve

### Proof

Codex compacted at approximately `22:01:02` local time. Before compaction, no publication had been
accepted. After compaction, Codex did not complete a valid `status` read or attach to any richer
record. Work continued from Codex's own context-compaction summary.

The post-run ledger still had only frontier 1 and one event. Therefore no durable Yoetz plan or
obligation existed from which re-grounding could have occurred.

### Bounded conclusion

Yoetz persistence survived, but there was no useful persisted work state. This run demonstrates
storage continuity, not task-continuity value.

### Improvement surface shown by the evidence

Compaction recovery depends on successful publication before compaction and a usable status call
after it. Neither precondition held.

## Claim 7: Yoetz had no semantic reviewer available to inspect the generated work

### Proof

The prior run ended with production semantic evaluation falling back to
`_semantic_not_configured`. The generated second-run code adds provider factories to the privacy
gateway in
[`ready_composition.py`](../../src/yoetz/service/ready_composition.py#L907), but the final ready
application still sets:

```python
semantic_evaluator=_semantic_not_configured
```

at [`ready_composition.py`](../../src/yoetz/service/ready_composition.py#L1317).

The durable `semantic_attempts` table contains zero rows. No external-provider egress receipt was
created for the task.

### Bounded conclusion

Even a correctly shaped `check(mode="semantic_if_configured")` would not have produced an LLM code
review through this ready application. The provider-factory work did not connect to the semantic
checker entry point.

### Improvement surface shown by the evidence

Provider binding/factory availability and semantic-check availability remain separate states, but
the user-facing flow does not make that separation operationally clear.

## Claim 8: deterministic Yoetz checking could not independently discover the provider bug

### Proof from product boundaries

Yoetz's deterministic checker evaluates the cooperatively published record. It does not read the
working tree or decide that a test is semantically relevant to a claim. In this run it had no plan,
obligation, evidence, or completion claim to evaluate.

The actual provider defect requires comparing three code paths:

1. `render_chat_case` commits canonical request bytes at
   [`openai_responses.py`](../../src/yoetz/adapters/providers/openai_responses.py#L423).
2. `OpenAIChatCompletionsEvaluator` gives a Python object to the OpenAI SDK at
   [`openai_responses.py`](../../src/yoetz/adapters/providers/openai_responses.py#L963).
3. `OneAttemptCredentialTransport` requires the SDK-emitted bytes to equal the separately
   canonicalized bytes at
   [`openai_responses.py`](../../src/yoetz/adapters/providers/openai_responses.py#L845).

The SDK serialization is not byte-identical, so the transport raises
`openai_transport_body_mismatch` before the request reaches the underlying HTTP transport.
Independent local MockTransport probes reproduced the failure for both Chat Completions and
Responses paths; the mock transport received zero requests.

### Bounded conclusion

This is a source-aware transport invariant defect. The deterministic ledger checker is not designed
to discover it. Yoetz could only have constrained the completion claim if the agent first published
specific runtime-dispatch obligations and honest evidence about their satisfaction.

### Improvement surface shown by the evidence

There is no active source-aware or semantic evidence assessor in this run. Deterministic structural
integrity and code correctness are distinct coverage classes.

## Claim 9: the generated tests were green because they tested metadata, not dispatch

### Proof

The generated tests assert:

- TOML/provider metadata and path construction in
  [`test_owner_declared_endpoint.py`](../../tests/unit/config/test_owner_declared_endpoint.py#L214);
- presence of expected strings in canonical rendered bytes at
  [`test_owner_declared_endpoint.py`](../../tests/unit/config/test_owner_declared_endpoint.py#L250);
- construction of a provider factory and expected path at
  [`test_owner_declared_endpoint.py`](../../tests/unit/config/test_owner_declared_endpoint.py#L291).

They never call an evaluator through the pinned SDK and exact-body transport. They therefore cannot
observe the serialization mismatch. This explains how 60 relevant tests, Ruff, Pyright, and
`git diff --check` could pass while every composed provider failed before network dispatch.

### Bounded conclusion

The green tests were real evidence for configuration and static construction. Codex generalized
that evidence beyond the behavior exercised.

### Improvement surface shown by the evidence

The evidence classification does not visibly separate metadata/path construction from SDK wire
dispatch and semantic-check execution.

## Claim 10: setup readiness is stronger than the state it verifies

### Proof

`run_provider_setup` verifies only:

```python
binding == "configured"
credential == "stored"
```

and then prints “Yoetz is ready to use this provider” at
[`cli/setup.py`](../../src/yoetz/cli/setup.py#L504).

That path does not demonstrate:

- that the optional OpenAI SDK is installed in the active artifact;
- that the ready application exposes a semantic evaluator;
- that the privacy policy authorizes the provider;
- that a factory can produce a successful request;
- that response normalization succeeds;
- that a live or local transport probe ran.

The installed `/Users/shayb/.local/bin/yoetz` remained version `0.1.0` from before the generated
change. Its `provider endpoint --help` listed only Official OpenAI, Fireworks, and owner-declared
origin. The new user-facing choices were not installed or exercised as an installed artifact.

### Bounded conclusion

“Configured and stored” is supported. “Ready to use” is not supported by this flow or run.

### Improvement surface shown by the evidence

Setup state, runtime composition state, policy state, installed dependency state, and demonstrated
provider execution are currently compressed into one readiness sentence.

## Claim 11: the generated transport weakens an existing request-commitment boundary

### Proof

The factory receives `request_commitment: RequestCommitment` and immediately discards it:

```python
del request_commitment
```

at [`ready_composition.py`](../../src/yoetz/service/ready_composition.py#L207).

The same code uses a `ContextVar` to retain the previously rendered request body. Regardless of
whether that mechanism can be made safe, the generated implementation does not use the explicit
commitment value supplied by the gateway at evaluator construction.

### Bounded conclusion

The implementation does not preserve the visible commitment-binding contract through this factory
boundary. This is separate from the SDK byte-mismatch defect.

### Improvement surface shown by the evidence

The relationship between gateway authorization, rendered bytes, evaluator construction, and the
exact committed request is not demonstrated by the generated tests.

## Claim 12: the missing Yoetz skill contributed to ceremony problems but does not explain the code
defect

### Proof

No Yoetz skill existed under `/Users/shayb/.codex-testing/skills` during the run. The packaged skill
manifest has:

```json
"capability_profile_ids": [],
"codex_version_bounds": {"denied": [], "supported": [], "tested": []},
"hooks_by_capability_profile": {}
```

`CodexSkillIntegration.install_skill` rejects installation when `harness_tested_set` is empty in
[`codex_skill.py`](../../src/yoetz/adapters/integrations/codex_skill.py#L629).

The skill text provides activation, publication, status, check, receipt, and claim-wording
guidance. It does not provide a simplified call builder, workspace observation, source inspection,
or provider-transport validation.

### Bounded conclusion

An installed skill could have increased workflow salience and reminded Codex to re-ground after
compaction. It would not, by its current content, have repaired the malformed requests or found the
SDK serialization defect. In this run, the user prompt already supplied its most important
activation instruction.

### Improvement surface shown by the evidence

Skill compatibility/installation, MCP schema usability, observation, and semantic review are four
separate capabilities. “Skill present” would not establish the other three.

## Claim 13: Codex behavior was mixed rather than uniformly poor

### Directly supported good behavior

- It searched issues and reused issue 6 rather than opening a duplicate.
- It did not open a design-gated PR without acknowledgement.
- It read the ADR/spec authority chain.
- It distinguished Responses from Chat Completions and corrected exact Gemini/OpenRouter paths.
- It fixed its own Ruff and Pyright failures.
- Its final answer disclosed that no live provider dispatch and no Yoetz receipt existed.
- It continued after optional Yoetz failure, matching Yoetz's documented degraded-mode behavior.

### Directly supported poor behavior

- It sent non-canonical publication data.
- It guessed hidden MCP fields, introduced unsupported `client.id`, and did not return to the
  canonical request schema.
- It treated construction/path tests as sufficient evidence for runtime behavior.
- It never tested the installed artifact.
- It never ran an SDK-level local transport test or live provider request.
- It left semantic evaluation explicitly unconfigured while expanding provider factory code.
- It produced a user-facing readiness statement stronger than the verified state.

### Bounded conclusion

The result was not caused by a generally incapable Codex agent. Codex handled repository intake,
spec navigation, ordinary implementation, and static debugging competently. Its main failure was
evidence selection: it did not test the boundary carrying the strongest correctness risk, and
Yoetz supplied no independent pressure or review to correct that selection.

## Materially new or newly exposed defects

This run exposed the following defects or product gaps that were not directly exercised in the
first run:

1. Complex Yoetz MCP schemas collapse to `unknown` in this Codex version's callable declaration.
2. Canonical event-ordering failures can surface as opaque `INTERNAL_ERROR`.
3. Codex can easily invent forbidden wrapper fields such as `client.id` when reconstructing the
   hidden schema.
4. The generated SDK path conflicts with Yoetz's exact canonical-body transport invariant.
5. Provider factories can be registered while the ready application's semantic evaluator remains
   `_semantic_not_configured`.
6. Provider setup reports readiness after binding/credential persistence only.
7. The generated factory discards the explicit request commitment.
8. Installed-artifact state can remain behind the source tree while source-level tests pass.
9. Tier-zero guidance says secrets must never travel through argv, while ADR-012 retains one narrow
   `--api-key` compatibility exception. Codex resolved the conflict by reading the ADR, not from
   Yoetz guidance.

Items 1–3 affect agent usability. Items 4–7 affect runtime/provider correctness. Item 8 affects
dogfood validity. Item 9 affects authority consistency. They should not be collapsed into one
“Yoetz failed” label because their evidence and owners differ.

## What is carefully set up and genuinely working

The negative result should not erase the infrastructure that passed:

- isolated `codex-testing` state and authentication;
- correct MCP registration and initialization;
- six-tool discovery;
- on-demand service launch;
- vault auto-unlock;
- service generation and local same-user connection;
- catalog/task allocation;
- encrypted durable session-open state;
- local privacy projection and omission behavior;
- no secret exposure in the recorded Yoetz calls;
- truthful absence of fabricated findings or receipts;
- ordinary Codex issue intake and static verification.

These are prerequisites. The run shows that prerequisites alone do not produce useful work
tracking or review.

## Overall causal assessment

The strongest evidence supports this ordering:

1. **Primary product limitation:** Yoetz is cooperative rather than observational. Once
   publication failed, it had no knowledge of subsequent work.
2. **Primary integration failure:** the target Codex version could not render Yoetz's request
   schemas into usable call signatures, and Yoetz's error projection was too weak to recover.
3. **Unavailable review capability:** the ready application had no semantic evaluator and the
   deterministic checker had no published record.
4. **Codex verification failure:** Codex selected static construction tests rather than the SDK
   wire boundary that determined whether the feature worked.
5. **Skill limitation:** the supported skill path was unavailable, but even the packaged skill
   would chiefly improve workflow prompting, not observation or code correctness.

The second run therefore did not fail at one point. It crossed the infrastructure boundary, then
failed at the first cooperative publication boundary. Everything after that was ordinary Codex
work with Yoetz present but informationally empty.

## Final conclusion

Compared with the first dogfood run, Yoetz now proves that it can launch, unlock, allocate, and
persist a real session. It still does not prove that it can reliably participate in a normal Codex
implementation workflow.

The most important distinction is:

> Yoetz was running, but it was not tracking the work.

That happened because the only tracking channel required strict agent-authored publications; the
first publication failed opaquely, later calls were malformed, no observation or semantic-review
channel filled the gap, and Codex continued independently. The generated code then failed at a
runtime boundary that neither the published record nor the selected tests examined.

The result is not evidence that the setup work was worthless. It is evidence that registration,
daemon readiness, vault readiness, durable allocation, skill support, MCP call usability,
publication success, observation, deterministic checking, semantic review, and provider dispatch
are distinct capability layers. In this run, only the first four were demonstrated.
