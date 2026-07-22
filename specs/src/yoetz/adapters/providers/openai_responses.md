# src/yoetz/adapters/providers/openai_responses.py — native OpenAI Responses semantic adapter

**Wave:** E | **ADRs:** ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`ports/semantic.md`, `ports/clock.md`, `ports/secret_memory.md`, `domain/privacy.md`, `domain/findings.md`,
`protocol/errors.md`
**Imported by:** `adapters/privacy/gateway.md` and semantic capability tests

## Purpose

This file implements the native semantic-evaluation adapter for the approved OpenAI profile. It is
the live provider bridge that turns an already approved outbound case into a structured judgment and then
normalizes it into Yoetz’s closed semantic-result union with provisional
`ProviderAttemptProvenance`. It never manufactures final receipt-bound provenance.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `OpenAIResponsesEvaluator` | implementation of `SemanticEvaluatorPort` for the native OpenAI profile |
| `OpenAIProfile` | frozen exact nonsecret model/endpoint-profile/capability identity |
| imported `ProviderDataUseProfile` | domain-owned versioned training/retention/human-access metadata bound to the endpoint profile |
| `RenderedOpenAIRequest` | exact final application JSON body bytes plus body SHA-256 and nonsecret profile/dispatch binding |
| `render_case(case)` | deterministically convert an approved case into `RenderedOpenAIRequest` |
| `OneAttemptCredentialTransport` | adapter-private custom HTTP transport that consumes one bound credential handle for one request |
| `validate_openai_credential(view)` | byte-exact offline validator used inside confidential vault storage |
| `OPENAI_CREDENTIAL_MIN_BYTES`, `OPENAI_CREDENTIAL_MAX_BYTES` | exact values `16` and `512` |
| `OPENAI_MAX_OUTPUT_TOKENS` | exact v0.1 profile value `2048` |
| `OPENAI_MAX_RESPONSE_BODY_BYTES` | exact v0.1 wire/decompressed cap `1_048_576` |
| `normalize_response(response, profile)` | turn an OpenAI response into a Yoetz semantic result |
| `normalize_judgment(...)` | validate the parsed judgment against Yoetz’s closed judgment shape |
| `classify_provider_failure(...)` | map transport/profile/provider failures to public error classes |

## Behavior

`OpenAIResponsesEvaluator` is constructed only behind the privacy gateway for one physical
attempt. The gateway supplies the approved case, an injected `ClockPort`, plus a
`OneAttemptCredentialTransport` already
holding a fresh opaque `ProviderCredentialHandle` and the gateway-precomputed final-body SHA-256
and privacy commitment; the evaluator API exposes neither handle, and the reviewed bundled implementation does
not introspect the transport. This is the in-process F-009 trust boundary, not resistance to
malicious Python code. It constructs and closes one `AsyncOpenAI`
client configured with:

- explicit timeout;
- `max_retries=0`;
- the service-resolved endpoint of the exact installed endpoint profile;
- the selected model identifier;
- a fixed public nonsecret API-key sentinel required only by SDK construction; and
- the one-attempt custom HTTP transport with `trust_env=False`, no proxy argument/support, no
  netrc/environment authentication, no cookie jar, and redirects disabled.

The real credential never enters the `AsyncOpenAI` object, its default headers, `OpenAIProfile`,
provider request model, environment, CLI/MCP, config, logs, or application values. A reusable or
long-lived SDK client with the real API key is forbidden.

The coordinator owns the retry budget and durable attempt identity; the adapter makes one physical
provider call per `evaluate(...)` invocation.

### Credential byte contract

For this exact provider profile, a credential is 16 through 512 bytes and matches the HTTP Bearer
`token68` byte grammar: one or more ASCII letters, digits, `-`, `.`, `_`, `~`, `+`, or `/`, followed
by zero or more trailing `=` bytes. `=` anywhere before the trailing suffix is invalid. Empty,
non-ASCII/UTF-8 multibyte input, space/tab, leading/trailing whitespace, CR, LF, NUL, DEL, every
other control, colon, and every other punctuation byte are rejected. The validator does not require
or infer an `sk-` prefix and does not test the credential against a network provider.

