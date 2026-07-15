# docs/protocol/data-egress-and-privacy.md — privacy classification and outbound-egress protocol

**Wave:** B/E/F | **ADRs:** ADR-004, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
privacy schemas, egress fixtures, configuration/service/gateway/semantic port specs | **Imported by:**
`PRIVACY.md`, provider adapters, setup surfaces, conformance and release evidence

## Purpose

Define the enforceable, provider-independent protocol that decides whether any data may cross the
trusted local service boundary. This is the authority for classification, policy resolution,
human authorization, minimization, dispatch, and structural audit evidence.

## Public surface

The future document publishes:

- trust-boundary and actor diagram;
- content-category registry and classification confidence;
- non-overridable never-send registry;
- privacy profile semantics;
- policy scope/precedence and change authorization;
- independent egress-channel matrix;
- outbound pipeline and state machine;
- preview/authorization contract;
- outbound-case and provider-adapter contract;
- egress-receipt semantics;
- failure, cancellation, retry, and crash behavior;
- extension/versioning requirements.

Normative names and enums equal the privacy schemas. Human examples are derived and cannot widen
their machine-readable contracts.

## Behavior

### Trust boundary and actors

The trusted local service owns decrypted local state, policy evaluation, authorizations, the egress
gateway, key access, and structural receipts. CLI, MCP, UI, plugins, importers, and provider
adapters are callers with bounded capabilities. An MCP caller or agent is never a local-human
authorization principal, even when its request asserts that the user consented.

### Content classes

Every candidate item is classified before policy evaluation as one of:

- `public_structural`: bounded IDs, declared file/media types, counts, digests and non-content
  protocol facts;
- `ordinary_user_content`: selected task text, code/evidence excerpts, and declared work product;
- `sensitive_confidential`: confidential, personal, regulated, proprietary, or explicitly user-marked
  material requiring stricter authorization;
- `secret_or_cryptographic`: the class that cannot enter a disclosure case. Separately, scope
  validation rejects unrelated content, environment, file, transcript, log, stderr, database row,
  or source outside the selected scope.

Ambiguous classification takes the stricter class. A detector miss cannot authorize an item:
category, scope, and never-send scanning are independent gates. The never-send registry is versioned
and includes the complete public list in `PRIVACY.md`; policy may add prohibitions but cannot remove
them.

### Policy resolution

The effective policy is the intersection of machine, workspace, task, and request policy, never a
union. More-specific policy may narrow automatically but cannot widen without a fresh local-human
authorization bound to the broader categories, channel, provider/endpoint, purpose, and scope.
The closed scope wire value retains its whole ancestor chain: every kind carries canonical
`installation_id` (`ins_`); workspace/task/request carry an installation-keyed
`workspace_ref_commitment`; task/request carry `task_id` (`tsk_`); request carries `request_id`
(`req_`). A generic single-reference shortcut is forbidden. Authorizations have internal IDs,
revocation, policy version, and that exact chain; paths and content are not authorization identity.
Unkeyed canonical digests use `sha256:<64 lowercase hex>` and keyed commitments use
`hmac-sha256:<64 lowercase hex>`.

Four LLM profiles are closed values: `local_only`, `confirm_every_request`, `minimal_external`, and
`trusted_provider`. They govern only LLM inference/content disclosure. `local_only` disables the
`llm_inference` network channel and external LLM-provider construction; it is not consent or denial
for the four non-LLM channels. Local model permission is orthogonal and names an exact trusted
runtime profile. “No-network runtime” may be claimed only for a support cell with enforceable
sandbox evidence; an AF_UNIX endpoint alone proves only Yoetz's delivery path (F-013).
External profiles bind the exact five-field `ProviderBinding`: `provider_id`, `model_id`,
`endpoint_profile_id`, `endpoint_profile_version`, and `transport`. `trusted_provider`
additionally binds allowed categories and purpose. Unknown profile, unbound endpoint, scope
mismatch, expired authorization, or policy version mismatch denies before adapter construction.

`confirm_every_request` may disclose a policy-allowed sensitive/confidential excerpt after the local
human previews and approves those exact minimized bytes. `minimal_external` excludes that data class
by default. `trusted_provider` may automate a broader but still enumerated set; it never means all
available content.

