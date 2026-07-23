# src/yoetz/adapters/privacy/gateway.py — authorization-fenced outbound and local-model gateway

**Wave:** E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/privacy.md`, `ports/semantic.md`, `ports/secret_memory.md`,
`observability/privacy.md`, provider adapters | **Imported by:**
runtime composition and privacy/provider tests

## Purpose

Implement the only gateway allowed to own semantic provider transports. It revalidates and consumes
privacy authority, dispatches an immutable approved case to one exact bound adapter, closes clients
on policy tightening, and exposes neither clients nor credentials to application code.

## Public surface

- `class PolicyEnforcingOutboundGateway(OutboundGatewayPort)`.
- `class ProviderRegistry` — generation-fenced exact `ProviderBinding` to
  credential-free provider-adapter factory mapping. Individual snapshots are immutable;
  `reconcile_policy` atomically swaps the current snapshot after policy/vault/human-authority
  validation. Every snapshot binds `HumanAuthorityCapability` generation/digest.
- `async close() -> None` — idempotent terminal closure of all bound transports.
- `configured_provider_ids()` / `connected_provider_ids()` — bounded structural snapshots used by
  observation advice composition; they expose provider IDs only, never bindings, endpoints,
  credentials, or clients.

## Behavior

Construction receives verified credential-free factories, the privacy audit authority needed for
atomic consumption, the exact same `PrivacyClassifierPort` instance used by the application
coordinator, one opaque `MacKeyHandle(purpose=privacy_audit)`, and a narrow service-vault factory
that can mint only exact `ProviderAttemptAuthBinding` handles; it does not read environment/config
secrets or discover endpoints. `dispatch_external_semantic` requires the caller to pass the exact
still-valid, unconsumed authorization. It verifies authorization/case/channel/provider/model/
endpoint/purpose/scope/policy/service generation/deadline, allocates the physical `dsp_` identity,
and invokes only the matched credential-free factory to deterministically render the exact final
application request body without I/O. The gateway verifies that rendering contains only the approved
logical case plus fixed reviewed template/schema fields, invokes that shared classifier's
`scan_exact_bytes` on the final body, and rejects any finding before credential minting,
authorization consumption, or network I/O. It
computes the body SHA-256 and `privacy_request_commitment` exactly once over those final bytes,
creates the exact provider/model/endpoint-profile/version/purpose/authorization-scope-digest/
purpose-digest/dispatch/body-digest/generation/deadline `ProviderAttemptAuthBinding`, mints one
fresh credential handle, and constructs a
one-attempt custom transport/evaluator carrying the immutable precomputed digest and, separately,
the request commitment—never a MAC callback. The commitment is not a field of
`ProviderAttemptAuthBinding`.
It then revalidates the unchanged body/binding/policy/deadline and atomically consumes authority
while recording the attempt-admission body digest/commitment through the audit port as the last
authoritative transition before calling that transport. Only the transport can consume the
credential handle for authentication-header injection
and immediate request start; no intervening await or fallible application step is permitted. The
gateway never accepts an already-consumed authority as proof of permission.

Policy-generation commit and this consume share the audit catalog's serialized generation CAS. If
tightening wins, consume fails and no transport callback/I/O occurs. If consume wins, the one
attempt is admitted and may send; later tightening best-effort closes it, fences its result from
selection, and cannot claim to retract bytes. The attempt still receives its actual terminal or
`outcome_unknown` receipt.

`dispatch_local_semantic` accepts only an `ApprovedLocalDisclosureCase`, verifies the exact local
AF_UNIX profile and durable local-disclosure reservation, and calls only the local evaluator. It
never consumes a network authorization or produces an `EgressReceipt`.

External semantic-provider adapters are absent in `local_only`. The four non-LLM channel values have
no v0.1 gateway/adapter implementation and cannot fall through this semantic gateway; policy setup
rejects their enablement and a forced enabled state returns `channel_unavailable` before this
gateway. The local-model adapter is distinct and can exist only
for an approved AF_UNIX endpoint profile. `close_revoked` immediately removes and closes adapters no
longer permitted. The registry has no default adapter, wildcard provider, generic URL, redirect, or
fallback. Retry is coordinated above the gateway and requires a new authorization/receipt.

`close` atomically installs a terminal deny fence and removes every visible binding before awaiting
transport closure. After the fence, `reconcile_policy`, both dispatch methods, and `close_revoked`
admit no new work, mint no credential handle, render no content-bearing request, and perform no new
adapter I/O. An in-flight unconsumed attempt is fenced before I/O. An already-consumed attempt is
best-effort closed and nonselectable but still resolves its actual or `outcome_unknown` durable
receipt; closure never claims to recall transmitted bytes. Repeated `close` calls await the same
closure and expose neither provider credentials nor approved content.

After every committed policy generation, `reconcile_policy` first installs a deny fence for all
bindings not permitted by the new effective policy and removes them from the visible snapshot.
Then, outside the registry lock, it builds newly allowed credential-free candidates from the exact
installed nonsecret provider profile. It performs no inference/capability request and mints no
credential handle.
Under the lock it rechecks policy/service/vault generations and atomically publishes only matching
candidates; stale candidates are closed. Profile/factory failures leave the binding absent and
return bounded structural unavailability. Missing credentials are discovered only while minting a
fresh dispatch handle and do not populate the registry. Startup uses this same path. A crash after
policy commit therefore restarts with no adapter until reconciliation,
never with an unauthorized old adapter.

Before candidate construction, reconciliation checks the current human-authority snapshot supplied
by daemon composition. `source=unavailable` creates an empty external candidate set, closes prior
external sessions, and reports `human_authority_unavailable` without rewriting policy or deleting
credentials. Dispatch checks the same generation-static capability digest/service/vault generation
immediately before privacy-authorization consumption. Restart/relock or an explicit human-control
capability-unavailable result forces fresh reconciliation; v0.1 has no hidden asynchronous presence
watcher. Restoration requires a fresh ready composition/reconciliation. Local-model dispatch is a
separate local disclosure sink and does not inherit external activation. This is an intentional
trust split, not an unguarded fallback: `HumanAuthorityCapability` fences external activation,
credential mutation, and policy widening, while an already enabled local-model row was itself
created only by strong local-human policy authority. Even when that capability snapshot is
unavailable, local dispatch still requires the exact durable row/profile plus matching service,
vault, and policy generations, the shared privacy classifier and never-send fence, and
`consume_local` before AF_UNIX I/O; it can neither create nor widen local permission.

## Errors and edge cases

Missing binding, mismatch, expiry, reused or already-consumed authorization, generation change,
deadline exhaustion, or closed adapter returns a bounded non-dispatch outcome before consumption.
An ambiguous network failure occurs after consumption and cannot restore authority. Provider
exception strings and response bytes never cross the gateway boundary.
- Approved-case/final-body scan failure, body/profile/deadline/commitment mismatch, privacy-MAC
  failure, credential-mint failure, callback reuse, or a
  stock/default transport fails before credential exposure/network I/O. Because authorization is
  still unconsumed, the gateway invalidates any unused handle and calls the audit port's
  preconsume `complete_decision` path: it atomically revokes the authorization and writes a
  no-dispatch decision receipt with the bounded outcome/reason. It omits dispatch/attempt/
  request-commitment fields and never calls `complete_egress`. A retry starts from a fresh proposal
  and authorization.
- If final revalidation or authorization consumption fails after a handle was minted, the unused
  handle is invalidated without invoking its callback or starting I/O.

## Invariants

1. Only an exact external case plus matching still-valid unconsumed authorization, or an exact
   approved local disclosure case plus reservation, can reach its corresponding adapter.
2. The registry cannot perform provider discovery or destination substitution.
3. Policy tightening serializes with consume, prevents every later admission, and best-effort
   closes attempts already admitted; it does not claim to recall bytes.
4. Credential handles are minted only after exact final-body rendering and never enter registry or
   SDK client state; one handle is consumed by one custom-transport callback per physical attempt.
5. One gateway call makes at most one physical provider request, and its authorization consumption
   is the last authoritative state transition before adapter I/O.
6. A widened policy can activate its exact adapter without service restart, while activation
   failure remains absent/incomplete and cannot roll back or bypass policy.
7. Durable policy plus a stored credential is insufficient when current human-authority capability
   is unavailable; external registry and dispatch remain fenced.
8. The coordinator's logical-case scan and gateway's final-body scan use the same injected
   `PrivacyClassifierPort`; detector drift or an independently configured gateway scanner is
   forbidden.
9. Unavailable external human-authority capability neither creates nor revokes local-model policy;
   an existing local permission remains independently profile/service/vault/policy-generation/
   consume gated.

## Tests

Mismatch/replay/generation/deadline matrices, constructor monkeypatches, immediate revocation,
exact provider/final-body binding, trailing-NUL audit-MAC vectors, one-call cardinality,
credential callback reuse/retention, SDK default-header inspection, and canary nonexposure are
covered. Capability tests also cover already-wide policy+credential with unavailable authority,
loss closing a live registry, restart remaining fenced, and fresh reconciliation on restoration.
Race tests force tighten-before-consume and consume-before-tighten, and preconsume fault tests prove
that no failure mints an attempt receipt or leaves reusable authorization.

## Open questions

None.