`validate_openai_credential(view)` scans the protected `memoryview` without converting it to
`str`/immutable `bytes`, trimming, Unicode decoding/normalization, case changing, prefix repair, or
logging. It returns no transformed value: the exact accepted bytes are encrypted in the vault. A
failure yields only bounded `credential_invalid`, consumes/overwrites the ingress handle, persists
nothing, and never exposes length, invalid offset/byte, prefix, or input. The generic confidential
protocol may impose a broader transport cap, but the selected adapter profile owns this narrower
content rule.

### Construction and profile validation

`OpenAIProfile` is a nonsecret identity object. It binds the exact provider identity and capability
profile Yoetz is willing to trust. Construction validates:

- a non-empty exact provider/model name;
- explicit timeout bounds;
- explicit endpoint profile identifier and version;
- capability flags that allow the exact JSON schema subset Yoetz expects;
- one exact current `ProviderDataUseProfile` (customer-content training / retention /
  provider-human access / review-expiry / evidence digest);
- HTTPS destination: host + port (defaults `api.openai.com:443` for the official preset;
  owner-declared profile `owner-declared-openai-responses` supplies host/port from validated
  config `https_origin` per ADR-014) with profile-fixed path `/v1/responses` or the reviewed
  Fireworks `/inference/v1/responses`, `POST`, platform CA
  trust, and hostname/SNI verification — no free user URL, proxy, or redirect, and no v0.1
  certificate/SPKI pinning claim;
- exact `max_output_tokens=2048`, raw response-body cap `1_048_576`, and identity-only content
  encoding.

`owner_declared_data_use_profile(...)` builds the unknown training/retention/human-access record
that never passes `recommendation_eligible`. `OneAttemptCredentialTransport` checks the request
destination against the bound host/port/path (not only a module-global official host).

The adapter fails fast if the profile claims structured outputs but does not actually support them
in the tested environment. An ambient “OpenAI-compatible” base URL is not accepted; the only
alternate is the exact owner-declared profile kind above.

The data-use record is inspectable recommendation evidence, not a provider capability inferred from
the name and not a technical no-training proof. Upstream `assisted` eligibility requires a current
record with training `prohibited`, retention `none|bounded`, and provider human access
`prohibited|restricted`. Known-broad, unknown, expiry, or evidence change removes the badge and
fences runtime only when the explicit current-evidence guard is true; a trusted `custom` policy may
turn that guard off without inheriting the recommendation claim.

### Approved-case rendering

`render_case(case)` accepts only the external `ApprovedOutboundCase` variant and transforms its already-approved bytes
into the exact provider request envelope and canonical UTF-8 JSON body bytes. It must not select,
minimize, summarize, redact, or add
content. The rendering steps are:

1. verify the approved case's provider/model/endpoint/profile/schema binding;
2. copy only its authorized canonical payload bytes into the fixed request field;
3. preserve the case’s frontier and dependency digest so the provider cannot accidentally answer
   against an older state;
4. reject any attempted provider-specific enrichment;
5. recheck the rendered size equals the authorized size ceiling;
6. attach the prompt/policy/schema digests so provenance can be reconstructed later;
7. set request `max_output_tokens` to exactly `2048` and `Accept-Encoding: identity` in the fixed
   nonsecret request metadata;
8. render the final application body with the frozen encoder and compute its internal
   `sha256:<hex>` body digest. The gateway independently checks the body contains only approved
   logical bytes plus fixed reviewed fields, runs the final exact-body scan, and computes the sole
   privacy commitment before transport construction.

Composition supplies the renderer no repository, bundle, transcript, environment, log, database,
keyring, or application-state handle. Minimization is complete before this adapter is called. This
reviewed bundled in-process module is trusted to honor that API boundary; v0.1 does not claim an OS
sandbox against malicious Python code.

### Reviewer prompt and structured output

The system instruction is versioned and semantically equivalent to the following stable template:

> You are a bounded reviewer helping the main agent complete the user's stated goal. Review only
> the supplied packet. Distinguish agent claims, deterministic observations, and unavailable
> content. Never say no code changed merely because no source excerpt was disclosed. Compare the
> completion claim with the goal, obligations, decisions, ordered timeline, deterministic finding
> bases, state/change observations, evidence freshness, failures, limitations, and selected
> excerpts. If a material discrepancy exists, address the main agent directly, explain the
> discrepancy and strongest plausible alternative, cite only supplied refs, and request the
> smallest resolving action or evidence. Do not invent repository facts, fetch more context,
> overrule deterministic results, waive findings, or claim stronger coverage than the packet.

