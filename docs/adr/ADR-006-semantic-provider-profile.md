# ADR-006 — AI-powered review provider profiles behind the privacy gateway

**Status:** Working decision revised 2026-08-30 (issue #404 external-runtime authority). Amended
2026-09-23 for issue #742 (adapter-boundary failure tokens, credential-retry exclusion, and
attempt-status projection). Ratification requires the privacy/egress gates in
ADR-009 plus recorded capability fixtures against every advertised provider/model/endpoint profile.
**Implemented by:** `src/yoetz/ports/semantic.py`,
`src/yoetz/ports/privacy.py`, `src/yoetz/application/egress.py`,
`src/yoetz/application/check.py`, `src/yoetz/adapters/providers/`, `src/yoetz/config/`,
and AI-powered review/privacy capability and conformance tests.

## Decisions

1. **No direct provider path:** application, CLI, MCP, plugin, and integration code cannot call an
   AI-powered review provider. A candidate AI-powered review context must traverse ADR-009's
   classification, policy, local minimization/redaction/secret scan, exact prepared-case approval
   when required, durable authorization, outbound gateway, and privacy-audit path. A provider
   adapter receives only an immutable `ApprovedOutboundCase`; composition supplies no repository,
   bundle, transcript, environment, log, database, keyring, or application-state handle. Standing
   external evaluation additionally requires an exact current grant for the service-derived
   repository-privacy commitment beneath the machine ceiling. A missing or mismatched repository
   grant fails before provider construction, credential-handle minting, authorization, or dispatch.
2. **First external adapter:** official `openai` Python SDK (pinned `2.46.0`), Responses API with
   structured outputs (`responses.parse` + frozen `ProviderJudgmentModel` schema). A release names
   an exact tested provider/model/endpoint-profile tuple. A generic or merely
   "OpenAI-compatible" URL is never trusted as an ambient override. One exact, versioned profile
   kind — `owner-declared-openai-responses` (ADR-014) — may bind an owner-supplied constrained
   HTTPS origin from service TOML (`[provider.owner_declared_endpoint].https_origin`); it reuses
   the Responses protocol cell, never inherits official OpenAI data-use / `assisted` eligibility,
   and still requires capability evidence for any advertised interoperability claim.

   **Amended 2026-07-24; extended 2026-07-27 for the Grok/xAI dogfood path — the
   OpenAI-compatible Chat Completions protocol cell.** Five further exact, versioned profile
   kinds are authorized, each pinned to one host and one fixed path
   prefix, none of them owner-editable: `anthropic-openai-chat-completions`
   (`api.anthropic.com/v1`), `google-gemini-openai-chat-completions`
   (`generativelanguage.googleapis.com/v1beta/openai`), `openrouter-openai-chat-completions`
   (`openrouter.ai/api/v1`), and `xai-openai-chat-completions` (`api.x.ai/v1`) use the
   OpenAI-compatible Chat Completions cell;
   `vercel-ai-gateway-openai-responses` (`ai-gateway.vercel.sh/v1`) reuses the Responses cell and
   needs no adapter of its own. A configurable profile with no runtime factory is not a neutral
   omission — it reports `factory_unavailable` and the requested review silently never runs — so
   each authorized profile resolves to exactly one factory in the dispatch table.

   Structured-output enforcement is recorded per profile from the vendor's own documentation, not
   assumed: a host documented to ignore `response_format` receives the judgment shape in the
   instruction instead, and any answer that is not the exact judgment shape degrades to an honest
   invalid AI-powered review result, never a fabricated pass. None of the five inherits official
   OpenAI data-use or `assisted` eligibility; each carries an unknown data-use record until a
   reviewed one exists. Being dispatchable is not being verified: advertising any of them as a
   working endpoint still requires the exact model/endpoint capability fixture and live evidence
   E-007 names.
3. **Local-model adapter:** v0.1 includes the contract for a separately configured locally hosted
   AI-powered evaluator. Its endpoint is an owner-only, service-approved AF_UNIX socket profile; it
   performs no DNS, AF_INET/AF_INET6 connection, redirect, proxy lookup, or fallback. It is a local
   disclosure sink, not network egress, but still traverses classification, minimization, never-send
   scanning, and local privacy auditing. A release advertises it only for exact model/endpoint
   profiles that pass capability fixtures.
4. **Credentials:** provider credential bytes are owned by the unlocked local service vault. They
   never enter provider configuration values, CLI/MCP arguments, environment variables, files,
   logs, traces, transcripts, prompts, or LLM context. For each physical dispatch, the gateway
   obtains a fresh service-issued `ProviderCredentialHandle` bound to exact provider/model/endpoint
   profile+version, purpose, authorization-scope digest, purpose digest, dispatch ID, final
   request-body digest, service generation, and deadline. Only the custom HTTP transport may
   consume it through a one-shot header-injection
   callback; the adapter and SDK never receive or retain reusable credential bytes. Under resolved
   decision F-012, the custom transport necessarily sends that separately
   provisioned credential as one-attempt authentication metadata to the exact profile-bound HTTPS
   endpoint selected by the reviewed registry, using platform CA trust and hostname validation,
   never as candidate/model content. v0.1 does not claim certificate or SPKI pinning.
