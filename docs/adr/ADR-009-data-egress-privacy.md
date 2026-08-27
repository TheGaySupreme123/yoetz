# ADR-009 — Central privacy, disclosure, and data-egress control

**Status:** Working decision revised 2026-07-17. Ratification requires an independent
privacy/security review plus executable no-bypass, never-send, approval-resume, and zero-egress
evidence.
**Implemented by:** `src/yoetz/domain/privacy.py`,
`src/yoetz/application/egress.py`,
`src/yoetz/application/privacy_policy.py`,
`src/yoetz/ports/privacy.py`, the privacy adapters/configuration/audit modules, ADR-006,
ADR-011 structural subject-state capture, `PRIVACY.md`, the technical privacy protocol, policy
schemas, fixtures, and tests.

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
   no such channel may carry task/user content. v0.1 ships production transport for
   `update_checks` only among the non-LLM channels (structural package identity against an
   allowlisted PyPI URL). The remaining three non-LLM channels (`product_telemetry`,
   `crash_diagnostics`, `capability_testing`) still have no production transport: proposed
   enablement is rejected and makes no I/O; adding one later requires an exact adapter/use-case
   owner, ADR review, and fresh human transition.
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
   sensitive task/user content.

   **Revised 2026-08-03 — `update_checks` transport and product default.** v0.1 ships a bounded
   structural transport for `update_checks` only: a fixed allowlisted HTTPS GET of the `yoetz`
   distribution identity on PyPI (`https://pypi.org/pypi/yoetz/json`), with `trust_env=False`,
   size/timeout caps, and response use limited to the latest version string. Interactive TUI
   surfaces (first-run finish, resume tip, `/doctor`) and interactive setup/`/connect` may surface
   an advisory plus the exact upgrade command `uv tool upgrade yoetz`; work receipts never carry
   update metadata. The product durable seed and named recipes default `update_checks` **on** with
   `network_egress_permitted=true` solely because that channel is on, while `llm_inference` and the
   other three non-LLM channels stay off and profile remains `local_only`. Config.toml generation-1
   bootstrap remains fail-safe all-denied and is not continuing disclosure authority. Operators may
   opt out in first-run setup or privacy custom section 5. **Amended 2026-08-12:** the first-run
   setup answer may replace only the `update_checks` value in its recommended or named privacy
   recipe before the exact candidate is rendered. `None` preserves the recipe default, and Custom
   continues to ask section 5 itself. The answer is not authority: the resulting candidate still
   requires the existing exact confirmation and service proposal/decision ceremony. The remaining
   three non-LLM channels stay unsupported: setup marks them read-only off, proposed enablement
   returns `channel_unavailable` without persistence or I/O, and forced enabled state yields a
   pre-dispatch `channel_unavailable` decision receipt with no DNS/socket I/O. Absence of a channel
   is not silently replaced by a generic HTTP client.