The user payload is canonical structured data, not a concatenated transcript. Its top-level
sections are `goal`, `obligations`, `claims`, `decisions`, `timeline`,
`deterministic_assessments`, `change_observations`, `coverage`, `targeted_excerpts`, and
`omissions`. The renderer obtains those sections only from the validated outbound case's
`review_packet` item-ID index and verifies its `review_selection_digest`; it never reconstructs
section meaning from generic item order. `structural` omits all user prose; `goal_aware` adds intent/claim prose; `assisted`
adds bounded problem-local recorded evidence/test/failure/diff/source excerpts; `expanded|custom`
may include a broader explicitly approved recorded set. The provider sees only approved structural
omission entries, never omitted bytes. A newly detected never-send match blocks before this request
exists; `redacted_never_send` can appear only as a pre-existing ledger redaction marker with no
forbidden bytes available to the adapter.

The structured output schema contains exactly `conclusion:
no_material_discrepancy|challenges_returned|insufficient_packet` and `reviewer_challenges` with at
most `MAX_REVIEW_CHALLENGES` entries. Challenges are the sole candidate-producing shape. Each has
one registered finding kind, bounded summary, case-bound `cited_refs`, discrepancy, alternative
interpretation, `message_to_main_agent`,
`requested_next_step` (`act|provide_evidence|revise_claim|dispute_with_evidence|
state_unresolved_limitation`), and uncertainty. It has no context-request/fetch/tool field and no
free-form assistant transcript. The normalizer/post-validator resolves cited refs to canonical
frozen event/obligation/claim roots and maps an accepted challenge into the existing semantic
finding summary/detail.

Illustrative inputs differ only by approved packet content:

- `structural`: “edit claimed; state relation unknown; excerpt not recorded; tests reported
  success” — the model may ask for state-bound evidence but may not assert no diff;
- `goal_aware`: adds “goal: preserve JSON output” and the completion claim, so the model can detect
  an omitted obligation without source;
- `assisted`: additionally includes the linked changed hunk, enclosing symbol, failing-test excerpt,
  and deterministic basis, so the model can challenge the exact mismatch;
- `expanded|custom`: may include more recorded in-scope excerpts only when their categories/classes
  were explicitly authorized.

### One-attempt credential transport