`ReviewContextProfile` is a separate closed value:
`structural|goal_aware|assisted|expanded|custom`, paired with an exact compiled
`ReviewSelectionPolicy` of sections, excerpt kinds, relevance, finding-prose eligibility,
command-text eligibility, and caps.
It determines which recorded case material the local selector considers, not whether that material
may leave. `structural` contains typed timeline/
state/rule/coverage facts; `goal_aware` adds allowed intent and claim prose; `assisted` adds
mechanically linked problem-local recorded evidence, test/failure, diff, and repository excerpts;
`expanded|custom` can select a broader explicitly approved recorded set. Every selected item still
needs category/class/scope/destination authority and the same minimization/never-send path. No value
creates a live repository/filesystem handle. The omission manifest distinguishes `not_recorded`,
`not_selected`, `withheld_by_policy`, and `redacted_never_send`; none is synonymous with an
unchanged subject state.

The fail-safe seed is `local_only + structural + network false + all channels off`. The upstream
CLI's *configured* recommendation is an inspectable standing workspace `trusted_provider +
assisted` recipe with public-structural and ordinary-user-content classes, sensitive/transcript
content off, and agent-context `finding_summary` enabled. It is eligible only for an exact current
provider data-use profile stating training `prohibited`, retention `none|bounded`, and provider
human access `prohibited|restricted`. Known-broad, unknown, or stale posture is ineligible. The recipe sets an editable
`require_current_provider_data_use_evidence=true` runtime guard. The evidence record controls the
badge and satisfies that explicit guard; it is not itself disclosure authority or a claim that
Yoetz technically proves provider conduct. A custom policy may visibly turn the guard off through
the normal widening ceremony and then carries no upstream no-training claim.

After that standing policy is human-confirmed, ordinary checks, automatic retries, reviewer
challenges, agent responses, and rechecks are automatic. Human involvement remains for widening,
credential mutation, `confirm_every_request`, and finding waiver. Never-send/out-of-scope content
has no approval path. Every physical attempt still receives a fresh authorization and receipt.

`network_egress_permitted` is the global network ceiling. When false, all five channels must be
disabled. When true, it authorizes no channel, category, destination, or request. The five exact
`EgressChannel` values are `llm_inference`, `product_telemetry`, `crash_diagnostics`,
`update_checks`, and `capability_testing`. Each has its own enabled flag, destinations, categories,
scope, and human authorization requirements. No channel reads the ceiling as consent or another
channel's approval. The four non-LLM channels accept only their reviewed bounded structural or
synthetic schemas and never task/user content. Therefore `local_only` may coexist with a separately
authorized non-LLM policy row while still forbidding external LLM/user-content egress. v0.1 owns no
production use case or transport for those four rows: an attempted use terminates before dispatch
with outcome/reason `channel_unavailable/channel_unavailable`, writes a no-dispatch structural decision receipt, and
makes no DNS/socket attempt. Future transport support requires an exact owner, reviewed ADR/schema,
and fresh local-human capability confirmation; a stored v0.1 policy row cannot activate silently.

Yoetz's zero-network state is the composite `profile=local_only`,
`network_egress_permitted=false`, and all five channel policies disabled. That state permits only
exact release-cell local IPC: service/confidential endpoints, an optional approved local-model
AF_UNIX profile, and measured OS credential/user-presence/session-lifecycle security IPC such as
allowlisted Linux AF_UNIX session-bus Secret Service routes and, separately, system-bus
`org.freedesktop.login1` routes, or macOS native security/presence/session notifications. Arbitrary AF_UNIX,
bus methods/peers, and proxies are forbidden. Evidence names platform/release profile, Yoetz-owned
service/client/helper processes, startup-through-`locked|ready` and operation interval, and local
peers. External OS agents and a separately running local-model process are outside that process
claim; the latter remains subject to F-013. Neither the profile token nor a true ceiling alone
supports a zero-network claim.

In v0.1, `crash_diagnostics` can contain only bounded structural diagnostic metadata already
allowed by the observability schema. It neither captures nor uploads exception messages, locals,
source/path excerpts, or raw tracebacks. A future encrypted diagnostic-content artifact requires a
separate reviewed schema, local privacy authorization, minimization/never-send enforcement,
encrypted storage, retention, and release evidence; it is not a logging/debug/support-bundle mode.

The exact `LocalDisclosureSink` values are `local_model`, `agent_context`, and
`trusted_human_control`. They are not network channels. In particular, `agent_context` is fenced
before CLI/MCP rendering; MCP is not a sink enum and cannot inherit an external consent.

### Outbound state machine

One request moves through exact states:

1. `candidate`: caller submits scoped candidate references, never arbitrary filesystem access.
2. `classified`: service resolves each item to category and scope.
3. `policy_denied` or `policy_eligible`: effective policy and never-send rules are applied.
4. `minimized`: irrelevant items are removed and excerpts are bounded locally.
5. `redacted`: deterministic transforms run and the exact prepared bytes are secret-scanned;
   secret-bearing indivisible items deny.
6. `awaiting_human` when the profile or sensitivity requires preview of that exact prepared case.
7. `approved`: the human or baseline-policy decision is bound to the prepared-case digest, current
   policy version, exact destination, purpose, scope, categories, and ceilings.
8. `validated`: the unchanged prepared bytes, final outbound-case schema, byte/token cap, scope,
   category, destination, and policy digest are rechecked.
9. `authorized`: the service mints an exact expiring one-use authorization but leaves it
   unconsumed for the gateway.
10. `receipt_pending`: the gateway validates the immutable case and still-valid unconsumed
    authorization, atomically consumes it into one exact dispatch/receipt obligation, and immediately
    invokes the one bound adapter.
11. `recorded`: a terminal structural receipt commits either to the audit subject/reservation for a
    pre-dispatch decision or to the exact final provider/application request-body bytes for a
    physical attempt.

Only a validated case with a still-valid unconsumed authorization can reach `receipt_pending`. A
provider adapter accepts `ApprovedOutboundCase`, not raw
candidate context, policy objects, repository handles, ledger handles, file paths, environment, or
decryption services. It cannot enrich the case or change destination. The gateway computes the keyed
request commitment through the opaque `privacy_audit` handle over the exact final application body
bytes immediately before I/O. Credential-bearing auth metadata, transport-generated fields, and
HTTP/TLS framing are excluded. A fresh endpoint/profile/body-digest/deadline-bound credential handle
is consumed only by the custom transport's one-request header-injection callback; no SDK client
retains the real credential.

The durable audit state graph is separate from the content-preparation stages:

```text
pre-dispatch subject:  decision_receipt_pending ── complete_decision ──> decision_completed

