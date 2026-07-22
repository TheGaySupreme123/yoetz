# Codex activation, lifecycle, and evidence remediation plan

**Date:** 2026-07-22  
**Status:** Proposed plan; not an approved architecture or implementation contract  
**Related postmortem:**
[`2026-07-22-codex-testing-yoetz-activation.md`](2026-07-22-codex-testing-yoetz-activation.md)  
**Repository baseline reviewed:** `c6a6fa6`  
**Primary failed run:** Codex session `019f89fc-ef51-7300-8adb-c02d60c63a45`  
**Tested host in that run:** `codex-testing 0.146.0-alpha.2`

## Document purpose and authority

This document consolidates the postmortem, the follow-up review, and the resulting implementation
plan. The review was decomposed into independent lanes covering:

1. setup and readiness;
2. skill and activation salience;
3. MCP protocol behavior;
4. Codex compaction, resume, and lifecycle recovery;
5. capability and dogfood harness design;
6. deterministic and semantic evidence boundaries;
7. the provider-preset control task; and
8. current Codex documentation, open-source implementation, and MCP specifications.

This is a planning and review artifact. It does not change public behavior, flip an open question,
establish a supported Codex profile, or authorize a new release surface. Any implementation must
follow the repository authority order:

1. `docs/adr/`;
2. `specs/INTERFACES.md`;
3. the owning file specification in `specs/`, as indexed by `specs/FILE_MANIFEST.md`.

Plugin packaging, hook behavior, MCP protocol changes, evidence-schema changes, and release support
are design-gated under `AGENTS.md` and `CONTRIBUTING.md`. Before implementation, search for duplicate
issues and pull requests, open the required issue or issues, and wait for maintainer acknowledgement
where the repository contract requires it.

## Executive decision

The central postmortem conclusion is correct:

> Registration is not activation.

The failed run proved that Codex could register and initialize the Yoetz MCP server and list its six
tools. It did not prove that Codex would select the Yoetz skill, call a Yoetz tool, create a live
task, survive compaction with its task identity, check a completion claim, or produce a receipt.

The recommended supported Codex integration is a versioned Codex plugin that bundles:

- a triggerable Yoetz skill;
- the Yoetz MCP server configuration;
- trusted lifecycle hooks;
- private, structural lifecycle correlation state; and
- exact-host capability evidence.

MCP-only operation remains a useful zero-integration baseline, but it is inherently best-effort.
MCP tools are model-controlled. `InitializeResult.instructions` is guidance that a client may add to
model context, not a protocol-level command that forces invocation. Resources and prompts can improve
discovery or manual invocation, but cannot require the host's main agent to adopt the workflow.

The release claim must therefore distinguish all of these states:

1. MCP registration;
2. MCP initialization and tool discovery;
3. skill installation and discovery;
4. hook installation and trust;
5. automatic activation capability;
6. live task participation;
7. compaction/resume recovery; and
8. receipt-backed completion.

## What the failed run established

| Layer | Observed result | Directly established | Not established |
| --- | --- | --- | --- |
| Codex discovery | Passed narrowly | The intended executable was found. | That prerelease identity was preserved or every later invocation used the same installation. |
| MCP registration | Passed | A `yoetz` stdio MCP entry was configured and enabled. | That the model would call it. |
| MCP initialization | Passed narrowly | The server initialized and six tools were listed without an observed startup error. | That the service, ledger, checker, or receipt path worked. |
| Skill installation | Failed/unsupported | No supported Yoetz skill was installed for the tested host. | Whether a tested skill would have activated. |
| Agent activation | Failed | The complete run contained zero Yoetz tool calls. | Any claim about downstream service correctness. |
| User disclosure | Failed | The user saw neither a successful activation notice nor a truthful inactive/unavailable limitation. | — |
| Compaction recovery | Failed for Yoetz | Codex compacted and resumed without a Yoetz `status` call. | Whether a previously activated task would have recovered under a tested hook. |
| Durable Yoetz state | Not exercised | No current-run task, session, event, check, finding, or receipt existed. | Ledger/checker correctness. |
| Generated provider configuration | Partial | Presets, CLI selection, persistence, and documentation were added. | Production provider dispatch or working inference. |
| Generated provider runtime | Failed | Production composition still supplied no external provider factories and lacked required adapters. | External endpoint behavior, because no live request occurred. |
| Yoetz product effect | None observed | Yoetz produced no record and constrained no claim. | Whether an activated, honestly populated workflow would have helped. |

The service, ledger, deterministic checker, and receipt subsystem must remain classified as **not
exercised**, not failed. No Yoetz call reached them.

## Root-cause model

The failure was a chain, not a single defect:

```text
MCP configuration existed
        |
        v
Codex initialized server and listed six tools
        |
        v
Highest-salience instructions did not contain the full intake trigger
        |
        +--> full trigger remained in an unread resource
        |
        +--> start description explained semantics, not when to call it
        |
        +--> supported skill was absent
        |
        +--> no tested host-native activation hook existed
        v
No start call
        |
        v
No task/session/writer identity
        |
        +--> no publications
        +--> no compaction re-grounding
        +--> no check
        +--> no receipt
        v
Final completion claim was unconstrained by Yoetz
```

Five failures should remain analytically separate:

1. **Activation salience:** the model-visible baseline did not state the decisive intake action early
   enough.
2. **Installation completeness:** setup registered MCP without installing or proving the skill and
   lifecycle integration.
3. **Host lifecycle integration:** no mechanism correlated Codex sessions with Yoetz identities or
   re-grounded after resume/compaction.
