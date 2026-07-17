# src/yoetz/domain/privacy.py — privacy policy, disclosure, authorization, and receipt values

**Wave:** C–E | **ADRs:** ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/values.md`, `protocol/canonical.md`, `protocol/ids.md` | **Imported by:**
`application/egress.md`, `application/privacy_policy.md`, `ports/privacy.md`,
`config/privacy.md`, privacy/provider adapters, CLI/MCP disclosure rendering, tests

## Purpose

Define the closed, immutable vocabulary that lets Yoetz decide whether content may reach a network
channel or local disclosure sink. These values keep privacy authority out of transport and provider
adapters, make every approval exact and replay-safe, and support durable receipts without storing
outbound request-body plaintext in structural audit records.

## Public surface

- `enum PrivacyProfile`: `local_only`, `confirm_every_request`, `minimal_external`,
  `trusted_provider`.
- `enum ReviewContextProfile`: `structural`, `goal_aware`, `assisted`, `expanded`, `custom`.
- `enum EgressChannel`: exactly `llm_inference`, `product_telemetry`, `crash_diagnostics`,
  `update_checks`, `capability_testing`.
- `enum LocalDisclosureSink`: `local_model`, `agent_context`, `local_human_view`,
  `trusted_human_control`.
- `enum DisclosureProvenance`: `self_authored`, `engine_derived_from_self_authored`,
  `other_writer`, `imported`. Computed server-side from the ledger at the frozen frontier and never
  asserted by a caller; it conditions the `agent_context` ceiling only.
- `enum DataClass`: `public_structural`, `ordinary_user_content`, `sensitive_confidential`,
  `secret_or_cryptographic`.
- `enum DataCategory`: `bounded_structural_metadata`, `declared_file_type`, `task_description`,
  `claim_text`, `obligation_text`, `decision_excerpt`, `evidence_excerpt`, `finding_summary`,
  `command_metadata`, `diff_metadata`, `repository_excerpt`, `transcript_excerpt`,
  `diagnostic_metadata`.
- `enum ForbiddenDataKind`: `encryption_key`, `recovery_or_unlock_secret`, `password`,
  `api_credential`, `authentication_token`, `cookie`, `private_certificate`, `keyring_content`,
  `unrelated_environment`, `credential_file`, `hidden_auth_configuration`, `raw_database`,
  `unrestricted_log`, `raw_stderr`, `complete_transcript`, `out_of_scope_file`.
- `enum AuthorizationScopeKind`: `machine`, `workspace`, `task`, `request`.
- `enum ConsentSource`: `none`, `baseline_policy`, `scoped_local_human`,
  `per_request_local_human`.
- `enum PrivacyOutcome`: `blocked_by_policy`, `blocked_forbidden_data`,
  `classification_uncertain`, `human_denied`, `approval_expired`,
  `channel_unavailable`, `provider_refused`, `timeout`, `invalid_response`, `transport_failed`,
  `late`, `stale`, `audit_failed`, `completed`.
- `enum PrivacyReason`: `policy_denied`, `never_send_detected`, `classification_uncertain`,
  `scope_mismatch`, `purpose_not_allowed`, `destination_not_allowed`, `category_not_allowed`,
  `channel_unavailable`,
  `human_denied`, `authorization_expired`, `authorization_stale`, `authorization_reused`,
  `insufficient_approved_context`, `provider_unavailable`, `provider_refused`, `provider_timeout`,
  `provider_invalid_response`, `transport_failed`, `audit_failed`, `deadline_expired`, `late`,
  `stale`, `outcome_unknown`.
- Frozen values: `ProviderBinding`, `ProviderDataUseProfile`, `ReviewSelectionPolicy`,
  `AuthorizationScope`, `ChannelPolicy`, `PrivacyPolicy`,
  `PolicyOverlay`, `CandidateContextItem`, `CandidateContext`, `ClassifiedContextItem`,
  `ClassifiedContext`, `PreDispatchAuditDecision`, `AgentProjectionAuditSubject`,
  `DisclosureProposal`, `PrivacyAuditSubject`,
  `HumanPrivacyDecision`, `EgressAuthorization`, `ApprovedOutboundCase`,
  `ApprovedLocalDisclosureCase`, `ApprovedProviderCase`, `EgressReceipt`,
  `LocalDisclosureReceipt`, `ApprovedLocalItem`, `LocalDisclosureOmission`,
  `LocalDisclosureApproved`, `LocalDisclosureBlocked`, `LocalDisclosureUnavailable`,
  `PrivacyDecision`.
- Constants `MAX_EGRESS_ITEM_BYTES = 16 * 1024`, `MAX_EGRESS_CASE_BYTES = 256 * 1024`, and the
  exact `NEVER_SEND_KINDS: frozenset[ForbiddenDataKind]` containing every enum member above.

All values are frozen dataclasses with slots. Mapping/set inputs normalize to sorted immutable
tuples before validation and canonicalization.

## Behavior

### Policy and binding values

`ProviderBinding` contains exactly the nonsecret structural fields `provider_id`, `model_id`,
`endpoint_profile_id`, `endpoint_profile_version`, and `transport` (`external` or
`local_af_unix`). It has
no URL, socket path, credential, header, or arbitrary option map.

`ProviderDataUseProfile` is release/profile metadata keyed by the exact external endpoint-profile
ID and version. It contains `data_use_profile_id`, `data_use_profile_version`,
`customer_content_training: prohibited|permitted|unknown`,
`retention: none|bounded|unbounded|unknown`, an exact nonnegative `retention_days_ceiling` only when
bounded, `provider_human_access: prohibited|restricted|permitted|unknown`, canonical `reviewed_at`
and `expires_at`, and an artifact-bound evidence digest. Known-broad posture is never collapsed into
`unknown`. It is nonsecret, inspectable setup information. It does not authorize disclosure and
does not claim Yoetz can technically verify the provider's downstream behavior. The upstream
`assisted` recommendation is eligible only while this record is current, training is `prohibited`,
retention is `none|bounded`, and human access is `prohibited|restricted`.

`ReviewSelectionPolicy` is the canonical persistable selector behind one
`ReviewContextProfile`. It contains sorted unique `sections` from
`goal|obligations|claims|decisions|timeline|deterministic_assessments|change_observations|coverage|
targeted_excerpts|omissions`; sorted unique `excerpt_kinds` from
`evidence|test|failure|diff|command|repository`; `relevance:
linked_subjects_only|linked_then_in_scope`; `include_finding_prose: bool`;
`include_exact_command_text: bool`; and exact integer
caps `max_timeline_items<=64`, `max_assessments<=64`, `max_change_observations<=32`,
`max_excerpts<=16`, `max_omissions<=64`, `max_excerpt_bytes<=16_384`, and
`max_total_excerpt_bytes<=131_072`. All caps are nonnegative; excerpt-byte caps are zero when
`max_excerpts=0` and positive otherwise. It grants no category/class/scope/provider/channel.

The four named selectors have canonical expansions: `structural` selects only
`timeline|deterministic_assessments|change_observations|coverage|omissions`, no excerpt kinds,
linked-only relevance, no finding prose, no exact commands, and zero excerpt caps; `goal_aware` adds
`goal|obligations|claims|decisions`, enables policy-gated finding prose, but keeps zero excerpts; `assisted` adds
`targeted_excerpts`, all six excerpt kinds, linked-only relevance, no exact command text, and the
hard caps above while retaining finding prose; `expanded` uses the same sections/kinds and hard caps with
`linked_then_in_scope` and exact command text eligible. `custom` stores the user's exact selector
inside those ceilings. A policy whose named profile and selector do not match these rules is
invalid.

`AuthorizationScope` uses the same closed wire shape everywhere. It contains `kind` and
`installation_id` (`ins_`) for every kind; workspace/task/request add
`workspace_ref_commitment` (`hmac-sha256:<64 lowercase hex>`); task/request add `task_id` (`tsk_`);
and request adds `request_id` (`req_`). Fields for deeper descendants are forbidden at shallower
kinds, so the value preserves the complete exact ancestor chain without a generic single-reference
shortcut. It
never contains a filesystem path, task title, repository name, or raw workspace identifier.

Every unkeyed canonical digest in these privacy values uses
`sha256:<64 lowercase hex>`. Installation-local keyed commitments use
`hmac-sha256:<64 lowercase hex>` and are never substituted for unkeyed digests.

`ChannelPolicy` contains the channel, `enabled`, allowed categories/data classes, optional exact
provider binding, allowed purposes, scope ceiling, preview requirement, byte/token ceilings, and
expiry ceiling. Each of the five channels has a separate value; no default is inherited from LLM
inference. `PrivacyPolicy` contains `policy_id`, monotonically increasing `version`, canonical
`policy_digest`, one `PrivacyProfile`, one `ReviewContextProfile`, its exact
`ReviewSelectionPolicy`, `require_current_provider_data_use_evidence: bool`,
`network_egress_permitted`, all five channel policies, `local_model_enabled`, optional local-model
binding, and local-sink category ceilings. The provider-data-use guard must be false without an
external provider; when true, dispatch requires a current recommendation-eligible record. The
guard—not the provider evidence itself—is user-controlled policy authority.

The local-sink ceilings are independent. `agent_context` controls ordinary CLI/MCP/UI result
projection; `local_model` controls only the named local runtime. First-run policy allows
`agent_context` only `bounded_structural_metadata` and `declared_file_type`, leaves local-model
categories empty, and permits no user/task prose. `trusted_human_control` is not widened through
ordinary policy answers: a YZH1-authenticated foreground ceremony may disclose the exact
scope-valid nonsecret categories needed for its preview, never a never-send kind. No external LLM
category grant is inherited by a local sink or vice versa.

`PrivacyProfile` governs only the `llm_inference` channel and LLM content-disclosure behavior.
`ReviewContextProfile` plus its compiled `ReviewSelectionPolicy` is orthogonal and only narrows
deterministic case construction before the same policy fence. Its human meanings are:

- `structural`: typed IDs, statuses, event order, digests, subject-state relations, deterministic
  rule/fact codes, coverage, and omission reasons; no user prose or source excerpts;
- `goal_aware`: structural plus allowed task goals, obligations/acceptance criteria, claims,
  decisions, and finding prose; no evidence/source/transcript excerpts;
- `assisted`: goal-aware plus problem-local allowed evidence, test/failure, diff/command metadata,
  and bounded repository excerpts already captured or agent-published in the frozen case;
- `expanded`: every relevance-ranked recorded item allowed by the exact categories/classes/scope
  and caps, still without ambient repository, transcript, log, or filesystem access;
- `custom`: the exact user-selected selector plus category/class/scope/budget configuration, with
  all ordinary classification and relevance rules still enforced.

No context profile grants a category, class, purpose, provider, scope, or extra byte. A selected
item must independently pass the `ChannelPolicy` intersection. `assisted` problem-local selection
mechanically follows the reviewed claim/obligation/finding/action/result/evidence refs and records
`not_recorded|not_selected|withheld_by_policy|redacted_never_send` omissions; unavailable content is
never represented as unchanged. The official CLI's recommended recipe uses `assisted`, but the
first-run seed uses `structural` with external disclosure disabled.

Selector intersection is an exact meet: intersect section/kind sets, take the more restrictive
relevance (`linked_subjects_only`), logical-AND finding-prose and exact-command eligibility, and
minimum caps. The
effective label remains a named profile only when the resulting selector exactly equals that named
expansion; otherwise it is `custom`. Diff classification compares compiled selectors: a strict
subset/min-cap reduction or either selector boolean true→false is tightening; a strict
superset/cap increase or either boolean false→true is loosening; mixed or incomparable changes are
treated as possible loosening and require trusted local-human authority.
Changing `require_current_provider_data_use_evidence` false→true is tightening; true→false is
loosening.

`local_only` requires that channel disabled with no external provider binding; it does not silently
disable a separately authorized non-LLM channel. `network_egress_permitted` is the global ceiling:
when false, all five channel policies must be disabled; when true, it grants nothing without an
enabled exact channel policy. The four non-LLM channels may carry only their reviewed bounded
`public_structural` or synthetic schemas and can never carry `ordinary_user_content` or
`sensitive_confidential`. External LLM profiles require the ceiling true and `llm_inference`
enabled with their exact branch constraints. In v0.1 the four non-LLM values are representable
schema rows but have no production use-case/transport implementation. Policy transition rejects
proposed enablement and stores no dormant consent. A forced/imported enabled state produces a
pre-dispatch outcome/reason `channel_unavailable/channel_unavailable` structural decision receipt
with no dispatch ID/time, request commitment, authorization consumption, DNS, or socket I/O. Future
support requires an exact owner, reviewed protocol, and fresh local-human transition rather than a
generic gateway fallback or activation of old intent. Local-model permission
is orthogonal to both the network ceiling and channels.

`PolicyOverlay` may only intersect/tighten the parent policy unless accompanied by a verified
local-human policy transition produced by `application/privacy_policy.md`. An overlay cannot add a
provider, category, purpose, scope, byte/token budget, or longer expiry that the machine ceiling
does not permit. `trusted_provider` still requires one exact binding and purpose; an empty allowed
category set means no user content, not all content.

### Candidate and classification values

`CandidateContextItem` carries an internal opaque item ID, declared category, source-owned scope,
origin reference, and local plaintext bytes. Candidate bytes remain in protected process memory and
encrypted objects only. They have no public serializer and may not be logged, placed in an error,
or passed to a provider.

`ClassifiedContextItem` adds exactly one `DataClass`, zero or more `ForbiddenDataKind` findings,
scope-validity status, and classifier ruleset version. A forbidden finding can never be waived.
Unknown source label, scope ambiguity, category conflict, or incomplete scan becomes
`classification_uncertain`; it is never guessed into an allowed class.

`CandidateContext`/`ClassifiedContext` bind every item to `request_id`, channel or local sink,
purpose, selected scope, frozen subject/dependency digest where applicable, and requested provider
binding. Network channel and local sink are mutually exclusive. `agent_context` is always a local
sink even when the client later sends its response elsewhere; Yoetz applies the fence before bytes
leave MCP.

### Proposal, authorization, and approved case

`PreDispatchAuditDecision` is the plaintext-free structural subject for a decision that fails closed
before a valid prepared disclosure case can exist. It allocates one `privacy_proposal_id` (`ppr_`)
as the audit-reservation key and contains request ID, channel or local sink, purpose, exact scope,
policy ID/version/digest, requested destination identity when known, sorted category names and
bounded counts, finish time, canonical `audit_subject_digest`, and exactly one terminal pre-dispatch
outcome/reason. Its permitted
outcomes are `blocked_by_policy`, `blocked_forbidden_data`, `classification_uncertain`, and
`channel_unavailable`. It has no candidate/prepared bytes, excerpt, source reference, prepared-case
digest, authorization/dispatch identity, or request commitment. Never-send matches contribute only
kind/count summaries; denied bytes are neither retained nor committed.

`DisclosureProposal` begins with `privacy_proposal_id` (`ppr_`), which is also the durable audit
reservation key; no second caller-visible reservation identifier exists. It is the encrypted
durable preview record created only after local
minimization, redaction, canonicalization, and exact-byte secret scanning. It binds the classified
source-item digests, exact prepared excerpts/categories, blocked categories, transformation
summary, prepared-case digest, provider and model, endpoint profile, purpose, scope, policy
version/digest, ceilings, proposal expiry, and canonical proposal digest. It contains no
never-send material. A trusted human preview may decrypt only those bounded prepared excerpts
through the confidential control surface; later dispatch may not add or substitute bytes.

`AgentProjectionAuditSubject` is the separate plaintext-free subject for every
`client_result_projection` to `agent_context` or `local_human_view`, whether it approves all, some,
or no content leaves.
It contains required service-allocated `projection_request_id` (`req_`), control RPC/method/
service-instance/generation binding, optional original workflow request ID, exact scope, policy
ID/version/digest, the resolved `LocalDisclosureSink`, the per-item `DisclosureProvenance` that
authorized each allowed field,
installation-keyed internal-result and projection commitments, sorted JSON Pointer/category/
allow-or-omit/reason decisions, bounded counts, service generation, and finish time. Recording the
sink and provenance is what makes a provenance-conditional allow auditable after the fact: a
reviewer can tell an allow that rested on a category grant from one that rested on self-authorship,
without the subject ever holding the content. It contains no
result value, excerpt, source reference, unkeyed plaintext digest, or encrypted duplicate object.
The source content remains in its existing task objects; projection is an atomic, non-previewed
operation and does not need resumable copied plaintext. Replay recomputes and verifies the keyed
commitment against the authoritative internal result.

`projection_request_id` is allocated once inside the audit reservation and idempotently reused for
the exact `(rpc_id, method, service_instance_id, service_generation,
HMAC(K_audit, canonical_control_request))` binding; it is never derived from those values. The
subject carries task ID and route-identity digest iff its `AuthorizationScope` is `task|request`;
they are forbidden at `machine|workspace`. An original operation `request_id` is required only when
the owning operation schema has one. Thus taskless ready support projections are explicit,
objectless installation-catalog rows rather than invented task attachments.

Every v0.1 content-bearing `DisclosureProposal` also carries one internal owning `task_id` used only
to select the bundle encryption/object-store boundary. That storage owner is distinct from the
authorization scope, which may be a wider machine/workspace/task ceiling. v0.1 rejects a
content-bearing proposal with no owning task; enabling a future taskless content channel requires a
separate installation-scoped encrypted-audit-object contract first.

`PrivacyAuditSubject` is the closed union `PreDispatchAuditDecision |
AgentProjectionAuditSubject | DisclosureProposal`.
`PrivacyAuditPort.reserve` accepts this union. Only the `DisclosureProposal` branch may enter human
preview, approval, authorization, or dispatch; the pre-dispatch branch can only receive its exact
structural decision receipt and become terminal. The agent-projection branch may only take the
atomic local-consume/completion path and never creates an encrypted audit object. The union's
lookup identity is
`PreDispatchAuditDecision.audit_subject_digest` or
`AgentProjectionAuditSubject.projection_commitment` or
`DisclosureProposal.proposal_commitment`. The latter is exactly
`HMAC-SHA256(K_audit, b"yoetz/privacy/disclosure-proposal/v1\\x00" ||
canonical_prepared_binding)` in `hmac-sha256:` form; the unkeyed prepared-case digest exists only
inside the encrypted proposal object. Callers never substitute a case/content digest for that
subject identity.

`HumanPrivacyDecision` is a consent-source-discriminated `approved|denied` value carrying the
proposal digest, decision time, optional shortened expiry, and exact accepted diff. The
`scoped_local_human` approval branch requires a one-use strong authority commitment from exact
action-bound OS presence or confidential reauthentication. The `per_request_local_human` approval
branch instead carries the live YZH1 foreground-ceremony/consent commitment for an exact prepared
case already inside every durable ceiling; it is not cryptographic human authority and cannot
widen policy. Neither branch serializes a reusable proof or accepts a caller-supplied assurance
upgrade, MCP actor assertion, or generic approved boolean.

`EgressAuthorization` is an opaque single-use authority value: authorization ID, proposal/case
digest, exact channel, provider/model/endpoint, purpose/scope, policy version/digest, ceilings,
consent source, issue/expiry times, and service generation. It contains no plaintext and cannot be
serialized to CLI/MCP or LLM-facing output. Consumption is atomic through `PrivacyAuditPort`.

`ApprovedOutboundCase` is the only content value an external provider adapter accepts. It contains the
already minimized/redacted/scanned payload bytes, media/schema identity, sorted included item IDs
and categories, blocked-category summary, byte/token counts, exact provider binding, purpose,
authorization ID, policy digest, and case digest. It contains all user-content fields permitted to
influence deterministic provider/application request-body rendering; a provider renderer may wrap
them but cannot fetch, add, or substitute content. Its constructor requires a valid authorization;
application/provider code cannot use a plain `SemanticCase` in its place.

`ApprovedLocalDisclosureCase` contains the same classified/minimized/scanned logical content and
exact local sink/profile binding but has no `EgressChannel`, network destination, outbound-body
commitment, or `EgressAuthorization`. `ApprovedProviderCase` is the closed union of external
`ApprovedOutboundCase` and local-model `ApprovedLocalDisclosureCase`; each adapter narrows to its
own variant and cannot reinterpret one as the other.

`ForbiddenDataKind.api_credential` and `authentication_token` classify candidate/disclosure-plane
content discovered in user input, repositories, configuration, transcripts, or other context. A
separately provisioned service-vault `ProviderCredentialHandle` is not a candidate/domain value and
cannot be converted into one. Under resolved decision F-012 it is governed only by the one-attempt
transport-authentication contract.

### Receipts

`EgressReceipt` always contains exact `receipt_id`, `request_id`, and `privacy_proposal_id`; channel;
exact provider/model/
endpoint profile when applicable; privacy-policy ID/version/digest; the complete authorization
scope ancestor chain and purpose; approved and blocked categories; input/output byte and token
counts when known; counts of dropped items, redactions, secret-scan findings, and minimization
steps; consent source; finish timestamp; and bounded canonical outcome/reason. A pre-dispatch
decision receipt is keyed by its audit reservation/privacy proposal and conditionally omits
`authorization_id`, `dispatch_id`, `dispatch_started_at`, and `request_commitment` because those
facts do not yet exist. A physical-attempt receipt requires the authorization and dispatch IDs,
dispatch timestamp, and
`request_commitment = audit_mac.mac(PRIVACY_REQUEST_BODY_DOMAIN, final_request_body)`. The body is
the exact deterministic provider/application request body after approved transforms and before
transport I/O. HTTP/TLS framing, transport-generated fields, and credential-bearing authentication
metadata are excluded. It never stores body bytes, excerpts, URLs, headers, credentials, provider
exception text, or raw response.
Each physical outbound attempt has one attempt receipt, including taskless channels; a decision
receipt never fabricates an attempt identity.

`audit_store_version` is the integer constant `1`. `request_commitment`, when required, is the
closed object `{algorithm, commitment}`: `algorithm` is exactly
`hmac-sha256/yoetz-privacy-egress-request-v1`, and `commitment` matches
`hmac-sha256:<64 lowercase hex>`. v0.1 has one stable installation-derived `K_audit`, no audit-key
rotation/slot interface, and therefore no `key_slot_ref`. A receipt with `dispatch_id` requires the
exact final `counts.request_body_bytes`; a receipt without `dispatch_id` forbids that attempt-only
count.

`audit_failed` is a receipt outcome only when an audit reservation already exists and a later
pre-dispatch audit transition fails closed, after storage recovery can durably complete that exact
reserved decision. Failure of the initial reservation itself has no durable subject on which to
write a receipt; it returns the separate bounded application status `audit_failed` with no receipt,
prompt, authorization, or dispatch. A post-consumption receipt-write failure remains internal
`receipt_pending` and recovery records the real attempt outcome; it never fabricates an
`audit_failed` attempt.

`awaiting_human` and `approved` are nonterminal `PrivacyAuditState` statuses, not
`PrivacyOutcome`/receipt values. Returning a pending proposal ID or continuing an approved proposal
does not finalize an `EgressReceipt`; the first receipt for that branch is a terminal denial/expiry/
pre-dispatch failure or the terminal result of its physical attempt.

`LocalDisclosureReceipt` uses the same structural fields but names a `LocalDisclosureSink`, has no
`EgressChannel`, and records whether the sink was `local_model`, `agent_context`,
`local_human_view`, or `trusted_human_control`. Both receipt types are persisted through one
`PrivacyAuditPort`; neither is
a task event. Both enforce the protocol's same closed outcome/reason compatibility matrix:
`completed` forbids a failure reason, while every other outcome requires exactly one reason valid
for that outcome. `approval_expired` admits `authorization_expired|authorization_stale|
authorization_reused`; `blocked_by_policy` admits `insufficient_approved_context` in addition to
the direct policy/scope/category/purpose/destination reasons. No other cross-pair is valid.

`ApprovedLocalItem` is the service-internal `(json_pointer, category, bounded_bytes)` leaf released
after local consumption. `LocalDisclosureOmission` is `(json_pointer, category, reason)` where
reason is exactly `local_disclosure_not_authorized|never_send_redacted`. `LocalDisclosureApproved`
carries proposal/request/sink/purpose/scope/
policy/case-or-projection bindings, sorted approved items and omissions, and a durable local
receipt; `LocalDisclosureBlocked` carries the same structural binding, no approved bytes, at least
one omission, and its durable terminal receipt. `LocalDisclosureUnavailable` carries only request,
sink, and `audit_failed`; it has no receipt/content. None has a public/MCP serializer.

A local receipt for `agent_context` records Yoetz's release to the ordinary client boundary, not the
client host's eventual model/provider/retention behavior. A local-model receipt records Yoetz's
AF_UNIX disclosure to the exact bound runtime, not that runtime's later network behavior.

## Errors and edge cases

- Any never-send finding forces `blocked_forbidden_data`, even under `trusted_provider` or a human
  approval. An attempted approval of it is invalid, not an override.
- Case/item cap overflow fails closed before proposal or dispatch; deterministic minimization may
  remove lower-priority material and records the exact removal count.
- Policy/version/provider/scope/purpose/case mismatch, expired authorization, already-consumed
  authorization, service generation change, or tightening after approval invalidates dispatch.
- Token counts are estimates before dispatch and optional provider-reported integers afterward;
  absence is tagged structurally, never represented as a fabricated zero.
- The scanner is defense in depth. A source that cannot prove its scope/category is blocked even if
  no byte pattern resembles a secret.
- A receipt commitment cannot be used as a lookup oracle: the key is installation-local and owned
  by the vault; public APIs expose the receipt ID and structural outcome, not commitment inputs.
- Initial audit-reservation failure is the sole fail-closed outbound-decision path with no durable
  receipt. Because reservation precedes prompt/authorization/dispatch, it can never hide a physical
  attempt.

## Invariants

1. Network channels and local disclosure sinks are disjoint closed enums.
2. Every policy includes the explicit global network ceiling and a decision for all five network
   channels; omission is invalid, and a false ceiling requires every channel disabled.
3. Privacy profiles constrain LLM inference only; `local_only` forbids external LLM/user-content
   disclosure but may coexist with an explicitly enabled bounded structural non-LLM channel.
4. No value can authorize a never-send kind, and no lower-scope overlay can exceed its ancestors.
5. Provider adapters can accept only their exact `ApprovedProviderCase` variant;
   candidate/classified values have no adapter-facing protocol.
6. Every authorization is exact, expiring, generation-bound, and consumable at most once.
7. Receipts are structural and commitment-bearing but contain no outbound request-body plaintext;
   the commitment does not claim coverage of HTTP/TLS framing or credential-auth metadata.
8. Agent-context output is classified and minimized before MCP rendering; MCP never acts as human
   approval authority.
9. v0.1 non-LLM channel unavailability is a no-dispatch structural decision, never an attempted or
   ambiguous transport failure.
10. A pre-dispatch block can be durably audited without constructing or retaining a denied prepared
    case, and only a content-bearing disclosure proposal can enter approval/authorization.
11. `PrivacyOutcome` contains receipt outcomes only; the consumed-but-unreceipted fact is represented
    by the in-flight `receipt_pending` audit lifecycle state, not a receipt outcome.
12. Every non-success receipt has exactly one outcome-compatible `PrivacyReason`; `completed` has
    none, for both network-egress and local-disclosure receipt variants.
13. A local proposal is consumed at most once; agent-context bytes exist in a serialized response
    only after the matching local receipt is durable, and local-model replay cannot resend it.
14. Review-context selection and disclosure authority are independent: changing the former cannot
    widen categories, classes, scope, destination, or caps, and missing excerpt content never proves
    an unchanged subject state.

## Tests

- Domain tests cover every enum, the network-ceiling x five-channel matrix, policy intersection,
  `local_only` plus bounded non-LLM channels, scope ancestry, provider/purpose binding, never-send
  non-overridability, every review-context profile, provider data-use recommendation eligibility,
  canonical ordering, case caps, and receipt plaintext rejection.
- Property tests generate arbitrary overlay chains and prove the effective permission set never
  grows without a verified loosening transition.
- Conformance tests prove provider constructors reject every input type except an exact approved
  case and that agent-context/local-model disclosures traverse the same never-send classifier.
- Receipt tests freeze the pre-dispatch-subject union, initial-reservation-failure exception,
  terminal-only outcome vocabulary, exact v0.1 commitment object, audit-store version, and
  dispatch/request-body-count conditionals.

## Open questions

None.
