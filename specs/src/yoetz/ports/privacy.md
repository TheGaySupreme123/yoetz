# src/yoetz/ports/privacy.py — privacy policy, audit, human-control, and outbound gateway ports

**Wave:** C–E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/semantic.md`, `ports/clock.md` | **Imported by:**
`application/egress.md`, `application/privacy_policy.md`, `application/service.md`,
privacy/provider adapters, CLI/UI trusted control surfaces

## Purpose

Define the only effect boundaries through which Yoetz may load/change privacy policy, persist a
disclosure decision, ask a local human for consent, or dispatch approved bytes. These ports keep
policy and network authority in the trusted local service while allowing catalog, control-surface,
and provider implementations to vary without granting CLI/MCP/provider code direct access.

## Public surface

- `class PrivacyPolicyStorePort(Protocol)`:
  - `async seed_if_absent(policy: PrivacyPolicy) -> PrivacyPolicy`
  - `async effective_policy(scope: AuthorizationScope) -> EffectivePrivacyPolicy`
  - `async prepare_transition(proposal: PolicyTransitionProposal) -> PreparedPolicyTransition`
  - `async commit_transition(prepared, decision: HumanPolicyDecision) -> PrivacyPolicy`
  - `async tighten(scope, overlay: PolicyOverlay) -> PrivacyPolicy`
  - `async watch_generation() -> int`
- `class PrivacyAuditPort(Protocol)`:
  - `async reserve(subject: PrivacyAuditSubject) -> PrivacyAuditReservation`
  - `async load(request_id: str, subject_digest: str) -> PrivacyAuditState | None`
  - `async record_human_decision(reservation_id, decision: HumanPrivacyDecision) -> PrivacyAuditState`
  - `async authorize(reservation_id, approved_case_digest, now) -> EgressAuthorization`
  - `async consume(authorization_id, dispatch_id, now) -> ConsumedAuthorization`
  - `async consume_local(reservation_id, approved_case_digest, now) -> ConsumedLocalDisclosure`
  - `async complete_decision(reservation_id, receipt: EgressReceipt | LocalDisclosureReceipt) -> None`
  - `async complete_egress(dispatch_id, receipt: EgressReceipt) -> None`
  - `async complete_local_disclosure(reservation_id, receipt: LocalDisclosureReceipt) -> None`
  - `async get_receipt(receipt_id, audience: PrivacyReceiptAudience) -> PrivacyReceiptView | None`
  - `async list_receipts(query: PrivacyReceiptQuery, audience: PrivacyReceiptAudience) -> PrivacyReceiptPage`
  - `async revoke_policy_generation(generation: int, reason: str) -> int`
  - `async live_object_roots(task_id: str, route_identity_digest: str) -> PrivacyAuditObjectRoots`
- `class PrivacyClassifierPort(Protocol)`:
  - `classify(candidate: CandidateContext, policy: EffectivePrivacyPolicy) -> ClassifiedContext`
  - `minimize_and_scan(classified, decision: PrivacyDecision) -> PreparedOutboundCase`
  - `scan_exact_bytes(data: bytes) -> tuple[ForbiddenDataKind, ...]`
- `class HumanPrivacyControlPort(Protocol)`:
  - `async request_disclosure_decision(proposal: DisclosureProposal) -> HumanPrivacyDecision | PendingHumanDecision`
  - `async request_policy_decision(proposal: PolicyTransitionProposal) -> HumanPolicyDecision | PendingHumanDecision`
- `class OutboundGatewayPort(Protocol)`:
  - `async reconcile_policy(policy: EffectivePrivacyPolicy, human_authority: HumanAuthorityCapability) -> ProviderReconciliation`
  - `async dispatch_external_semantic(case: ApprovedOutboundCase, authorization: EgressAuthorization, deadline: Deadline) -> SemanticResult`
  - `async dispatch_local_semantic(case: ApprovedLocalDisclosureCase, deadline: Deadline) -> SemanticResult`
  - `async close_revoked(policy_generation: int) -> None`
- Frozen authority/state values: `EffectivePrivacyPolicy`, `PolicyTransitionProposal`,
  `PreparedPolicyTransition`, `HumanPolicyDecision`, `PrivacyAuditReservation`,
  `PrivacyAuditSubject`, `PreDispatchAuditDecision`, `AgentProjectionAuditSubject`,
  `PrivacyAuditState`,
  `PendingHumanDecision`, `PreparedOutboundCase`,
  `ConsumedAuthorization`, `ConsumedLocalDisclosure`, `PrivacyAuditObjectRoots`,
  `PrivacyReceiptQuery`, `PrivacyReceiptPage`, `PrivacyReceiptView`.
- `ProviderReconciliation` — policy generation, activated/deactivated counts, and sorted unavailable
  binding digests/reasons; no credentials, URLs, clients, or exception text.
- `HumanAuthorityCapability` — generation-bound structural snapshot with source
  `os_user_presence|established_passphrase|unavailable`, measured presence-capability digest,
  vault mode/generation, and `external_activation_allowed`; it is not an approval proof.

`HumanPrivacyControlPort` exists only inside `service/human_control.md`. MCP, an ordinary control
session, an agent message, an LLM prompt, a plugin, and a normal/noninteractive CLI invocation must
not implement it or supply its decision values. A policy expansion consumes strong reauthentication
inside this service call. A `confirm_every_request` disclosure already within durable policy instead
consumes exact foreground digest-bound TTY consent and mints no widening proof; the two decisions
are not interchangeable and neither crosses the helper/client boundary as a token.

Every `reservation_id` parameter above is the audit subject's canonical `privacy_proposal_id`
(`ppr_`), whether the subject is a prepared proposal or a structural pre-dispatch decision; there is
no second public reservation-ID vocabulary.

`PrivacyAuditObjectRoots` is service-internal and contains exact task ID, active route-identity
digest, monotonic catalog `privacy_root_generation`, sorted unique `ObjectRef` values of kind
`privacy_audit`, and a canonical root-set digest. It contains no excerpts or paths. Every committed
add/clear of a privacy content reference increments the generation. Maintenance, recovery, backup,
and object GC use this value as the authoritative cross-catalog live-root set; a task-ledger object
inventory row is neither required nor permitted solely to keep a privacy object alive.

## Behavior

### Policy store

`seed_if_absent` is the sole first-run bootstrap mutation. It atomically commits the supplied exact
denied machine policy as generation 1 only when the store proves absence. A concurrent or repeated
call returns the existing policy only when its complete canonical bytes and identity are equal;
any different existing row fails with a bounded conflict and is never overwritten.

`effective_policy` loads the durable machine policy and applicable workspace/task/request overlays
and returns their deterministic intersection with a canonical digest and generation. On a truly
absent first-run store, `config/privacy.md` atomically seeds the denied machine policy before ready;
bootstrap config is not a permanent ceiling. Missing-after-bootstrap or unreadable policy fails
closed to `local_only`, `review_context_profile=structural`, with every network channel denied; it
also uses the canonical structural `ReviewSelectionPolicy` and a false current-data-use guard; it
never falls back to a permissive built-in profile.

The durable policy has a boolean `network_egress_permitted` global ceiling plus all five channel
rows. False requires every row disabled; true grants none. `PrivacyProfile` constrains only
`llm_inference`. Each `ReviewContextProfile` is compiled to its exact `ReviewSelectionPolicy` and
intersected independently by section/kind set intersection, stricter relevance, logical-AND
finding-prose and exact-command eligibility, and minimum caps. A noncanonical meet is labeled
`custom`. It can only reduce
semantic case selection and grants no channel/category/class/scope. Selector superset/cap increase,
either selector boolean false→true, mixed/incomparable change, or turning the current-data-use guard
off is policy widening; selector subset/min-cap reduction, either selector boolean true→false, or
turning the guard on is tightening. v0.1 policy transitions reject
enablement of the four unsupported non-LLM rows as
`channel_unavailable` and persist nothing. If imported/corrupt-forward state contains such a row,
evaluation fences it before authorization and completes a pre-dispatch
`channel_unavailable/channel_unavailable` decision receipt with no attempt-only fields or I/O. A
future capability/reconciliation event cannot activate old intent; it requires a fresh exact
local-human transition.

`tighten` accepts only a mathematical subset of current permissions and commits it immediately. It
increments policy generation, revokes pending/issued authorizations that no longer fit, and causes
the gateway to close affected external sessions. It needs no preview because it cannot disclose
more data.

Policy commit and both `consume`/`consume_local` use the same catalog serialization point and
compare the same effective policy generation. Their race has exactly two legal orderings:

1. Tightening commits first: generation increments and affected reserved/approved/authorized local
   or external branches are revoked/terminalized. A later consume CAS fails, no adapter/AF_UNIX/
   response serialization I/O occurs, any unused credential/socket candidate is invalidated, and a
   no-dispatch decision receipt records the exact policy/stale-authorization reason.
2. Consume commits first: the one attempt/disclosure is admitted under the old generation and may
   physically cross the boundary; tightening cannot retract bytes. It installs the new deny fence,
   best-effort cancels/closes the affected transport, marks the consumed result nonselectable under
   the new policy, and requires repair/completion of the real terminal or `outcome_unknown` receipt.
   No retry may use old authority.

There is no third interleaving and no claim that tightening can recall already admitted bytes.
`tighten` reports separately revoked preconsume branches, already-consumed affected attempts, and
best-effort closed sessions.

`prepare_transition` handles any potential expansion. It computes an exact canonical diff against
the effective ancestor intersection, persists its closed nonsecret structural fields in the catalog,
and returns no write token to the client. A v0.1 policy diff contains only IDs, enum/category names,
digests, ceilings, scope commitments, and expiry—never excerpts, URLs, credentials, or user content;
a future content-bearing policy-diff format requires an encrypted-object owner before it can be
accepted. `commit_transition` requires a still-current prepared digest plus locally authenticated,
reauthenticated human decision from the trusted control port. Any generation/diff mismatch restarts
the proposal; a caller assertion cannot be upgraded into policy authority.

### Classifier and minimizer

`PrivacyClassifierPort` is deterministic, local, and network-free. Classification uses source-owned
labels, scope membership, the reviewed forbidden-source rules, and the versioned secret scanner.
It does not call a model. `minimize_and_scan` chooses only policy-relevant fields, applies fixed
redaction transforms, serializes canonical bounded bytes, and scans the exact bytes that would be
sent. `scan_exact_bytes` is the same immutable scanner entry point used for those prepared logical
bytes and for a provider renderer's exact final application-body bytes. It returns only a canonical
sorted tuple of closed `ForbiddenDataKind` values, one per match; duplicate kinds are retained so
`len(result)` is the bounded finding count. It retains no input and exposes no matching substring.
Runtime composition injects the same `PrivacyClassifierPort` instance into the coordinator and
gateway; a scanner/ruleset change requires reconstruction and reconciliation rather than an
in-place hot swap. A forbidden match, uncertain classification, cap violation, or policy mismatch
returns a blocked decision; no caller can request `ignore`.

### Durable approval/audit state machine

`reserve` discriminates the closed subject union before any prompt or dispatch. A
`DisclosureProposal` stores its content-bearing prepared case as encrypted
`ObjectKind.privacy_audit` bytes in the owning task bundle, then stores only its `ObjectRef` plus a
structural row. A `PreDispatchAuditDecision` has no content object and atomically stores only its
closed structural subject in `decision_receipt_pending`. An `AgentProjectionAuditSubject` stores
only keyed commitments, field decisions, counts, and policy/scope identities—never a duplicate
result/excerpt object—and enters the atomic local branch. The state graph is:

```text
pre-dispatch subject:  decision_receipt_pending ── complete_decision ──> decision_completed