3. **Local disclosure sinks:** `local_model`, `agent_context`, `local_human_view`, and
   `trusted_human_control` are not network-egress channels. They receive only policy-approved
   minimized content and are covered by the never-send fence. The trusted human surface may render an
   approval preview and exact policy diff, but it never exposes cryptographic material or
   service-vault credentials.
   A local-model runtime receives plaintext and belongs to the trusted local computing base unless
   its exact artifact/profile supplies independently enforceable sandbox evidence; Yoetz's adapter
   makes no claim about another process's ambient network authority.
   `HumanAuthorityCapability.source=unavailable` fences external-provider activation, credential
   mutation, and durable policy widening; it is not a second use-time authorization gate for an
   already reauthenticated exact local-model policy row. Local-model use remains independently
   gated by deterministic classification, exact installed profile, service/vault/policy generation,
   and one-shot `consume_local` authorization.

   **Revised 2026-07-16 (see ADR-010, F-018/F-019).** The original single `agent_context` sink
   conflated two audiences with different risk, and its structural-only default therefore withheld
   finding prose from the local human as well as from the agent, on the zero-egress install every
   user starts with. That default protected nobody in the common case and broke the
   `check → respond → recheck` loop the product exists for. Two changes correct it without weakening
   the fence:

   `local_human_view` is now a separate sink for ordinary human-readable rendering to an attached
   controlling terminal. A local human reading a vault they unlocked is not a third-party
   disclosure. It admits every non-never-send category at
   `public_structural|ordinary_user_content`. `--json`, non-TTY or redirected streams, and every
   `mcp_bridge` client remain `agent_context`; the client never selects its own sink. Terminal
   emulation by a same-UID process is the stated threat-model limit that already bounds the unlock
   TTY contract, not a claimed cryptographic exclusion.

   `agent_context` is now conditioned on a computed closed `DisclosureProvenance`
   (`self_authored`, `engine_derived_from_self_authored`, `other_writer`, `imported`). Material the
   requesting writer authored at the frozen frontier, and kernel prose derived solely from it,
   project at the policy's data classes without a category grant: that content is already in the
   host's context, so withholding it discloses nothing new and costs the loop. Other-writer
   material, imports, and provider-derived semantic prose — including every reviewer challenge —
   still require the explicit `agent_context_categories` grant.

   ADR-022's derived observation writer is `engine_derived_from_self_authored` for this provenance
   decision: a hook observation records the requesting agent's own action, not another writer's
   independent authorship. Production still constructs `LocalPrivacyEnforcer` without a provenance
   resolver, so this classification is a forward contract for the resolver rather than a claim that
   the widening path ships today.

   **Revised 2026-07-24 — default agent-context disclosure of verification output.** Under the
   default LOCAL_ONLY bootstrap policy, `agent_context` may include Yoetz-authored verification
   projection content for the requesting agent's own task: `finding_summary` and
   `obligation_text` (receipt document sections, human_text, check findings/obligations). These
   remain `ordinary_user_content` at the data-class layer; the default policy allowlist is widened
   rather than reclassifying leaves as public structural. Observation-derived repository/transcript
   excerpts and vault material stay blocked. Stricter owner policies may still block JSON receipt
   projection (fail closed as `privacy_projection_blocked` → `PRIVACY_AUTHORITY_REQUIRED`);
   markdown/text degrade with omission markers. Provenance is computed from the
   ledger, never asserted by a caller; ambiguity or an unresolvable ref denies; it is recomputed per
   projection and never cached across frontiers.

   Both local client sinks take the same reserve/complete receipt path: `local_human_view` is a
   looser ceiling, never an unaudited one. Provenance widens only `agent_context`, only for the
   requesting writer, and never past `sensitive_confidential` or the never-send set, which remain
   absolute at every sink under every provenance. Serving static reviewed guidance documents is not a
   sink at all, because they carry no user content and are identical for every installation.
   An exact same-request delivery retry may reuse a completed `publish_work` projection and its
   original receipt only for the same writer, request digest, and local sink. This is continuation
   of the already-audited disclosure, not a new projection: the cached body is restricted to public
   structural fields and omission markers, while the current route is revalidated and the outer
   control correlation is freshly stamped. Any plaintext event summary prevents caching and uses
   the ordinary reserve/complete path again.
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
6. **Policy composition:** the machine policy is an installation ceiling, not a standing grant for
   every repository. The effective decision is the intersection of that ceiling with repository,
   task, and request overlays. Lower scopes may tighten immediately. External LLM admission under
   any policy additionally requires an exact current repository row beneath the ceiling; absence or
   mismatch denies before provider construction, credential-handle minting, authorization, or
   dispatch. Machine-scoped structural channels such as update checks remain independently governed
   by their channel rows and do not acquire this task-content authority. Loosening any
   effective permission requires a locally authenticated human on a trusted control surface,
   reauthentication, an exact diff, and a durable decision; MCP/agent/LLM calls can request more
   context but cannot approve or persist the expansion.
   Answering a `confirm_every_request` disclosure is deliberately *not* one of these loosening
   operations and does not require passphrase mode or strong reauthentication: it decides one exact
   prepared case already bounded by the committed policy, and it can neither widen the policy nor
   authorize any other case. It requires a ready vault and the trusted foreground surface, nothing
   more. Requiring passphrase mode made the decision unreachable on a keyring installation, which
   is the ordinary configuration, so the posture existed with no way to answer it.
   Policy commit and external/local consumption share one generation-CAS linearization point.
   Tightening-first prevents I/O and closes the unconsumed branch with a no-dispatch receipt;
   consume-first admits one attempt that may send, must be best-effort closed/nonselectable, and
   records its actual terminal or unknown receipt. Tightening never claims to retract bytes already
   admitted.
   **Desired-state TOML (ADR-014):** `yoetz privacy export-desired` / `apply-desired` may declare
   nonsecret policy intent in a sidecar TOML document. Apply classifies against the effective
   store: equivalent is a no-op; tighten routes to the existing tighten gate; widen never commits
   from the file alone and requires the ordinary propose→decide path. The generation-1
   `[privacy]` bootstrap seed in service `config.toml` remains fail-safe only and is not
   continuing disclosure authority.
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
   one-attempt authentication metadata to the exact profile-bound HTTPS endpoint selected by the
   reviewed registry, using platform CA trust and hostname validation; v0.1 does not claim
   certificate or SPKI pinning. Candidate/user-discovered credentials remain never-send.
   Catalog-backed audit is permitted; content-bearing task audit
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
    network-capable runtime path. The product default is **not** that state: it is `local_only`
    with structural `update_checks` permitted (opt-out) and no task-content egress. Zero-network
    for package identity checks requires the operator to disable `update_checks` (and therefore
    the ceiling when no other channel is on). In the true zero-network state the Yoetz-owned tested
    process set permits only the
    exact service/confidential AF_UNIX endpoints; a separately approved exact local-model AF_UNIX
    endpoint; and exact release-cell platform IPC needed for OS credential storage, user presence,
    or session-lifecycle security events.
    The last category includes measured Linux AF_UNIX routes to allowlisted session-bus Secret
    Service peers/methods and, separately, system-bus `org.freedesktop.login1` peers/methods, or
    measured macOS native security/presence/session notifications; it never permits
    arbitrary AF_UNIX, arbitrary bus names/methods, or a local proxy. Yoetz denies AF_INET, AF_INET6,
    DNS, proxies, redirects, external provider
    construction, telemetry, diagnostics upload, update checks, and capability calls. The
    `local_only` profile alone is not a zero-network claim because a separately authorized
    structural non-LLM row (today: `update_checks`) may raise the ceiling without enabling LLM
    disclosure; the remaining three non-LLM rows stay unsupported/off.
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
    preview mode, telemetry, and scope. CLI and TUI derive an optional trusted workspace locator from
    their actual process working directory; the MCP bridge derives it from the configured/session
    working directory. The locator is never accepted from public or model-controlled
    `workspace_ref`. The service resolves symlinks, uses Git's canonical common repository root for
    Git workspaces and the resolved directory otherwise, creates an installation-keyed
    repository-privacy commitment, and immediately discards the raw path. Branches and linked
    worktrees therefore share authority; independent clones and unrelated repositories do not.
