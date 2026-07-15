# ADR-009 — Central privacy, disclosure, and data-egress control

**Status:** Working decision for spec drafting (2026-07-14). Ratification requires an independent
privacy/security review plus executable no-bypass, never-send, approval-resume, and zero-egress
evidence.
**Owning public specs:** `specs/src/yoetz/domain/privacy.md`,
`specs/src/yoetz/application/egress.md`,
`specs/src/yoetz/application/privacy_policy.md`,
`specs/src/yoetz/ports/privacy.md`, privacy adapters/configuration/audit specs, ADR-006,
`PRIVACY.md`, the technical privacy protocol, policy schemas, fixtures, and tests.

## Context and trust boundary

Yoetz is the policy authority. CLI, MCP, future UI, plugins, provider adapters, integrations,
agents, and LLMs are clients or constrained effectors; none may decide what data is disclosable.
Network egress and local disclosure are distinct: an MCP response may enter an agent/LLM context,
and a local model still receives user content through Yoetz's AF_UNIX-only path. A pre-existing
model runtime is a separate trusted disclosure sink unless its exact support cell proves enforced
no-network isolation; AF_UNIX delivery alone does not prove what that process does later (F-013).

Every candidate disclosure follows one enforced path. After audit reservation succeeds, each
terminal branch ends in a durable privacy receipt; the sole exception and its fail-closed boundary
are fixed in decision 9:

`candidate context → deterministic classification → effective policy → local
minimization/redaction/secret scan → optional durable human preview/approval of the exact prepared
case → single-use authorization → bounded gateway → bound sink/provider → durable privacy receipt`

## Decisions

1. **Four LLM-disclosure profiles:** `local_only`, `confirm_every_request`, `minimal_external`, and
   `trusted_provider`. These values govern LLM inference and its content-disclosure rules; they are
   not a bundled consent switch for telemetry, diagnostics, updates, or capability testing.
   `local_only` constructs no external LLM-provider transport, disables the `llm_inference`
   network channel, and permits a local model only when separately configured and explicitly
   trusted under resolved decision F-013. It may coexist with a separately authorized bounded
   non-LLM policy row, but
   no such channel may carry task/user content. v0.1 owns no production transport for those four
   channels, so proposed enablement is rejected and makes no I/O; adding one later requires an exact
   adapter/use-case owner, ADR review, and fresh human transition.
   `confirm_every_request` requires an exact durable preview
   of the already minimized/redacted/scanned outbound case and a local-human decision for each
   external request. `minimal_external` automatically permits
   only the smallest context allowed by its policy. `trusted_provider` permits explicitly listed
   categories only for one bound provider, endpoint profile, workspace/task scope, and purpose; it
   never means unrestricted access.
2. **Global ceiling plus independent network channels:** `network_egress_permitted` is an explicit
   global boolean ceiling. `false` requires every network channel disabled; `true` authorizes
   nothing by itself. Beneath that ceiling, policy and consent are separate for exactly
   `llm_inference`, `product_telemetry`, `crash_diagnostics`, `update_checks`, and
   `capability_testing`. Enabling one never enables another. All four non-LLM channels are limited
   to their reviewed bounded structural or synthetic schemas and cannot carry ordinary or
   sensitive task/user content. In v0.1 they are policy vocabulary with no production transport:
   setup marks them unsupported/off and rejects proposed enablement as `channel_unavailable`
   without persisting dormant consent. A forced/imported enabled state is fenced at use time,
   records a structural no-dispatch `channel_unavailable` decision receipt, and performs no
   DNS/socket I/O. A future capability needs a fresh local-human policy transition. The ceiling and
   every channel default denied; absence is not silently replaced by a generic HTTP client.
3. **Local disclosure sinks:** `local_model`, `agent_context`, and `trusted_human_control` are not
   network-egress channels. The first two receive only policy-approved minimized content and are
   covered by the never-send fence. The trusted human surface may render an approval preview and
   exact policy diff, but it never exposes cryptographic material or service-vault credentials.
   A local-model runtime receives plaintext and belongs to the trusted local computing base unless
   its exact artifact/profile supplies independently enforceable sandbox evidence; Yoetz's adapter
   makes no claim about another process's ambient network authority.
4. **Data classes:** structural public data, ordinary task/user content, sensitive/confidential
   content, and secrets/cryptographic material are distinct. Classification combines source-owned
   structural labels, scope validation, and deterministic secret scanning. Unknown or conflicting
   classification fails closed for disclosure.
