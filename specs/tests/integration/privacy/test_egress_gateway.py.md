# tests/integration/privacy/test_egress_gateway.py — durable privacy pipeline and adapter isolation

**Wave:** C/E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
privacy application/ports/adapters, local service, object/privacy audit stores, PRIV fixtures
**Imported by:** integration suite, release privacy evidence

## Purpose

Exercise classification through durable authorization, final validation, dispatch and structural
receipt across crashes/retries while proving composition supplies reviewed bundled adapters no
ambient local handles and those adapters honor the no-fetch contract. This is not a sandbox test.

## Public surface

Tests cover `PRIV-001` through `PRIV-008` against memory and durable privacy-audit adapters, scripted
classifier/minimizer/scanner, isolated local/external provider fakes, deterministic clock/IDs, and a
network spy distinguishing AF_UNIX from AF_INET/AF_INET6/DNS/redirect.

## Behavior

Reconciliation is asserted against the real dispatch table, not a stub: a bundled Chat Completions
preset built by `adapters/providers/factory.md` activates with no unavailable binding, and an empty
builder table still reports `factory_unavailable` rather than passing quietly. The config-to-policy
binding key agreement has no other executable owner, and its failure mode is invisible from the
caller's side — the check returns, the semantic review just never happened.

For each profile assert exact state transitions, adapter constructor count, final bytes, categories,
scope, review-context selection, omissions, destination and receipt. Confirm-every-request persists
exact one-dispatch approval across a
crash before authorization consumption and resumes that same dispatch without re-prompting. Once a
physical attempt is consumed, retry—even with identical bytes—requires a fresh exact foreground
preview/decision and new proposal/authorization/dispatch/receipt; changed bytes/policy/scope/expiry
also invalidate prior authority. Provider adapters accept only
`ApprovedOutboundCase`; attempts to receive candidate context, repository/bundle/object handles,
environment or policy authority fail. Audit prepare must be durable before I/O; kill before/after
wire boundary yields a terminal blocked receipt, internal `receipt_pending`, or a recovered
`transport_failed/outcome_unknown` terminal attempt receipt without blind duplicate. No
`dispatched` receipt outcome exists. `awaiting_human` and `approved` remain nonterminal state and
produce no finished receipt.
The registry rejects third-party/dynamic adapter factories. Monkeypatched file/environment/database/
subprocess APIs prove the bundled adapter does not call them, without claiming hostile in-process
Python could not do so.

Before classification, the local selector deterministically applies the effective
`ReviewContextProfile`; after classification, policy can only reduce that result. Assisted fixtures
contain the goal, material timeline, deterministic bases, honest change visibility, and only
mechanically linked recorded excerpts. Their omission manifest distinguishes
`not_recorded|not_selected|withheld_by_policy|redacted_never_send`. Monkeypatched Git/filesystem/
object lookup proves neither selection nor provider evaluation performs a live source fetch.
Structural review sends deterministic kind/priority/root/basis fields without finding prose or
summary/detail item refs. Goal-aware-or-richer review includes those refs only as an exact pair when
`finding_summary` and its data class are independently authorized.

The upstream assisted recommendation is available only when the exact endpoint's current installed
data-use record says training `prohibited`, retention `none|bounded`, and provider human access
`prohibited|restricted`. For an already selected external `assisted` case, known-broad/stale/unknown/mismatched records
return `endpoint_profile_unavailable` before proposal or dispatch when the editable current-evidence
guard is true; an explicit trusted custom transition can turn that guard off and still select the
exact otherwise-supported endpoint without the recommendation claim. Once the resulting standing policy is
committed, ordinary attempts and approved retries use fresh authorizations and receipts with zero
human-control calls; only `confirm_every_request` re-prompts per attempt.

With agent-context `finding_summary` allowed, the projected ordinary finding summary/detail carries
the post-validated direct reviewer message and requested next step. With it denied, the normal
omission marker appears and no challenge plaintext leaks. A reviewer challenge is provider-derived,
so provenance never admits it and only this explicit grant does. There is no second advisory-message
projection or provider-conversation channel.

Provenance cases run on the default zero-egress policy with no `agent_context` category grant. The
requesting writer's own claim prose, and a deterministic finding whose `subject_refs` are all its
own, project; a subagent's material, an imported Codex event, a finding mixing self-authored and
other-writer refs, and a semantic challenge do not. A caller-asserted provenance is ignored, and an
unresolvable ref denies. Re-projecting at a later frontier recomputes provenance and proves a
foreign event appended afterwards cannot retroactively widen an earlier resolution.

Sink cases prove the same operation projects to `local_human_view` for human-readable output on an
attached controlling terminal and to `agent_context` for `--json`, a redirected stream, and every
`mcp_bridge` client; that the client cannot select its own sink; that `local_human_view` admits
ordinary user content the unwidened `agent_context` withholds; and that both sinks reserve and
complete a `LocalDisclosureReceipt`. Neither sink admits `sensitive_confidential` or any never-send
kind under any provenance.