12. **Safe failure:** policy block, human denial/expiry, classifier uncertainty, scanner finding,
    provider refusal/timeout/invalid output, or audit failure cannot be treated as a semantic
    success. For `semantic_required`, the check returns deterministic results with
    `incomplete_check`; for optional semantic work, the result records the exact gap and follows its
    deterministic verdict rules. A request is never dispatched unless the audit reservation is
    durable, and success is never acknowledged unless its terminal receipt is durable. Waiting and
    approval remain resumable state rather than fake completed receipts.
13. **Useful review context remains user-controlled:** a closed `ReviewContextProfile` is stored
    independently from the four LLM-disclosure profiles. `structural` selects only typed metadata;
    `goal_aware` adds category-separated detail from the bounded accepted-event history frozen with
    the check; `assisted` adds bounded problem-local recorded evidence, test/failure, diff, and
    source excerpts; `expanded` and `custom` admit broader explicitly allowed recorded material.
    The history is capped at 64 newest material events and 512 KiB of canonical payload, with exact
    `not_recorded`, `not_selected`, and older-window accounting. Exact command text remains
    independently selected and is not part of `goal_aware` or `assisted`. Every selected item
    still passes category/class/scope policy, minimization, redaction, never-send scanning, provider
    binding, caps, authorization, and receipt. The selection profile grants neither live filesystem
    access nor permission beyond the effective privacy policy.