5. **Non-overridable never-send set:** encryption keys, recovery/unlock secrets, passwords,
   candidate/user-discovered API keys, authentication tokens, cookies, private certificates,
   keyring contents, unrelated
   environment variables, credential files, hidden authentication configuration, unselected raw
   database contents, unrestricted logs/stderr/transcripts, and files outside the selected scope
   can never be placed in an approved external, local-model, or agent-context case. No profile,
   local-human click, provider trust label, plugin, or request override can weaken this set.
6. **Policy composition:** the effective decision is the intersection of a machine policy ceiling
   with workspace, task, and request overlays. Lower scopes may tighten immediately. Loosening any
   effective permission requires a locally authenticated human on a trusted control surface,
   reauthentication, an exact diff, and a durable decision; MCP/agent/LLM calls can request more
   context but cannot approve or persist the expansion.
   Policy commit and external/local consumption share one generation-CAS linearization point.
   Tightening-first prevents I/O and closes the unconsumed branch with a no-dispatch receipt;
   consume-first admits one attempt that may send, must be best-effort closed/nonselectable, and
   records its actual terminal or unknown receipt. Tightening never claims to retract bytes already
   admitted.
7. **Human approval is resumable authority:** content-bearing previews and decisions are encrypted
   durable `ObjectKind.privacy_audit` objects in the owning task bundle. Taskless v0.1 decisions and
   machine policy diffs are closed nonsecret structural catalog rows; a future taskless
   content-bearing channel requires its own installation-scoped encrypted audit-object contract.
   Agent-context result projection instead uses a dedicated structural subject with keyed
   internal/projection commitments and field decisions; it never duplicates result plaintext.
   Each task-bundle privacy object is an explicit installation-catalog live root without a fabricated
   task-ledger inventory row. v0.1 retains it for the supported installation-data lifetime and has no
   individual privacy-audit-content deletion operation; ordinary ledger redaction/GC cannot clear
   the root.
   Approval binds exact request/case digest, excerpts/categories, provider/model/endpoint,
   purpose, scope, policy version, byte/token ceilings, expiry, and one dispatch. Denial and expiry
   are terminal, durable outcomes. Restart never converts a pending or expired proposal into
   approval. Under `confirm_every_request`, every physical attempt—including an otherwise eligible
   retry of identical bytes—requires a fresh exact foreground preview and decision; consumed or
   outcome-unknown authority cannot authorize another attempt. Crash/resume before authorization
   consumption continues the same approved dispatch and does not invent a retry. Automatic
   profiles may mint retry attempts inside their existing policy and total retry budget, but each
   still receives a fresh authorization, dispatch identity, credential handle, and receipt.
8. **Provider and integration confinement:** provider adapters receive only an
   `ApprovedOutboundCase`; composition gives them no repository/database/environment/transcript or
   other ambient handle, and the outbound gateway is the only component holding a network-capable
   transport. v0.1 permits only reviewed bundled adapters in the closed registry and forbids
   third-party/dynamic adapters. This is a dependency/capability contract, not an OS sandbox: a
   malicious in-process Python adapter could use the service account's ambient authority. Bundled
   adapters are reviewed/tested not to fetch additional context, follow redirects, inspect process
   state, or widen content. A final-body/profile/MAC/credential failure before consume revokes the
   unconsumed authorization and writes a no-dispatch decision receipt; only consumed work can
   create an attempt receipt. Tightening revokes pending grants and best-effort closes affected
   admitted transports.
