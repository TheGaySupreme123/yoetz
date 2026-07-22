# Codex-testing Yoetz activation dogfood postmortem

**Date:** 2026-07-22

**Tested Codex session:** `019f89fc-ef51-7300-8adb-c02d60c63a45`

**Repository baseline:** `fed3169`

**Post-run preserved artifact:** branch `experiment/codex-testing-provider-presets`, commit
`d30f762dde641e03d0acf53513acd51a53554915`

**GitHub intake:** [issue 6](https://github.com/TheGaySupreme123/yoetz/issues/6)

**Overall verdict:** host registration passed; agent activation failed; the Yoetz workflow and
service path were not exercised; the generated implementation did not satisfy “working
endpoints”; improvement attributable to Yoetz was zero.

## Executive summary

Codex-side MCP registration, discovery, and initialization showed no error, but agent activation
failed before the Yoetz workflow began. The most accurate conclusion is:

> Yoetz was registered with Codex, but it was not operationally activated by Codex.

Codex discovered the registered MCP server and exposed all six Yoetz tools. It never called one.
Consequently, the on-demand local service was never exercised for this task, no current task or
ledger was created, no obligations or evidence were published, and no check or receipt constrained
the final completion claim.

The run therefore does **not** show that the lazy service connection, service handshake, ledger,
or deterministic checker failed. None was invoked. It shows that the host-facing activation path
was insufficient for a real material task, and that setup made transport registration too easy to
interpret as operational agent integration.

The control task also exposed why this matters. Codex added plausible provider labels, endpoints,
configuration records, and tests, then claimed “Implemented and verified.” Independent review
found that the production gateway still registers no external provider factories, the only
production HTTP adapter is Responses-only, three new presets require an absent Chat Completions
adapter, and no live provider request was executed. Yoetz neither prevented nor improved that
overclaim because it recorded nothing.

## Verdict matrix

| Layer | Verdict | What the evidence establishes | What it does not establish |
| --- | --- | --- | --- |
| Codex binary discovery | Pass | Setup selected the intended `codex-testing` executable. | That every invocation will use the same home/configuration. |
| MCP registration | Pass | The `yoetz` stdio entry was enabled and later classified `yoetz_owned`. | That Codex will call a Yoetz tool. |
| MCP host discovery | Pass | Codex exposed all six Yoetz tools and reported no Yoetz startup warning. | That MCP initialize guidance affected model behavior. |
| MCP protocol process | Pass, narrow | No initialization, framing, stdout-purity, or schema error was observed. | MCP-to-service connectivity or any workflow operation. |
| Skill installation | Fail/unsupported | No skill was installed; the packaged manifest has no tested Codex profile and installation fails closed. | That a future tested skill would activate reliably. |
| Agent activation | Fail | Zero Yoetz calls occurred in a clearly material 23-minute task. | — |
| User disclosure | Fail | No “using Yoetz” notice and no “Yoetz unavailable/not active” limitation appeared. | — |
| Compaction recovery | Fail for Yoetz | Codex compacted and resumed without `status` or any Yoetz re-grounding. | Whether an already-started Yoetz session would survive compaction. |
| Lazy service launch and handshake | Not exercised | No Yoetz tool was called, so the lazy path was never entered. | Service reliability. |
| Ledger/checker/receipt | Not exercised | No current task, publication, check, finding, response, or receipt exists. | Correctness of these components. |
| Generated provider configuration | Partial | Presets, CLI choices, TOML persistence, and documentation were added. | Runtime provider dispatch or working inference. |
| Generated runtime support | Fail | Production gateway factory registration remains empty; required transport adapters are absent. | — |
| Yoetz improvement to code | None observed | Every correction and validation came from ordinary Codex work or independent review. | Whether a correctly activated and honestly populated Yoetz record could help. |

## Scope and method

The dogfood task asked Codex to add setup choices for Anthropic Claude, Google Gemini, OpenRouter,
and Vercel AI Gateway. Three independent lanes observed the run:

1. a user-facing lane drove the real `codex-testing` terminal;
2. a Yoetz lane inspected MCP registration, Codex logs, the Yoetz catalog, task ledgers, and run
   artifacts;
3. an independent validation lane reviewed the generated diff and ran focused and bounded
   non-live verification.

The provider implementation itself is preserved outside `main` so this document can describe the
Yoetz outcome independently.

The live run itself edited `main`; the experiment branch and commit were created only afterward to
preserve the generated diff while returning this postmortem to `main`. Immediately before launch,
the terminal driver observed 21 modified first-run/vault files. The Codex session then initialized
on a clean `fed3169`, already `main...origin/main`. The tested agent did not create that baseline;
it appeared between preflight and session initialization. This shared-checkout transition is a
reproducibility limitation even though the session metadata gives an exact tested commit.

The conclusions use four evidence classes:

1. **Host transcript:** the complete Codex JSONL rollout, including tool calls, progress messages,
   compaction, final answer, repository baseline, and elapsed time.
2. **Host runtime state:** Codex's MCP registration/log database and isolated home, used to
   distinguish “registered” from “called.”
3. **Yoetz durable state:** the catalog and only existing task ledger, queried read-only after the
   run to determine whether any current-run logical row existed.
4. **Code and verification:** the experiment commit, current architecture/specifications, focused
   tests, static checks, and an independent bounded non-live gate.

The analysis deliberately separates direct observation from inference:

- “zero Yoetz calls” and the database row counts are direct observations;
- “weak activation salience” is a causal inference supported by the absent skill, guidance
  placement, tool-catalog scale, and actual behavior;
- service/checker behavior is classified **not exercised**, not failed;
- provider endpoints are classified as plausible metadata, while working inference is failed due
  to directly observed missing production factories/adapters and absent smoke evidence.

## Timeline

- `13:21:22.804Z`: Codex session initialized at clean `main` baseline `fed3169`, Codex
  `0.146.0-alpha.2`, model `gpt-5.6-luna`.
- `13:21:31.936Z`: first user-facing progress update, 9.132 seconds after session metadata.
- `13:22:28Z`–`13:22:43Z`: four issue searches, four PR searches, and one issue fetch completed.
- `13:22:54.777Z`: Codex requested GitHub issue creation; the approval-delayed tool output arrived
  at `13:24:06.823Z`, the MCP completion event at `13:24:07.758Z`, and the follow-up wait ended at
  `13:24:09.796Z`. Codex reported issue 6 to the user at `13:24:16.018Z`.
- `13:28:50.657Z`: first implementation edit, 7 minutes 27.853 seconds after session metadata.
- `13:35:55.036Z`: automatic mid-turn `ContextLimit` compaction. Immediately beforehand the
  rollout reported `246,251 / 258,400` tokens used: about 95.3 percent used and 4.7 percent
  remaining.
- `13:35:57.794Z`: reasoning resumed 2.763 seconds after compaction; the next user-facing update
  followed at `13:35:58.830Z`, 3.799 seconds after compaction. The post-compaction rollout token
  report was `17,213 / 258,400`.
- `13:44:31.612Z`: Codex returned `Implemented and verified`; `task_complete` followed at
  `13:44:31.638Z`.

The recorded task duration was `1,388,263 ms`, approximately 23 minutes 8 seconds. Session
metadata to completion was 23 minutes 8.834 seconds. The rollout
contains 94 `exec` calls and two waits. Its ten recorded MCP calls were all GitHub intake calls:
four `github.search_issues`, four `github.search_prs`, one `github.fetch_issue`, and one
`github.create_issue`. It contains 26 completed patch applications and four completed web searches.

Across the complete run there were no Yoetz `start`, `publish_work`, `status`, `check`, `respond`,
or `receipt` calls.

That zero is not an interpretation based on the final answer. It is a count over the complete
rollout and is corroborated by the absence of a new Yoetz catalog route, session, operation, or
ledger event.

## What worked

### Codex discovery and MCP registration

The setup layer found the exact `codex-testing` executable, registered `yoetz` as an enabled stdio
MCP server, and later reported the entry as `yoetz_owned`. Codex reported its configured MCP servers
available without a Yoetz startup warning.

This proves the binary-discovery and MCP-configuration layer worked.

### MCP bridge initialization

The six intended tools were present:

- `start`
- `publish_work`
- `check`
- `respond`
- `status`
- `receipt`

There was no observed bridge-startup stdout corruption, initialization exception,
schema-loading failure, or host-side MCP error. The bridge reached the host and its reviewed tool
descriptions were discoverable. This does not establish its lazy connection to the service.

### Safe non-participation

Yoetz did not fabricate activity when activation failed. It produced no phantom session, finding,
or receipt and did not silently attach the new work to an older task. The existing catalog and
ledger retained only pre-run logical state.

This is an important safety success: absence of participation remained absence of evidence rather
than becoming false evidence.

### Automatic Codex compaction recovery

Codex itself resumed reasoning within approximately three seconds and sent its next user update
within approximately four seconds after compaction, while retaining the dirty
worktree. This was good Codex behavior, although Yoetz did not participate in the recovery.

## What failed

### Setup communicated registration more strongly than operational readiness

The first-run wizard registers MCP and handles local service/provider setup, but it does not install
the Yoetz Codex skill. Its completion marker records that the wizard ran; it does not establish that
Codex will activate Yoetz for material work.

The resulting human interpretation was nevertheless that "Yoetz is set up on codex-testing." The
status surface did expose `compatibility=untested` and `service.reachable=false`, but the overall
setup experience did not make the missing activation layer sufficiently prominent.

Relevant implementation: `src/yoetz/cli/setup.py::run_setup_wizard`.

### The Codex skill was absent and not support-installable

No Yoetz skill existed in the isolated Codex home or at `.agents/skills/yoetz` in the project.

This was not merely an accidentally skipped step. The packaged Codex skill manifest currently has
an empty capability-profile set, empty tested/supported Codex-version sets, and no hook profiles.
`CodexSkillIntegration.install_skill` deliberately rejects installation when
`harness_tested_set` is empty with `version_incompatible`.

The material-task activation criteria in the skill therefore could not reach this Codex session by
the supported installation path.

Absence was checked in the isolated Codex skill directory, the repository-local `.agents` skill
directory, and the user's shared agent skill directory. The installed Yoetz release also described
itself honestly as `development_unverified`, with limitations `development_unverified` and
`mcp_capability_unverified`, and with empty Codex capability-profile, MCP-protocol-support, and
service-capability sets. This honest support status is a product safety success; the setup UX did
not surface its operational consequence strongly enough.

Relevant implementation:

- `src/yoetz/resources/skills/codex/yoetz/manifest.json`
- `src/yoetz/resources/skills/codex/yoetz/SKILL.md`
- `src/yoetz/adapters/integrations/codex_skill.py::install_skill`

There is also a host-identity precision problem. The direct binary and session metadata reported
Codex `0.146.0-alpha.2`, while setup status reported `0.146.0` because discovery extracts only the
`X.Y.Z` portion and drops the prerelease suffix. Even after capability evidence exists, support
must be tied to the exact tested identity rather than an overbroad normalized family.

### The zero-integration activation path was insufficient

The MCP bridge is specified as a zero-integration baseline that should remain useful without an
installed skill. In practice, that path depends on:

1. MCP `initialize.instructions`;
2. six tool descriptions;
3. optional MCP guidance resources.

The full "when to activate" rule lives in `guidance/workflow.md`, not in the short tier-zero
`agent-instructions.md` or the `start` tool description. The short instructions explain what Yoetz
is and what to do before claiming done, but they do not directly tell the agent at task intake to
call `start` for the current material task.

The run contained no evidence that Codex consumed the MCP `initialize.instructions` field
specifically, and it fetched no Yoetz workflow resource. However, the same tier-zero instructions
did reach model-visible context: the tool-discovery output prepended the complete
`agent-instructions.md` text to the Yoetz tool metadata. Codex still ignored both the visible
completion cue (“Before you claim done ... call `check`”) and all six tools.

The Yoetz spec explicitly allows for a host that ignores instructions or does not support
resources. The failure is therefore more specific than “guidance was absent.” Tier zero lacks the
explicit intake-time material-task activation rule, while the completion-time cue it does contain
was present but ineffective. The full activation rule remained behind an unfetched resource.

This is the primary failure.

### Tool-catalog scale reduced Yoetz salience

Codex logs reported exactly 667 discoverable MCP tools across five available servers, of which six
were Yoetz tools. A model-authored, filtered `ALL_TOOLS` metadata dump was truncated after an
original 157,818 tokens and 291 lines. It was filtered for GitHub/repository-related tools, so it
must not be treated as the complete definitions of all 667 tools. Codex also used deferred MCP
discovery; 667 discoverable tools does not mean 667 complete definitions entered every request.

Tool-catalog scale is a contributing cause rather than the root cause: an installed skill or a
tested host-native activation mechanism could still have selected Yoetz. The catalog size and large
metadata operation plausibly reduced salience and consumed context, but the evidence does not prove
they caused compaction or non-activation.

Host-selection logs add a stronger signal. They repeatedly recorded
`selected_capability_root_count=0`. The skills renderer saw 114 skills and included all 114, but
truncated 113 descriptions to 193 characters under a 4,000-character budget. At compaction, 110
descriptions were truncated to 142 characters under a 5,440-character budget. This does not prove
that truncation caused the failure, but it does prove that no capability root selected Yoetz and
that the activation surface competed under tight description budgets.

### No Yoetz lifecycle began

Because no Yoetz tool was called:

- the MCP bridge never attempted its lazy service connection for this task;
- no task was created or resumed;
- no plan, requested outcome, obligation, action, result, claim, or evidence was published;
- no valid check or receipt for the current task could be produced.

The post-run service state was unreachable, but that is not evidence of a service-start failure.
The bridge intentionally starts without a service connection and only invokes the on-demand
launcher when a tool is called. Service behavior is therefore classified as **not exercised**.

### Durable state corroborated zero participation

The post-run catalog contained exactly one route and one completed start operation, both from an
earlier pre-run probe:

- installation `ins_67d23692-883c-4d8d-97b6-ed276b5f30a3`, owner generation 23;
- task `tsk_bf3294b7-cbc8-435d-8cfa-a2d919d715a3`;
- session `ses_280aaa86-b011-4134-8fef-eae4ceac7298`;
- request `req_12345678-1234-4234-8234-123456789abc`;
- route created at `13:00:36.354Z` and last updated at `13:00:36.387Z`, more than 20 minutes
  before the tested session;
- start state `complete`, phase `terminal`.

That ledger contained exactly one `session_opened` event, accepted at `13:00:36.380Z`, and zero
plans, obligations, actions, results, evidence, claims, checks, findings, and responses. No second
task directory or current-run logical row existed.

The catalog file's filesystem modification time later advanced to `16:28:40` local time, but all
logical rows remained pre-run and no task artifact changed. The safest interpretation is SQLite
checkpoint/access activity, not Yoetz participation. Logical state, not file mtime, controls this
conclusion.

Post-run setup status reported `service.reachable=false`; the runtime directory was empty; and
`service-generation.json` remained generation 23 with a pre-run modification time. Together with
zero tool dispatch, these facts support **not exercised**, not “service attempted and failed.”

### Compaction re-grounding did not occur

The cooperative workflow requires `status` after resume, compaction, handoff, or uncertainty. The
status descriptor also carries a weaker re-grounding cue.

After compaction, Codex resumed ordinary work without a Yoetz call. By that point there was no
Yoetz session to attach to, no installed skill carrying the explicit compaction procedure, and no
tested host-native hook. This failure was downstream of the initial activation failure rather than
an independent ledger failure.

### User-facing availability disclosure failed

A successfully integrated run should briefly announce that Yoetz is being used and should not
imply success before `start` returns. If unavailable, it should disclose that no live Yoetz record
or receipt will exist.

This run provided neither message. Codex did not fabricate Yoetz state, but it also did not tell the
user that the intended integrity layer had not activated.

### The user-facing dogfood path was noisy and approval-heavy

Several frictions were independent of Yoetz activation but materially affected the trial:

- The first launch failed before session creation because `~/.codex-testing/state_5.sqlite` could
  not be written: `attempt to write a readonly database`. The terminal had to relaunch elevated.
  Because the failure preceded rollout creation, its exact timestamp is unavailable in the JSONL.
- `TERM=dumb` produced a `Continue anyway? [y/N]` prompt and extremely noisy ANSI/full-screen
  refresh output, often exceeding 100 KB per terminal poll. The JSONL, not the TUI, became the
  practical audit source.
- GitHub issue creation required a user approval; the driver allowed it for the session.
- The first pytest attempt failed before pytest because uv could not initialize its cache under
  `/Users/shayb/.cache/uv` and could not open `sdists-v9/.git` due to `Operation not permitted`.
  A persistent `uv run pytest` approval was then required. Ruff required a separate persistent
  approval.
- User-visible update gaps reached 2 minutes 44.819 seconds, 3 minutes 44.202 seconds including
  compaction, and 3 minutes 10.448 seconds.
- A monolithic ADR/spec patch failed because its ADR-006 context was stale. After compaction Codex
  recovered by using smaller patches.
- One later patch accidentally indented the top-level `CodexMcpAdapter` import. Codex detected and
  repaired that error within approximately 27 seconds.

These are not the root cause of zero Yoetz calls, but they matter to a real setup evaluation. A
future dogfood harness should capture structured events directly and avoid making the evaluator
infer state from a noisy terminal renderer.

### The final completion claim remained unconstrained

Codex concluded `Implemented and verified` after focused configuration tests. Independent review
showed that the generated patch had not wired production provider dispatch and had not proven
working inference endpoints.

Yoetz supplied no challenge because no record or check existed. This is the exact class of moment
where the product should at minimum constrain evidence wording and expose unfinished obligations.

#### What the generated patch actually implemented

The preserved experiment changes 16 files with 543 insertions and 90 deletions. It adds:

- reviewed-looking provider preset metadata for Anthropic, Gemini, OpenRouter, and Vercel AI
  Gateway;
- interactive and non-interactive provider choices;
- default models, fixed hosts/path prefixes, protocol-style labels, and credential-target
  persistence;
- ADR/spec updates and focused config/CLI tests.

The literal endpoint strings are broadly consistent with the providers' published compatibility
documentation. The principal defect is not “the URLs are obviously wrong.” It is that the patch
stops at setup/configuration metadata and then describes those bindings as usable.

#### Why the new endpoints are not operational

Commit-qualified source inspection gives a direct failure chain:

1. `d30f762:src/yoetz/config/write.py:75-143` defines preset metadata. The patch does not modify the
   production provider adapter, external-factory registry, or gateway composition.
   `ProviderPreset.host`, `base_path_prefix`, and `api_style` are not persisted into
   `ProviderProfileConfig`; runtime would need a reviewed endpoint-profile registry/factory to turn
   the saved identity back into transport behavior.
2. `d30f762:src/yoetz/service/ready_composition.py:914-916` constructs the sole production
   `PolicyEnforcingOutboundGateway` with `external_factory_builders={}`.
3. `d30f762:src/yoetz/adapters/privacy/gateway.py:336-338` reconciles a configured external binding
   with no builder as `factory_unavailable`.
4. The required factory contract is defined at
   `d30f762:src/yoetz/adapters/privacy/gateway.py:127-145`; repository search found no concrete
   production implementation. Concrete builders exist only as test fakes in
   `tests/integration/privacy/test_egress_gateway.py:193-228`.
5. The only production HTTP transport is
   `d30f762:src/yoetz/adapters/providers/openai_responses.py`. Its profile accepts only `/v1` or
   `/inference/v1` and appends `/responses` at lines 235-240. No Chat Completions adapter exists.
6. Anthropic, Gemini, and OpenRouter were configured as Chat Completions. Vercel was configured as
   Responses, but it also has no registered production factory. Therefore none of the four new
   presets reaches a production inference call.

The false-positive readiness path is also concrete. At
`d30f762:src/yoetz/cli/setup.py:459-470`, config binding plus credential storage produces “Yoetz is
ready to use this provider.” The daemon's `stored` result at
`d30f762:src/yoetz/service/daemon.py:1182-1204` proves vault persistence only; it does not validate
factory availability or execute inference. Setup reaches/starts the service before writing the
binding and demonstrates no restart, reload, dispatch, or smoke request afterward.

#### Spec and architecture inconsistencies

The generated documentation widened claims beyond implemented authority:

- `d30f762:docs/adr/ADR-006-semantic-provider-profile.md` requires recorded capability fixtures for
  each advertised provider/model/endpoint tuple, but the patch adds none.
- That ADR newly advertises three Chat Completions profiles and one Vercel Responses profile, while
  the only owning adapter spec remains `specs/src/yoetz/adapters/providers/openai_responses.md`,
  limited to the existing Responses implementation. `specs/FILE_MANIFEST.md` names no Chat
  Completions adapter owner.
- `specs/INTERFACES.md` requires each installed external endpoint profile to carry a current
  `ProviderDataUseProfile`; the patch adds none for the four new presets.
- E-007 remains evidence-gated in `d30f762:specs/OPEN_QUESTIONS.md`; setup should not translate
  stored configuration into operational readiness.
- The root README remained stale, describing Official OpenAI/custom origin rather than the expanded
  provider menu.
- Alias behavior drifted: config accepts `official-openai`, `google`, and `google-gemini`, while the
  provider endpoint command rejects those three aliases with exit 2.

#### What the tests proved and did not prove

Codex's focused verification was real but narrower than its conclusion:

- Final generated-agent slice:
  `uv run pytest tests/unit/config/test_owner_declared_endpoint.py tests/unit/config/test_models.py tests/subprocess/test_setup_wizard_cli.py`
  — 56 passed in 0.76 seconds.
- Ruff eventually passed after first reporting two import-order errors and formatting three files.
- Pyright eventually reported zero errors after first reporting 23 errors.
- `git diff --check` passed.

The new test named `test_bundled_provider_presets_write_exact_usable_bindings` only constructs
expected URL strings, writes/reloads TOML, and compares preset fields and a path suffix. It never
constructs an adapter or factory, reconciles the production gateway, renders a request, binds a
credential to a dispatch, normalizes a response, or makes a smoke request. The setup wizard test
monkeypatches `run_provider_setup` and checks argument forwarding.

Independent validation ran the broader focused slice:

```text
uv run pytest \
  tests/unit/config/test_owner_declared_endpoint.py \
  tests/subprocess/test_setup_wizard_cli.py \
  tests/integration/service/test_ready_composition.py \
  tests/integration/privacy/test_egress_gateway.py
```

It produced 54 passes in 10.11 seconds. That establishes config/CLI compatibility and no regression
in existing gateway tests; it does not establish new provider runtime support. Independent Ruff,
Pyright, and diff-whitespace checks also passed.

The bounded non-live gate produced 19 failures, 1,623 passes, 3 skips, 29 deselections, and 4
expected failures in 162.72 seconds. All 19 failures were packaging clean/offline/uninstall
subprocess tests that failed before Yoetz import because the managed Python installation under
`/private/tmp/yoetz-phase0-uv-python/.../lib/python3.14/encodings` contained only `__pycache__`,
causing `ModuleNotFoundError: No module named 'encodings'`. This appears unrelated to the patch, but
the honest result is still that the full bounded gate was not green.

No live provider request, e2e dispatch, or smoke request was executed. Issue 6 explicitly asked for
transport usability and smoke-level request shapes. Therefore “configuration choices implemented”
is supported; “working configurations” and “Implemented and verified” are not.

## What was not shown to be broken

The run did not exercise, and therefore cannot pass or fail, these components:

- on-demand service launch;
- same-UID service handshake;
- task allocation and resume;
- encrypted ledger writes;
- projections and obligation state;
- deterministic work-integrity checks;
- finding response and recheck;
- receipt generation;
- reconnect and replay behavior.

The correct statement is "Codex failed to invoke the checker," not "the checker ran and failed."

## Would an activated Yoetz have caught the implementation gap?

Not automatically.

Yoetz is a cooperative ledger and deterministic checker, not a workspace observer. Its
work-integrity policy can find structural conditions such as:

- completion with open obligations;
- a requested item with no recorded attempt;
- a claim with no admissible evidence;
- an omitted failed result;
- evidence stale relative to a changed state.

It intentionally does not parse claim prose or decide whether cited evidence is semantically
relevant. If an agent records "working endpoints" as complete and links successful configuration
tests, deterministic structure alone may not understand that those tests do not exercise runtime
dispatch.

Yoetz could have constrained the conclusion if Codex had turned the user request and issue 6 into
granular obligations: provider setup choices, runtime transport usability for each protocol style,
credential-bound dispatch, response normalization, and smoke-level request shapes. If Codex then
published configuration as complete but runtime adapter absent, no smoke dispatch, or the bounded
gate failure, deterministic checks could preserve open obligations, surface no-attempt or
no-admissible-evidence structure, prevent omission of a failed result, and weaken the receipt.

It still could not independently discover `external_factory_builders={}` or infer that a passing
TOML round-trip test is semantically irrelevant to runtime dispatch. That conclusion required
source-aware code review. The checker cannot force the agent to publish a fact the agent omits and
cannot assess semantic evidence relevance by design.

This means the realistic value proposition is:

> Yoetz makes obligations, evidence, gaps, and completion claims durable and auditable so that
> overclaiming is harder—not that it independently understands every code defect.

## Verification gap in Yoetz's own evidence

The MCP server spec says `tests/capability/test_mcp_protocol_and_sdk.py` covers an unprofiled host
completing the workflow with no installed skill.

The actual capability test proves protocol/SDK identity, exact initialize instructions, the six
tool names, unsupported-protocol handling, malformed/null-ID framing, and the presence of output
schemas and annotations. It does not send `tools/call`, list or read resources, compare exact schema
identities or digests, run a real Codex agent, test spontaneous activation, complete
`start -> publish_work -> check -> receipt`, or force compaction and require `status`.

The nominal live Codex capability test has another explicit gap. Without authorization it records
`UNSUPPORTED`; with authorization it unconditionally fails with “drive tools before claiming
pass.” Its non-live strict-local case calls `Application.start`, `publish_work`, and `check`
directly, bypassing Codex and MCP and not exercising all six tools. A real live driver does not yet
exist.

Protocol availability was therefore treated too much like agent usability. This dogfood run is
direct counterevidence to the stronger interpretation.

## Root-cause chain

```text
Setup wizard registered MCP
        |
        +-- Yoetz skill absent
        |     +-- installation refused: no tested Codex profile
        |
        +-- no tested activation/compaction hook
        |
        +-- zero-integration path depended on weak or ignored MCP guidance
                       |
                       v
             Codex never called any Yoetz tool
                       |
                       v
             service was never exercised
                       |
                       v
        no task, obligations, evidence, check, or receipt
                       |
                       v
        compaction had nothing durable to re-ground against
                       |
                       v
        final overclaim received no Yoetz challenge
```

## Corrective actions

### Priority 0: make activation support real and visible

1. Test the exact Codex `0.146.0-alpha.2` host/binary identity and only then add a
   support-installable capability profile. Do not infer support for all `0.146.x` builds.
2. Preserve prerelease identity in discovery instead of normalizing `0.146.0-alpha.2` to
   `0.146.0`.
3. Make setup output distinguish MCP registration, skill installation/support, automatic
   activation, lazy service state, and live task state.
4. Put the material-task activation rule in a highest-salience surface proven to reach the model;
   do not depend only on an optional linked resource.
5. Add a tested host-native activation or compaction trigger where Codex exposes one safely.

### Priority 1: replace protocol-only evidence with agent-behavior evidence

1. Add a real Codex dogfood capability run for a material task.
2. Require spontaneous `start -> publish_work -> check -> receipt` without telling the agent to
   call Yoetz in the user prompt.
3. Force compaction or resume and require `status` before further completion work.
4. Keep protocol/SDK negotiation and agent workflow activation as separate capability families.

### Priority 2: improve evidence specificity without overstating observation

1. Distinguish unit/config, integration/transport, capability, and live-smoke evidence classes.
2. Bind important acceptance criteria to the evidence class expected to satisfy them.
3. Preserve the rule that Yoetz does not claim to observe code or prove correctness.
4. If semantic evidence relevance is required, add an explicit semantic reviewer or source-aware
   evidence producer rather than stretching deterministic rules beyond their knowledge.

### Exit criteria for the next dogfood run

The next run should not be called successful unless all of these are directly observed:

| Stage | Required evidence |
| --- | --- |
| Setup | Exact Codex prerelease identity retained; MCP registration, skill state, compatibility, service state, and activation state reported separately. |
| Intake activation | A material user request with no Yoetz-specific wording causes one successful `start`; the user sees a truthful activation notice only after success. |
| Work publication | Requested outcomes and acceptance obligations are published before implementation is described as complete. |
| Multi-agent work | Each lane has a distinct writer/assignment or the run explicitly records that attribution is unavailable. |
| Verification | Evidence is labeled by class: config/unit, adapter integration, live capability, or external smoke. |
| Failure handling | A deliberate missing-runtime-path fixture produces an open obligation or finding and constrains the completion claim. |
| Compaction | Forced mid-turn compaction is followed by `status` before material work or completion continues. |
| Completion | The completion claim is published, `check` is recorded, findings are dispositioned, a recheck occurs after changes, and a receipt is read. |
| User wording | Final wording is no stronger than the receipt and explicitly lists unavailable or untested boundaries. |
| Durability | Catalog and ledger contain the current task/session, expected operations, events, checks, and receipt; replay uses stable request IDs. |

For the provider control task specifically, “working configuration” must include at least one
production-composition test per API style, one credential-bound request-shape test, one response
normalization test, and a capability-gated external smoke or an explicit unavailable limitation.
TOML round-trip and CLI forwarding alone are insufficient.

## Evidence and reference index

### Primary run artifacts

- Canonical complete rollout, 1,972,069 bytes at review time:
  `/Users/shayb/.codex-testing/sessions/2026/07/22/rollout-2026-07-22T16-21-22-019f89fc-ef51-7300-8adb-c02d60c63a45.jsonl`.
  It is the authority for complete chronology, tool calls, progress, compaction, and final wording.
- Codex host logs: `/Users/shayb/.codex-testing/logs_2.sqlite`, keyed by the session/thread ID.
  Retention covered only 1,000 rows for this thread, from approximately `13:29:17Z` through
  `13:44:31Z`; it is useful for catalog and selection details, not the complete chronology.
- Registration config: `/Users/shayb/.codex-testing/config.toml`, whose `mcp_servers.yoetz` entry
  uses command `yoetz` and arguments `mcp`, `serve`.
- Shell snapshot:
  `/Users/shayb/.codex-testing/shell_snapshots/019f89fc-ef51-7300-8adb-c02d60c63a45.1784726482940965000.sh`.
- Setup marker: `/Users/shayb/Library/Application Support/yoetz/setup-wizard.json`, with a pre-run
  modification time.

These local artifacts can contain environment-specific data. They are evidence locations, not
files intended for repository publication or copying.

### Yoetz durable-state artifacts

- Catalog: `/Users/shayb/Library/Application Support/yoetz/catalog.sqlite3`.
- Only pre-run ledger:
  `/Users/shayb/Library/Application Support/yoetz/tasks/tsk_bf3294b7-cbc8-435d-8cfa-a2d919d715a3/ledger.sqlite3`.
- Runtime directory checked after the run:
  `/Users/shayb/Library/Caches/TemporaryItems/yoetz`.
- Skill paths checked and absent:
  `/Users/shayb/.codex-testing/skills/yoetz`,
  `/Users/shayb/yoetz-core/.agents/skills/yoetz`, and
  `/Users/shayb/.agents/skills/yoetz`.

### Repository implementation and contract references

- [Tier-zero agent instructions](../../src/yoetz/resources/guidance/agent-instructions.md) define
  the completion cue and honesty boundary.
- [Full workflow guidance](../../src/yoetz/resources/guidance/workflow.md) defines material-task
  activation, disclosure, the ten steps, and post-compaction `status`.
- [Codex skill manifest](../../src/yoetz/resources/skills/codex/yoetz/manifest.json) has empty
  capability, version-support, and hook sets.
- [Codex skill integration](../../src/yoetz/adapters/integrations/codex_skill.py) rejects
  installation when `harness_tested_set` is empty.
- [Codex discovery](../../src/yoetz/adapters/integrations/codex_discovery.py) owns the version
  parsing that dropped the prerelease suffix.
- [MCP server specification](../../specs/src/yoetz/mcp/server.md) defines the zero-integration
  baseline, lazy service connection, degraded-host behavior, and its current test claim.
- [MCP descriptors](../../src/yoetz/mcp/descriptors.py) show that `start` describes the operation
  but does not carry the material-task intake activation rule.
- [MCP protocol capability test](../../tests/capability/test_mcp_protocol_and_sdk.py) is protocol
  evidence, not a live Codex workflow test.
- [Codex six-tools capability test](../../tests/capability/test_codex_six_tools.py) contains the
  live-driver placeholder and direct local application case.
- [Work-integrity specification](../../specs/src/yoetz/kernel/policies/work_integrity.md) states
  that deterministic checks do not inspect claim prose or interpret evidence relevance.
- [Production composition](../../src/yoetz/service/ready_composition.py) registers an empty
  `external_factory_builders` map in both the baseline and preserved experiment.
- [Privacy gateway](../../src/yoetz/adapters/privacy/gateway.py) owns factory lookup and the
  `factory_unavailable` outcome.
- [Existing Responses adapter](../../src/yoetz/adapters/providers/openai_responses.py) is the only
  production external HTTP adapter.
- [Provider gateway integration tests](../../tests/integration/privacy/test_egress_gateway.py) use
  test builders and do not establish production factory registration.

### Preserved experiment references

The generated code is not on `main`. Inspect it with commit-qualified Git commands so the
postmortem remains stable even while branches move:

```text
git show d30f762:src/yoetz/config/write.py
git show d30f762:src/yoetz/cli/setup.py
git show d30f762:tests/unit/config/test_owner_declared_endpoint.py
git diff fed3169..d30f762
```

The key commit-qualified locations are:

- `d30f762:src/yoetz/config/write.py:75-143` — provider preset metadata;
- `d30f762:src/yoetz/cli/setup.py:459-470` — readiness message based on config/credential state;
- `d30f762:src/yoetz/service/ready_composition.py:914-916` — empty production factory map;
- `d30f762:src/yoetz/adapters/privacy/gateway.py:336-338` — `factory_unavailable` path;
- `d30f762:src/yoetz/adapters/providers/openai_responses.py:235-240` — Responses-only path logic;
- `d30f762:tests/unit/config/test_owner_declared_endpoint.py:194-234` — metadata/TOML “usable
  bindings” test;
- `d30f762:tests/subprocess/test_setup_wizard_cli.py:326-358` — monkeypatched CLI forwarding test.

### External provider references used by the generated agent and review

- [Anthropic OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)
  describes a compatibility layer oriented mainly toward testing/evaluation and notes important
  parameter differences, including ignored `response_format` behavior.
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) documents Google's
  OpenAI-compatible base and request shape.