prepared proposal:     reserved ── baseline policy ──> approved ──> authorized
                           └── human required ──> awaiting_human ─┬─> approved ──> authorized
                                                                 ├─> denied  (terminal)
                                                                 └─> expired (terminal)

physical attempt:      authorized ── consume CAS ──> receipt_pending ── complete_egress ──> attempt_completed
                         └── preconsume failure/revocation ── complete_decision ──> decision_completed

local disclosure:      approved ── consume_local CAS ──> local_disclosure_pending
                         ── complete_local_disclosure ──> local_disclosure_completed
```

`PrivacyAuditState.status` is exactly `decision_receipt_pending|decision_completed|reserved|
awaiting_human|approved|authorized|receipt_pending|attempt_completed|local_disclosure_pending|
local_disclosure_completed|denied|expired|quarantined`. The
`ConsumedAuthorization` returned by the consume CAS is the proof carried by `receipt_pending`; there
is no separate `dispatched` receipt outcome or final receipt before the real attempt result is known.
Expiry or revocation before consumption may move any `reserved`, `awaiting_human`, `approved`, or
`authorized` state to terminal `expired`; neither `denied`, `expired`, nor `decision_completed` has
an edge to `authorized`. `quarantined` is a recovery-only terminal safety state entered
only when a committed catalog content root cannot be verified; it is never a receipt outcome and
has no edge to approval/authorization. Automatic profiles take the
baseline branch with `ConsentSource.baseline_policy` only when the effective policy proves every
category/scope/provider/purpose constraint. The human branch records
`scoped_local_human|per_request_local_human`; approval is bound to the proposal digest and cannot
edit it in place. Changed excerpts or destination require a new proposal. Restart resumes the
exact state. Policy-generation change or service-vault generation loss can only narrow/revoke
state.

For `confirm_every_request`, `per_request_local_human` is an explicit foreground `/dev/tty`
approve/deny over the exact minimized preview and is valid only while the case remains within every
durable policy ceiling. It needs no OS/passphrase reauthentication because it grants no new durable
authority. Any requested category/destination/scope/purpose beyond policy is not a disclosure
decision; it must use the strongly reauthenticated policy-transition path. The decision binds one
physical dispatch, not a retry budget. Each later physical attempt under
`confirm_every_request` needs a fresh proposal/preview/decision, even for identical bytes.

`authorize` verifies the approved minimized case digest and creates one opaque, expiring authority.
The application passes that still-valid unconsumed value to the gateway. The gateway calls
`consume` as an atomic compare-and-set immediately before adapter I/O; no caller may pre-consume it.
A consumed authority cannot be replayed, even if the network result was ambiguous. The resulting
state is `receipt_pending` with its exact consumed authorization and dispatch ID until
`complete_egress` durably records one terminal attempt receipt. Retry requires a new authorization and
receipt while remaining inside policy ceilings. For `confirm_every_request`, retry also requires a
new foreground decision and cannot reuse the original approval; automatic profiles may create it
from baseline policy within the shared total retry/deadline budget. Resume before consumption
continues the same dispatch and is not a physical retry.

`consume_local` is the single linearization point for any content crossing a local sink. It
validates the still-current policy/service generation, exact sink, encrypted case digest or keyed
projection commitment,
categories, scope, purpose, and unused proposal ID, then atomically moves `approved ->
local_disclosure_pending` and returns a nonserializable `ConsumedLocalDisclosure`. The canonical
`privacy_proposal_id` is the local replay/attempt key; there is no second local-dispatch ID.

For `agent_context` purpose `client_result_projection`, reserve allocates/reuses the explicit
`projection_request_id` under the exact control-RPC binding described in `domain/privacy.md`; then baseline approval,
`consume_local`, and `complete_local_disclosure` are one atomic audit transaction before any
ordinary response serialization. The terminal receipt and exact keyed projection commitment commit together;
only then may `Application` receive approved leaf bytes. Replay of the same logical
`(projection_request_id, rpc binding, method, keyed_internal_result_commitment, sink,
policy_digest)` returns the same proposal,
projection, and receipt. Changed bytes/policy conflict and require a new projection. A zero-content
structural result uses the same `AgentProjectionAuditSubject` and still receives a receipt. No
agent projection duplicates plaintext into `ObjectKind.privacy_audit`. An initial reserve failure
yields `LocalDisclosureUnavailable(audit_failed)` and no bytes.

For `local_model`, the gateway calls `consume_local` immediately before the first AF_UNIX write.
`local_disclosure_pending` is recoverable and forbids resend of that proposal because bytes may
already have crossed. The actual success/refusal/timeout/invalid/unknown result is durably closed by
`complete_local_disclosure`; retry requires a fresh proposal/reservation/consume/receipt. A crash or
lost response after consume records `outcome_unknown` if no stronger fact exists and never reuses
the proposal. Thus local-model disclosure has the same at-most-one-physical-attempt authority as
external dispatch without pretending it is network egress.

`complete_decision` is mandatory only for a terminal pre-dispatch blocked, denied, expired,
post-reservation audit-failed, `channel_unavailable`, revoked authorization, or other verified
preconsume failure decision. For a
`PreDispatchAuditDecision`, it requires the receipt outcome/reason to equal the reserved subject and
atomically moves `decision_receipt_pending -> decision_completed`; that branch cannot authorize.
For a prepared proposal, `awaiting_human` and `approved` remain nonterminal
`PrivacyAuditState` statuses and never finalize a receipt; denial/expiry/a later pre-dispatch failure
is terminal. `complete_decision` keys every structural receipt by the exact audit
reservation/privacy proposal and requires dispatch fields to be absent before consumption.
From `authorized`, it atomically revokes the unconsumed authorization and moves to
`decision_completed`; a preallocated in-memory `dsp_` is discarded and never appears in the
receipt. This path handles final-body/profile/MAC/credential-mint/deadline failures before consume.
It is not an attempt receipt and never claims network I/O. Retry, if policy permits, begins with a
fresh proposal/reservation/authorization.
`complete_egress` is mandatory for every physical outbound attempt and is the only transition from
`receipt_pending -> attempt_completed`,
including taskless telemetry, update, diagnostic, or capability channels; its attempt receipt has
the consumed authorization and dispatch ID. Both methods commit the structural receipt and
   the already-existing encrypted proposal reference when one exists; they never invent an encrypted
   object for a predispatch or agent-projection subject. `complete_local_disclosure` is the only transition from
`local_disclosure_pending -> local_disclosure_completed`; it records model/agent disclosure under
the same audit service. All three completion methods enforce the receipt schema's total
outcome/reason matrix: `completed` has no failure reason and every failure has exactly one compatible
reason. A semantic success is not acknowledged until its receipt is durable; inability to reserve
audit means no dispatch.

### Gateway

`OutboundGatewayPort` is the only injected semantic provider effect visible to application code.
Its implementation owns external and local-model adapters. External dispatch accepts only a
still-valid unconsumed authorization, revalidates policy/service generation, channel
`llm_inference`, exact provider/model/endpoint, purpose, scope, case digest, ceilings, and deadline,
then atomically consumes authority immediately before adapter I/O. Local dispatch accepts only an
`ApprovedLocalDisclosureCase`, uses no network authorization, and requires the durable local
disclosure reservation. Each path passes only its exact case variant to the bound provider and
never exposes credentials or transport objects upstream.

`LocalDisclosureApproved` contains exact proposal/request IDs, sink, purpose, scope/policy digest,
encrypted-case digest/keyed-projection commitment, sorted
`ApprovedLocalItem(pointer, category, bounded_bytes)` values, sorted
`LocalDisclosureOmission(pointer, category, reason)` values, and the durable
`LocalDisclosureReceipt`/receipt ID. `LocalDisclosureBlocked` contains the same structural binding,
no approved bytes, at least one omission, and its durable terminal receipt. `LocalDisclosureUnavailable`
contains only request/sink and reason `audit_failed`, no receipt or content. Pointers/bounds follow
`protocol/models.md`; these are service-internal frozen values and have no MCP serializer.

### Privacy receipt inspection

`get_receipt` and `list_receipts` expose bounded structural views only to authenticated ordinary
CLI/UI privacy-control callers; `PrivacyReceiptAudience` has only `trusted_local_control` in v0.1.
MCP, providers, plugins, and workflow operations cannot call them. Query filters are exact
`receipt_id`, outcome, channel/local sink, provider/profile identity, policy version, scope kind,
and bounded UTC interval; pages are at most 100, sorted `(finished_at, receipt_id)` descending, with
an opaque authenticated cursor. Views contain the receipt fields already declared in
`domain/privacy.md`, never proposal/object plaintext, request bodies, excerpts, source pointers,
credentials, or object dereference handles. Privacy receipts remain distinct from the six-operation
verification `receipt` document.

For external dispatch, the credential-free adapter deterministically renders the final application
request body first. The gateway commits that exact body through its `privacy_audit` MAC handle,
binds a fresh one-physical-attempt `ProviderCredentialHandle` to provider/model/endpoint profile+
version, purpose, authorization-scope digest, purpose digest, dispatch ID, body digest, service
generation, and deadline, and places it only
inside the custom HTTP transport callback. The gateway then revalidates the unchanged binding and
atomically consumes privacy authorization as the last authoritative transition before invoking
that callback; a failed consumption invalidates the unused handle. Authentication-header injection cannot change the body;
HTTP/TLS framing and auth metadata are outside the request commitment. Retry requires a new
authorization, dispatch ID, SDK client/transport, credential handle, and receipt.

The v0.1 method is semantic-specific; the other four network channels have independent policy/audit
rows and no generic escape-hatch dispatch method. A future channel adapter must add an explicit
typed method and use the same reserve/authorize/consume/receipt lifecycle.

`reconcile_policy` is the only activation path. After a durable policy commit it immediately fences
and removes newly disallowed bindings, then constructs each newly allowed exact adapter off-registry
using only installed nonsecret profile identity and a credential-free adapter factory. It atomically
swaps a candidate in only if policy/service/vault generations still match. Factory/profile failure
leaves that binding absent. Credential unavailability is checked only while minting the fresh
attempt handle and returns structural semantic unavailability without adding a reusable credential
to the registry. The policy commit remains valid and semantic checks use incomplete-check behavior.
When `require_current_provider_data_use_evidence=true`, external reconciliation additionally
requires the exact installed endpoint's current recommendation-eligible `ProviderDataUseProfile`.
Expiry, unknown/known-broad training/retention/human-access posture, or a changed evidence digest
removes/fences that binding with bounded `endpoint_profile_unavailable`; it does not rewrite policy
or claim provider misbehavior. When the user explicitly sets the guard false through a trusted
policy transition, reconciliation uses the otherwise-supported exact endpoint without presenting
the upstream no-training recommendation.
Startup and successful credential provisioning call the same reconciliation, so a crash after policy commit cannot require an
untracked restart and cannot dispatch through the pre-commit registry.

Reconciliation also requires one service/vault-generation-static `HumanAuthorityCapability`. When source is `unavailable`
(notably `os_keyring` mode without measured strong presence), it atomically removes/fences every
external network binding and reports `human_authority_unavailable`; stored policy is not rewritten
and local-model capability is evaluated separately. External dispatch rechecks the same generation-
bound snapshot before authorization consumption. Restart/relock/ready recomposition remeasures it;
an explicit `UserPresencePort` unavailable result during human control invalidates the snapshot and
triggers reconciliation. v0.1 claims no asynchronous OS-presence watcher or instantaneous detection
of an otherwise unobserved mid-generation platform change. Restoration requires a fresh ready
composition/reconciliation.
The other four network channels use the same composition fence if implemented. This is a safe
capability restriction, not evidence that a human approved an individual request.

The separation is deliberate and exact: `HumanAuthorityCapability` is a runtime fence for external
network activation, provider-credential mutation, and privacy-policy widening; it is not a second
per-call approval for a local disclosure already inside durable policy. Consequently
`source=unavailable` does not by itself disable an already enabled, exact-profile local model. That
local path still requires a policy row whose widening was committed through strong local-human
reauthentication, the exact installed AF_UNIX profile, matching service/vault/policy generations,
the full classifier/minimizer/never-send path, and atomic `consume_local` before the first write. An
invalid or forged durable row fails policy/catalog validation; unavailable authority cannot create
or widen one.

## Errors and edge cases

- Pending human input is a durable `PendingHumanDecision`, not a blocking prompt inside MCP. The
  check can return `OPERATION_PENDING`; local control later resolves it and an identical request
  resumes.
- Human denial and approval expiry are terminal audit outcomes. They never throw a provider error
  or silently become `not_configured`.
- Failure of the initial `reserve` has no durable subject/receipt ID to complete. It returns only the
  bounded application status `audit_failed` and, because it precedes prompt/authorization/dispatch,
  makes no physical attempt. If a later transition fails for an existing reservation, recovery may
  complete that reserved decision with outcome/reason `audit_failed/audit_failed`.
- A dispatch that loses response authority still completes a `late` or `transport_failed` receipt;
  ambiguity never permits reuse of its authorization.
- Audit completion failure after dispatch makes the semantic attempt nonselectable until the exact
  receipt is repaired/resumed from `receipt_pending`; recovery records the real attempt outcome and
  never relabels it `audit_failed` merely because receipt persistence was temporarily unavailable.
- Taskless operations use installation/catalog scope and the same service-vault audit key. They do
  not invent a task/session ID or append a task event. In v0.1 they are structural-only unavailable
  decisions and need no encrypted content object. A future taskless content-bearing channel cannot
  activate until an installation-scoped encrypted audit-object owner/key/storage contract exists.
- `live_object_roots` returns every noncleared catalog content reference for the task regardless of
  proposal state or age. v0.1 has no individual privacy-audit-content deletion operation, so these
  references remain live for the supported installation-data lifetime; ordinary ledger redaction
  and 24-hour orphan GC cannot clear them. A future privacy-audit redaction use case must atomically
  mark content unavailable, clear the catalog ref/increment root generation, preserve structural
  receipts, and only then make ciphertext GC-eligible. Until that use case exists, a ledger
  redaction request targeting a catalog-only privacy object is rejected rather than partially
  deleting it.
- A missing, wrong-task, wrong-kind, digest-mismatched, or undecryptable catalog-rooted object moves
  the affected privacy row to internal `quarantined`, fences all task content disclosure and resume,
  and reports bounded audit degradation; deterministic no-egress task work may continue. Recovery
  never clears/invents the reference. Backup/restore fails `object_missing|object_tampered` until
  explicit verified repair. `quarantined` is a `PrivacyAuditState.status`, not a receipt outcome.
- `close_revoked` is idempotent and closes all sessions whose policy generation or destination is no
  longer permitted; it never waits for a future request to notice tightening.

## Invariants

1. The policy store is the sole mutable policy authority; configuration is a first-run denied seed,
   not a continuing ceiling or bypass around it.
2. No dispatch occurs before durable reservation; the gateway alone atomically consumes
   authorization immediately before adapter I/O.
3. Every physical network attempt has one durable structural receipt, even without a task.
4. Only strong local-human reauthentication can loosen policy/admin authority; an exact
   confirm-every-request preview already inside durable policy uses foreground TTY consent without
   widening proof.
5. Tightening is immediate, monotonic, generation-changing, and closes affected transports.
   “Immediate” means serialized against consume: it prevents any later admission but cannot retract
   an attempt that won the consume CAS first.
6. The gateway never accepts candidate/classified/unapproved bytes or generic URLs.
7. Provider credentials remain behind the service-vault and adapter boundary.
8. Policy commit and registry activation are generation-fenced: absent activation is safe;
   pre-policy or revoked activation is never usable.
9. Per-request TTY consent and durable policy authority are separate types; neither can substitute
   for the other.
10. External network adapters are absent whenever no strong durable-authority mechanism is
    currently available; stored policy/credentials alone cannot bypass the capability fence.
11. Initial reservation failure is the sole no-receipt decision exception; it is pre-prompt and
    pre-dispatch. Every successfully reserved terminal decision and every physical attempt is
    durably receipted before its result is usable.
12. Content-bearing audit objects are task-bundle encrypted in v0.1; taskless audit rows contain only
    their closed structural schema.
13. Every catalog-held privacy `ObjectRef` is an explicit live root independent of task-ledger object
    inventory; GC/backup/restore bind its exact root generation and digest.
14. Decision, network-attempt, and local-disclosure completion reject missing or cross-paired
    failure reasons and reject a reason on `completed`.
15. A local sink receives bytes only after `consume_local`; agent-context completion is durable
    before serialization and local-model replay never repeats a consumed proposal.
16. Structural privacy receipt inspection is CLI/UI-only and cannot dereference content.
17. External human-authority capability and local-model disclosure authority are intentionally
    non-interchangeable: unavailable external activation neither widens nor revokes a valid local
    policy, and every local write remains policy-, profile-, generation-, scan-, and receipt-gated.

## Tests

- Contract tests run policy/audit state machines against memory and catalog-backed adapters,
  including both audit-subject branches, restart, generation race, expiry, denial,
  `receipt_pending`, ambiguous dispatch, taskless structural decisions, and receipt repair.
- Conformance tests prove MCP/agent inputs cannot construct human decisions, every provider call is
  preceded by authorization consumption, and policy tightening closes live external sessions.
- Fault tests fail each audit write and prove initial reserve returns bounded no-receipt
  `audit_failed`, later reserved failures can be receipted after recovery, and no unreceipted
  semantic result can be selected.
- Root-set tests race catalog proposal commits/route changes against GC, backup, restore, and
  recovery; no live catalog ref is swept, omitted, silently rebound, or forced into ledger inventory.
- Race tests force both tighten-before-consume and consume-before-tighten orderings for external,
  local-model, and agent-context sinks and assert the exact no-I/O/admitted-attempt semantics.
- Local-disclosure tests cover atomic agent projection, local-model consume-before-write, crash/
  replay, fresh-retry identity, all-approved/all-omitted/mixed fields, and receipt list/get access
  control.

## Open questions

None.