14. **Two defaults are intentionally different:** an unconfigured installation's durable seed is
    `local_only`, structural `update_checks` on (opt-out), other network channels off, global
    ceiling true only because update checks are on, and no local model. Config.toml generation-1
    remains fail-safe all-denied. When a technical user deliberately runs external semantic setup,
    the upstream CLI recommends the inspectable `assisted` recipe for an exact endpoint profile
    with a current data-use record that states customer-content training `prohibited`, retention
    `none|bounded` with any bounded ceiling at most 30 days. Provider human-access posture and
    documented safety, support, legal, and abuse-monitoring exceptions remain prominent disclosure
    facts. Known-broad, unknown, or
    stale posture removes the badge. The recipe sets the editable
    `require_current_provider_data_use_evidence=true` runtime guard. A technical user may turn it
    off only through a trusted loosening/custom transition, after which the policy carries no
    upstream no-training recommendation. The user reviews and commits the expanded policy once.
    Within that standing exact-repository policy, checks, retries, reviewer challenges, agent responses,
    and rechecks run without per-request human prompts. `confirm_every_request` remains the
    optional high-ceremony alternative. A new repository proposal may need both to widen the machine
    ceiling and to insert the first repository row. Those changes are one transition bundle, one
    complete trusted preview, and one authority-digest-bound CAS commit; no intermediate state may
    authorize every repository.
15. **Structural subject-state hashing is a local non-disclosure support effect:** ADR-011 permits
    one explicit trusted local CLI command to read bounded Git/worktree bytes only into streaming
    hashers and return a versioned `SubjectStateRef`. It returns no source, diff, filename, path,
    branch, remote, Git output, or component digest; writes no ledger/audit row; opens no network;
    and cannot be invoked through MCP with an arbitrary path. Intermediate bytes are discarded
    before rendering. This narrow content-withholding fingerprint is not a local disclosure sink,
    does not authorize semantic/live artifact inspection, and does not weaken never-send. An
    unsupported, partial, unsafe, changing, or over-limit capture returns no comparable state.
16. **Live harness observation retention (first-party Codex, ADR-010 amendment 2026-07-22):**
    Observation consent is independent of egress consent. One project-level confirmation records a
    private workspace commitment (never a raw path). The normalized workspace locator is an
    authenticated encrypted task object; plaintext state retains only its commitment and object
    identity. Locator normalization selects a safe Git root and performs lexical path cleanup; it
    does not apply Unicode normalization. The exact filesystem-encoded spelling feeds the
    commitment, so canonically equivalent names that identify distinct directories never share
    consent. A legacy grant under another spelling requires an explicit regrant. Revocation stops
    new ingestion, deactivates the locator and check-policy trust
    bindings, and retains already encrypted task evidence.

    Retain bounded task-relevant visible user/assistant/subagent messages, tool inputs/results,
    task-linked terminal results, selected changed-file/diff material, approved-check output,
    lifecycle structure, and composition readiness. Reject hidden reasoning, system/developer/
    platform prompts, credentials, detected secret spans, unrelated files, and ambient logs before
    persistence. Secret-bearing spans are redacted in memory before authenticated encryption.
    SQLite, observation envelopes, cursors, local outboxes, status, hook context, and logs contain
    only allowlisted structure, encrypted object identities/commitments, sizes, classifications,
    and relations. A locked vault, absent service, or failed encryption keeps the structural
    envelope with `content_capture_unavailable`; it never creates a plaintext fallback spool.

    This boundary protects stolen object files, SQLite files, backups, and copied local state when
    vault keys are unavailable, and tampering must fail authenticated decryption. It does **not**
    claim protection from root, kernel compromise, or a compromised same-user Yoetz/Codex process
    while the vault is unlocked; immutable Python buffers are not promised zeroized. Public copy
    must describe authenticated encryption at rest and secret exclusion, never “cannot be hacked”
    or absolute host immunity.

    Exact project check policy bytes propose no authority. One trusted-local confirmation binds the
    raw `.yoetz/checks.toml` digest; any byte change suspends all listed commands. Approved checks
    use exact argv, `shell=False`, sanitized environment, bounded output/time, and an enforcing
    sandbox. Network-requiring checks fail closed unless a separately reviewed authorization and
    sandbox prove the permission. Redacted output is encrypted before durable retention. Optional
    semantic observation advice remains additive and passes only minimized approved packets through
    the existing privacy gateway. Observation, trust, verification management, and local advice
    diagnostics are local control, not network-egress channels and not additional MCP tools.