The SDK request must carry body bytes exactly equal to `RenderedOpenAIRequest.body`. The custom
transport inspects the prepared request before any DNS/connect/write and rejects a byte or digest,
precomputed commitment, method, exact HTTPS scheme/host/port/path, endpoint profile/version,
platform-CA/hostname/SNI verification posture, dispatch, or deadline mismatch. It does not claim
certificate or SPKI pinning. It ignores poisoned
`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, netrc, credential, certificate, and transport
environment/config; any proxy, redirect, URL rewrite, non-HTTPS destination, or alternate resolved
request target fails before credential exposure. It strips the SDK's fixed nonsecret
`Authorization` header, equality-checks the actual body SHA-256/bytes against the gateway's
precomputed digest and separately supplied request commitment without recomputing HMAC, and calls
`ProviderCredentialHandle.authorize_attempt(binding, inject_and_start)`.
That commitment belongs to the one-attempt transport state, not to the
`ProviderAttemptAuthBinding`, whose closed fields include the exact authorization-scope and purpose
digests and end at body digest, service generation, and deadline.

Inside that callback only, the transport uses the protected credential view to inject the one bound
authentication header and start the request. It may not place credentials in query/body/cookies,
change the body, redirect, retry, or retain the view. The callback returns immediately after header
injection/request start; the view is then released/overwritten best-effort while the response may
continue. Authentication metadata, HTTP/TLS framing, and transport-generated fields are excluded
from `request_commitment`. A body generated differently by the pinned SDK is a capability-profile
failure, never grounds for committing different bytes or rebinding the handle.

The transport sends `Accept-Encoding: identity` and rejects any other nonempty `Content-Encoding`.
It rejects a declared `Content-Length` above `1_048_576` before reading, counts chunked/streamed raw
bytes, aborts at cap+1, and never hands an oversized or compressed body to the SDK parser. The same
`1_048_576` cap applies after any future explicitly reviewed decoding path; v0.1 performs none.
Oversize/encoding violations normalize to bounded invalid/unavailable status with structural
`raw_size=1_048_577`, retain no body/prefix, and cannot influence semantic findings.

### Response normalization

`normalize_response(response, profile)` classifies the provider result into the closed result union.
The classification must inspect the provider response in a strict order:

1. explicit refusal surface first;
2. deadline/cancellation conditions next;
3. parse/schema validity next;
4. late-arrival state last.

If the provider returned a parseable judgment, the function wraps it in the success variant only
after a strict judgment normalization pass. If the response is invalid or truncated, the function
returns the invalid variant and records the raw size only through the coordinator’s encrypted audit
path.

If the provider response arrives after the coordinator has already lost lease authority, the adapter
or coordinator classifies it as late. A late response never becomes a success result even if the
judgment content looks plausible.

### Judgment normalization

`normalize_judgment(...)` validates the parsed judgment against the contract:

- allowed finding kinds only;
- bounded summaries and details;
- allowed cited IDs only, with deterministic resolution to public subject roots;
- no coverage inflation beyond what the case supports;
- no novel conclusion vocabulary outside the approved set;
- no invented provenance or request ID;
- no overlong arrays or recursive structures.
- challenge refs are within `frontier_refs ∪ local_check_refs`, every challenge has a supported
  discrepancy/direct main-agent message/closed next step, and missing/hidden source is never
  normalized into unchanged source;

The judgment remains untrusted until the coordinator’s deterministic post-validation accepts it. This
module only ensures that the model output is structurally compatible with a Yoetz judgment.

### Failure classification

`classify_provider_failure(...)` maps the native provider failure to the public error taxonomy while
avoiding raw exception text. The classification should distinguish:

- authentication or authorization failure;
- rate limit / quota exhaustion;
- timeout / deadline expiry;
- network or connection failure;
- unsupported profile / malformed schema support;
- internal provider outage.

The adapter converts every expected provider/transport outcome to the closed `SemanticResult`
union without exposing native exception text. Authentication/authorization failure, quota or
connection unavailability, unsupported bound capability, and provider outage return
`SemanticResultUnavailable`; refusal, invalid output, timeout, and late return their matching
value. The privacy/check coordinator then applies the requested mode. `PROVIDER_UNAVAILABLE` is
reserved for explicit non-check capability/support surfaces and is never allowed to erase an
already-computed deterministic check result.

Every returned variant carries only `ProviderAttemptProvenance`. The coordinator supplies attempt,
authorization, receipt, commitment, and exact final reason fields after terminal receipt
durability; the adapter cannot accept those values from model output or guess them early.

### Deadline and retry boundary

The adapter enforces one physical provider request per evaluate call. It does not perform retries
internally; the coordinator owns the retry budget and can re-invoke the adapter with the same
request identity if policy allows.

The adapter must respect:

- the caller’s absolute deadline;
- the configured safety margin before deadline expiry;
- the maximum input/output sizes;
- the exact entry sequence: capture
  `now_monotonic = clock.monotonic_seconds()` once, compute
  `remaining = deadline.remaining_seconds(now_monotonic)`, and make no network call when
  `deadline.expired(now_monotonic)` or the safety-margin-adjusted remaining budget is `0.0`.

The adapter never reads `time.monotonic()`, an event-loop clock, wall time, or the deadline's
diagnostic `expires_at_utc` to enforce the request budget. The same injected monotonic domain used
to construct `deadline.monotonic_deadline` supplies the current sample.

`render_case(case)` emits the minimized semantic prompt/input payload. It includes only the items
allowed by the semantic port:

- goal, claims under review, open/relevant obligations, and accepted decisions;
- ordered material timeline and coverage/omission facts;
- deterministic findings plus their exact machine-readable bases;
- change observations that keep state relation and content visibility separate;
- evidence/test/failure excerpts or digests and diff/command metadata; and
- policy-approved problem-local repository excerpts already recorded in the case.

It must not add a repository handle, broad/ambient source, secrets, unrelated conversation, or
out-of-case material. A targeted recorded excerpt is allowed content; ambient repository access is
not.

`normalize_response(response, profile)` converts the provider result into one of the closed semantic
result variants:

- success with a parsed judgment;
- refusal;
- timeout;
- invalid output;
- late arrival.

`normalize_judgment(...)` validates the parsed judgment before it can be accepted. It ensures that
returned challenges use allowed kinds, case-bound cited refs, conservative coverage, and the exact
text/count limits. It also
keeps the judgment provenance-bounded instead of letting provider output write directly into the
ledger.

`classify_provider_failure(...)` maps transport, authentication, and provider-profile failures to
public error classes. It must not leak provider exception text or raw case content.

### Privacy and audit boundary

The adapter keeps response material only long enough to parse/classify it. Invalid/refused/truncated
raw plaintext is not retained in v0.1. The gateway/coordinator writes a structural commitment-bearing
privacy receipt for every physical request; the adapter cannot mark an unreceipted request complete.

The module must never:

- store the full case in a global variable;
- log the raw provider output;
- echo provider exception strings back into public errors;
- weaken the case to make the provider look successful;
- accept output that cannot be represented in the closed semantic result union.
- accept a plain `SemanticCase`, generic URL, or caller-supplied credential.
- construct/reuse an SDK client with a real credential, or allow SDK retries/redirects.

## Errors and edge cases

- Refusal, invalid output, timeout, and late results are returned, not raised.
- Expected transport/auth/capability failures return `SemanticResultUnavailable`; native errors
  and their text never escape the adapter.
- An explicit monotonic sample at or after `deadline.monotonic_deadline` immediately returns a
  timeout result without network I/O.
- Provider output that invents IDs, widens coverage, or exceeds the permitted schema is invalid.
- The adapter never writes to SQLite and never reads the ledger directly.
- The adapter must treat truncated or partially streamed provider output as invalid unless the
  provider profile explicitly supports and proves recovery of the exact structured judgment.
- A response that matches the schema but references out-of-case evidence is still invalid at the
  judgment-normalization stage.
- If the response parser itself fails, the public classification should still be bounded and not
  leak parse internals.
- Actual request-body/digest/commitment/destination/TLS/profile/deadline mismatch, credential
  callback reuse, proxy/netrc/environment influence, or a stock/default transport
  fails before credential exposure and network I/O.
- Declared, streamed, chunked, or compressed response data above the exact cap aborts without raw
  retention and returns only the bounded status/size sentinel.
- Credential format failure occurs during confidential vault provisioning, before encrypted record
  mutation or adapter reconciliation; provider authentication failure for a format-valid key remains
  a bounded runtime `SemanticResultUnavailable`, not a validator detail.

## Invariants

1. One call, one physical provider request.
2. The adapter never retries internally.
3. The semantic case is minimized and bounded.
4. Parsed judgments are validated before they can influence a check result.
5. No raw provider text leaks into exceptions or logs by default.
6. Late results never become successful semantic evidence.
7. The coordinator/gateway owns privacy authorization, receipt, durable attempt identity, and retry
   policy.
8. The adapter constructor/case receives no handle to unapproved local state or reusable credential
   bytes; this is a reviewed in-process least-authority contract, not process isolation.
9. Every physical attempt has one per-attempt SDK client, custom transport, dispatch-bound
   credential handle, and exact final-body commitment; authentication/framing are not commitment
   input.
10. Credential validation is byte-exact, non-normalizing, offline, and never logs or returns the
    protected view.
11. Provider data-use posture controls upstream recommendation eligibility and is never presented
    as technical proof of downstream provider behavior.
12. Request-budget decisions use only the injected monotonic clock and the frozen deadline; wall
    time and ambient clock APIs cannot affect them.

## Tests

- `tests/capability/test_provider_profile_live.py` — opt-in exact SDK/provider/model success,
  refusal, invalid, timeout, provenance, exact SDK body-byte parity, per-attempt custom transport,
  no retained real credential in client/default headers, poisoned proxy/netrc/environment denial,
  exact TLS/destination enforcement, current data-use-profile/recommendation gating,
  `max_output_tokens`, and raw/chunked/compressed body caps.
- `tests/unit/application/test_semantic_post_validation.py` — allowed IDs, coverage ceiling,
  split frontier/local-check IDs, review challenges, change/visibility honesty, schema, freshness,
  and out-of-case rejection.
- `tests/unit/service/test_secret_memory.py` — credential boundary vectors at lengths 0/15/16/512/513,
  every accepted token68 class, misplaced `=`, whitespace/CRLF/NUL/control/non-ASCII, hostile
  representation, no normalization/copy/log, and no vault mutation on failure.
- `tests/integration/application/test_check.py` — coordinator retries, deadlines, audit objects,
  and deterministic fallback behavior.
- `tests/conformance/honesty/test_strict_local_zero_egress.py` — adapter absence in strict-local.

## Open questions

None.
