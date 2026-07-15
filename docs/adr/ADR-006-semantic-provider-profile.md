# ADR-006 — Semantic provider profiles behind the privacy gateway

**Status:** Working decision revised 2026-07-14. Ratification requires the privacy/egress gates in
ADR-009 plus recorded capability fixtures against every advertised provider/model/endpoint profile.
**Owning public specs:** `specs/src/yoetz/ports/semantic.md`,
`specs/src/yoetz/ports/privacy.md`, `specs/src/yoetz/application/egress.md`,
`specs/src/yoetz/application/check.md`, provider-adapter specs, privacy configuration specs,
and semantic/privacy capability and conformance tests.

## Decisions

1. **No direct provider path:** application, CLI, MCP, plugin, and integration code cannot call a
   semantic provider. A candidate semantic context must traverse ADR-009's classification, policy,
   local minimization/redaction/secret scan, exact prepared-case approval when required, durable
   authorization, outbound gateway, and privacy-audit path. A provider adapter receives only an immutable
   `ApprovedOutboundCase`; composition supplies no repository, bundle, transcript, environment,
   log, database, keyring, or application-state handle.
2. **First external adapter:** official `openai` Python SDK (pinned `2.45.0`), Responses API with
   structured outputs (`responses.parse` + frozen `ProviderJudgmentModel` schema). A release names
   an exact tested provider/model/endpoint-profile tuple. A generic or merely
   "OpenAI-compatible" URL is never trusted; an alternate endpoint requires its own exact,
   versioned, executable capability profile.
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
   profile+version, purpose, dispatch ID, final request-body digest, service generation, and
   deadline. Only the custom HTTP transport may consume it through a one-shot header-injection
   callback; the adapter and SDK never receive or retain reusable credential bytes. Under resolved
   decision F-012, the custom transport necessarily sends that separately
   provisioned credential as one-attempt authentication metadata to the exact pinned TLS endpoint,
   never as candidate/model content.
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

## Deterministic fencing

The semantic case is built from a frozen frontier and dependency digest. Approval is bound to the
exact minimized case digest, provider/model/endpoint profile, purpose, scope, policy version, and
one dispatch. The provider is called outside every SQLite transaction. Post-validation rejects
invented IDs, out-of-case quotes, coverage upgrades, deterministic-status claims, and stale
frontiers. Rejected output never projects a finding.
