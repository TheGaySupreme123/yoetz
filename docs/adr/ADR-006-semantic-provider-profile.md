# ADR-006 — Semantic provider profiles behind the privacy gateway

**Status:** Working decision revised 2026-07-16. Ratification requires the privacy/egress gates in
ADR-009 plus recorded capability fixtures against every advertised provider/model/endpoint profile.
**Implemented by:** `src/yoetz/ports/semantic.py`,
`src/yoetz/ports/privacy.py`, `src/yoetz/application/egress.py`,
`src/yoetz/application/check.py`, `src/yoetz/adapters/providers/`, `src/yoetz/config/`,
and semantic/privacy capability and conformance tests.

## Decisions

1. **No direct provider path:** application, CLI, MCP, plugin, and integration code cannot call a
   semantic provider. A candidate semantic context must traverse ADR-009's classification, policy,
   local minimization/redaction/secret scan, exact prepared-case approval when required, durable
   authorization, outbound gateway, and privacy-audit path. A provider adapter receives only an immutable
   `ApprovedOutboundCase`; composition supplies no repository, bundle, transcript, environment,
   log, database, keyring, or application-state handle.
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
   invalid semantic result, never a fabricated pass. None of the five inherits official OpenAI
   data-use or `assisted` eligibility; each carries an unknown data-use record until a reviewed one
   exists. Being dispatchable is not being verified: advertising any of them as a working endpoint
   still requires the exact model/endpoint capability fixture and live evidence E-007 names.
3. **Local-model adapter:** v0.1 includes the contract for a separately configured local semantic
   evaluator. Its endpoint is an owner-only, service-approved AF_UNIX socket profile; it performs no
   DNS, AF_INET/AF_INET6 connection, redirect, proxy lookup, or fallback. It is a local disclosure
   sink, not network egress, but still traverses classification, minimization, never-send scanning,
   and local privacy auditing. A release advertises it only for exact model/endpoint profiles that
   pass capability fixtures.
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
   all within one total deadline and one durable semantic operation. One durable attempt and one
   privacy receipt, SDK client, custom transport, and credential handle are created per physical
   dispatch. For `confirm_every_request`, each physical retry also requires a fresh exact foreground
   preview/decision and a new one-dispatch proposal; the original human decision cannot cover a
   hidden multi-attempt budget. Crash/resume before authorization consumption remains the same
   attempt. Automatic profiles may retry within their existing policy/total deadline without a
   human prompt, but never reuse authorization or attempt identity.
6. **Required semantic means verdict completeness, not operation availability:** deterministic
   freeze and deterministic results always survive. With `semantic_required`, missing approved
   capability, privacy-policy block, human denial or approval expiry, provider refusal, timeout,
   invalid output, exhausted retry, late response, or stale response completes the check with
   `verdict=incomplete_check`, no semantic findings, and the exact closed
   `(SemanticStatus, SemanticReason)` pair. It does
   not fail the operation or discard deterministic findings. `semantic_if_configured` may complete
   with its deterministic verdict when semantic capability is absent or policy-disabled, while an
   attempted but unsuccessful semantic evaluation is represented honestly in status and coverage.
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
   v0.1 semantic contract.
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
    user selects and confirms an exact provider, workspace scope, categories, classes, and limits.
    The safe installation seed remains zero-egress `local_only`; a recommendation is never implicit
    consent.
12. **The recommended packet is rich but problem-local:** `assisted` contains the task goal,
    obligations, current completion/material claims, accepted decisions, a material ordered
    timeline, deterministic findings and their machine-readable bases, change-observation facts,
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
13. **Reviewer output talks to the main agent through the existing workflow:** a successful model
    judgment may propose bounded `ReviewerChallenge` values. Each challenge names only case-bound
    refs, explains the discrepancy, states an alternative interpretation, addresses the main agent
    directly, and requests the smallest next step: act, provide evidence, revise the claim, dispute
    with evidence, or state an unresolved limitation. Post-validation maps an accepted challenge to
    the existing semantic `Finding.summary/detail`; the main agent uses the existing `respond` and
    `publish_work` operations, then runs `check` again. There is no provider-driven fetch loop, new
    event family, seventh public operation, or model waiver authority.
14. **Recommendation eligibility is evidence-bound, not a brand promise:** every installed external
    endpoint profile carries a versioned data-use record stating customer-content training use,
    retention posture, provider-human-access posture, review/expiry times, and an evidence digest.
    The upstream `assisted` badge requires a current record with training `prohibited`, retention
    `none|bounded`, and provider human access `prohibited|restricted`.
    The recommended recipe also sets an editable
    `require_current_provider_data_use_evidence=true` runtime guard. Yoetz does not technically
    prove provider behavior. Unknown, known-broad, or stale status removes the recommendation badge
    and trips that guard; an informed user may explicitly turn the guard off through a custom policy,
    and a fork may change the rule without inheriting upstream privacy/support evidence.

## Review packet and agent loop

```mermaid
flowchart LR
    A["Main agent publishes goal, work, evidence, and claim"] --> B["Deterministic checks build findings plus exact bases"]
    B --> C["Context profile selects timeline and problem-local recorded excerpts"]
    C --> D["Privacy policy classifies, minimizes, scans, and authorizes"]
    D -->|"authorized"| E["Reviewer model returns a bounded outcome"]
    E --> H["Terminal provider-attempt or local-model privacy receipt"]
    H -->|"valid structured judgment"| F["Post-validation creates ordinary semantic findings"]
    H -->|"refusal, invalid, timeout, or unavailable"| K["Record semantic gap and keep deterministic result"]
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

## Deterministic fencing

The semantic case is built from a frozen frontier and dependency digest. It carries separate
`frontier_refs` (IDs present at the frozen frontier) and `local_check_refs` (deterministic finding
IDs allocated and durably pinned by this check); their union is bound into the case digest. This
lets the reviewer discuss deterministic findings without pretending those post-frontier IDs were
already in the ledger. Every deterministic finding carries a paired `FindingBasis` containing the
rule ID, triggering observed facts, required-but-missing facts, subject-state relation, source
availability, coverage gaps, and bounded supporting refs. Later disclosure-time
`ChangeObservation` and content-visibility facts remain separate. `same`, `different`, and
`unknown` retain their exact three-valued meaning; hidden or unrecorded source is never represented
as `same`.

Approval is bound to the exact minimized case digest, provider/model/endpoint profile, purpose,
scope, policy version, and one dispatch. The provider is called outside every SQLite transaction.
Post-validation rejects invented IDs, out-of-case quotes, coverage upgrades, deterministic-status
claims, challenges without a material discrepancy or requested next step, and stale frontiers.
Rejected output never projects a finding.