prepared proposal:     reserved ── baseline policy ──> approved ──> authorized
                           └── human required ──> awaiting_human ─┬─> approved ──> authorized
                                                                 ├─> denied  (terminal)
                                                                 └─> expired (terminal)

physical attempt:      authorized ── consume CAS ──> receipt_pending ── complete_egress ──> attempt_completed
```

Expiry/revocation may terminate an approved or authorized-but-unconsumed branch. Denied and expired
states cannot authorize or dispatch. The gateway, not its caller, performs the one atomic
`authorized → receipt_pending` consumption compare-and-set immediately before adapter I/O.
`awaiting_human`, `approved`, and `receipt_pending` are nonterminal audit states, not
`PrivacyOutcome` values or finished receipts.

For `confirm_every_request`, preview occurs after local minimization/redaction/secret scanning and
shows destination, purpose, scope labels, category names, bounded exact excerpts as they would be
sent, removals/redactions, and counts. Approval is request-specific and expires if prepared bytes,
policy, destination, scope, or purpose changes. No later stage may add or substitute content;
denial and cancellation make no network attempt. The decision binds one physical dispatch. Crash
resume before authorization consumption continues that same dispatch; after any consumed physical
attempt, a retry requires a new proposal and fresh exact foreground preview/decision even when the
body is identical. The profile never converts one prompt into a hidden multi-attempt budget.

### Receipt and retention

Every successfully reserved terminal pre-dispatch decision creates a local structural decision
receipt keyed by `privacy_proposal_id`/audit reservation. A pending human proposal returns only its
bounded pending state/IDs, and approval continues the state machine; neither finalizes a receipt.
A terminal decision receipt conditionally omits authorization and dispatch IDs and must omit dispatch
time, request commitment, and attempt-body count when no physical attempt occurred. Every
physical attempt creates a separate receipt with its authorization ID, fresh dispatch ID, dispatch
time, and keyed final-request-body commitment. Receipts contain structural classifications and keyed
commitments, not outbound plaintext or provider response. The egress-receipt encryption/retention
policy is local and separate from telemetry. Retrying identical approved bytes after consumption
always creates a new authorization and distinct attempt receipt. Under automatic profiles it may
remain linked to the same baseline-policy proposal within the total retry budget. Under
`confirm_every_request` it requires a new proposal/preview/decision. No retry can reuse consumed or
expired authority.

An initial audit-reservation failure is the sole no-receipt decision exception. It returns bounded
application status `audit_failed` before preview, authorization, or dispatch and fabricates no
`ppr_`/`egr_`. A later pre-dispatch audit transition failure for an existing reservation may be
durably completed as `audit_failed/audit_failed` after recovery. A receipt-write failure after
consumption remains internal `receipt_pending`; recovery writes the real terminal attempt outcome,
not an audit-failure substitute.

Content-bearing v0.1 disclosure proposals are encrypted as `ObjectKind.privacy_audit` in their
owning task bundle; catalog rows retain only the `ObjectRef` and structural state. Taskless v0.1
channel-unavailable decisions and machine policy diffs contain only their closed nonsecret
structural fields and have no content object. A future taskless content-bearing channel requires a
separate installation-scoped encrypted audit-object owner/key/storage contract before activation.
The catalog ObjectRef is an explicit live root without task-ledger inventory and remains so for the
supported installation-data lifetime; v0.1 has no individual privacy-audit-content deletion
operation, and ordinary ledger redaction/orphan GC cannot clear it. Backup pins the catalog root
generation/digest, copies a canonical structural audit sidecar plus every rooted encrypted object,
and restore verifies the same union. Route moves preserve current refs; clean restore expires
nonterminal authority and resolves `receipt_pending` without reviving dispatch. A dangling/tampered
root quarantines that audit row and fences task content disclosure until verified repair.

Outcome/reason pairs use only the shared closed vocabularies:

| Situation | `PrivacyOutcome` | `PrivacyReason` |
|---|---|---|
| policy/category/purpose/destination/scope/minimized-empty block | `blocked_by_policy` | exact applicable token: `policy_denied`, `category_not_allowed`, `purpose_not_allowed`, `destination_not_allowed`, `scope_mismatch`, or `insufficient_approved_context` |
| never-send match | `blocked_forbidden_data` | `never_send_detected` |
| unresolved classification | `classification_uncertain` | `classification_uncertain` |
| local-human denial | `human_denied` | `human_denied` |
| approval/authorization expiry, stale generation, or replay | `approval_expired` | `authorization_expired`, `authorization_stale`, or `authorization_reused`, as applicable |
| successful bounded result and durable receipt | `completed` | absent |
| provider refusal | `provider_refused` | `provider_refused` |
| provider/deadline timeout | `timeout` | `provider_timeout` or `deadline_expired`, as applicable |
| invalid provider response | `invalid_response` | `provider_invalid_response` |
| unavailable provider/transport failure | `transport_failed` | `provider_unavailable` or `transport_failed`, as applicable |
| v0.1 non-LLM channel has no owned transport | `channel_unavailable` | `channel_unavailable` |
| possible I/O with indeterminate result | `transport_failed` | `outcome_unknown` |
| late result | `late` | `late` |
| stale result | `stale` | `stale` |
| later pre-dispatch audit transition failure on an existing reservation, durably completed after recovery | `audit_failed` | `audit_failed` |

Where a cell names alternatives in prose, the implementation selects exactly one existing enum
token; it never constructs a combined wire token. `safe_failure_reason` is required for every
outcome except `completed`, for which it is forbidden. This same total matrix governs
`LocalDisclosureReceipt`; sink/channel shape does not weaken reason compatibility.

### Policy mutation

Trusted local control surfaces may submit changes. The service classifies the diff as tightening,
neutral, or loosening. Server-proven tightening can commit immediately. Loosening returns
`decision_required` with a pending proposal ID and exact digest; it cannot commit through ordinary
control. A separate foreground `HumanControlService` confidentially renders, reauthenticates, and
commits internally without serializing a reusable proof/token. Ordinary MCP/agent schemas and
import graphs expose no preview, decision, or policy-authority method; arbitrary malicious
same-UID code is outside that transport claim. All mutations are versioned and auditable without
content. Revocation is immediate.

## Errors and edge cases

- Classification, minimization, redaction, schema, secret scan, or receipt-persistence uncertainty
  fails closed before dispatch.
- Initial reservation failure returns bounded `audit_failed` with no receipt/proposal identity;
  later reserved failures and post-consumption `receipt_pending` repair follow the distinct rules
  above.
- Service lock, policy unavailability, unknown scope, stale authorization, or unbound provider denies
  egress but does not erase deterministic local work.
- Dispatch ambiguity records outcome `transport_failed` plus reason `outcome_unknown`; it never
  claims unsent or retries blindly.
- Cancellation before I/O leaves the proposal pending until an explicit `human_denied` decision or
  `approval_expired` terminal transition; it does not invent a `cancelled` egress outcome.
  Cancellation after possible I/O records outcome `transport_failed`, reason `outcome_unknown`, and
  the exact final-request-body commitment.
- Provider refusal/timeout/invalid output does not change what was authorized and completes semantic
  review as incomplete while deterministic results remain available.
- Local-model permission cannot make Yoetz launch/download/update a model or open IP networking; it
  does not attest a separately running model process's ambient network behavior (F-013).
- Telemetry or crash adapters cannot reuse an approved LLM outbound case.
- `network_egress_permitted=false` plus any enabled channel is invalid and dispatches nothing;
  `true` plus all channels disabled is valid and also dispatches nothing.
- A v0.1 non-LLM enabled row returns `channel_unavailable` before authorization consumption or I/O;
  its receipt omits dispatch ID/time and request commitment, so it cannot be confused with an
  attempted or ambiguous transport failure.

## Invariants

1. There is one centrally enforced path for every outbound request.
2. Effective policy is monotone toward less disclosure unless a local human confirms widening.
3. Never-send and out-of-scope data never reach an adapter.
4. Destination, purpose, category, the complete scope ancestor chain, and exact bytes remain bound
   from approval through dispatch.
5. The global ceiling grants nothing, and channel authorizations are independent.
6. Structural receipts never retain request or response plaintext.
7. Semantic failure cannot discard deterministic results or imply semantic completion.
8. Request commitments cover exact final application body bytes, not authentication metadata or
   HTTP/TLS framing; each physical attempt uses one scoped credential callback and no retained SDK
   credential.
9. Privacy profiles govern LLM disclosure only; true zero network requires the false global ceiling
   and all five channels disabled in addition to `local_only`.
10. v0.1 non-LLM channels are unavailable policy vocabulary, not hidden or generic network
    transports; later activation is a newly confirmed widening.
11. `confirm_every_request` means one exact foreground decision per physical dispatch, including
    every retry; only pre-consumption resume continues existing authority.
12. Receipts are terminal. Pending human, approved, and consumed-but-unreceipted states remain only
    in `PrivacyAuditState` and cannot be serialized as a finished `PrivacyOutcome`.
13. Initial reservation failure is necessarily pre-dispatch and explicitly unreceipted; every
    successfully reserved terminal decision and physical attempt is durably receipted.
14. Catalog privacy roots survive GC/backup/route move without ledger inventory, and restore never
    turns backed-up pending authority into a usable authorization.
15. Review-context selection can only narrow candidate material; missing/hidden source is never
    represented as observed unchanged source.

## Tests

- all `fixtures/privacy/PRIV-*.case.json` cases;
- `tests/unit/privacy/test_policy_and_contracts.py`;
- `tests/property/test_egress_policy_properties.py`;
- `tests/integration/privacy/test_egress_gateway.py`;
- `tests/conformance/privacy/test_privacy_profiles.py`;
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`;
- `tests/subprocess/test_service_lock_and_confidential_unlock.py`;
- `tests/capability/test_privacy_provider_and_local_model_profiles.py`.

## Open questions

None.
