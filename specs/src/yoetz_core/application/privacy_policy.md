# src/yoetz_core/application/privacy_policy.py — privacy setup and policy-transition use cases

**Wave:** D | **ADRs:** ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/privacy.md`, `protocol/errors.md` | **Imported by:**
`application/service.md`, trusted privacy CLI/control UI, setup wizard

## Purpose

Provide the only supported way to inspect, initially configure, tighten, or loosen privacy policy.
The setup wizard and future UI are clients of these use cases; they never write TOML, catalog rows,
or service-vault state directly. This keeps policy inheritance, human authority, exact diffs, and
revocation behavior identical across surfaces.

## Public surface

- `async get_privacy_setup(app, request: GetPrivacySetupRequest) -> PrivacySetupView`.
- `async propose_privacy_policy(app, request: ProposePrivacyPolicyRequest) -> PolicyProposalResult`.
- `async decide_privacy_policy(app, request: DecidePrivacyPolicyRequest) -> PrivacyPolicyResult`.
- `async tighten_privacy_policy(app, request: TightenPrivacyPolicyRequest) -> PrivacyPolicyResult`.
- `async list_privacy_receipts(app, request: ListPrivacyReceiptsRequest) -> PrivacyReceiptPage`.
- `async get_privacy_receipt(app, request: GetPrivacyReceiptRequest) -> PrivacyReceiptView`.
- Frozen setup values: `PrivacySetupView`, `ChannelSetupChoice`, `AllowedBlockedExample`,
  `PolicyProposalResult`, `PrivacyPolicyResult`.

These are support/control-plane operations, not additions to the six public workflow operations and
not MCP tools. Their wire schemas belong to the trusted local control protocol, not the agent-facing
workflow protocol.

Receipt list/get is admitted only for authenticated CLI/UI ordinary control. It delegates to the
bounded structural `PrivacyAuditPort` query, never dereferences proposal/object content, and is
explicitly local-projection/audit-exempt so inspecting receipts does not create receipts. `list`
binds its cursor to one exact catalog audit snapshot generation and excludes records committed after
that generation; page traversal is stable and non-self-mutating. `get` returns one exact structural
view or bounded `not_found` without an existence oracle outside the local authenticated user.

## Behavior

`get_privacy_setup` returns the effective policy, ancestor ceilings, editable scope, explicit
`network_egress_permitted` global ceiling, exact five network-channel decisions and capability
states, local-model decision, provider/model/endpoint binding, allowed content categories, preview
requirement, telemetry choice, independent `agent_context_categories` and
`local_model_categories`, and concrete examples. `content_categories` is external-LLM-only; no
category selection is copied between those three destinations. Required examples include:

- allowed: selected task description, selected evidence excerpt, bounded structural metadata,
  declared file type;
- blocked: credentials, unrelated files, complete transcripts, environment variables, encryption
  material, and content outside approved scope.

The view is structural and category-based. It does not load repository files, sample secrets, dump
configuration/environment, or reveal provider credential presence beyond `configured|unavailable`.
It states that approved `agent_context` content may enter the host agent's model context and that a
Core local-disclosure receipt cannot attest that host's later egress or retention. Authenticated
`trusted_human_control` previews may show exact scope-valid nonsecret content needed by the YZH1
ceremony and are never configurable by an ordinary or MCP answer.

`propose_privacy_policy` validates an exact target scope (`machine|workspace|task|request`), computes
the effective before/after intersection and canonical diff, and classifies the transition. A pure
tightening delegates to `tighten_privacy_policy`. Any possible expansion persists an encrypted
proposal and requires trusted-control reauthentication; it cannot be committed by the proposing
request.

`decide_privacy_policy` is service-internal and absent from ordinary control. It accepts only the
proposal ID/digest and decision from `HumanControlService` together with a still-current one-use
reauthentication proof bound to the exact proposal/service/vault generations. The service consumes
that proof atomically with the exact prepared diff; it is never serialized or returned to the
helper. Approval durably records the authority commitment, increments policy generation, and
closes/revokes affected sessions/grants. It then calls the gateway's generation-fenced
`reconcile_policy` with current `HumanAuthorityCapability`: newly disallowed adapters are fenced immediately and newly allowed exact
credential-free adapter factories activate atomically only when their installed nonsecret profile
is valid. The result reports structural activation/unavailability. Policy success never depends on
provider construction. Missing credentials are discovered only when the gateway tries to mint the
fresh per-physical-attempt body/profile/deadline-bound handle; semantic work then remains explicitly
incomplete, and later credential provisioning needs no reusable SDK client or adapter reconstruction.
Editing choices creates a new proposal.

The initial wizard asks independently: the global network-egress ceiling; local model permission; exact external
provider/endpoint; allowed categories; per-request preview; bounded telemetry; and policy scope. It
also shows all five channel decisions. Defaults are `local_only`, no network channels, no local
model until separately configured, and request content blocked from agent context unless the
workflow's scope policy explicitly permits its bounded projection.

`network_egress=false` requires all five rows off; `true` grants none. The four privacy profiles
govern only LLM inference. v0.1 reports the four non-LLM channel capabilities
`unsupported` and rejects any transition that turns one on with bounded `channel_unavailable`,
without persisting the draft or making I/O. A crafted/imported enabled row remains fenced by the
coordinator and yields only a no-dispatch unavailable decision receipt. A future release that adds
an exact channel implementation treats capability availability plus enablement as a fresh widening:
it must keep the row off until a new exact local-human transition, never activate a prior answer or
stored intent during reconciliation/update.

## Errors and edge cases

- MCP, an agent, LLM, plugin, imported actor, noninteractive stdin, ordinary control session, or
  ordinary CLI argument cannot satisfy decision authority; return `PRIVACY_AUTHORITY_REQUIRED`
  without a prompt. Ordinary trusted-local control may inspect, propose, and tighten only.
- An ancestor ceiling prevents a requested child expansion; return a bounded exact diff reason and
  leave policy unchanged.
- Concurrent transition or service generation change makes a prepared proposal stale; never merge
  it silently.
- Disabling a provider/channel serializes on the same policy-generation CAS as external/local
  consumption. If tightening commits first, consume fails and no bytes cross; the unconsumed branch
  closes with a no-dispatch receipt. If consume commits first, exactly that admitted disclosure may
  cross, tightening best-effort closes it, its actual/unknown terminal receipt is repaired, and its
  result cannot be selected under the newer policy. The UI says this honestly rather than claiming
  already admitted bytes can be retracted.
- Crash/failure after policy commit but before adapter activation leaves the new policy durable and
  the adapter absent. Ready startup reconciles it; it never restores a pre-policy adapter or
  requires policy replay.
- The never-send set is shown but not editable; attempts to modify it are invalid requests.
- Unsupported non-LLM enablement returns `channel_unavailable`; it is never silently stored,
  normalized to enabled, or converted to consent when an upgrade adds a capability.
- MCP/agent/provider callers cannot list/get privacy receipts; queries cannot expose request body,
  excerpt, source path, encrypted-object reference, or content-derived unkeyed digest.

## Invariants

1. No UI/wizard/CLI writes policy storage directly.
2. Every expansion has reauthentication, exact diff, explicit local-human decision, and durable
   provenance.
3. Every child scope is bounded by all ancestors.
4. Network channels are configured independently and default denied.
5. The never-send set is neither configurable nor overridable.
6. Setup examples accurately reflect the enforced classifier and policy vocabulary.
7. Every committed policy generation is reconciled to the provider registry; unavailable
   activation is explicit and safe, never an implicit restart requirement.
8. Confirm-every-request case consent is not a policy expansion and cannot call this commit path;
   it uses exact foreground TTY consent only while inside existing durable ceilings.
9. A committed policy never bypasses the runtime human-authority capability fence; unavailable
   keyring-mode authority leaves policy stored but all external bindings inactive.
10. The global ceiling grants no channel, and v0.1 stores no dormant consent for an unsupported
    non-LLM capability.
11. Privacy receipt inspection is structural, snapshot-stable, non-mutating, and never an MCP tool.

## Tests

- Unit tests cover setup defaults/examples, transition classification, intersection, exact diff,
  and stale proposal behavior.
- Integration tests exercise initial setup, immediate tightening/session closure, authenticated
  loosening, restart, and concurrent updates against the policy store.
- Conformance tests prove MCP/agent calls cannot reach decision authority or write policy state.
- Inspection tests prove list/get snapshot stability, cursor authentication, bounded filtering,
  plaintext absence, no audit recursion, and MCP denial.

## Open questions

None.