9. **Unified durable audit:** every successfully reserved terminal outbound decision and every
   physical outbound attempt, including taskless update or capability channels, produces a durable
   structural `EgressReceipt` through `PrivacyAuditPort`. `awaiting_human`, `approved`, and
   `receipt_pending` are nonterminal audit states, never receipt outcomes. Initial reservation
   failure is the sole no-receipt exception: it returns bounded `audit_failed` before prompt,
   authorization, or dispatch and fabricates no receipt identity. A later transition failure for an
   existing reservation may be durably completed `audit_failed/audit_failed`; post-consumption
   receipt failure stays `receipt_pending` until recovery records the real attempt outcome.
   It records provider/model/endpoint profile, policy version/digest, authorization scope,
   categories approved/blocked, byte/token counts, redaction/minimization counts, consent source,
   outcome/reason, and a keyed commitment to the exact final provider/application request body
   bytes—never plaintext. v0.1 freezes `audit_store_version=1` and commitment object
   `{algorithm: "hmac-sha256/yoetz-privacy-egress-request-v1", commitment:
   "hmac-sha256:<64 lowercase hex>"}`; it has no `key_slot_ref` because K_audit has no v0.1
   rotation/slot interface. A dispatch receipt also requires exact `request_body_bytes`; a
   pre-dispatch receipt forbids it. HTTP/TLS framing, transport-generated metadata, and credential-bearing
   authentication fields are outside that commitment; provider credentials enter only through a
   separately bound vault handle at the transport boundary and cannot alter the approved body.
   Resolved decision F-012 permits this separately provisioned vault credential to leave only as
   one-attempt authentication metadata to the exact pinned TLS
   endpoint; candidate/user-discovered credentials remain never-send. Catalog-backed audit is
   permitted; content-bearing task audit
   uses encrypted bundle objects referenced directly from the privacy catalog, while structural-only
   taskless audit requires no content object. No new task event family is required. A v0.1 non-LLM
   `channel_unavailable` decision is pre-dispatch: it has no dispatch
   ID/time, request commitment, or network I/O and is distinguishable from an attempted or
   ambiguous transport failure.
   Backup pins the privacy-root generation/digest and carries a canonical structural audit sidecar
   plus every rooted encrypted object. Route move proves the complete current root set before CAS
   and invalidates nonterminal disclosure authority under the new owner generation; clean restore
   likewise preserves terminal evidence but restores no live disclosure authority.
   A dangling/tampered catalog root quarantines the audit row and fences content disclosure rather
   than being swept or repaired with an invented ledger row.
10. **Zero-egress definition:** true Yoetz zero-network egress is the composite policy state
    `profile=local_only`, `network_egress_permitted=false`, all five channels disabled, and no
    network-capable runtime path. In that state the Yoetz-owned tested process set permits only the
    exact service/confidential AF_UNIX endpoints; a separately approved exact local-model AF_UNIX
    endpoint; and exact release-cell platform IPC needed for OS credential storage, user presence,
    or session-lifecycle security events.
    The last category includes measured Linux AF_UNIX routes to allowlisted session-bus Secret
    Service peers/methods and, separately, system-bus `org.freedesktop.login1` peers/methods, or
    measured macOS native security/presence/session notifications; it never permits
    arbitrary AF_UNIX, arbitrary bus names/methods, or a local proxy. Yoetz denies AF_INET, AF_INET6,
    DNS, proxies, redirects, external provider
    construction, telemetry, diagnostics upload, update checks, and capability calls. The
    `local_only` profile alone is not a zero-network claim because a future owned capability may be
    separately enabled under the global ceiling (v0.1's four non-LLM rows remain unsupported/off).
    Evidence names the exact platform/release profile, Yoetz service/client/confidential-helper
    processes, lifecycle interval from startup through `locked|ready` and tested operations, and
    allowlisted local IPC peers. It proves those Yoetz paths, not the ambient authority of the OS
    credential agent or a separately running model process; public wording is conditional on the
    resolved F-013 trust boundary.
11. **Setup is an application use case:** a wizard or UI reads effective policy and submits a
    proposed policy transition through the Yoetz use case. It cannot write configuration or policy
    storage directly. Its first question, `network_egress`, controls the global
    `network_egress_permitted` ceiling; answering yes still leaves all five channels denied until
    they are chosen independently. Setup explains concrete allowed/blocked examples, all five
    channel decisions, local-model permission, provider/endpoint binding, content categories,
    preview mode, telemetry, and scope.
12. **Safe failure:** policy block, human denial/expiry, classifier uncertainty, scanner finding,
    provider refusal/timeout/invalid output, or audit failure cannot be treated as a semantic
    success. For `semantic_required`, the check returns deterministic results with
    `incomplete_check`; for optional semantic work, the result records the exact gap and follows its
    deterministic verdict rules. A request is never dispatched unless the audit reservation is
    durable, and success is never acknowledged unless its terminal receipt is durable. Waiting and
    approval remain resumable state rather than fake completed receipts.

## Consequences and proof obligations

The privacy policy is more than a semantic-provider toggle and cannot be represented by one
`network=true` capability. Releases must prove the global ceiling; policy-intersection behavior;
valid `local_only` policies with one bounded but v0.1-unavailable non-LLM channel; channel independence;
agent-context and local-model fences; exact approval binding/restart behavior; no secret-bearing
   configuration/environment/arguments; reviewed bundled-adapter composition (without claiming OS
   sandbox isolation); keyed terminal-receipt commitments; the initial-reservation no-receipt
   exception; Yoetz-process local-only AF_UNIX behavior without
overclaiming a separate model runtime; and no plaintext canaries across databases, objects, logs, traces, prompts,
receipts, errors, or transports. Public copy must reserve “zero network egress” for the composite
ceiling-plus-channel state, not infer it from `local_only` alone.