17. **Repository authority migration is bounded narrowing, not package consent:** package upgrades
    preserve accepted machine-policy bytes. Catalog migration records only the pre-upgrade legacy
    route frontier and bounded entitlements; it does not infer repository identity from
    model-controlled workspace references. When an eligible legacy route next arrives with a trusted
    repository locator, the service atomically clones the accepted machine policy into that exact
    repository row, binds the route to the repository-privacy commitment, and consumes the entitlement.
    If there was no eligible legacy route, exactly one bounded first-repository carry-forward may do
    the same. This needs no new prompt because it narrows already accepted authority and never widens
    or rewrites the machine row. Later repositories inherit nothing. Fresh `local_only` installations
    enter repository-grant mode immediately; migration is a no-op for their disclosure authority.
    Repeated startup and replay are idempotent. Missing locators, older control shapes, stale authority
    digests, expiry, denial, crash rollback, or exhausted entitlements leave the prior rows unchanged
    and external LLM admission blocked.

### Human involvement under the recommended recipe

An agent may guide the owner to a trusted ceremony but cannot stand in for it. Both a nonterminal
per-request decision and an explicitly reported missing standing repository grant use the exact
command carried by their distinct continuation kinds and preserve the original check request
identity. Repository setup uses the trusted CLI/TUI entrypoint `yoetz --privacy`; the one-use
decision carries a proposal id and expiry. Recovery uses `status(view=operation)` or exact replay of
the original request, never a fresh check. Chat assent is never authority, and denial, expiry,
cancellation, stale authority, or incomplete review remains pre-dispatch.

The repository continuation is permitted only after the trusted policy store returns a valid,
exactly bound authority whose observed `grant_state` is `missing`. The operation records that
suspension discriminator transactionally; status never infers it from later mutable authority.
Missing route commitment, unbound or mismatched identity, coordinator closure, policy failure,
invalid effective policy, unavailable reconciliation, and reconciliation failure are terminal
no-dispatch policy outcomes and advertise no approval surface. Repository privacy setup/effective/
propose result projection derives its workspace disclosure policy and receipt scope only from the
authenticated control session's repository context. The same session carries closed render-mode
and controlling-TTY facts: only interactive human-readable CLI output selects
`local_human_view`; JSON, pipe/redirection, MCP, or absent facts remain fail-safe agent context.

