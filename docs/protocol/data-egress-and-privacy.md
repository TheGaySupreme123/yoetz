# Data egress and privacy protocol

This is the enforceable, provider-independent protocol that decides whether any data may cross the
trusted local service boundary. It is the authority for classification, policy resolution, human
authorization, minimization, dispatch, and structural audit evidence. See
[`../../PRIVACY.md`](../../PRIVACY.md) for the user-facing summary and
[`docs/adr/ADR-009-data-egress-privacy.md`](../adr/ADR-009-data-egress-privacy.md) for the full
architecture decision this page implements.

## Trust boundary and actors

The trusted persistent local service owns decrypted local state, policy evaluation, authorizations,
the egress gateway, key access, and structural receipts. CLI, MCP, a future UI, plugins, importers,
and provider adapters are callers with bounded capabilities. An MCP caller or agent is never a
local-human authorization principal, even when its request asserts that the user consented.

## Content classes

Every candidate item is classified before policy evaluation as exactly one of:

- **`public_structural`** — bounded IDs, declared file/media types, counts, digests, and other
  non-content protocol facts.
- **`ordinary_user_content`** — selected task text, code/evidence excerpts, and declared work
  product.
- **`sensitive_confidential`** — confidential, personal, regulated, proprietary, or explicitly
  user-marked material requiring stricter authorization.
- **`secret_or_cryptographic`** — the class that can never enter a disclosure case at all.

Ambiguous classification always takes the stricter class. A scanner miss never authorizes an item —
category classification, scope validation, and never-send scanning are independent gates, and
content outside the selected scope (unrelated files, environment, transcripts, logs, stderr,
database rows) is rejected regardless of category. The never-send registry is versioned and its
complete public list is in [`../../PRIVACY.md`](../../PRIVACY.md); policy may add prohibitions but
can never remove one.

## Policy resolution

The machine policy is an installation ceiling. The effective policy is the **intersection** of that
ceiling with repository, task, and request policy — never a union. Standing external LLM admission
also requires an exact current repository row; a permissive machine ceiling alone grants no
repository. A more specific scope may narrow automatically, but widening always requires a fresh
local-human authorization bound to the exact broader categories, channel, provider/endpoint,
purpose, and scope. Every scope carries its full ancestor chain — installation ID, and then
workspace, task, and request identifiers as applicable; a shortcut single reference is never
accepted as authorization identity. Privacy repository identity comes from the service's trusted
session locator, never from the operation's public `workspace_ref`.

The four **`PrivacyProfile`** values govern LLM inference and its content-disclosure rules only —
they are not a bundled consent switch for telemetry, diagnostics, updates, or capability testing:

- **`local_only`** — constructs no external LLM-provider transport and disables the `llm_inference`
  network channel. It may coexist with a separately authorized bounded non-LLM policy row, but no
  such channel may carry task/user content.
- **`confirm_every_request`** — requires an exact durable preview of the already
  minimized/redacted/scanned outbound case and a local-human decision for every external request.
- **`minimal_external`** — automatically permits only the smallest context its policy allows.
- **`trusted_provider`** — permits explicitly listed categories only for one bound provider,
  endpoint profile, workspace/task scope, and purpose; it never means unrestricted access.

External profiles bind an exact five-field `ProviderBinding`: `provider_id`, `model_id`,
`endpoint_profile_id`, `endpoint_profile_version`, and `transport`. An unknown profile, unbound
endpoint, scope mismatch, expired authorization, or policy version mismatch denies before the
provider adapter is even constructed. A missing or mismatched exact repository grant additionally
denies before credential-handle minting, authorization, or dispatch.

`credential-probe` is a distinct `llm_inference` purpose, not an implication of enabling semantic
review. During provider-credential setup, the local human separately decides whether one fixed,
content-free request may verify the just-stored credential. The policy preview and its widening
decision display the resulting allowed-purpose set, and a policy that omits `credential-probe`
blocks it before adapter construction. An admitted probe uses the exact configured provider
binding, carries only bounded structural metadata, and follows the ordinary authorization and
egress-receipt path. A classified authentication or authorization refusal withdraws the stored
credential; uncertain transport, outage, timeout, unsupported-profile, and invalid-response
outcomes retain it as unverified.