For every external physical attempt, freeze the deterministic adapter's exact final application
body bytes and assert the receipt MAC uses the byte-exact
`b"yoetz/privacy-egress-request/v1\x00"` domain plus that body only. Mutating body bytes changes or
invalidates the attempt; changing auth headers/TLS framing is outside the commitment and cannot
change approved content. The gateway mints a fresh provider/model/endpoint-profile/version/purpose/
authorization-scope-digest/purpose-digest/dispatch/body-digest/service-generation/deadline-bound
credential handle; the transport carries the
precomputed request commitment separately. Only the custom transport callback consumes the handle
for one header injection after byte/digest/commitment equality;
the per-attempt SDK client/default headers retain no real credential, and retry uses a distinct
handle/client/dispatch. Renderer-injected secret canaries fail the gateway's final-body scan before
consumption or I/O. Identity assertions prove the coordinator and gateway call the same injected
`PrivacyClassifierPort`/immutable ruleset rather than two independently configured scanners.
The attempt receipt also requires `audit_store_version=1`, exact algorithm token
`hmac-sha256/yoetz-privacy-egress-request-v1`, canonical commitment, and exact
`counts.request_body_bytes`; `key_slot_ref` is rejected.
Terminal completion omits `safe_failure_reason`; every failed decision/attempt requires exactly one
reason compatible with its outcome. The same check covers local-model and agent-context
`LocalDisclosureReceipt` completion/failure paths.

Run the task-scoped LLM case plus taskless telemetry/crash/update/capability unavailability cases.
With the global ceiling true, the LLM-only row makes exactly one approved network attempt/attempt
receipt. Each non-LLM enabling transition is rejected `channel_unavailable`, keeps the durable row
off, constructs no adapter, and makes zero network attempts. A forced/imported enabled row is fenced
again at use time and yields only a pre-dispatch outcome/reason `channel_unavailable/channel_unavailable` decision
receipt without authorization, dispatch, request commitment, or attempt-body fields. False ceiling
plus any enabled channel fails before construction; true ceiling plus no enabled channel makes zero
attempts. Reconciliation after an upgrade/new capability still leaves those rows off and requires a
fresh exact local-human transition; no dormant v0.1 consent activates. Run the composite
zero-network deterministic and local-model variants with `local_only`,
false ceiling, and all channels denied: authenticated AF_UNIX service control is allowed, external
socket/DNS/redirect/download/telemetry/update/capability attempts are zero, and local disclosure
receipts contain no canary. The harness also allows only exact release-cell service/confidential,
optional local-model, and OS credential/user-presence/session-lifecycle security IPC; arbitrary
AF_UNIX/D-Bus/proxy use fails,
and evidence names the exact Yoetz-owned process/readiness boundary and external-agent exclusions.

Provider refusal, timeout or invalid response completes deterministic check as
`incomplete_check`, exposes that semantic review is incomplete, and returns final deterministic
results with no semantic findings. It does not roll back the already accurate egress receipt.

## Errors and edge cases

Fault points cover proposal persistence, approval commit, authorization consume, audit prepare,
body render/commit, credential-handle mint/consume/header injection, before connect, after
write/before response, receipt finish, service generation change, cancellation, relock and policy
tightening. Wrong body/profile/commitment/deadline, callback reuse/retention, proxy/netrc/env
influence, destination/TLS rewrite, stock SDK transport, nonidentity content encoding, or declared/
chunked response over 1_048_576 bytes fails before credential exposure or bounded parsing. No fault
logs case/response/proxy URL/secret plaintext.
Initial reserve failure returns bounded `audit_failed` with no `ppr_`/`egr_`, prompt,
authorization, or dispatch. Failure after a committed reservation can terminalize
`audit_failed/audit_failed` after recovery. Failure after consumption remains `receipt_pending`,
quarantines the semantic result, and recovery writes the real attempt outcome.

## Invariants

1. Only validated approved cases reach adapters.
2. Privacy audit covers taskless and task-scoped channels durably.
3. Retry cannot broaden or duplicate disclosure.
4. Local service control is not external egress.
5. Already-wide policy plus an existing credential still produces an empty/fenced external registry
   when human authority is unavailable; restart/relock stays fenced, an explicit presence-
   unavailable result reconciles closed, and restoration requires fresh ready composition without
   rewriting stored policy. No asynchronous watcher is assumed.
6. Semantic incompleteness preserves deterministic output and is explicit.
7. Request commitments bind only exact final application body bytes, while real credentials exist
   only in one-attempt custom-transport callbacks and never SDK client state.
8. Adapter least authority is a closed-composition/review/test property, not OS process isolation.
9. The global network ceiling and privacy profile are independent: the ceiling grants nothing,
   while `local_only` constrains external LLM/user-content disclosure rather than all non-LLM
   structural traffic.
10. v0.1 non-LLM channel absence is a pre-dispatch unavailable decision, not a physical or ambiguous
    transport attempt, and later capability reconciliation consumes no old intent.
11. `confirm_every_request` binds one foreground decision to one physical dispatch; only automatic
    profiles can consume retry budget without another human preview.
12. Early blocks reserve the structural `PreDispatchAuditDecision` branch; prepared proposals are
    task-bundle encrypted privacy-audit objects referenced from catalog, while taskless unavailable
    decisions contain no content object.
13. Receipt outcomes are terminal and initial reservation failure is the sole no-receipt exception.
14. A committed proposal increments catalog privacy-root generation and becomes live without a
    task-ledger inventory row; route/GC races preserve or quarantine it but never sweep it.
15. Context selection, disclosure authority, and provider recommendation evidence are separate
    gates; none can widen either of the others.
16. `HumanAuthorityCapability.source=unavailable` empties the external registry but does not itself
    disable a previously reauthenticated exact local-model row; the local case still traverses
    exact profile/service/vault/policy-generation/shared-scan/`consume_local` gates, and no
    unavailable snapshot can enable or widen it.

## Tests

Run `uv run --locked pytest tests/integration/privacy/test_egress_gateway.py -q --timeout=180` with
network denial except the fixture-owned authenticated AF_UNIX control socket.

## Open questions

None.
