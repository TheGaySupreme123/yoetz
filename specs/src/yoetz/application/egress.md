# src/yoetz/application/egress.py — centralized disclosure and outbound-request coordinator

**Wave:** D–E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/privacy.md`, `ports/semantic.md`, `ports/clock.md`, `protocol/errors.md` |
**Imported by:** `application/check.md`, `application/service.md`, MCP disclosure projection,
future telemetry/diagnostic/update/capability use cases

## Purpose

Implement the one Yoetz-enforced route from candidate context to a local disclosure or external
request. It classifies, intersects policy, obtains durable human approval when required, minimizes
and scans exact bytes before any preview, obtains durable human approval of that exact prepared
case when required, mints a single-use authorization, calls the bounded gateway, and guarantees a
privacy receipt after successful audit reservation. Initial reservation failure is the one explicit
pre-prompt/pre-dispatch no-receipt exception. No provider, transport, CLI, MCP, plugin, or feature
coordinator may reproduce or skip these steps.

## Public surface

- `class PrivacyCoordinator` with injected `PrivacyPolicyStorePort`, `PrivacyClassifierPort`,
  `PrivacyAuditPort`, optional `HumanPrivacyControlPort`, `OutboundGatewayPort`, and `ClockPort`.
- `async evaluate_semantic(candidate: CandidateContext, deadline: Deadline) -> SemanticEgressResult`.
- `async prepare_local_disclosure(candidate: CandidateContext) -> LocalDisclosureResult`.
- `async resume(request_id: str, case_digest: str, deadline: Deadline) -> SemanticEgressResult`.
- `async close() -> None` — closes/revokes gateway sessions and releases no vault secret upstream.
- Closed `SemanticEgressResult`: `SemanticEgressSuccess`, `SemanticEgressAwaitingHuman`,
  `SemanticEgressBlocked`, `SemanticEgressProviderOutcome`.
- Closed `LocalDisclosureResult`: `LocalDisclosureApproved`, `LocalDisclosureBlocked`, or
  `LocalDisclosureUnavailable`, with exact fields owned by `domain/privacy.md` and
  `ports/privacy.md`.

The coordinator returns only bounded values and reason codes. It never returns candidate plaintext,
approval preview bytes, provider credential handles, authorization bearer material, or raw provider
responses to CLI/MCP.

## Behavior

### Semantic egress pipeline

For an LLM semantic request, `evaluate_semantic` performs exactly:

1. Validate `candidate.channel == llm_inference`, exact purpose `semantic_check`, frozen
   frontier/dependency/case identity, exact `ReviewContextProfile`, and requested local/external
   provider binding.
2. Load the effective policy as the intersection of machine ceiling plus workspace/task/request
   overlays. A concurrent generation change restarts policy evaluation before dispatch.
3. Classify every candidate item locally. Any unknown/conflicting scope or never-send finding
   creates a structural `PreDispatchAuditDecision`, reserves it, durably completes its exact blocked
   decision receipt, and performs no prepared-case construction, provider construction, or call.
4. Evaluate profile and sink rules:
   - `local_only`: external binding is blocked; a separately enabled exact local-model binding may
     continue through the same classifier/minimizer/scan path.
   - `confirm_every_request`: mark the prepared case as requiring a trusted local-human decision.
   - `minimal_external`: auto-approval is allowed only for the policy's smallest category/size set.
   - `trusted_provider`: auto-approval is allowed only for the exact provider/model/endpoint,
     purpose, scope, and listed categories; broader candidate items are removed or blocked.
   Independently enforce the effective compiled `ReviewSelectionPolicy` before those disclosure
   rules. `structural` and `goal_aware` cannot contain excerpts; `assisted` may contain only
   mechanically linked recorded problem-local excerpts; `expanded|custom` still remain inside
   their exact section/kind/relevance/command/cap selector and recorded refs. When
   `require_current_provider_data_use_evidence=true`, the bound installed endpoint must retain a
   current recommendation-eligible record; stale, unknown, known-broad, or mismatched evidence
   returns bounded `endpoint_profile_unavailable` before proposal/dispatch. With the guard false,
   an explicitly approved otherwise-supported exact endpoint may proceed but has no upstream
   no-training recommendation claim.
5. Minimize, redact, canonicalize, and scan the exact proposed bytes locally. Re-run the policy
   intersection after preparation. A forbidden match, empty/misleading case, or changed policy
   before a valid case exists follows the same structural pre-dispatch-subject branch and retains no
   denied bytes. Otherwise construct a task-owned `DisclosureProposal` and persist
   `PrivacyAuditPort.reserve` with its encrypted exact prepared excerpts and case digest before
   presenting a preview or preparing dispatch.
   The omission manifest preserves `not_recorded|not_selected|withheld_by_policy|
   redacted_never_send` without omitted plaintext. It is invalid to rewrite any of those as
   unchanged content or an empty diff.
6. If human approval is required, the confidential control surface renders those exact prepared
   excerpts, transformations, categories, destination, purpose, scope, and ceilings. If no trusted
   control port is attached, return `SemanticEgressAwaitingHuman` with only proposal/request IDs
   and expiry; this is a nonterminal audit state and creates no `EgressReceipt`. MCP never receives
   excerpts or an approval token. For `confirm_every_request`, the
   case must already be inside every durable policy ceiling and exact foreground `/dev/tty`
   approve/deny is sufficient—no OS/passphrase reauthentication is requested and no durable
   authority changes. The decision authorizes one physical dispatch only. A broader case is blocked
   and routed to a separate policy proposal.
7. Persist approval, denial, or expiry against the prepared-case digest. Denial/expiry returns a
   terminal blocked result. Changed bytes, policy, case, provider, purpose, or scope invalidates the
   decision. Revalidate the prepared bytes and current policy, then mint an exact one-use
   authorization; no post-approval transform may add or substitute content.
8. For external inference, pass the exact still-valid, unconsumed authorization with the immutable
   approved case to `OutboundGatewayPort.dispatch_external_semantic`. The gateway is the sole
   component that atomically consumes it, immediately before invoking adapter I/O. For local
   inference, finalize the approved local-disclosure case/reservation and call
   `dispatch_local_semantic` with no network authorization. External retry always has a new
   dispatch/authorization/receipt. Under `confirm_every_request`, it additionally creates a fresh
   proposal and repeats the exact foreground preview/decision even when body bytes are identical;
   the consumed decision cannot hide multiple physical attempts. Automatic profiles may create the
   retry within current policy and the one total retry/deadline budget without human preview.
9. For every successfully reserved terminal pre-dispatch decision, durably
   complete a structural `EgressReceipt` keyed by its audit subject/reservation. Such a receipt has
   no dispatch or authorization ID unless those stages actually occurred. Build and durably
   complete a separate terminal attempt receipt for every physical provider attempt. Initial
   reservation failure is the sole no-receipt decision exception and necessarily occurs before
   prompt, authorization, or dispatch. Only after the applicable receipt is durable may a semantic
   result return for deterministic post-validation. Refusal, timeout, invalid, unavailable, late,
   or stale outcomes carry no semantic findings.

### Resume and human approval

`resume` loads state by exact request and audit-subject digest. `awaiting_human` remains pending; it never
prompts on an MCP worker thread. Approved proposals continue from the persisted prepared boundary;
denied/expired proposals return their terminal reason; `receipt_pending` attempts repair or
complete their terminal receipt before considering retry. If authorization was never consumed, resume
continues the same approved dispatch without another prompt. Once a physical attempt was consumed,
`confirm_every_request` requires a fresh preview and decision; automatic profiles may follow their
bounded retry policy. Restart never rebuilds preview bytes from a changed frontier under an old
approval.

A trusted UI or explicit privacy-control CLI invokes `application/privacy_policy.md`/the confidential
control surface to resolve the proposal. A normal workflow CLI argument, stdin piped by an agent,
MCP tool argument, environment variable, config file, or LLM message cannot submit approval.

The committed `assisted` workspace policy is automatic baseline authority: ordinary check calls,
eligible retries, reviewer challenges, agent responses, and rechecks never enter the human-preview
branch. Each physical attempt still receives its own authorization and receipt. Only
`confirm_every_request`, policy widening, credential mutation, and the separate human waiver flow
require a human; never-send matches remain unapprovable.

### Local disclosure

`prepare_local_disclosure` applies classification, effective policy, minimization/redaction, exact
secret scan, local consume, and durable `LocalDisclosureReceipt` without creating an
`EgressAuthorization`.

- `local_model` receives only an approved bounded model case over its exact service-approved AF_UNIX
  profile. It cannot receive never-send material and Yoetz's delivery is not counted as network
  egress. A separately running model process is a trusted disclosure sink whose own ambient
  network behavior is outside that transport fact unless the exact profile proves sandboxing
  (F-013).
- `agent_context` is the required path before user/task content enters an MCP response or other
  agent/LLM-facing projection. For purpose `client_result_projection`, the service classifies every
  registered content leaf, deterministically preserves allowed leaves/replaces denied leaves with
  exact omission markers, and atomically reserves, baseline-approves, consumes, and completes the
  local receipt before returning any projected bytes for serialization. The replay key is exact
  request/method/internal-result/sink/policy digest; a same-key replay returns the same projection
  and receipt.
- `trusted_human_control` may render a bounded preview or exact policy diff after local control
  authentication. It still cannot render provider credentials, keyring contents, encryption keys,
  recovery/unlock secrets, or hidden authentication material.

### Required semantic fallback

The coordinator communicates exact blocked/provider status to `application/check.md`. In
`semantic_required`, absent approved external/local capability, policy block, forbidden data,
classification uncertainty, human denial/expiry, refusal, timeout, invalid response, exhausted
retry, audit failure, late, or stale result means the public check completes with deterministic
findings, no semantic findings, `verdict=incomplete_check`, weakened coverage, and the exact
semantic/egress reason. "Required" never means throw away the deterministic result or fail to
return. Malformed request or storage corruption before the deterministic result remains an ordinary
operation error.

v0.1 has no callable telemetry, crash-upload, update-check, or capability-test use case. If a
forced/imported policy state presents one of those channels to this coordinator, it completes a
structural `PreDispatchAuditDecision` plus outcome/reason
`channel_unavailable/channel_unavailable` decision receipt and returns without authorization,
gateway invocation, DNS, or socket I/O. Setup rejects such enablement and
stores no dormant consent; future capability installation requires a fresh human-confirmed policy
transition before any new use case can be composed.

For `semantic_if_configured`, capability absent or policy-disabled before an attempt may complete
with the deterministic verdict while recording `not_configured`/`blocked_by_policy`; an attempted
but unsuccessful semantic evaluation records its exact status and coverage gap.

## Errors and edge cases

- Initial audit reservation failure: no prompt, authorization, dispatch, or receipt ID; return only
  bounded application status `audit_failed` to the coordinator. If a later audit transition fails
  after reservation, keep the reserved subject recoverable and durably complete its exact
  `audit_failed/audit_failed` decision receipt once the store recovers. A receipt failure after
  consumption remains `receipt_pending` and preserves the real attempt outcome.
- Policy tightening and consume share one generation CAS. Tightening-first revokes authority,
  records a no-dispatch `blocked_by_policy` decision, and performs no I/O. Consume-first admits one
  attempt that may cross before best-effort closure; it records the real terminal/unknown receipt,
  cannot steer under the new policy, and cannot be retried with old authority.
- Cancellation before consumption leaves resumable proposal/approval state. Cancellation after
  consumption resolves the physical attempt and receipt as late/ambiguous before any retry.
- A response body that cannot be normalized is `invalid_response`; raw bytes are not retained by
  the normal privacy audit path.
- Minimization that removes all material returns outcome `blocked_by_policy` with exact reason
  `insufficient_approved_context`; it never sends an empty misleading prompt.
- Taskless channels must supply installation scope. This coordinator refuses invented task IDs and
  uses the same audit lifecycle.
- For `agent_context`, initial reserve failure returns
  `LocalDisclosureUnavailable(audit_failed)` and no content bytes; all-approved, all-omitted, and
  mixed results are successful durable local-disclosure outcomes. For `local_model`,
  `consume_local` occurs immediately before the first AF_UNIX write; after consumption,
  crash/timeout cannot resend that proposal and must close its real or `outcome_unknown` receipt
  before a fresh-proposal retry.

## Invariants

1. There is one path from candidate content to any model or agent-context sink.
2. Every user-content byte in the exact final provider/application request body has passed
   classification, policy, minimization/redaction, and secret scanning under the still-current
   generation; authentication metadata and HTTP/TLS framing are separate transport material.
3. The coordinator supplies only a still-valid, unconsumed, exact one-use authorization; the
   gateway atomically consumes it immediately before the provider call.
4. Every physical outbound attempt has a durable receipt before its result can influence a check.
5. Human approval is local, authenticated, resumable, and never transport-supplied.
6. Provider failure cannot erase or falsely upgrade deterministic results.
7. Closing/tightening policy does not wait for the next request to revoke live external sessions.
8. A pre-dispatch block is represented by a structural audit subject, never a fabricated prepared
   case or commitment to denied content.
9. Initial reservation failure is explicitly unreceipted but cannot conceal an attempt; every
   successfully reserved terminal decision is durably receipted. `awaiting_human` and `approved`
   remain nonterminal state and never masquerade as finished receipts.
10. Agent-context projection is completed and receipted before ordinary serialization; local-model
    disclosure is consumed once immediately before its physical write.
11. Review-context selection can remove candidate items but cannot authorize them, and missing code
    visibility never becomes a same-state observation.

## Tests

- Unit tests cover every profile, five-channel independence, three local sinks, scope/category
  intersection, minimization order, never-send block, and semantic fallback matrix.
- Integration tests kill/restart at every proposal/decision/prepare/authorize/consume/receipt
  boundary, including `decision_receipt_pending` and `receipt_pending`, and prove exact resume
  without duplicate dispatch.
- Conformance tests monkeypatch every provider/network constructor and prove it is reachable only
  through the gateway with an approved case; MCP output canaries prove the agent-context fence.
- Zero-egress subprocess tests allow only exact profiled AF_UNIX service/confidential/local-model
  sockets plus release-tested OS credential, user-presence, and session-lifecycle local IPC; they
  deny arbitrary AF_UNIX or bus use, DNS, AF_INET, AF_INET6, proxies, redirects, and all five
  network channels.
- Fault tests distinguish no-receipt initial reservation failure, recoverable post-reservation
  `audit_failed`, and post-consumption receipt repair that retains the real attempt outcome.

## Open questions

None.