`ReviewContextProfile` is a **separate** closed value — `structural`, `goal_aware`, `assisted`,
`expanded`, or `custom` — that determines which recorded case material a local selector considers,
never whether that material may leave. `structural` is typed timeline/state/coverage facts only;
`goal_aware` adds allowed intent and claim prose; `assisted` adds mechanically linked problem-local
recorded evidence, test/failure, diff, and repository excerpts; `expanded`/`custom` can select a
broader explicitly approved recorded set. Every selected item still needs category/class/scope
authority and passes the same minimization/never-send path. No profile creates a live
repository/filesystem handle — a missing excerpt is reported as `not_recorded`, `not_selected`, or
`withheld_by_policy`, never presented as observed-unchanged content.

The fail-safe LLM seed for every new installation is `local_only + structural`, with no external LLM
channel authority; the product may separately enable its bounded structural update-check channel.
The CLI's *configured, opt-in* recommendation is an inspectable standing exact-repository
`trusted_provider + assisted` recipe, eligible only for an exact
current provider data-use record stating training `prohibited`, retention `none|bounded`, and
provider human access `prohibited|restricted`. Unknown or stale posture removes the recommendation.
The recipe ships with an editable `require_current_provider_data_use_evidence=true` runtime guard.
Once a standing policy is human-confirmed, ordinary checks, automatic retries, reviewer challenges,
agent responses, and rechecks proceed without further human prompts; human involvement remains
required only for widening, credential mutation, `confirm_every_request` decisions, and finding
waivers. Never-send content has no approval path under any profile.

Repository binding is a trusted-control effect. CLI/UI supply their actual working directory and MCP
supplies its configured/session working directory. The service resolves symlinks, selects the Git
common root (or resolved non-Git directory), creates an installation-keyed commitment, and discards
the raw path. Branches and linked worktrees share one grant; independent clones and unrelated
repositories require their own.

`network_egress_permitted` is the global network ceiling. When `false`, all five channels must be
disabled. When `true`, it authorizes nothing by itself — every channel still needs its own consent.
The five independent `EgressChannel` values are `llm_inference`, `product_telemetry`,
`crash_diagnostics`, `update_checks`, and `capability_testing`. Enabling one never enables another.
The four non-LLM channels accept only their reviewed bounded structural/synthetic schemas and can
never carry task/user content. **v0.1 ships a production transport for `update_checks` only**
(allowlisted PyPI JSON GET of the `yoetz` distribution version; interactive advisory surfaces
only). The other three non-LLM channels still have no production transport: an attempted use
terminates before dispatch with outcome `channel_unavailable`, writes a no-dispatch structural
decision receipt, and makes no DNS or socket attempt.

The product durable default is `local_only` with structural `update_checks` on (opt-out) and the
global ceiling true only because that channel is on. Yoetz's true zero-network state is the
composite `profile=local_only`, `network_egress_permitted=false`,
and all five channel policies disabled. That state permits only exact release-cell local IPC:
Yoetz's own service/confidential endpoints, an optional approved local-model AF_UNIX profile, and
measured OS credential/user-presence/session-lifecycle IPC (for example allowlisted Linux
session-bus Secret Service routes, system-bus `org.freedesktop.login1`, or macOS native
security/presence notifications). Arbitrary AF_UNIX destinations, bus methods, or proxies are
forbidden. Neither the `local_only` profile token alone nor a true global ceiling alone supports a
zero-network claim by itself — both, plus all five channels disabled, are required together.