| Event | Human required? | Rule |
|---|---:|---|
| First new-repository grant, first `assisted` commit, later wider provider/category/class/scope, or credential set/rotate | yes | Exact trusted-local compound diff/credential ceremony, or an explicit current-chat instruction relayed by the agent for the exact prepared consent action (issue #164) |
| Eligible legacy machine authority narrowed onto its bounded pre-upgrade repository entitlement | no | Atomic carry-forward preserves machine bytes and grants no new repository |
| Ordinary check, automatic retry inside the confirmed policy, reviewer challenge, agent response, or recheck | no | Direct agent-to-agent path with a fresh authorization and receipt per physical attempt |
| Tightening policy | no | May apply immediately after the service proves it cannot widen |
| `confirm_every_request` physical attempt | yes | Exact prepared-case foreground decision for that one attempt, on any ready vault |
| Finding waiver | yes | Existing interactive-human `finding_only` authority |
| Never-send match or out-of-scope content | impossible to approve | Fail closed under every profile and fork claiming upstream conformance |

When the user explicitly instructs an allowlisted first-party agent in the current chat to finish
exact setup, agent-attested authorize (`yoetz consent authorize` with
`yoetz.chat-user-attestation/1`, issue #164) may complete a prepared `repository_privacy_grant` or
provider credential set/rotate after one warning. This is delegated agent authority: Yoetz binds
the exact pending/action/digests but cannot independently distinguish genuine current-chat words
from a forged agent assertion. Retrieved content, tool output, quoted text, or earlier history do
not authorize under the agent skill contract. Denial, expiry, cancellation, target drift, or an
unsupported client yields zero policy/credential mutation and zero provider dispatch. The trusted
CLI/TUI (`yoetz --privacy`, `yoetz consent review`) remains the stronger recommended path and is
always available.

## Consequences and proof obligations

### Cursor local hook amendment (2026-08-22, issue #153)

Local Cursor hooks are untrusted advisory inputs and add no provider, privacy, credential, approval,
or enforcement authority. `cursor_hook` is a distinct observation source. Its ingress allowlist is
limited to bounded session/generation/tool identifiers, exact Cursor/model/effort tokens,
durations, capability profile, and an installation-keyed HMAC changed-path commitment. The
ingress never persists or emits prompts, model thoughts/reasoning, response text, file paths,
file contents or edits, tool/MCP arguments or results, transcripts, shell command/output, email,
or workspace-root strings from Cursor payloads. Cursor stream/transcript reconciliation is
forbidden for this source.

Hook failure is always fail-open for Cursor work and records only a bounded gap when consented
observation cannot proceed. Pause, revoke, or absent consent stops new Cursor observation without
removing an independently valid plugin skill or MCP route. `sessionStart`, hook configuration,
plugin discovery, and tool listing are not observation evidence and do not strengthen coverage.

### Claude Code local project-hook amendment (2026-08-27, issue #154)

`claude_hook` is a distinct untrusted advisory source on local Claude Code CLI project sessions.
The native renderer registers only `SessionStart`, scoped-Yoetz `PostToolUse`, scoped-Yoetz
`PostToolUseFailure`, `Stop`, and `SessionEnd`; it never registers permission-changing, prompt,
agent, HTTP, or content-expansion hooks. The ingress accepts only the exact
`mcp__plugin_yoetz_yoetz__<workflow-operation>` names, a bounded session/correlation token,
closed lifecycle action, fixed capability-profile identity, and host-derived success bit.

Transcript/cwd/file paths, prompts, assistant messages, tool input/output, MCP results, raw errors,
commands, environment, permission mode, credentials, and all unknown fields are discarded before
the common observation boundary. No Claude content capture or transcript reconciliation exists.
Hook failure and the host's three-second timeout are fail-soft; declaration, delivery, consent, and
accepted evidence remain separate. Local-control schema `2.2.0` appends only the `claude_hook`
envelope/coverage row and retains `2.1.0` byte-for-byte for Cursor peers. Claude trust or plugin
enablement grants no Yoetz observation/egress/provider authority.

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
The runtime plaintext release gate binds one per-run synthetic canary to the privacy integration
suite and retains that suite's encrypted/structural state under the isolated XDG data tree. It then
recursively scans only the explicitly selected XDG data, config, cache, and runtime trees under the
same fixed file, aggregate-byte, member-count, and no-symlink caps as release evidence. A planted
runtime-tree negative control must be detected without exposing the canary, and the subsequent clean
scan must emit a nonempty canonical redacted report; a finding, missing surface, over-limit surface,
or absent report fails the release gate.
The setup/conformance matrix additionally proves every `ReviewContextProfile`, the recommended
recipe expansion, problem-local selection, agent-context delivery of reviewer findings, current
provider data-use recommendation metadata, and automatic no-prompt behavior after standing policy
authorization. A data-use record is evidence for recommendation wording, not technical proof of a
provider's downstream behavior. Repository-identity evidence additionally proves symlink
normalization, Git-common-root sharing across branches and linked worktrees, non-transferability to
independent clones, and non-Git resolved-directory behavior. Migration evidence proves bounded
entitlement snapshot/consumption, the no-route one-time carry-forward, exact replay, stale CAS,
crash rollback, no machine-byte rewrite, and no prompt for narrowing. A missing or mismatched grant
must produce zero provider constructions, credential handles, authorizations, and dispatch attempts.
Raw paths must remain absent from policy/catalog bytes, logs, receipts, errors, and agent projections.

Installed-wheel proof remains a separate acceptance gate: two consecutive real semantic checks in
one approved repository must show distinct one-use authorizations, credential handles, dispatch
identities, semantic provenance, and terminal privacy receipts, while a second repository remains
blocked. Router downstream/fallback authority and issue #141's foreground disclosure continuation
remain out of scope for this decision.
ADR-011 capability evidence additionally proves structural capture is read-only, bounded,
network-free, path/content withholding, fail-closed on ambiguity, and incapable of strengthening
publication/authorship/artifact-observation coverage.