- [OpenRouter Responses API](https://openrouter.ai/docs/api/reference/responses/overview) shows that
  a Responses path exists; selecting Chat Completions increased the unimplemented transport scope.
- [Vercel AI Gateway OpenResponses](https://vercel.com/docs/ai-gateway/sdks-and-apis/openresponses)
  supports the plausibility of the configured base while not proving Yoetz wiring.

These references support endpoint/API-shape plausibility. They do not substitute for Yoetz's
required capability evidence or a production dispatch test.

## Confidence and unresolved uncertainty

Confidence is **high** that Yoetz made zero calls and produced no current-run record: the complete
rollout and durable databases independently agree. Confidence is **high** that the generated four
providers are not runnable through production composition: the factory registry is empty and the
required adapters are absent. Confidence is **medium** about the relative causal weight of absent
skill support, guidance placement, tool-catalog scale, and description truncation. All are observed
conditions, but this single run cannot isolate which activation mechanism would have changed model
behavior.

The most useful follow-up is therefore a controlled matrix, not a single retest: same task and
baseline with (a) MCP only, (b) explicit tier-zero intake cue, (c) installed tested skill, and (d)
skill plus forced compaction trigger. Compare spontaneous lifecycle calls, user disclosure,
context cost, and receipt-bounded completion across cells.

## Final product lesson

Registration is not activation.

Yoetz's host-facing MCP registration/discovery remained available and its durable state did not
fabricate participation, but the integration never crossed the line from “tools exist” to “the
agent uses them.” The run cannot make a broader claim about the unexercised service, ledger,
checker, or receipt architecture. The next release claim must distinguish registration, protocol
availability, skill support, automatic activation, live workflow participation, and receipt-backed
completion.