The four `LocalDisclosureSink` values — `local_model`, `agent_context`, `local_human_view`, and
`trusted_human_control` — are not network channels; they are covered by the same never-send fence.
`local_human_view` is ordinary human-readable rendering to an attached controlling terminal for the
local user who already unlocked this vault — it is not a third-party disclosure. `--json`, a piped
or redirected stream, a non-TTY invocation, and every MCP-bridge client resolve to `agent_context`
instead; a client never selects its own sink. `agent_context` is further conditioned on a computed
`DisclosureProvenance` (`self_authored`, `engine_derived_from_self_authored`, `other_writer`,
`imported`): material the requesting writer authored at the frozen frontier, and prose derived
solely from it, project without an extra grant, because withholding a writer's own words discloses
nothing new. Everything else — other-writer material, imports, and every provider-derived semantic
finding — requires the explicit `agent_context_categories` grant. No sink or provenance combination
ever admits `sensitive_confidential` content or anything in the never-send set. Serving the static
`guidance/` documents over MCP resources is not a disclosure sink at all — those bytes carry no
ledger, task, or user content and are identical for every installation.

## Live harness observation retention

First-party Codex live observation (ADR-010) is local control, not a network egress channel and not
a seventh MCP tool. Observation consent is independent of egress consent: one project-level
confirmation records a private workspace commitment (never a raw path). Revocation stops new
ingestion and retains already-kept evidence. Never retain hidden reasoning or complete transcript
prose. Sensitive bounded observation evidence lives only in encrypted objects; plaintext state is
allowlisted structural fields plus commitments. Never create an unencrypted transcript spool on
vault/service outage. Semantic review receives only minimized approved packets. Secret-like command
output never appears in status, logs, hook advice, or semantic packets.

## Outbound pipeline and state machine

One candidate request moves through exact states: `candidate` → `classified` →
`policy_denied`/`policy_eligible` → `minimized` → `redacted` → (`awaiting_human` when required) →
`approved` → `validated` → `authorized` → `receipt_pending` → `recorded`. Only a validated case with
a still-valid, unconsumed authorization can reach `receipt_pending`. A provider adapter receives
only an immutable `ApprovedOutboundCase` — never raw candidate context, policy objects, repository
handles, ledger handles, file paths, environment, or decryption services — and cannot enrich the
case or change its destination. The gateway computes the keyed request commitment over the exact
final application body bytes immediately before I/O; credential-bearing auth metadata and HTTP/TLS
framing are excluded from that commitment.

For `confirm_every_request`, the preview occurs after local minimization/redaction/secret scanning
and shows destination, purpose, scope, category names, the exact bounded excerpts as they would be
sent, and redaction counts. Approval is request-specific and expires if the prepared bytes, policy,
destination, scope, or purpose changes. Every physical retry — even of identical bytes — requires a
fresh exact foreground preview and decision; only crash/resume *before* authorization consumption
continues the same dispatch rather than minting a retry.

## Egress receipts

Every successfully reserved terminal outbound decision and every physical outbound attempt produces
a durable, structural `EgressReceipt` (or `LocalDisclosureReceipt` for a local sink) through the
privacy audit path. `awaiting_human`, `approved`, and `receipt_pending` are nonterminal audit
states — never finished receipt outcomes. The sole no-receipt exception is an **initial audit
reservation failure**, which returns bounded `audit_failed` before any preview, authorization, or
dispatch and fabricates no receipt identity. Receipts record provider/model/endpoint profile,
policy version/digest, authorization scope, approved/blocked categories, byte/token counts,
redaction counts, consent source, outcome/reason, and a keyed commitment to the exact final request
body bytes — never plaintext content or the provider's response.

Content-bearing v0.1 disclosure proposals are encrypted as `ObjectKind.privacy_audit` objects in
their owning task bundle; taskless channel-unavailable decisions and machine policy diffs are
closed nonsecret structural rows with no content object. Backup pins the privacy-root
generation/digest and carries the structural audit sidecar plus every rooted encrypted object;
restore preserves terminal evidence but never revives a pending or expired authorization.