5. **Client policy:** each physical attempt constructs and closes one
   `AsyncOpenAI(base_url=service-resolved exact profile endpoint, timeout=explicit,
   max_retries=0, api_key=fixed_nonsecret_sentinel, http_client=one_attempt_custom_transport)`.
   The adapter renders the exact final application JSON body deterministically. The custom
   transport rejects any actual body digest/profile/deadline mismatch, removes the sentinel header,
   invokes the attempt-bound credential callback only to inject the real authentication header and
   start that one request, then releases the protected view immediately. The privacy commitment is
   over the exact final application body bytes, excluding authentication metadata and HTTP/TLS
   framing. No long-lived SDK client or default-header object holds the real key. Yoetz owns the retry
   budget: at most two retries, only for approved timeout/connection/429 classes, jittered backoff,
   all within one total deadline and one durable AI-powered review operation (per endpoint when a
   fallback endpoint is declared — see the fallback endpoint amendment below). **Amended
   2026-08-29 (issue #348):** inside that same budget and deadline, exactly one repair retry is
   also admitted after `invalid / response_content_invalid` — the provider was reached and its
   answer was incomplete
   or overlong (`failure_class=response_content`, output-token truncation, Chat Completions
   `finish_reason=length`). The repair resubmits the same frozen case under the same authorized
   provider/profile/model/category/retention ceiling with unchanged sampling; it is a new physical
   attempt with fresh dispatch, provider request, authorization, privacy receipt, transport, and
   credential-handle identity, and no provider plaintext is retained. A second content-invalid
   answer is terminal and both attempts remain in accounting. `response_schema_invalid`,
   `semantic_judgment_rejected`, refusal, policy or human denial, invalid case, stale frontier,
   quota exhaustion, secret or never-send detection, exhausted authority, and a rejected
   credential (`failure_class=authentication` or `authorization`) are never retried. A rejected
   credential may still surface as public reason `transport_unavailable` — the transport catch-all
   — so retry consults the recorded `failure_class`, not the public reason alone (issue #742).
   The same class also vetoes fallback engagement: a rejected primary credential ends the job
   rather than dispatching the case to the fallback endpoint.
   One durable attempt and one
   privacy receipt, SDK client, custom transport, and credential handle are created per physical
   dispatch. For `confirm_every_request`, each physical retry also requires a fresh exact foreground
   preview/decision and a new one-dispatch proposal; the original human decision cannot cover a
   hidden multi-attempt budget. Crash/resume before authorization consumption remains the same
   attempt. Automatic profiles may retry within their existing policy/total deadline without a
   human prompt, but never reuse authorization or attempt identity.
6. **Required AI-powered review means verdict completeness, not operation availability:**
   local-check freeze and local-check results always survive. With `semantic_required`, missing
   approved capability, privacy-policy block, human denial or approval expiry, provider refusal,
   timeout, invalid output, exhausted retry, late response, or stale response completes the check
   with `verdict=incomplete_check`, no AI-powered findings, and the exact closed `(SemanticStatus,
   SemanticReason)` pair. It does not fail the operation or discard local findings.
   `semantic_if_configured` may complete with its local-check verdict when AI-powered review
   capability is absent or policy-disabled, while an attempted but unsuccessful AI-powered
   evaluation is represented honestly in status and coverage.
7. **Provenance has two truthful stages:** the adapter returns bounded
   `ProviderAttemptProvenance` containing only provider/profile/model/request/SDK/digest/usage/
   failure facts it knows at return time. It cannot name a privacy receipt that is not yet closed.
   After the matching terminal `EgressReceipt` or `LocalDisclosureReceipt` is durable, the
   coordinator constructs final `SemanticProvenance` with attempt identity, exact dispatch kind,
   external authorization or local-disclosure reservation, receipt identity, external request
   commitment when applicable, and final status/reason. Only final provenance may be attached to
   a finding or public result. Predispatch gaps have no attempt provenance and remain exactly
   explained by status/reason. Model output is always labeled `semantic_model_derived`.
8. **No raw response retention by default:** success persists only the bounded parsed judgment and
   structural provenance. Refused, malformed, truncated, late, or rejected provider plaintext is
   not retained merely for debugging. If a future opt-in encrypted diagnostic capture is added, it
   requires its own explicit local-human authorization and retention policy; it is not part of the
   v0.1 AI-powered review contract.
9. **Fake provider:** `adapters/providers/fake.py` is a scripted implementation behind the same
   policy-enforcing gateway. It supports results, delays, denials, refusals, malformed output, and
   late responses without network access. Tests may not inject the fake downstream of the gateway
   when claiming privacy-path coverage.
10. **In-process adapter trust limit:** v0.1 loads only reviewed bundled adapters selected by the
    closed registry; third-party/plugin provider adapters and dynamic adapter paths are absent.
    Approved-case types and dependency injection remove ambient capabilities from normal
    composition, and tests can prove the bundled adapter does not use forbidden APIs. They do not
    create an OS/process sandbox: malicious or compromised Python code running inside the trusted
    service could exercise the active user's ambient authority. Process/native sandboxing remains a
    separate stronger architecture option, not a v0.1 privacy claim.
11. **Review context is a separate policy dimension:** `PrivacyProfile` answers whether and how a
    model disclosure is authorized. `ReviewContextProfile` answers which useful facts the case
    builder selects before privacy enforcement. The closed values are `structural`, `goal_aware`,
    `assisted`, `expanded`, and `custom`. The official CLI recommends `assisted` only after the
    user selects and confirms an exact provider, repository scope, categories, classes, and limits.
    Repository scope is derived by the trusted service from the client session's actual working
    directory, never from model-controlled `workspace_ref`: branches and linked worktrees resolve to
    one Git common root, while independent clones and unrelated repositories do not share authority.
    The safe installation seed remains zero-egress `local_only`; a recommendation is never implicit
    consent.
12. **The recommended packet is rich but problem-local:** `assisted` contains the task goal,
    obligations, current completion/material claims, accepted decisions, a material ordered
    timeline, local findings and their machine-readable bases, change-observation facts,
    coverage gaps, and bounded linked test/failure/evidence/source excerpts. The frozen case retains
    the newest 64 material accepted events in ingestion order with at most 512 KiB of canonical
    payload. Newest payloads win that byte budget; retained over-budget events are `not_selected`,
    older events are represented by an exact omitted-before count, and legacy cases state
    `not_recorded`. A source excerpt must
    already be captured or agent-published in the frozen case and must be linked to the reviewed
    claim, obligation, finding, action, result, or evidence. The case builder has no live Git or
    filesystem browser and never upgrades missing content into observed content. `expanded` and
    `custom` may select more *already recorded* in-scope material, but no profile grants ambient
    repository access or defeats the existing item/case caps.
    Native edit arguments from Codex, Claude Code and Cursor may enter this lane as dedicated
    diff/changed-file content, using only recognized visible patch or replacement fields, captured
    once from the post-tool event and labelled with the host-reported outcome (`applied`, `failed`
    or `unknown`). Unknown nested fields are excluded, and every file locator in the edit, including
    patch headers, is made workspace-relative or masked as `<outside-workspace>` (POSIX, Windows
    drive, UNC and WSL `/mnt/<drive>` spellings alike). Capture consent, secret scanning, outbox
    admission, generation fences and disclosure approval remain required. Claim-linked evidence
    wins selection first, then captured edits (newest first), then other captures, including before
    bounded object resolution; a selected capture outside the latest-256 envelope window is read by
    exact content reference or disclosed as `content_unselected`. Shell heredoc edits whose new
    bytes are in the command (`apply_patch`/`git apply`/`cat >`/`tee` heredocs) are captured the same
    way; script-mediated edits are not. Authenticated
    code is split into UTF-8-safe items within the existing per-item, item-count and total-byte caps;
    a delivered prefix reports `truncated_payload`, and excluded retained content reports
    `content_unselected`. This is not a fresh Git snapshot at check time: shell-mediated edits,
    changes outside supported visible fields, and uncaptured current state still need separately
    recorded content/state evidence. A digest alone cannot supply missing code.
13. **Reviewer output talks to the main agent through the existing workflow:** a successful model
    judgment may propose bounded `ReviewerChallenge` values. Each challenge names only case-bound
    refs, explains the discrepancy, states an alternative interpretation, addresses the main agent
    directly, and requests the smallest next step: act, provide evidence, revise the claim, dispute
    with evidence, or state an unresolved limitation. Post-validation maps an accepted challenge to
    the existing AI-powered `Finding.summary/detail`; the main agent uses the existing `respond` and
    `publish_work` operations, then runs `check` again. There is no provider-driven fetch loop, new
    event family, seventh public operation, or model waiver authority.
14. **Recommendation eligibility is evidence-bound, not a brand promise:** every installed external
    endpoint profile carries a versioned data-use record stating customer-content training use,
    retention posture, provider-human-access posture, review/expiry times, and an evidence digest.
    The upstream `assisted` badge requires a current record with training `prohibited`, retention
    `none|bounded` with any bounded ceiling at most 30 days. Provider human-access posture and
    documented safety, support, legal, and abuse-monitoring exceptions remain mandatory disclosure
    facts, but do not replace or silently raise that recommendation threshold.
    The recommended recipe also sets an editable
    `require_current_provider_data_use_evidence=true` runtime guard. Yoetz does not technically
    prove provider behavior. Unknown, known-broad, or stale status removes the recommendation badge
    and trips that guard; an informed user may explicitly turn the guard off through a custom policy,
    and a fork may change the rule without inheriting upstream privacy/support evidence.
15. **Adapter classification and attempt-status projection (issue #742):** every provider
    failure is classified at the adapter boundary into the closed `SemanticFailureClass` set.
    Public `SemanticReason` values stay the existing closed pair vocabulary; renderers resolve
    recovery through `continuation_for_semantic_outcome` from that pair plus `failure_class`.
    A successful review with zero findings is `semantic_status=succeeded` /
    `semantic_reason=semantic_completed` on the check path, and `semantic_state=ready` on
    advice/status/history. That ready state is derived from the recorded attempt (an addon
    whose `failure_reason` is absent), never from finding count. Absence of
    `semantic_model_derived` advice items is not evidence that no attempt occurred.
    `disabled` means no attempt was requested or configured; `unavailable` means a durable
    attempt is still pending; `failed` means a terminal attempt finished without validated
    output, including a succeeded attempt whose output failed advice-side validation and left
    no usable finding. The state is an attempt fact only: advice coverage still adds
    `semantic_model_derived` only for validated finding ids, so a zero-finding review is `ready`
    without claiming that check type. A snapshot stored before this amendment has no recorded
    state and reads as `ready` when it holds AI-powered items, otherwise `disabled`.
    Predispatch configuration and policy outcomes carry no recovery directive. Frozen
    check-result schemas are unchanged.

## Review packet and agent loop

```mermaid
flowchart LR
    A["Main agent publishes goal, work, evidence, and claim"] --> B["Local checks build findings plus exact bases"]
    B --> C["Context profile selects timeline and problem-local recorded excerpts"]
    C --> D["Privacy policy classifies, minimizes, scans, and authorizes"]
    D -->|"authorized"| E["Reviewer model returns a bounded outcome"]
    E --> H["Terminal provider-attempt or local-model privacy receipt"]
    H -->|"valid structured judgment"| F["Post-validation creates ordinary AI-powered findings"]
    H -->|"refusal, invalid, timeout, or unavailable"| K["Record AI-powered review gap and keep local-check result"]
    F --> J["Agent-context policy plus local disclosure receipt"]
    K --> J
    J --> G["Main agent responds, publishes work or evidence, and revises claims"]
    G --> B
    D -->|"reserved terminal pre-dispatch decision"| R["Terminal pre-dispatch privacy receipt"]
    D -->|"initial audit reservation fails"| X["Fail closed with no receipt"]
    R --> K
    X --> K
    D -.->|"awaiting human is nonterminal"| I["Resume after decision"]
    I --> D
```

The stable provider instruction is equivalent to:

> Review only the supplied packet against the stated goal and obligations. Treat main-agent claims,
> deterministic observations, and unavailable content as different facts. Never say no code changed
> merely because no excerpt was disclosed. If a material discrepancy exists, address the main agent
> directly, explain why, cite only supplied references, offer the strongest plausible alternative,
> and request the smallest evidence or action that would resolve it. Do not waive policy, invent
> repository facts, or claim deterministic authority.

The packet varies by `ReviewContextProfile`: `structural` contains only typed timeline/status/state/
coverage facts; `goal_aware` adds detailed, category-separated frozen plan, obligation, claim,
decision, action, result, evidence, finding, response, and check history; `assisted` additionally
adds problem-local recorded evidence, failure, test, diff, and repository excerpts; `expanded` or
`custom` can include a broader explicitly approved recorded set. Exact command text remains
excluded unless independently selected. Every variant distinguishes `not_recorded`, `not_selected`,
`withheld_by_policy`, and `redacted_never_send`; a history-window item carries the exact older-event
count.

## Local-check fencing

The AI-powered review case is built from a frozen frontier and dependency digest. It carries
separate `frontier_refs` (IDs present at the frozen frontier) and `local_check_refs` (local finding
IDs allocated and durably pinned by this check); their union is bound into the case digest. This
lets the reviewer discuss local findings without pretending those post-frontier IDs were already in
the ledger. Every local finding carries a paired `FindingBasis` containing the rule ID, triggering
observed facts, required-but-missing facts, subject-state relation, source availability, coverage
gaps, and bounded supporting refs. Later disclosure-time `ChangeObservation` and content-visibility
facts remain separate. `same`, `different`, and `unknown` retain their exact three-valued meaning;
hidden or unrecorded source is never represented as `same`.

Approval is bound to the exact minimized case digest, provider/model/endpoint profile, purpose,
composed machine/repository/task/request authority, policy version, and one dispatch. The provider
is called outside every SQLite transaction.
Post-validation rejects invented IDs, out-of-case quotes, coverage upgrades, local-check-status
claims, challenges without a material discrepancy or requested next step, and stale frontiers.
Rejected output never projects a finding.

## Codex subscription-runtime amendment (2026-08-30, issue #404)

External AI-powered review authentication has two authorities. Existing HTTP profiles
use `yoetz_vault_api_credential`. The exact `codex-chatgpt-subscription@1` profile uses
`external_runtime_oauth`: one selected OpenAI Codex app-server owns ChatGPT login, refresh,
credential storage, model discovery, and the upstream OpenAI request. Yoetz never reads or imports
that credential. The only fallback behind this profile is the other declared authority — an
exact API-provider binding paired under the fallback endpoint amendment below (2026-09-04,
issue #582) — never an API key read from the environment, a generic endpoint, a proxy, or an
ambient Codex home. Until that amendment the two authorities were mutually exclusive in
configuration.

The initial closed compatibility cell is Codex npm `0.150.1` on macOS arm64, app-server v2 over
stdio JSONL, with an exact native executable digest, protocol-schema digest, digest-bound isolated
configuration, model, reasoning effort, and `codex-evaluator/0.150.1/v1` capability identity. A
capability-cell identity digest and evidence expiry are bound separately; an expired cell cannot
launch a child. A neighboring version, changed binary/config, absent ChatGPT login, unavailable
exact model/reasoning cell, or unproved isolation fails before case disclosure. This cell has
unknown data-use posture and receives no Assisted recommendation badge.

The September 7, 2026 amendment for issue #584 pins `codex-evaluator/0.150.1/v2` to the same
native binary, schema, configuration, and expiry. It accepts the pinned schema's sparse rate-limit
bookkeeping independently of bucket name; malformed bookkeeping after acknowledgement records
only a bounded nonterminal diagnostic. Native quota and 429 errors remain authoritative. Tool
and unreviewed event failures stay terminal but are unavailable rather than invalid model answers.
The runbook records the exact new cell digest and synthetic evidence boundary. Existing bindings
require explicit setup to accept the new identity; no privacy authority migrates implicitly.

### Linux x86_64 cell amendment (2026-09-13, issue #716)

The same evaluator contract now has a separate Linux x86_64 implementation cell for Codex npm
`0.150.1-linux-x64` (`@openai/codex-linux-x64`). Its native executable, source identity, package
layout, platform, and capability-cell digest are distinct from the macOS arm64 cell; its app-server
v2 schema, isolated configuration, model/reasoning contract, OAuth authority, and privacy/cleanup
fences remain identical. An x86_64 WSL2 Linux userspace is eligible for the Linux cell, but
WSL-specific smoke evidence is pending; this amendment creates no native Windows cell. The Linux
cell remains an implementation candidate until its packaged Yoetz lifecycle and AI-powered review
receipt evidence is complete. The empty `runtime-support.json` arrays therefore remain unchanged.

The gateway issues a secret-free, dispatch-bound `ExternalRuntimeAuthority` instead of minting a
vault handle. The runtime may receive only the already-approved canonical case through stdin. Its
`RuntimeAttemptEvidence` commits to the disclosed case, instruction, output schema, launcher,
configuration, executable, protocol, capability, model/reasoning selection, safe correlation,
terminal output digest, and process cleanup. It explicitly records
`upstream_body_observability=unavailable`; the disclosed-case commitment must never be described as
the upstream OpenAI body. Current runtime evidence may also record the Codex app-server's bounded
cumulative token snapshot for that exact thread and turn: input/output/total counters plus cached
input and reasoning-output subsets, with cache-write input retained as a separate provider
counter. Repeated cumulative updates replace one another; they are never summed, and missing,
malformed, unrelated, or regressing updates remain
unknown or become a bounded `token_usage_invalid` diagnostic. Account identifiers and raw provider
notification bodies never enter provenance.

For the same reason, each observed `RuntimeTokenUsage` sample is copied into the corresponding
`semantic_attempts` ledger row as six bounded numeric counters before the attempt is closed. The
nullable columns preserve older ledgers and keep cache-write and reasoning subsets separate; a
partial or invariant-breaking sample fails closed. Internal attempt accounting for an operation
that is still in flight can thus recover usage for selected, failed, and expired physical attempts
across a service restart, while public provenance continues to describe only the provider result
it actually represents and never invents provenance for a failed recovery. Usage for attempts of
already-completed operations stays in the owner ledger rows and reaches no public surface. A
failure while persisting a successful response is terminalized as a bounded
`coordinator_failure` with the attempt's usage retained, rather than leaving the attempt
`started`.

Retries remain within the durable attempt budget. A pre-`turn/start`-acknowledgement transient may
receive a fresh one-use authorization and exact retry. After acknowledgement, transport ambiguity
or unconfirmed process-group cleanup is terminal `unavailable/outcome_unknown` and is never
automatically retried. Schema-valid model output remains advisory and follows the unchanged
post-validation/finding path.

### Evaluator runtime retention and repair amendment (2026-09-26, issue #855)

Binding the exact cell to an ordinary host installation coupled AI-powered review to the host's
package manager: a routine Codex update replaced the bytes at the bound path, and a capability
identity change (the #584 v1 → v2 transition) stranded a valid login behind an outdated binding.
Both failed before dispatch and surfaced only as `credential_unavailable`. This amendment keeps
exact-version admission and separates the **evaluator runtime** from the **host installation**.

1. **Admission is unchanged.** Each platform cell still admits exactly one reviewed native
   executable digest. No newer Codex release is admitted, and nothing is inferred from a version
   string, path, or discovery order. Capability-based admission of further releases stays future
   work that needs its own evidence and amendment.
2. **A retained runtime per data bundle.** Setup and repair bind an owner-private copy at
   `<data bundle>/external-runtimes/codex-evaluator/<source identity>/codex` (directory `0700`,
   file `0500`). Bytes are hashed while they are copied and committed by atomic replace only when
   they equal the admitted digest; the unchanged launch fence re-verifies them before every child.
   Host package-manager updates cannot replace the copy, and every instance keeps its own.
3. **Sources for the copy.** A selected local executable that resolves to the admitted cell, or,
   after explicit consent, the operator's own `npm` installing the exact admitted release into an
   owner-private staging prefix with lifecycle scripts disabled. Only the verified native
   executable is kept; the staging prefix is always removed. This is package acquisition under the
   operator's registry settings. No task content, credential, or account data is sent on it, and
   it grants no review egress.
4. **Selection by eligibility.** Setup defaults to the retained copy, then the existing binding's
   executable when it still holds the admitted bytes, then the first discovered installation that
   resolves to the admitted cell. An unadmitted discovered binary is never presented as a default.
5. **Rebinding preserves choices.** Setup and repair preserve the provider role, model, reasoning
   effort, timeout, retry budget, and dedicated home unless the owner explicitly changes one.
   `repair` is the explicit capability-identity transition the #584 amendment requires: it shows the
   changed fields, needs acceptance, and reuses the login only when Codex's own
   `account/read`/`model/list` probe reports the home signed in with the exact model. It never
   starts a sign-in, logs out, or switches accounts, and it writes against the exact configuration
   preimage. Privacy grants are not part of the binding and do not migrate. A missing home, an
   unsafe home, and a modified isolated config are refused for the owner to fix; only a missing
   Yoetz-owned isolated config is restored.
6. **Precise pre-dispatch diagnosis.** A closed structural state is computed from local structure
   only (binding, executable bytes, dedicated home): `codex_runtime_platform_unsupported`,
   `codex_runtime_binding_invalid`, `codex_runtime_capability_evidence_stale`,
   `codex_runtime_capability_unsupported`, `codex_runtime_executable_missing|invalid|changed`,
   `codex_runtime_profile_outdated`, `codex_home_missing|unsafe`,
   `codex_runtime_config_missing|changed`, `codex_runtime_unavailable`, or `ready`. `ready` is
   reported only when the launch fence also accepts the binding. READY composition still spawns no
   app-server. The public semantic outcome is unchanged; a request-joined companion diagnostic names
   the structural state, and `provider status` reports it with its continuation.
7. **Reverse operations stay distinct.** `runtime remove` refuses while the binding uses the copy.
   Disconnect and rollback leave the copy in place. A stranded binding can still be disconnected:
   the admitted runtime is used in memory for that one logout.
   Setup, repair, install, and removal hold one owner-private lock per runtime store. Setup and
   repair keep it through the readiness probe and configuration write, so removal cannot delete
   a runtime between those steps. Contention fails immediately as `codex_evaluator_runtime_busy`;
   the operator retries after the other command finishes. The lock is released on every exit.

Evidence expiry is unchanged: after `2026-11-30T00:00:00Z` every cell reports
`codex_runtime_capability_evidence_stale`, which only a Yoetz release carrying renewed evidence
resolves. The upgrade plan names the evaluator check; applying a repair stays an explicit owner
step.

### Codex 0.157.1 cell amendment (2026-09-27, issue #871)

The admitted release moves from Codex npm `0.150.1` to `0.157.1` on both platform cells. Each
cell still admits exactly one reviewed native executable, so decision 1 of the #855 amendment
holds. `0.150.1` cannot list or run the `gpt-6-luna` new-binding default on a ChatGPT account;
`0.157.1` can. This is a new exact-version admission with its own evidence, not capability-based
admission of further releases.

1. **New identities.** The macOS arm64 cell (`@openai/codex-darwin-arm64`) and the Linux x86_64
   cell (`@openai/codex-linux-x64`) carry new native digests, a new app-server v2 schema digest,
   and the capability profile `codex-evaluator/0.157.1/v1`. Each has its own capability-cell
   digest.
2. **Unchanged.** The isolated configuration, launch argv, OAuth authority, privacy and cleanup
   fences, evidence expiry (`2026-11-30T00:00:00Z`) and the `implementation_candidate` / `pending`
   release posture stay the same.
3. **Schema review.** Two changes follow the reviewed schema difference:
   - rate-limit bookkeeping accepts and discards the new nullable `normalModelSlug`;
   - the native `rateLimitExceeded` error classifies as `provider_rate_limited`, like HTTP 429.

   New notifications and item types stay outside the allowlists and fail closed.
4. **Existing bindings.** A binding written under `0.150.1` names a runtime this release does not
   admit. It reports `codex_runtime_capability_unsupported` and continues through `repair`, the
   explicit identity transition from the #855 amendment. `repair` retains the new copy under its
   own source-identity directory and reuses the sign-in only when Codex's probe proves the exact
   model. No privacy authority migrates.
5. **Superseded copy.** The retained `0.150.1` copy is not deleted automatically. Removing
   superseded retained runtimes is recorded as follow-up work on #871.

Authenticated Yoetz evidence through the `0.157.1` cells is pending; the runbook records the
exact evidence boundary.

**Code-mode host amendment (2026-09-27, issue #874).** Codex `0.157.1` starts its sibling
`codex-code-mode-host` at turn start. With that helper absent it emits a native `warning`, and the
evaluator fails closed on it. The `v1` cell retained only `codex`, so its packaged Linux smoke
stopped there.

Profile `codex-evaluator/0.157.1/v2` makes the helper part of each exact cell:

- a per-platform pinned SHA-256, covered by the capability-cell digest;
- retained beside `codex` in the same owner-private store with the same modes and hash-while-copy
  commit;
- re-verified by the launch fence, and removed together with `codex`;
- taken only from beside the resolved native executable; nothing is searched.

The isolated configuration is unchanged, so dedicated homes and sign-ins carry over. A v1 binding
moves through `repair`. Warnings keep failing closed, and disabling the host by configuration is
not a substitute: it produces a different warning. Retaining Codex's bundled `bwrap` stays a
separate decision on #874.

## Fallback endpoint amendment (2026-09-04, issue #582)

AI-powered review may bind one primary endpoint plus exactly one fallback endpoint. The pairing is
exactly the two external authorities above: the API provider (`[provider]`,
`yoetz_vault_api_credential`) and the Codex ChatGPT subscription evaluator (`[external_runtime]`,
`external_runtime_oauth`). A nonsecret `[semantic_fallback]` table with
`primary = "api_provider"` or `primary = "codex_subscription"` names which serves first; the other
bound table is the fallback. Two API providers cannot pair, a generic or owner-declared endpoint is
never an implicit fallback, and there is no third slot. `profile` must agree with the primary
(`local-openai` for the API provider, `codex-subscription` for the subscription); a pairing with a
table missing fails as `semantic_fallback_endpoint_missing`, a disagreeing profile as
`semantic_fallback_profile_mismatch`. Removing the fallback restores the exact single-endpoint
configuration; swapping the primary keeps both bindings and both approvals.

1. **Closed engagement rule.** The primary is given up for the fallback only for the closed
   fallback-licensing set — `timeout/provider_timeout`, `unavailable/transport_unavailable`,
   `unavailable/provider_rate_limited`, `unavailable/provider_quota_exhausted` — and only after
   two such failures (`FALLBACK_PRIMARY_FAILURE_LIMIT = 2`, not owner-configurable), one quota
   exhaustion, or the primary's own exhausted retry budget. A primary that cannot be resolved
   before dispatch (`credential_unavailable`) hands every attempt to the fallback with zero
   primary attempts recorded. Content-shaped outcomes — `response_content_invalid` including its
   issue #348 repair retry, `response_schema_invalid`, refusal, `semantic_judgment_rejected` —
   policy and human outcomes, and `outcome_unknown` never engage the fallback: the primary
   answered, or may have, and a second destination cannot repair a content answer. Once engaged,
   a job never returns to the primary.
   A rejected primary credential (`failure_class=authentication` or `authorization`, issue #742)
   does not engage the fallback either, even under a licensing reason: it ends the job.
2. **Per-endpoint budgets.** Each endpoint keeps its own decision-5 retry budget (at most two
   retries) and its own configured timeout; primary failures never spend the fallback's budget.
   The overall deadline is the primary timeout plus the fallback timeout; primary dispatches
   are capped at the frozen primary cutoff, without dividing that timeout among retry slots.
   After a licensed transition, the fallback's aggregate timeout starts at its first durable
   attempt claim and is capped by the overall deadline. Retries and disclosure replay do not
   restart either clock. A single primary timeout that exhausts time but leaves retry slots does
   not bypass the two-failure/quota/attempt-budget engagement rule; it ends without fallback.
   A single fallback
   failure keeps its exact reason rather than reading as an exhausted budget it never had.
3. **Replay-safe endpoint selection.** Which endpoint an attempt uses is a pure function of the
   durable attempt rows before it and the immutable execution snapshot in the encrypted
   `SEMANTIC_CASE` object (`yoetz.semantic-case/2`), never mutable provider readiness. The snapshot
   binds exact endpoints, initial primary availability, retry budgets, and UTC cutoff times.
   Crash, restart, and `awaiting_human` replay resume the endpoint the attempt was claimed for;
   changed configuration cannot reinterpret earlier ordinals. Every attempt still checks current
   privacy authority for that frozen binding. Legacy terminal cases retain stored-result recovery;
   pending cases lacking the snapshot terminate without dispatch rather than acquiring a newly
   configured pairing (`coordinator_failure` before dispatch or during a disclosure wait,
   an uncertain started attempt retains `outcome_unknown` durably and reports the provenance-free
   public gap `receipt_persistence_unknown`). The internal attempt projection
   exposes the existing durable `started_at` timestamp; usage counters are an additive nullable
   bundle migration (0013), so legacy rows remain readable. An expired
   resumed attempt without a disclosure wait preserves `outcome_unknown`; a known undispatched
   expiry records `provider_timeout`. If provider-result provenance is unavailable on recovery,
   the public result uses `receipt_persistence_unknown` while retaining the original durable reason.
   Retained provider-result objects are recovered when their status and reason match that row.
   **Lease/recovery amendment, 2026-09-07 (#616, #620):** live AI-powered review operation and job leases
   use the authenticated execution snapshot's total expiry plus five seconds for local cleanup,
   rather than a renewable heartbeat. The current two-endpoint maximum makes that live bound
   at most 7205 seconds; a crash can consequently delay reclaim until that bound. Claim/reclaim
   retains an existing `started` or `response_durable` attempt and its physical request identity.
   A saved response is selected and recovered before any new attempt is considered. After the
   execution bound, an already reclaimed ordinary operation lease may perform bounded local
   terminal recovery; it cannot renew AI-powered review execution or dispatch after the immutable provider
   deadline. Provider deadlines and human approval expiry remain separate from lease ownership.
4. **Every fallback attempt is a fresh physical attempt** under ADR-009: its own privacy
   evaluation against the exact fallback binding, authorization, dispatch identity, credential
   handle or `ExternalRuntimeAuthority`, and privacy receipt. Under `confirm_every_request` it
   needs its own foreground preview and decision. Nothing approved for the primary is reused.
5. **Provenance names both endpoints.** `SemanticProvenance.fallback_from` (provider, endpoint
   profile id and version, model, `attempted_count`, `reason`) is present exactly when the
   fallback served; the top-level provider/model/endpoint then name the fallback and
   `fallback_from` names the primary, its physical attempts before engagement, and its last
   closed failure reason (`semantic-provenance-1.1.0`, append-only). It appears in check results,
   check-recorded events, findings, and JSON receipts; markdown and text receipts name the
   endpoint that served and the primary's closed failure reason. Attempt accounting carries one
   per-endpoint slice.
6. **Readiness and capability.** `yoetz provider status` reports `endpoint` (role `primary`) and,
   with a pairing, `fallback_endpoint` plus `fallback_credential_connected` and a separate
   `fallback_provider_credential` blocker; the service advertises a `fallback_provider`
   capability when the fallback's credential is structurally present. The fallback's credential
   never gates the primary and `semantic_ready` never depends on it. The pairing is a
   service-side dispatch decision with no host-specific behaviour; ADR-018's route ceiling
   applies to dispatch authority regardless of which endpoint serves.

Consequences: the Assisted recommendation rule reads the primary endpoint's data-use record, and
setup asks for `require_current_provider_data_use_evidence` only when every bound endpoint has a
reviewed record — the requirement is enforced per dispatch, so a fallback with unknown posture
would otherwise be policy-denied at the one moment the pairing exists for. The subscription cell
has unknown data-use posture whichever role it holds, so an attempt it serves carries no
upstream no-training claim. A fallback whose factory cannot be built or whose
credential is absent is reported unavailable on its own row without fencing the primary. No live
interoperability of a paired dispatch is claimed until authorized evidence records the exact
request, response, route, and receipt for the endpoint that served.


## Amendment: bounded reference scope and exceptional exits (#675, #676)

The AI-powered review packet selects a deterministic dependency closure from the frozen allowlist.
Retained packet relations, canonical payload dependencies, recorded findings and source-event
identities remain connected. Unrelated frontier IDs are counted as omitted, bound into the case
digest, and reported through partial `semantic_reference_scope_reduced` coverage. The local-check
case is not reduced. The existing envelope byte limit and independent disclosure policy remain in
force. Irreducible required structure fails before job/attempt creation with
`case_capacity_exceeded` and `semantic_case_capacity_exceeded` coverage. Narrowing scope creates new
work; it does not replay a terminal check or imply that the reduced packet reviewed the whole task.

A local finding wider than one case item's reference bound (16 subjects; findings may cite 64) is
not irreducible structure and does not fail the case (#858). The builder omits that finding's prose
and projected assessment as explicit `not_selected` omissions, declares
`semantic_case_finding_refs_over_limit` on packet, check, status and receipt coverage, and
dispatches the bounded case once. Subject references are never truncated to fit: a partial subject
list would misstate the finding's identity. The finding itself remains a complete local check
result and a citable `local_check_refs` entry.

Exceptional attempts retain a request-joined stage/category before cleanup. Dispatch entry is an
uncertain execution boundary; null provenance and missing diagnostics are not non-dispatch proof.
Provider-return, mapping and persistence faults remain distinct. Diagnostics cannot change retry
eligibility, durable-response recovery, cancellation or lease fencing. See `docs/INTERFACES.md` for
the public reason, coverage and owner diagnostic lookup contracts.


### Long external Codex reviews (2026-09-16, #496 / #746)

The external Codex evaluator defaults to 900 seconds and accepts explicit values from 1 to 3600
seconds. Existing explicit shorter values are preserved. Other provider kinds keep their existing
limits. The primary and optional fallback each retain their own frozen budget, with a combined
execution bound of 7200 seconds and five seconds of lease cleanup. Recovery never resets that clock
or mints a new provider request after authority was consumed.

A client wait timeout or disconnect leaves an admitted semantic check running under service
ownership. At most eight such checks can be retained; same-identity retries report pending, and a
changed body conflicts. An explicit attached control cancellation or service shutdown cancels and
joins the work. This does not introduce parallel semantic scheduling; structural phase progress
was added later (see the amendment below).

### Phase-aware Codex review budgets (2026-09-22, #571 item A1)

Each semantic check runs under exactly one closed **budget profile**. `final` applies when the
frozen case carries an effective, readable completion claim (`claim_kind=completion`, not
superseded by an ADR-025 correction); every other check is a `routine` checkpoint. The selection
is a pure function of the frozen projection, so the same case always selects the same profile.
It is frozen into the execution snapshot of the encrypted `SEMANTIC_CASE` object as the optional
`execution.budget_profile` key when the job is created. Every physical attempt, including
retries, disclosure-wait resume, and started-attempt recovery, dispatches under that frozen
value; changed configuration or a later claim cannot re-select it. Snapshots written before
this amendment lack the key and replay as `final`, which is the pre-amendment single-effort
behavior. The `yoetz.semantic-case/2` reader ignores unknown execution keys, so no case-schema
bump is needed. Unscoped credential probes also use `final`. Background observation advice uses `routine`
(issue #888); it cannot infer completion from a hook and must not consume a final-check budget.

The Codex subscription binding (`[external_runtime]`) expresses the two profiles separately:

- `reasoning_effort` (existing, required) is the final-profile effort.
- `routine_reasoning_effort` (optional). New setups write the bounded recommendation `medium`.
  When it is absent (every binding written before this amendment), routine checks keep the
  single configured effort. A persisted choice is therefore never silently lowered. Re-running
  setup keeps the existing binding's routine choice unless `--routine-reasoning-effort` is
  given. An explicit flag or setup-screen selection always wins.
- `routine_output_limit` and `final_output_limit` count output tokens. Both are bounded to
  1–8192 and default to 4096 and 8192. They are carried over when setup is re-run.

Readiness (`status`, setup) requires the exact model to list every configured profile effort. A
single attempt requires only the effort its budget selected, and a missing effort still fails as
`model_unavailable` before case disclosure.

The pinned app-server v2 protocol has no per-turn output ceiling. Yoetz therefore enforces the
selected output limit on the runtime's own cumulative `thread/tokenUsage/updated` counters.
Only visible output counts (`output_tokens − reasoning_output_tokens`), because reasoning
tokens are governed by the effort. The check is applied to each valid snapshot. A snapshot over
the limit interrupts the turn and ends it as the existing closed `output_oversize` stage
(`invalid/response_schema_invalid`). That outcome is content-shaped, like API-path
`max_output_tokens` truncation: it is never retried and never engages the fallback. When the
runtime reports no usage, the limit cannot be verified. The answer stays bounded by the
constrained output schema and the 1 MiB message cap, and the absent `token_usage` in the
evidence shows that the limit was not measured.

Provenance records the exact selection per check without a wire change:

- `semantic_provenance.model`
- `runtime_evidence.reasoning_effort` (the selected effort, no longer the binding's single
  value)
- `sampling_params.max_output_tokens` (the selected output limit, replacing the constant 2048
  that the Codex path previously copied from the API adapter and never enforced)

`runtime_evidence.selection_sha256` now commits to
`{"budget_profile","model","output_limit","reasoning_effort"}`. The profile name itself is
recorded only in that commitment. Markdown and text receipts name the model, effort, and output
limit beside the attempt usage. `yoetz provider status`, `yoetz provider codex-subscription
status`, the setup preview, and the terminal interface show both profiles.

The profile changes nothing else: deterministic checks, disclosure categories, the privacy
gateway, retention, provider authority, deadlines (#746), retry eligibility, and fallback
licensing are unchanged. A per-request override is not part of this amendment; a check can
request the final profile only by carrying a completion claim. API-provider endpoints keep
their existing fixed output limit. The installed Luna latency, output-size, and validity
comparison per profile needs a live provider and remains a separate #571 acceptance item.

### Structural review progress (2026-09-22, #571 A2)

A durable AI-powered review job exposes bounded structural progress through
`status view=operation`, and through every surface that renders that page (CLI text and JSON, the
MCP structured result and its text summary, and the terminal interface). The page's optional
`semantic_progress` object appears only for a pending or complete check with recorded progress.

The phase vocabulary is closed and ordered by actual execution: `queued`, `case_admitted`,
`runtime_starting`, `account_model_validation`, `provider_sampling`, `response_validation`,
`cleanup`, `terminal`. `case_admitted` is the privacy audit's consumption of the egress
authorization (the point after which no failure restores authority); because that happens before
a runtime-backed provider is launched, it precedes `runtime_starting`. Runtime-backed providers
(the Codex subscription runtime) report launch, account/model validation, turn acknowledgement,
turn completion, and process cleanup. Direct endpoints report `provider_sampling` immediately after
admission. The service reports `queued` when an attempt is claimed and `response_validation` when
a provider response returned for validation and recording. Phases a provider cannot observe are
skipped, never invented.

Progress is monotonic: within an attempt by that order and across retries by attempt ordinal, so
a retry restarts at `queued` with a higher ordinal. A resumed attempt that repeats an earlier step
does not move the phase backward; the stored phase stays at the furthest observed step until the
attempt advances past it or ends. Only the job's active attempt can write, and nothing can follow
the terminal state, which is derived from the terminal job row (outcome `succeeded`, `failed`, or
`quarantined` with its closed `SemanticReason`). `queued_at` and `deadline_at` are fixed when the
job's progress begins; `deadline_at` is the frozen total execution expiry, never a client wait, and
no replay resets either. The service derives `elapsed_ms`, `remaining_ms`, and `condition`
(`active`, `overdue`, or `terminal`) at one observation time so all renderings agree. `overdue`
means the deadline passed without a terminal row: the owner is finishing cleanup, or the service
restarted and a same-request replay will reclaim and terminalize the job.

The progress record carries no prompt, case or response text, token or delta text, token counts,
reasoning, credential, account identity, plan type, model output digest, or path. Provider code
reaches the store only through a task-local sink that accepts a closed phase value, bound by the
service to exactly one claimed attempt. Recording is advisory: a failed write is a bounded
diagnostic and never changes the attempt's outcome, retry, fallback, or provider authority.

A check holds the service's maintenance and observation gates for its whole lifetime. While a
service-owned check holds them, only `status view=operation` reads are admitted beside it; the
check closes that window and drains admitted readers before it releases the gates, so maintenance,
recovery, and observation sweeps remain excluded. Every other read still waits as before. A read
from a different session or writer of the same task can still receive retryable `BUNDLE_BUSY`.


### Background observation review admission (issue #888)

The shared service deduplicates advisory review by the stable advice-candidate identity: the set
of distinct candidates (kind, rule, next action, summary), the scoped coverage gaps and the packet
policy. It excludes the rolling observation stream digest, per-rule evidence counts and repeats of
the same candidate, so more evidence for an already-reviewed candidate reuses that review, while a
new candidate, changed next action or changed gap is new advice. The frozen packet retains its
original basis; its exact digest remains the disclosure subject. Reusing a review is only review of
those structural summaries, never evidence that later source was read.

A durable per-session admission interval defaults to 180 seconds and counts only attempts that
could have reached a provider (`authorization_missing` and `provider_unavailable` rows do not). A
refused admission writes no attempt, never claims review, and is reported with the coverage gap
`advice_semantic_deferred`; the service schedules an in-memory revisit that rebuilds advice when
the interval elapses, so the trailing condition is reviewed without waiting for another hook (the
next hook re-derives the same deferral after a restart). A terminal non-success is never sticky:
the same identity is re-admitted as `<identity>#<generation>`, keeping every earlier receipt, after
a backoff from its terminal time. `superseded` retries immediately; other pre-provider reasons
(`authorization_missing`, `provider_unavailable`, `queue_full`) wait the base interval, and
`authorization_missing` retries at once when the task route has become ACTIVE with repository
authority; provider-reaching failures double from the base interval up to 16 times it. The
admission interval still applies to every retry. Explicit checks retain their separate authority,
completion-profile selection and scheduling.

Background dispatch runs under the `routine` profile inside a background scope. The Codex adapter
then uses the owner's `routine_reasoning_effort` when set, and otherwise `low` when the configured
effort is a known effort above it (it never raises or rewrites an effort). API-key adapters
(`openai-responses`, `openai-chat-completions`) send no reasoning-effort parameter and already cap
output at 2,048 tokens, below the routine limit; the admission interval, deduplication, backoff and
disable switch are enforced before dispatch and therefore apply to every provider identically.

`observation.semantic_advice_enabled` can disable background scheduling and dispatch, including
rediscovered pending work. Re-enabling it on service restart permits pending work to drain.
`observation.semantic_advice_min_interval_seconds` accepts 1–86400. Both settings apply to Codex,
Claude Code and Cursor on macOS, Linux and Windows through WSL 2. This does not change capture
consent, deterministic advice, or the explicit check policy.

### Unassessable content and repair-first feedback (issue #885)

The existing `insufficient_packet` judgment is the nonblocking outcome when missing content
prevents assessment and readable evidence establishes no separate discrepancy. It has no
challenges and now adds `semantic_packet_insufficient` to committed check coverage: provider
execution succeeded, assessment coverage did not. Receipts retain insufficient coverage;
this outcome cannot resolve an existing semantic finding. Local checks remain independent.
A digest-only diff or an already reported capture gap alone must not become a new unsupported
claim finding. Concrete discrepancies grounded in readable evidence still produce challenges.

Reviewer and agent guidance require a specific authorized resolution attempt before accepting a
remediable limitation, or an explicit authority/dependency blocker. The existing obligation
requested-items and action-attempt records make the attempted work inspectable. `respond`
enforces the minimum: an `acknowledged` response to a `semantic_model_derived` finding must cite
in `evidence_refs` at least one evidence or result record first recorded or revised after the
finding frontier, otherwise it is rejected with `resolution_attempt_required` before any write.
A recorded failed or blocked result that names the authority blocker qualifies. Disputes
(`rejected`, `provenance_disputed`) are unchanged. Yoetz does not judge whether the attempt was
adequate, and `respond` still records only a disposition and never clears a finding. No provider
schema or new finding kind is required.
The same prompt, result commitment and guidance serve all hosts and supported operating systems.


### Capture baseline for semantic finding resolution (issue #884)

A completed semantic re-review with a recorded assessable conclusion may resolve an absent issue
while retaining the original readable finding's closed native capture limits:
`content_unselected`, `content_capture_unavailable`, `captured_object_unavailable`,
`host_outcome_unavailable`, `unpaired_event`, and `semantic_case_content_over_item_limit`
(recorded clipping of an oversized item). Only codes already present on that original finding are
tolerated. The check stamps the capture limits its review ran under onto every semantic finding it
raises, so the baseline is durable finding coverage rather than the deterministic case alone.
Original and current freshness must be readable (`current` or `partial`); `redacted_gap` is
accepted only when a tolerated `captured_object_unavailable` explains it, mirroring the local
host-limited exception. New gaps, redacted or missing ledger payloads, stale/unknown state,
withheld reviewer context, dropped challenges, reference-limit clipping, an `insufficient_packet`
answer, failed review, suppression, scope mismatch and a returned issue still prevent resolution.

A semantic finding also resolves only over state that changed materially after it was recorded
and at or before the tested frontier: a new or revised readable action, result or evidence record,
or a revision of a claim or obligation the finding names (including a superseding claim). A
reviewer that merely does not repeat an issue over unchanged state proves nothing, so a re-roll
never resolves a finding (`no_material_change_since_finding`). Yoetz cannot bind arbitrary new
evidence to reviewer prose; this requires new work, not proof of its relevance, and the later
review must still complete without returning the issue. Local findings are unaffected. Local
re-derivations reuse only local finding IDs, so a same-subject semantic row can no longer be
rewritten as a local one.

This is absence of a previously raised issue under the same bounded coverage, not proof of
unseen code correctness. Coverage gaps remain verbatim and keep the receipt insufficient.
Acknowledging or accepting a limitation alone never resolves a defect. Coverage-only review
outcomes use `insufficient_packet`; `ledger_stale_or_incomplete` remains the existing nonblocking
finding kind. Do not retroactively relabel a historical unsupported-claim finding by matching
its prose. Actual defects and omitted material limitations remain actionable until qualified.
The same pure replay rule drives all hosts' CLI, MCP, status and receipt projections on every
supported OS. Legacy findings with unreadable original proof stay explicitly unresolved.


Check event version `1.3.0` records `semantic_conclusion` for succeeded provider attempts. Its
closed values are `no_material_discrepancy`, `challenges_returned`, and `insufficient_packet`.
Older event bytes stay unchanged and read with no conclusion. Only a recorded assessable
conclusion enables the capture-baseline exception; legacy succeeded status alone is insufficient,
because older engines could collapse an unassessable answer to zero findings without a gap.
Failed/local-only checks retain their prior event shape. Public check-result schemas are unchanged.


### Repair evidence selection for rechecks (2026-09-28, #884)

A response to a readable, unresolved, in-scope finding establishes a relevance link for its
readable evidence, including evidence named by one directly linked result. Both captured-object
preselection and final excerpt selection prioritize that repair evidence before other linked
material. The finding, response event, optional result and evidence must remain inside the frozen
allowed-reference fence. Redacted/missing inputs and resolved findings cannot establish this link.
This changes relevance, not proof: every capture still requires authentication, each excerpt still
obeys the selected privacy profile and byte/count limits, and a response never clears a finding.
An insufficient packet or a deterministic-only recheck still cannot resolve a semantic defect.
The same selection applies to all hosts and supported OSes.


### Converging review dialogue (2026-09-30, issue #905)

The reviewer is a verifying partner the main agent converses with; a recheck must be able to
change finding state, and the findings list must behave like a todo list that ends. Rechecks stay
uncapped: they surfaced most real defects.

**Every distinct problem, no answered re-raise.** The shared instruction asks for one challenge per
distinct material problem up to `MAX_REVIEW_CHALLENGES` (unchanged at 3), never only the most
important one. It forbids raising again a finding the main agent answered, or requesting an action
the packet shows was done, unless material newer than the response shows the problem remains; the
re-raise then cites that material and the earlier finding's `fnd_` id. Every provider cell sends
the same instruction text; the Chat Completions cell only appends its output-shape suffix.

**Citable prior findings.** A cited `fnd_` resolves to that finding's subject refs when it is one
of this check's local findings or a readable recorded finding inside the frozen fence. Dropping a
challenge that follows the prompt was a fence mismatch, not a reviewer error; the fence is not
loosened otherwise, and an unreadable or unknown finding id still drops its challenge. A challenge
whose resolved subjects exceed one finding's 64-subject bound is dropped and counted
(`subject_refs_over_limit`) instead of failing the check.

**Advisory rejections.** `weak_or_stale_response` is minted only for local findings, matching
`questionable_finding_rejection`. Rejecting an AI-powered finding without evidence no longer adds a
local receipt-blocking finding; the rejection is judged by the next review.

**The dialogue record.** The reviewer keeps no memory between checks, and a persistent provider
thread would be provider-specific and unauditable, so the ledger carries the dialogue. An
AI-powered finding records the reviewer's remaining challenge fields (`discrepancy`,
`alternative_interpretation`, `requested_next_step`, `uncertainty`) and a `relates_to` link to the
earlier recorded findings its challenge cited, as optional fields of the unreleased
`finding_recorded/1.3.0` (extended in place). Local findings and rows written by earlier 0.3
builds keep their bytes and still validate; the public finding wire is unchanged.

**The prior-findings section.** The review packet (`outbound-case/1.2.0`) carries the newest
unresolved AI-powered findings in their own `prior_finding` section, outside the 64-row timeline so
hook rows never crowd it out. Under egress-envelope pressure its rows yield first, oldest finding
first, so it never displaces work content: a structural row with what was asked, the latest answer
(disposition, cited refs) and the evidence and results recorded after the finding, plus the prose
rows the profile's finding-prose selection already permits. It is bounded (8 findings, 48 KiB,
leftover case capacity only); what does not fit is named as `not_selected` omissions and discloses
`semantic_prior_findings_over_limit`, a gap that stays on the receipt but does not veto absence
proof. Findings recorded without challenge fields degrade to summary and message with a `not_recorded`
omission. No new data category leaves the machine.

**Per-finding rulings.** `provider-judgment/1.1.0` adds a required `prior_finding_verdicts`
array (at most 8) to every conclusion branch: `{finding_id, verdict, cited_refs, note}` with
`verdict` one of `fixed`, `still_present`, `answered_not_fixed`, `unassessable`, `withdrawn`.
The note is turn-local reasoning and is never recorded. A reply without the array reads as the 1.0.0
shape with no rulings, so a local model or prompt-only host that has not adopted it still gets its
challenges read, and a malformed or surplus ruling is dropped and counted rather than failing the
review. Post-validation keeps a ruling only for a readable, unresolved AI-powered finding inside
the frozen fence, and trims its cited refs to the packet's `citable_refs`. A ruling on such a
finding whose prior-findings row the packet did not carry (past the row cap, or removed by envelope
bounding) is kept as `unassessable`, never dropped to silence. What a ruling may claim is bounded by what it still cites: `fixed` must cite
evidence or a result recorded after the finding (a hallucinated `fixed` must not close a real
defect); `still_present` and `answered_not_fixed` must cite material; `withdrawn` accepts only a
readable `rejected` response. A ruling that loses a cited ref or fails its claim is kept as
`unassessable` for that finding, so a bad ruling can never leave its finding to close by silence;
two different rulings on one finding, or `fixed` on a finding an admitted challenge of the same
review re-raises, become `unassessable`. Every dropped, trimmed, reduced or repeated ruling adds
`semantic_prior_verdicts_unsupported` (disclosed, not a veto on other findings). Admitted rulings
are recorded on the check as the optional `prior_finding_verdicts` field of the unreleased
`check_recorded/1.3.0` (extended in place), present only when at least one ruling was admitted.

A `fixed` ruling lets that finding resolve on that check even when the packet as a whole concluded
`insufficient_packet`: the whole-packet veto and its `semantic_packet_insufficient` marker no longer
block a finding the reviewer judged on newer material. Every other rule still applies: completed
review, the finding inside the tested frontier, no suppression, scope, readable freshness, the
capture baseline, a material change after the finding, and the issue not returned again.
`withdrawn` keeps the earlier absence rules and never lifts the whole-packet veto; on a finding
whose latest readable response is a reasoned `rejected`, the check that rules it `withdrawn` records
`rejection_accepted` rather than resolution (below). `still_present`,
`answered_not_fixed` and `unassessable` block only their own finding by name
(`reviewer_verdict_<verdict>`). Without a ruling the earlier rules are unchanged; silence is never
read as `fixed`. Silence also proves nothing when the finding may never have been assessed: on a
check whose packet left prior findings out (`semantic_prior_findings_over_limit`, including a
selection without the assessments section) or dropped a ruling
(`semantic_prior_verdicts_unsupported`), every AI-powered finding the check recorded no ruling for
is blocked as `reviewer_assessment_incomplete`. The codes stay disclosures, never vetoes on ruled
findings. A ruling on an item that is already final, or one contradicted by the same review's
restatement, is set aside without that gap (a diagnostic count only), so it cannot stall the
review's other open findings.

**Terminal states: findings as a to-do list that ends.** Every recorded finding is in exactly one
to-do state, read from replayed projection facts only (`kernel/finding_todo.py`, transition table
kept as data): `open`, or one of three terminal states. `verified_resolved` is the existing
proof-based resolution. `acknowledged_not_done` is a new `respond` disposition, defined here for
the whole issue set: the agent states that it will not do what the finding asks, with a required
non-empty reason. `rejection_accepted` latches when a later review rules `withdrawn` on an
AI-powered finding whose latest readable response is a reasoned `rejected`. When that same review
would also prove the finding absent (assessable, over changed state, not returned again), the
explicit ruling takes precedence over the implicit not-returned inference: the reviewer accepted
the agent's reason, it did not observe a repair, so the item is `rejection_accepted` and that
check's absence mark is dropped. A finding an earlier check already proved absent stays
`verified_resolved`. The optional
`superseded` state is not introduced: a successor row (#458) already starts `open` beside the
resolved row it follows.

Terminal is final. There is no reopen and no upgrade: `respond` records nothing on a terminal item
and refuses with the typed reason `finding_terminal` (an exact replay of an earlier respond is still
the idempotent stored answer); a latched `rejection_accepted` never later becomes resolved; an
`acknowledged_not_done` row never reads as resolved, even if a later check proves the issue absent.
New evidence about the same problem becomes a new finding. The only thing that clears a latch is
redaction of the event that set it, because unreadable proof is no proof. A terminal item is never
re-reviewed: it leaves the prior-findings section, and a ruling on it is not admitted.

On the receipt, `acknowledged_not_done` keeps counting as receipt-blocking, so it can never read as
clean, and has its own section ("Acknowledged, not done"). `rejection_accepted` stops blocking but
stays disclosed in its own section ("Rejection accepted"). Both are named by finding id only; how
the receipt conclusion names them is owned by issue #913. Using `acknowledged_not_done` to shorten
a receipt is therefore impossible by construction.

**Review rounds and the owner's budget.** Each later recorded check that assessed an item and left it
open adds one review round: a local check that returned the same finding over a later subject, or a
review that ruled it `still_present`, `answered_not_fixed` or `unassessable`. The owner's
`verification.finding_attempt_budget` (default 5, 1–50; maintainer decision 2026-09-30) only
changes what Yoetz asks next: at the budget it asks for a repair with new evidence or an explicit
`acknowledged_not_done`. It never throttles `check`, never closes, and never acknowledges on the
agent's behalf. The check result's `finding_checklist` and the status findings view carry each
item's state and rounds, and a closed `next` token, for a checklist such as
`[ ] F-3 open (2/5) | [x] F-1 verified_resolved | [~] F-2 acknowledged_not_done`.

**Stable identity: seen again, suppressed.** An item's key is its origin, kind and subjects; its
evidence fingerprint is what it rests on. A challenge with the kind of a recorded AI-powered finding,
exactly that finding's subjects, and nothing among them recorded after that finding restates it (a
narrower or wider challenge is a distinct issue, minted with its own discrepancy and next step): no second row is minted and the check discloses `semantic_restatements_suppressed`, so three
identical re-raises remain one item with one state. Suppression must never read as absence, so on an
open item the check records the restatement as a `still_present` ruling (a contradicting `fixed` or
`withdrawn` becomes `unassessable`), which also counts a review round; an `acknowledged_not_done` or
`rejection_accepted` item needs nothing recorded and stays disclosed. A `verified_resolved` row is
never a restatement target: done stays done, and the problem raised again after that proof is a #458
successor, minted and blocking. A challenge that cites newer material is a new item, linked to the
earlier one when it cites it. This is the ledger-side half of "no re-raise
without new material"; the prompt asks for the same.

**Compatibility.** `response_recorded` 1.0.0 and `respond-request`/`respond-result` 1.0.0 are
released, so the new disposition rides new 1.1.0 versions (every other disposition keeps 1.0.0
bytes). Control 2.9.0 (unreleased) moves to the respond 1.1.0 pair in place. Every earlier control
version keeps the 1.0.0 pair (v0.2.5 ships control up to 2.6.1; 2.7.0 and 2.8.0 are earlier 0.3
builds), so an older service refuses the new disposition at its own schema boundary. Old ledgers
replay to the same resolution and receipt outcomes: nothing they contain is `acknowledged_not_done`
or `withdrawn`, and no earlier check carries the #905 packet gaps. The projection does derive one
new fact from them: `review_rounds` counts a local finding each later check returned again over a
later subject, so an old ledger's projection snapshot can now carry `review_rounds`. It feeds only
the checklist and the budget's `next` token.