4. **Capability evidence:** existing tests proved metadata or local application behavior, not real
   Codex agent behavior.
5. **Evidence relevance:** even if activated, current deterministic rules cannot independently know
   that configuration tests do not exercise production transport.

## Current external contracts

### Codex MCP behavior

Current Codex documentation says it reads server instructions and advises making the first 512
characters self-contained. Current open-source Codex passes MCP initialization instructions into
the model-visible MCP namespace/tool metadata. That establishes delivery to a model-visible surface;
it does not guarantee tool execution.

Relevant references:

- [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp)
- [Codex MCP client source](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/rmcp_client.rs)
- [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)

Stable conclusion: MCP instructions can improve salience, but MCP cannot force activation or expose
Codex-specific compaction and resume events to the server.

### Codex skills

Codex uses skill descriptions for implicit matching and loads the full `SKILL.md` progressively.
The initial catalog is context-budgeted, so trigger language must be concise and front-loaded.

The Yoetz skill's full description already names useful materiality triggers, but its
`metadata.short-description` is only “Local work ledger and deterministic checker.” In current Codex
implementations the short description may be the catalog text shown to the model, hiding the actual
activation cues.

Relevant references:

- [Codex skills documentation](https://learn.chatgpt.com/docs/build-skills)
- [Skills and MCP together](https://learn.chatgpt.com/docs/customization/overview#skills--mcp-together)
- `skills/codex/yoetz/SKILL.md`
- `src/yoetz/resources/skills/codex/yoetz/manifest.json`
- `src/yoetz/adapters/integrations/codex_skill.py`

Implicit matching is a behavioral capability to test, not a guarantee that every material prompt
will invoke the skill.

### Codex hooks

Current Codex exposes command hooks including:

- `SessionStart` with `startup`, `resume`, `clear`, and `compact` sources;
- `UserPromptSubmit`;
- `PreCompact`;
- `PostCompact`;
- `PreToolUse`;
- `PostToolUse`; and
- `Stop`.

`SessionStart` and `UserPromptSubmit` can add developer context. MCP calls appear in tool-use hooks.
Plain stdout from compaction hooks is not the correct context reinjection mechanism; the
`SessionStart(source=compact)` event is the documented seam for adding context after compaction.

Non-managed hooks require explicit trust and may be disabled or administratively excluded. Setup
must report that state honestly.

Relevant references:

- [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks)
- [Codex plugin documentation](https://learn.chatgpt.com/docs/build-plugins)

### Codex app-server

Codex app-server is the preferred integration-test driver because it exposes structured operations
and events for:

- skill listing;
- hook listing and trust state;
- MCP server status;
- MCP tool calls and resource reads;
- thread start, resume, and fork;
- forced compaction;
- instruction sources;
- structured hook and MCP events; and
- persisted thread items.

The app-server surface evolves. Tests must pin the exact Codex artifact and use or generate the
schema belonging to that artifact. Direct app-server MCP tool calls prove conduit behavior, not
spontaneous model activation.

Reference:

- [Codex app-server source documentation](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)

## Target product architecture

### Distribution unit

The supported Codex integration should be one versioned plugin containing:

```text
yoetz-codex-plugin/
├── .codex-plugin/
│   └── plugin.json
├── skills/
│   └── yoetz/
│       ├── SKILL.md
│       ├── agents/
│       │   └── openai.yaml
│       └── references/
├── .mcp.json
└── hooks/
    ├── hooks.json
    └── commands/
```

The exact checked-in layout must be confirmed against current Codex plugin documentation and then
owned in the Yoetz specification tree. The diagram is a proposal, not a frozen path contract.

The plugin should provide:

1. the material-task skill;
2. the six-tool Yoetz MCP launcher;
3. intake salience through `UserPromptSubmit`;
4. successful-start correlation through `PostToolUse`;
5. compaction/resume recovery through `SessionStart`; and
6. private plugin state containing only structural identifiers.

### Readiness model

Setup and status must stop collapsing several states into “configured” or “ready.” The proposed
readiness projection is:

| Dimension | Example values | Evidence source |
| --- | --- | --- |
| Host identity | exact version and artifact digest | Executable probe plus digest |
| Plugin | absent, installed-disabled, installed-enabled | Codex plugin inventory |
| Skill | absent, discovered-disabled, discovered-enabled | `skills/list` or exact equivalent |
| MCP | absent, configured, initialized, degraded | Codex MCP status plus tool count |
| Hooks | absent, untrusted, trusted, disabled, failing | Hook inventory and capability probe |
| Service | lazy, reachable, locked, degraded, unavailable | Bounded Yoetz status/start result |
| Task | inactive, active, mapping-stale, mapping-unavailable | Plugin mapping plus Yoetz status |
| Completion | no-check, findings-open, recheck-required, receipt-current | Yoetz status/receipt |

Setup language must remain structural. For example:

- “MCP registration verified; automatic activation not tested.”
- “Plugin installed; hooks are awaiting Codex trust.”
- “Skill discovered; no supported capability result exists for this exact Codex artifact.”
- “Yoetz task active and re-grounded at frontier …”.

It must not say “Yoetz is set up” when only the MCP entry exists.

### Exact host identity

`src/yoetz/adapters/integrations/codex_discovery.py` currently matches only `X.Y.Z`. It therefore
normalizes `0.146.0-alpha.2` to `0.146.0`. This can associate evidence with a host identity that was
never tested.

The future identity must include at least:

- complete reported semantic version, including prerelease/build suffix;
- executable path as observed at setup time;
- executable or package digest;
- platform and architecture;
- relevant stable/experimental Codex feature inventory;
- plugin, skill, hook, and configuration digests; and
- capability-fixture/harness version.

Version ranges must not imply compatibility across prerelease and stable builds without evidence.

`selected_capability_root_count` is not a general skill-selection metric. Current Codex uses selected
capability roots for client-selected plugin or standalone-skill roots. A zero count is useful
forensic telemetry for one host path, but should not become a public readiness or activation rule.

## Work package A: immediate activation floor

### Objective

Improve the best-effort MCP and skill path before adding lifecycle machinery.

### Proposed changes

1. Put the complete material-task decision at the beginning of
   `guidance/agent-instructions.md` and its installed resource mirror.
2. Ensure the decisive text fits comfortably inside the first 512 characters.
3. Begin the `start` tool description with the same intake condition.
4. Preserve the trivial-task exclusion.
5. Front-load the skill description with the same triggers.
6. Replace or remove the weak `metadata.short-description` after exact-host testing.
7. Keep completion and compaction cues for `check`, `receipt`, and `status`.

Candidate intent, subject to wording review:

> Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work. Call
> `start` before substantive work. Skip Yoetz for trivial questions or edits where the ceremony
> exceeds the integrity benefit. Never claim Yoetz is active until `start` succeeds.

### Required specifications

Likely owners include:

- `specs/src/yoetz/resources/guidance/agent-instructions.md`;
- `specs/src/yoetz/mcp/descriptors.md`;
- `specs/skills/codex/yoetz/SKILL.md`;
- installed-resource mirror specifications; and
- affected conformance/capability test specifications.

### Acceptance evidence

- Static assertion that the intake cue is within the first 512 UTF-8 bytes.
- Descriptor digest regeneration through the owning mechanism.
- Skill catalog evidence showing material triggers survive actual Codex rendering.
- Material-prompt behavior test with no Yoetz vocabulary.
- Trivial-prompt negative control.

Static text presence proves delivery only; it does not prove activation.

## Work package B: Codex plugin and lifecycle hooks

### Objective

Make activation salience and post-activation continuity a tested Codex integration rather than a
hope attached to MCP discovery.

### Proposed hook flow

```text
UserPromptSubmit
    |
    +--> inject concise materiality/activation rule

PostToolUse(successful Yoetz start)
    |
    +--> validate allowlisted structural result
    +--> store Codex session -> Yoetz task/session/writer mapping

SessionStart(source=resume|compact)
    |
    +--> load mapping
    +--> call Yoetz status read-only
    +--> inject bounded active/inactive/unavailable developer context
```

### Correlation record

The private plugin record should contain only an allowlisted structural shape similar to:

```json
{
  "codex_session_id": "opaque host identifier",
  "yoetz_task_id": "opaque Yoetz identifier",
  "yoetz_session_id": "opaque Yoetz identifier",
  "yoetz_writer_id": "opaque Yoetz identifier",
  "last_frontier": "opaque frontier identifier or null",
  "mapping_version": 1
}
```

It must not store:

- prompts or responses;
- transcript contents or transcript paths;
- tool payload prose beyond the allowlisted structural result;
- cwd or repository paths;
- task titles;
- source excerpts;
- credentials, environment values, or secrets.

### Identity rules

Keep these identities separate:

- Codex thread ID;
- Codex session-tree ID;
- Codex hook session ID;
- Codex subagent/agent ID;
- Yoetz task ID;
- Yoetz session ID; and
- Yoetz writer ID.

Capture Yoetz identities only from a successful `start` result. Do not reconstruct them from cwd,
branch name, transcript, title, rollout filename, or similarity.

Fork behavior requires an explicit product decision:

1. inherit the existing Yoetz task with a new writer; or
2. create a linked descendant task.

Never silently treat a fork or subagent as the parent's writer. Hook correlation remains structural
transport evidence; it does not upgrade self-asserted authorship into observed authorship.

### Resume and compaction behavior

On `SessionStart(source=resume|compact)`:

1. If no mapping exists, inject a bounded inactive message and do not invent or attach a task.
2. If a mapping exists and `status` succeeds, inject the active task/frontier and the required next
   action.
3. If a mapping exists but Yoetz is unavailable, inject a truthful unavailable message and state
   that no live receipt can be promised.
4. Coalesce duplicate events and single-flight concurrent status reads.
5. Never create a ledger event merely for reading status.

`PreCompact` and `PostCompact` can record diagnostic ordering and capability evidence. They should
not be the primary context-injection mechanism.

### Optional Stop policy

A `Stop` hook could inspect an already-active Yoetz task and continue the turn once when:

- no current check exists;
- findings remain undispositioned;
- material changes made the check stale; or
- no current receipt exists.

This is not part of the minimum activation fix. It changes the product from a trigger-only,
best-effort helper toward completion interception. If adopted, it needs:

- a separate ADR or explicit authority decision;
- `stop_hook_active` or equivalent loop prevention;
- one-continuation bounds;
- safe behavior when Yoetz is inactive or unavailable;
- no blocking behavior merely because optional Yoetz is absent; and
- precise distinction between an agent ending a turn and claiming material completion.

### Hook trust and failure behavior

For an optional integration, all hook failures must fail truthfully and narrowly:

- absent mapping: inactive;
- service unreachable: unavailable;
- vault locked: locked/degraded boundary preserved;
- hook untrusted or disabled: lifecycle integration inactive;
- hook timeout, crash, or invalid JSON: visible degraded state, no false success;
- failed `start`: no mapping created;
- failed compaction: no claim that re-grounding completed.

No hook may auto-unlock a vault, transmit a secret, parse a transcript for task identity, or block
ordinary work unless an explicit host/user policy requires Yoetz.

## Work package C: setup and readiness repair

### Objective

Make the setup result accurately describe every integration layer.

### Proposed setup stages

1. Discover the exact Codex executable and preserve its complete identity.
2. Inspect existing MCP, plugin, skill, and hook state without mutation.
3. Preview the exact plugin/configuration changes.
4. Obtain any required local consent or Codex trust.
5. Install or enable the plugin without overwriting modified user-owned files.
6. Re-read Codex state through public observable surfaces.
7. Run deterministic conduit preflight.
8. Report automatic activation support only when exact-host capability evidence exists.
9. Keep service state and live task state separate from installation state.

### Compatibility state

Recommended structural states:

- `unregistered`;
- `mcp_registered_unprofiled`;
- `plugin_installed_untrusted`;
- `plugin_installed_untested`;
- `capability_tested_supported`;
- `capability_tested_failed`;
- `artifact_drifted`;
- `degraded`; and
- `unsupported`.

Names must be reconciled with existing shared interfaces before adoption.

### Safety rules

- Never overwrite a modified plugin, skill, or hook file silently.
- Never infer trust from file presence.
- Never infer automatic activation from MCP tool listing.
- Never normalize away a prerelease version.
- Never publish a supported profile without retained exact-host evidence.
- Never mutate user repositories merely to inject `AGENTS.md` guidance.
- Offer repository-specific `AGENTS.md` policy only as an explicit opt-in for teams that want it.

## Work package D: MCP conformance corrections

These findings are independent of the Codex activation failure and should be tracked separately.

### D1. Correct `receipt` annotations

`src/yoetz/mcp/descriptors.py` currently marks `receipt` as read-only. The application stages an
object and appends a `receipt_recorded` event. The annotation is therefore inconsistent with actual
behavior and may affect trusted-host approval decisions.

Proposed annotation:

| Annotation | Value |
| --- | --- |
| `readOnlyHint` | `false` |
| `destructiveHint` | `false` |
| `idempotentHint` | `true` |

Also:

- change the title/description from “Read…” to “Create/record and return…”;
- replace any derived `idempotent = not read_only` rule with an explicit per-tool table;
- reconcile `specs/INTERFACES.md` and the owning descriptor spec;
- regenerate descriptor digests through the owning process; and
- behavior-test that the first request appends one event and an idempotent retry appends no second
  event.

`status` is logically read-only with respect to the ledger, but may lazily start a process or create
runtime files. The trusted-host contract must explicitly decide how such operational side effects
map to annotations.

### D2. Correct protocol-version negotiation

`src/yoetz/mcp/server.py` currently pre-rejects unknown protocol versions with `-32602`. MCP requires
the server to answer with a supported version; the client then disconnects if it cannot support the
selected version. The pinned Python SDK already implements fallback behavior, so Yoetz's pre-screen
overrides the SDK's conformant negotiation.

Proposed correction:

- remove the custom unsupported-version rejection;
- echo mutually supported versions;
- return the latest supported version for an unknown version; and
- continue rejecting structurally malformed initialize requests through normal protocol errors.

### D3. Correct unknown-tool handling

An unregistered tool name should produce a sanitized JSON-RPC/protocol error. Input and business
validation failures for registered tools belong in tool execution results.

The pinned SDK may log the caller-controlled unknown name to stderr before Yoetz's handler runs.
Treat the correction as compatibility-sensitive and add a malicious-name stderr test. No
caller-controlled tool name should break log-line structure or escape the sanitized boundary.

### D4. Improve resource metadata without overclaiming

Consider resource annotations and descriptions that identify:

- assistant audience;
- high priority for workflow/agent instructions; and
- when the resource should be read.

This can improve discovery in cooperative clients. It cannot guarantee retrieval or activation.

An optional user-invoked MCP prompt could improve manual cross-host discovery. Sampling,
elicitation, experimental tasks, and list-change notifications should not be added merely to
manufacture activation; none forces the host's main agent to use Yoetz.

## Work package E: capability and dogfood harness

### Objective

Replace protocol-only or placeholder evidence with separate protocol, conduit, and real-agent
behavior gates.

### Current gaps

The present capability story is insufficient because:

- the capability workflow installs and runs no Codex artifact;
- the release capability policy has no required cases;
- authorized live tests are placeholders that deliberately fail;
- the “six tools” local case bypasses Codex/MCP and directly calls application behavior;
- protocol initialization and inventory are described too broadly as workflow capability; and
- no retained test proves spontaneous activation, compaction recovery, or receipt-bounded wording.

### Gate 1: MCP protocol conformance

Run the installed `yoetz mcp serve` through an official MCP client session or Inspector.

Required coverage:

- initialize negotiation for all supported versions;
- unknown-version fallback;
- exact capability declaration;
- tool listing and schemas;
- resource listing and reading for every bundled resource;
- real calls for all six tools;
- output-schema validation;
- malformed JSON-RPC and framing;
- unknown tool and malicious name;
- cancellation and EOF;
- stdout purity;
- bounded stderr behavior;
- timeout and idempotent retry; and
- pending-response flush during shutdown.

This gate says nothing about model activation.

### Gate 2: deterministic Codex conduit

Run an exact Codex binary through app-server with:

- isolated Codex home;
- isolated temporary repository;
- exact plugin/skill/hook/config fixtures;
- scripted Responses/model stub;
- deterministic service state;
- forced compaction and resume;
- fork and subagent cases; and
- structured event capture.

Required observations:

- skill appears in the actual skill inventory;
- hook inventory and trust state are observable;
- MCP server initializes with six tools;
- all six calls traverse Codex and real MCP transport;
- successful `start` creates exactly one correlation record;
- failed `start` creates none;
- resume and compaction call `status` before continued material work;
- missing mapping does not invent a task;
- unavailable service creates bounded degraded context;
- duplicate lifecycle events coalesce; and
- fork/subagent writers remain distinct.

This gate proves integration plumbing, not spontaneous model choice.

### Gate 3: repeated real-model behavior

Use the same material control prompt without Yoetz-specific words across a controlled matrix:

| Cell | Integration surface |
| --- | --- |
| A | MCP only |
| B | MCP plus first-512-character cue |
| C | MCP plus installed skill |
| D | Plugin with skill, MCP, and trusted hooks |
| E | Plugin with crowded skill/tool catalog |
| F | Plugin with hooks untrusted/disabled |

For each exact host/model/reasoning/configuration cell:

- run a fixed number of attempts, for example five;
- retain every attempt, including failures;
- do not retry-to-pass;
- use the same baseline and prompt;
- bind evidence to the exact artifact and fixture digests; and
- record structured lifecycle/tool events rather than relying only on final prose.

Positive material-task assertion:

```text
one successful start
    -> bounded plan/outcome/obligation publication
    -> material work publication
    -> current evidence publication
    -> check
    -> finding disposition where applicable
    -> recheck after material change
    -> receipt
    -> final wording no stronger than receipt
```

Negative and degraded assertions:

- trivial question creates no Yoetz task;
- missing runtime continues ordinary work with truthful disclosure;
- untrusted hooks are reported as inactive;
- forced compaction/resume triggers `status` before further material work;
- a deliberate missing-runtime-path fixture leaves an open obligation or finding;
- superficial configuration evidence does not permit a “working endpoint” claim; and
- optional Yoetz unavailability never fabricates a receipt.

### Flake controls

- No arbitrary sleeps as correctness assertions.
- Use structured event completion and bounded timeouts.
- Assert partial order rather than incidental total ordering.
- Isolate homes, repositories, ports, task state, and plugin state per attempt.
- Pin binary, model, reasoning level, system configuration, tool catalog, skill catalog, and hooks.
- Retain prompts, public outputs, event metadata, and structural evidence according to the project's
  privacy rules; never copy secrets or broad local transcripts into repository fixtures.
- Mark experimental Codex/app-server fields as such and avoid stable public claims based solely on
  them.

## Work package F: obligation and evidence relevance

### Control-task reconstruction

The provider-preset task required more than configuration metadata. A correct Yoetz plan should
have separated at least these obligations:

| Obligation | Required outcome | State in preserved implementation |
| --- | --- | --- |
| Maintainer acknowledgement | Design-gated provider/privacy work acknowledged before coding | Open/missed |
| Configuration and CLI | Presets selectable, persisted, and reread correctly | Substantially exercised |
| Production composition | Factories registered in the actual ready service graph | Open |
| Transport dispatch | Request reaches the correct API style with credential handling | Open |
| Response normalization | Provider response becomes Yoetz's expected result shape | Open |
| Capability/privacy | Endpoint/data-use/approval behavior has admissible evidence | Open |
| Documentation and aliases | Names and limitations agree with behavior | Partial/open |
| Live smoke or limitation | External request succeeds, or unavailability is explicit | Open |

The issue itself changed provider/privacy/egress behavior. Repository process required maintainer
acknowledgement before coding. The issue was opened and coding began minutes later without recorded
acknowledgement. That should have been the first blocking obligation.

The preserved implementation establishes configuration metadata and CLI/TOML behavior. It does not
establish production routability because the service composition supplies an empty external factory
map and the required transport adapters are absent. The most precise conclusion is:

> Production routability is absent; external endpoint behavior was not exercised.

The empty factory map existed in the baseline. The patch failed to close a prerequisite; it did not
introduce the entire pre-existing runtime gap.

### What current deterministic checks can catch

If participants publish the record honestly, current deterministic rules can detect or preserve:

- open obligations;
- unattempted requested items;
- omitted failed evidence;
- stale evidence after material change;
- unresolved findings;
- missing recheck; and
- completion wording that conflicts with explicitly published limitations.

They cannot independently infer that:

- a TOML round-trip does not exercise production dispatch;
- a monkeypatched setup test does not exercise the real factory;
- a test builder differs from production composition;
- a filename containing “integration” provides integration coverage; or
- evidence prose is semantically relevant to a claim.

Those are semantic relevance judgments unless the evidence producer and obligation schema carry
explicit, reviewed structure.

### Immediate improvement without protocol changes

Use current plan/obligation fields more rigorously:

1. Decompose each acceptance criterion into an explicit obligation.
2. State the expected evidence and unavailable boundary for each obligation.
3. Keep production composition, transport, capability, and live smoke separate from configuration.
4. Require scope revision rather than silently dropping an unavailable obligation.
5. Publish the final claim only after every material obligation is resolved, waived through the
   reviewed authority path, or stated as an unresolved limitation.

### Proposed verification classes

A later, versioned schema could add orthogonal exact-match classes such as:

- `unit_config`;
- `integration_transport`;
- `production_composition`;
- `capability`;
- `live_smoke`; and
- `source_review`.

The final names require interface review. The essential rules are:

- an obligation declares the exact classes it requires;
- evidence declares only classes produced by a reviewed producer/profile;
- a result derives class satisfaction only from linked admissible evidence;
- classes are orthogonal, not a strength ladder;
- broader classes are never inferred from filenames, commands, or prose;
- config evidence cannot satisfy transport or live-smoke requirements; and
- schema changes are versioned rather than hand-editing frozen/generated artifacts.

### Bounded evidence producers

After the schema exists, add reviewed producers for:

- command/test execution with exact argv, exit status, output digest, and state binding;
- production-composition integration tests;
- capability harness results;
- live smoke results with explicit privacy authority;
- bounded commit-bound source excerpts; and
- exact-host/plugin/hook identity.

Producer invariants:

- nonzero exit is failure, never positive evidence;
- later material edits make stale evidence visibly stale;
- secrets and unrelated repository content never enter evidence;
- captured excerpts are minimal and commit/state bound; and
- a producer never claims coverage beyond its reviewed capability profile.

### Semantic relevance review

The repository already defines a privacy-fenced optional semantic-review architecture, while current
production composition uses a not-configured semantic path. If product evidence shows value, a later
work package may productionize that existing architecture.

The semantic reviewer must remain:

- optional and provenance-labeled;
- bounded to an explicit review packet;
- unable to fetch arbitrary workspace content;
- unable to mutate deterministic truth;
- unable to upgrade evidence coverage;
- unable to invent source references;
- subject to privacy/egress policy; and
- advisory through the existing finding/respond/recheck workflow.

If unavailable, the deterministic result remains intact and the receipt states that semantic
relevance review did not run.

### Honest receipt wording

For a record containing only configuration evidence, preferred wording is:

> Configuration behavior was exercised by unit/config evidence. Production transport and live
> provider behavior were not exercised. Yoetz found no unresolved deterministic issue in the
> published record; semantic relevance review was not run.

Yoetz must never convert “no deterministic issue in the cooperatively published record” into
“Yoetz verified the implementation.”

## Work package G: documentation and claims

Update public documentation only alongside approved behavior and evidence.

Required documentation outcomes:

1. Setup/runbook language distinguishes registration, discovery, activation, live task state, and
   receipt state.
2. Codex integration documentation describes plugin/skill/hook trust and degraded modes.
3. MCP documentation states that instructions/resources are best-effort activation signals.
4. Capability documentation separates protocol conformance, conduit testing, and real-agent
   behavior.
5. Public claims identify the exact supported Codex artifact/profile.
6. Receipt documentation explains verification classes and semantic-review absence if those
   features are approved.
7. The postmortem remains historical evidence and is amended only to correct factual uncertainty,
   not rewritten to imply that future fixes already existed.

Postmortem wording that can be tightened based on current source review:

- Current Codex does consume MCP initialization instructions into model-visible MCP namespace/tool
  metadata.
- Consumption did not cause invocation in the failed run.
- `selected_capability_root_count=0` means no externally selected capability root in that observed
  path, not that ordinary skill selection definitely failed.
- Current documented hook and plugin surfaces provide stronger remediation options than were known
  at the start of the postmortem.

## Proposed implementation sequence

The work should be split into separately reviewable, authority-aligned changes.

### Issue 1: MCP conformance corrections

Scope:

- `receipt` annotations and wording;
- protocol-version negotiation;
- unknown-tool error and stderr sanitation; and
- optional resource metadata.

Gate: protocol behavior is design-gated. Wait for maintainer acknowledgement.

### Issue 2: activation floor and exact host identity

Scope:

- first-512-character intake cue;
- `start` invocation description;
- skill short-description/catalog behavior;
- prerelease-preserving Codex version parsing;
- setup readiness projection; and
- static/conduit tests that do not yet claim automatic support.

Gate: any support/profile or packaging claim remains disabled until capability evidence exists.

### Issue 3: Codex plugin and lifecycle integration

Scope:

- plugin layout and installer;
- hook trust/state inspection;
- structural mapping store;
- `PostToolUse` start correlation;
- `SessionStart(resume|compact)` re-grounding;
- explicit fork/subagent writer rules; and
- safe degraded behavior.

Gate: new packaging surface and lifecycle behavior require design acknowledgement and owning specs.

### Issue 4: Codex capability harness

Scope:

- exact artifact acquisition/identity;
- MCP Inspector/SDK conformance;
- app-server deterministic conduit harness;
- repeated real-model behavioral matrix;
- retained evidence artifacts; and
- release capability policy with non-empty required cases.

Gate: no supported Codex profile until all mandatory cells pass under the reviewed retry/flake
policy.

### Issue 5: evidence classes and producers

Scope:

- versioned obligation/evidence class contract;
- schemas and migrations if required;
- deterministic policy updates;
- bounded evidence producers;
- control/adversarial fixtures; and
- receipt wording.

Gate: shared interface/schema changes require authority updates before code.

### Issue 6: optional semantic relevance production path

Scope only if justified by product evidence:

- production composition of the existing privacy-fenced semantic architecture;
- provider/capability requirements;
- ADV-004-style relevance fixtures;
- failure/unavailability semantics; and
- public claims.

Gate: privacy/egress behavior is design-gated and must not be bundled casually into activation work.

## Detailed dependency graph

```text
MCP conformance corrections ------------------------------+
                                                          |
activation cue + exact host identity ---------------------+----> deterministic conduit
                                                          |             |
plugin authority + packaging spec ------------------------+             |
                                                                        v
                                                            lifecycle hook integration
                                                                        |
                                                                        v
                                                            real-model qualification
                                                                        |
                                                                        v
                                                            supported Codex profile

obligation decomposition ---------------------------------+
                                                          |
verification-class authority/schema ----------------------+----> bounded producers
                                                                        |
                                                                        v
                                                            deterministic policy/receipt
                                                                        |
                                                                        v
                                                          optional semantic relevance review
```

The activation/plugin work and evidence-class work may proceed independently after their respective
design gates. Neither should block the narrow MCP conformance corrections.

## Full verification matrix

| Area | Case | Required result |
| --- | --- | --- |
| Version identity | `0.146.0-alpha.2` | Full prerelease retained; evidence does not bind to `0.146.0`. |
| Artifact drift | Same path, different digest | Prior capability result becomes inapplicable. |
| MCP initialize | Supported version | Same mutually supported version returned. |
| MCP initialize | Unknown version | Server returns its supported version; client decides whether to disconnect. |
| MCP initialize | Malformed request | Sanitized protocol error. |
| Tool catalog | Six tools | Exact names, schemas, descriptions, and explicit annotations. |
| Unknown tool | Benign and malicious name | Sanitized protocol error; no stderr/log injection. |
| Receipt | First call | One object/event produced; annotation is not read-only. |
| Receipt | Idempotent retry | Same result identity; no duplicate event. |
| Status | Active session | Read result without ledger mutation. |
| Resources | All four resources | List/read succeeds; content and digests agree with installed set. |
| Activation cue | Tier-zero bytes | Complete material-task rule within first 512 bytes. |
| Skill catalog | Normal and crowded catalog | Trigger remains visible or test fails closed. |
| Material prompt | No Yoetz vocabulary | Exactly one successful `start`. |
| Trivial prompt | Ordinary question/one-line edit | No task created. |
| Start correlation | Successful start | One structural mapping with returned IDs. |
| Start correlation | Failed/timeout start | No invented mapping; retry/unknown outcome handled idempotently. |
| Resume | Existing active mapping | `status` precedes further material work. |
| Resume | No mapping | Inactive context; no invented attach. |
| Resume | Service unavailable | Unavailable context; ordinary work continues where allowed. |
| Auto compaction | Active task | Pre/Post events plus `SessionStart(compact)`; status before continued work. |
| Manual compaction | Active task | Same invariant with manual trigger. |
| Failed compaction | Hook blocks/fails | No false re-grounding success. |
| Duplicate lifecycle | Repeated events | One coalesced status read; no duplicate ledger event. |
| Hook trust | Untrusted/disabled | Setup reports lifecycle inactive; no support claim. |
| Hook failure | Timeout/crash/invalid JSON | Visible degraded state; no fabricated active state. |
| Fork | Inherited-task policy | New distinct writer and explicit relation. |
| Fork | New-task policy | New linked task according to approved contract. |
| Subagent | Delegated work | Distinct writer/agent mapping; no parent impersonation. |
| Missing runtime fixture | Material prerequisite absent | Open obligation/finding; no “implemented and verified” wording. |
| Config-only evidence | Provider preset | Clears config obligation only. |
| Transport evidence | Real production composition | Clears exact integration obligation only. |
| Live smoke absent | External provider | Explicit limitation; “working endpoint” forbidden. |
| Semantic reviewer absent | Any completion | Deterministic result preserved; absence disclosed. |
| Stop hook, if approved | Missing current receipt | At most one continuation; no loop. |
| Optional Yoetz absent | Completion | Never blocks merely due to absence; no receipt claim. |

## Release gates

An exact Codex profile must remain unsupported until all mandatory evidence is retained and bound to
the exact release cell.

Minimum release cell dimensions:

- Codex executable/package identity and digest;
- version including prerelease suffix;
- operating system and architecture;
- model and reasoning mode for behavioral tests;
- plugin digest;
- skill digest and rendered catalog evidence;
- hook configuration/command digests and trust state;
- MCP configuration and descriptor/resource digests;
- Yoetz version and capability harness version;
- fixture repository/state digest; and
- required test-case result set.

Minimum supported-profile exit criteria:

1. Protocol conformance passes.
2. Deterministic conduit passes.
3. Repeated real-model material-task activation meets the reviewed threshold.
4. Trivial-task negative controls pass.
5. Resume and forced compaction re-ground correctly.
6. Untrusted/disabled/missing-service degradation is truthful.
7. The missing-runtime control constrains the final claim.
8. Full workflow reaches a current receipt.
9. The release capability policy names non-empty required cases.
10. All artifacts and results are retained according to the reviewed evidence policy.

No single successful dogfood run is sufficient. Do not discard failed attempts or average across
different host/model/configuration cells.

## Explicit non-goals

This plan does not propose that Yoetz:

- observe all workspace activity;
- enforce arbitrary host behavior through MCP;
- record prompts, transcripts, or hidden reasoning;
- infer authorship from process identity;
- silently edit every user's `AGENTS.md`;
- auto-unlock the vault or accept secrets through hooks/MCP;
- treat MCP resource reads as guaranteed;
- use sampling/elicitation to hijack the host conversation;
- call a deterministic clean result “verification”;
- infer evidence coverage from filenames or prose;
- make semantic review authoritative; or
- advertise experimental Codex implementation details as stable contracts.

## Risks and tradeoffs

### Plugin and hook adoption

Plugins and hooks are stronger than MCP instructions but require installation and trust. Some users
or managed environments will disable them. Yoetz must keep a useful MCP-only baseline and report the
weaker assurance explicitly.

### Model behavior variability

Skill matching and model tool choice remain probabilistic. Behavioral qualification can support an
exact release claim with measured evidence; it cannot convert model choice into a protocol
guarantee.

### Context and catalog competition

Large skill/tool catalogs may truncate descriptions or reduce salience. Qualification must include
a crowded-catalog cell and bind results to the actual rendered inventory.

### Hook loops and blocked completion

Completion hooks can create loops or frustrate users. Keep `Stop` outside the minimum plan until an
explicit product decision and robust loop/degraded tests exist.

### Identity leakage

Lifecycle correlation is useful but can become a covert transcript/workspace index if allowed to
store titles, paths, or prose. Enforce a small structural schema and private plugin storage.

### App-server compatibility

App-server is a strong test/control surface but evolves. Pin the exact schema/artifact and avoid
making cross-version promises from one build.

### Evidence-model complexity

Verification classes improve honesty but can turn into ceremony or a false hierarchy. Keep the set
small, orthogonal, producer-backed, and tied to real acceptance obligations.

## Completion definition for this remediation program

The program is complete only when all of the following are true:

- The MCP conformance defects are corrected and independently tested.
- Setup preserves exact Codex identity and reports every integration layer separately.
- The materiality trigger reaches both tier-zero MCP instructions and the rendered skill catalog.
- A reviewed Codex plugin installs the skill, MCP configuration, and lifecycle hooks.
- Hook trust and degraded states are observable.
- Successful `start` creates an allowlisted structural correlation mapping.
- Resume and compaction re-ground through `status` before further material work.
- Forks and subagents receive explicit, non-impersonating writer behavior.
- Protocol, conduit, and real-agent behavior are separate capability families.
- The release policy contains real mandatory capability cases.
- A repeated exact-host dogfood matrix demonstrates spontaneous activation without prompt hints.
- A deliberate semantic/runtime gap remains visible as an open obligation or finding.
- Receipt and final wording remain bounded by actual evidence coverage.
- Public documentation and claims describe only the capability actually demonstrated.

## Immediate next actions

No implementation should begin from this document alone. The next repository actions are:

1. Search again for duplicate issues/PRs at implementation time.
2. Open the separate design issues described above rather than one unreviewable mega-patch.
3. Obtain maintainer acknowledgement for protocol, packaging/hooks, schema, privacy, and release
   changes as applicable.
4. Reconcile every proposed name and behavior through ADRs, `specs/INTERFACES.md`, owning specs, and
   `specs/OPEN_QUESTIONS.md`.
5. Implement the smallest authority-complete slice first: MCP correctness, activation cue, and exact
   host identity.
6. Build deterministic app-server/conformance infrastructure before claiming automatic support.
7. Add plugin lifecycle behavior only with its full trust, identity, privacy, and failure matrix.
8. Add evidence classes separately, using the provider task as a control fixture.
9. Run repeated real-agent dogfood only after deterministic plumbing passes.
10. Publish a supported Codex profile only when all mandatory evidence is retained and current.

## Source index

### Local evidence and implementation

- `docs/postmortems/2026-07-22-codex-testing-yoetz-activation.md`
- `guidance/agent-instructions.md`
- `guidance/workflow.md`
- `skills/codex/yoetz/SKILL.md`
- `src/yoetz/resources/skills/codex/yoetz/manifest.json`
- `src/yoetz/adapters/integrations/codex_discovery.py`
- `src/yoetz/adapters/integrations/codex_skill.py`
- `src/yoetz/cli/setup.py`
- `src/yoetz/mcp/descriptors.py`
- `src/yoetz/mcp/resources.py`
- `src/yoetz/mcp/server.py`
- `src/yoetz/application/receipt.py`
- `src/yoetz/service/ready_composition.py`
- `src/yoetz/adapters/privacy/gateway.py`
- `src/yoetz/adapters/providers/openai_responses.py`
- `.github/workflows/capability.yml`
- `release/capability-policy.json`
- `tests/capability/test_codex_skill_discovery.py`
- `tests/capability/test_codex_resume_reattach.py`
- `tests/capability/test_codex_six_tools.py`
- `tests/capability/test_mcp_protocol_and_sdk.py`
- `tests/subprocess/test_mcp_initialize_and_tools.py`
- `tests/conformance/surfaces/test_mcp_contract_matrix.py`
- `fixtures/adversarial/ADV-004-irrelevant-evidence.case.json`

### Codex documentation and source

- [MCP integration](https://learn.chatgpt.com/docs/extend/mcp)
- [Hooks](https://learn.chatgpt.com/docs/hooks)
- [Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Customization overview](https://learn.chatgpt.com/docs/customization/overview)
- [Open-source Codex repository](https://github.com/openai/codex)
- [Codex MCP client](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/rmcp_client.rs)
- [Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)

### MCP specification and SDK

- [MCP specification repository](https://github.com/modelcontextprotocol/modelcontextprotocol)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Lifecycle and negotiation](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
- [Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)
- [Schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)

## Final principle

Yoetz's useful product boundary is not “the agent had tools available.” It is:

> The agent entered a live, durable, receipt-bounded workflow; the host could recover that workflow
> across its lifecycle; and every completion claim remained no stronger than the evidence that was
> actually published and checked.

Until that full path is directly observed for an exact Codex release cell, the honest claim is only
that Yoetz is registered and available—not that it is activated, participating, or verifying work.