| Situation | `PrivacyOutcome` | `PrivacyReason` |
|---|---|---|
| Policy/category/purpose/destination/scope block | `blocked_by_policy` | exact applicable token |
| Never-send match | `blocked_forbidden_data` | `never_send_detected` |
| Unresolved classification | `classification_uncertain` | `classification_uncertain` |
| Local-human denial | `human_denied` | `human_denied` |
| Authorization expired, stale, or reused | `approval_expired` | `authorization_expired` / `authorization_stale` / `authorization_reused` |
| Success | `completed` | absent |
| Provider refusal | `provider_refused` | `provider_refused` |
| Timeout | `timeout` | `provider_timeout` / `deadline_expired` |
| Invalid provider response | `invalid_response` | `provider_invalid_response` |
| Transport/provider unavailable | `transport_failed` | `provider_unavailable` / `transport_failed` |
| v0.1 non-LLM channel has no owned transport | `channel_unavailable` | `channel_unavailable` |
| Ambiguous I/O outcome | `transport_failed` | `outcome_unknown` |
| Late / stale result | `late` / `stale` | `late` / `stale` |
| Reserved decision, later transition fails | `audit_failed` | `audit_failed` |

`safe_failure_reason` is required for every outcome except `completed`, for which it is forbidden.

## Policy mutation

A trusted local control surface may propose a policy change. The service classifies the diff as
tightening, neutral, or loosening. Server-proven tightening commits immediately. Loosening always
returns `decision_required` with a pending proposal ID and exact digest — it can never commit
through the ordinary control channel. A separate foreground human-control surface confidentially
renders, reauthenticates, and commits it. Ordinary MCP/agent schemas expose no method that can reach
that surface. Revocation of an existing authorization is immediate.

For a new repository, one proposal may contain both a necessary machine-ceiling widening and the
first repository row. The service returns one authority digest covering the complete ancestor and
migration state; the trusted preview shows both changes, and one CAS commits every member or none.
Policy-digest-only older proposal shapes cannot perform this transition and fail closed under
repository-grant mode.

Package upgrades preserve accepted machine-policy bytes. An append-only catalog migration snapshots
only the bounded pre-upgrade legacy-route frontier and entitlements. When an eligible route next
arrives with a trusted locator, its accepted policy is cloned beneath the unchanged machine ceiling
and the entitlement is atomically consumed. If no legacy route existed, one bounded first-repository
carry-forward is available. This is no-reapproval narrowing, not new authority; later repositories
remain Private. Replay is idempotent, and stale CAS, crash, expiry, denial, missing locator, or old
decoder leaves external LLM admission blocked.

## Failure, cancellation, retry, and crash behavior

Classification, minimization, redaction, schema, secret-scan, or receipt-persistence uncertainty
fails closed before dispatch. Service lock, policy unavailability, unknown scope, stale
authorization, or an unbound provider denies egress but never erases deterministic local work.
Dispatch ambiguity (for example a connection drop mid-request) records `transport_failed` /
`outcome_unknown` — never "unsent," and never a blind retry. Cancellation before any I/O leaves a
proposal pending until an explicit denial or expiry; cancellation after possible I/O records
`transport_failed`/`outcome_unknown` with the exact final-request-body commitment. Provider
refusal, timeout, or invalid output completes semantic review as `incomplete_check` while
deterministic results remain fully available — semantic failure never discards a deterministic
result.

## Extension and versioning

New content categories, disclosure sinks, egress channels, or repository-authority shapes require a
coordinated schema, ADR, and fixture update — none is added as a silent policy-vocabulary change.
`audit_store_version=1` and the
`hmac-sha256/yoetz-privacy-egress-request-v1` commitment format are frozen for v0.1.

Installed-wheel two-repository semantic and receipt proof remains outstanding for issue #139.
Router downstream/fallback authority and issue #141's foreground disclosure continuation are not
part of this protocol change.

## See also

- [`../../PRIVACY.md`](../../PRIVACY.md) — the public promise this protocol enforces.
- [`docs/adr/ADR-009-data-egress-privacy.md`](../adr/ADR-009-data-egress-privacy.md) — the
  architecture decision.
- [`privacy-setup-wizard.md`](privacy-setup-wizard.md) — the setup/policy-change contract.
- [`local-service-security.md`](local-service-security.md) — the trust boundary that hosts this
  protocol.
