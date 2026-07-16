# docs/protocol/privacy-setup-wizard.md — trusted local privacy setup and policy-change UI contract

**Wave:** C/E (contract), post-v0.1 (optional graphical implementation) | **ADRs:** ADR-004,
ADR-006, ADR-008, ADR-009 | **Imports (spec-tree):**
`schemas/privacy/setup-wizard-contract-1.0.0.schema.json`,
privacy-policy and outbound-case schemas, data-egress protocol | **Imported by:** CLI setup,
future desktop UI, documentation, conformance tests

## Purpose

Freeze the experience and security contract for creating or widening privacy policy without tying
Yoetz to one graphical toolkit. A CLI trusted-control flow can satisfy v0.1; a future UI must present
the same questions, examples, preview, and confirmation semantics.

## Public surface

The setup session exposes closed actions `begin`, `answer`, `review`, `propose`, `tighten`, and
`cancel`, and closed states `recipe`, `network`, `local_models`, `provider`, `review_context`, `categories`,
`agent_context_categories`, `local_model_categories`,
`request_confirmation`, `channel_consents`, `scope`, `review`, `decision_required`, `complete`, and
`cancelled`.

After an optional user-selected CLI recipe is expanded into visible answers, it asks exactly the
following thirteen questions; the token and answer shape are protocol, not UI copy:

| Order | `question_id` | Meaning | Answer type |
|---:|---|---|---|
| 1 | `network_egress` | global ceiling: whether any Yoetz network egress may be permitted | boolean |
| 2 | `local_models` | whether local models are permitted | boolean |
| 3 | `external_provider` | which external provider/model/endpoint profile is trusted and whether current eligible data-use evidence is a runtime guard | closed `{binding: ProviderBinding, require_current_provider_data_use_evidence: boolean}` or fixed token `none` |
| 4 | `review_context` | which deterministic selection strategy builds semantic review cases | closed `{profile: structural\|goal_aware\|assisted\|expanded\|custom, selection: ReviewSelectionPolicy}` |
| 5 | `content_categories` | which categories/classes may be sent to the external LLM binding | closed `{categories: DataCategory[], data_classes: DataClass[]}` |
| 6 | `agent_context_categories` | which categories/classes results may release to an agent-capable host for material that host did not author; self-authored material is already provenance-allowed and human-readable local terminal output is the separate `local_human_view` sink | same closed selection object |
| 7 | `local_model_categories` | which categories/classes the selected local model runtime may receive | same closed selection object |
| 8 | `request_confirmation` | whether every external LLM request needs preview/confirmation | boolean |
| 9 | `product_telemetry` | whether product telemetry is permitted | boolean |
| 10 | `crash_diagnostics` | whether crash diagnostics are permitted | boolean |
| 11 | `update_checks` | whether update checks are permitted | boolean |
| 12 | `capability_testing` | whether capability testing is permitted | boolean |
| 13 | `authorization_scope` | machine, workspace, task, or this request | closed full-ancestor-chain `AuthorizationScope` |

The nested provider binding's closed fields are `provider_id`, `model_id`, `endpoint_profile_id`,
`endpoint_profile_version`, and `transport: external`. The evidence-guard boolean is editable and
grants no disclosure by itself. The scope answer always carries
`installation_id`; adds `workspace_ref_commitment` at workspace and below; adds `task_id` at task
and below; and adds `request_id` only at request scope. UI labels may be localized, but IDs and
answer types cannot change.

The UI renders policy-diff and request-preview view models from the setup contract schema. It never
accepts credentials, keys, recovery secrets, provider tokens, or vault passphrases.

## Behavior

`begin` reads the current policy version and available non-secret provider/local-runtime profiles,
including each exact external profile's current `ProviderDataUseProfile`, from the trusted service.
It does not probe the network. Answers produce a draft only; no egress or provider construction
occurs during setup. Data-use wording is labeled provider-profile evidence, not a technical guarantee.

The technical CLI may first offer five convenience labels: `private`, `metadata-only`,
`assisted-review` (recommended when an eligible endpoint exists), `expanded-review`, and `custom`.
They map exactly to protocol `recipe_hint` tokens `private`, `metadata_only`, `assisted_review`,
`expanded_review`, and `custom`, respectively.
A `begin` action may carry that optional non-authoritative `recipe_hint`; the service and CLI both
verify its frozen expansion. After the user chooses it, the service emits the thirteen ordinary
typed answers, the CLI shows all of them, and the user may edit any answer before review. The label
is echoed only while the answers still exactly match the recipe; after an edit it becomes `custom`.
The service validates only the resulting exact policy. Selecting a recipe cannot commit a widening,
provision credentials, or skip the trusted-local policy decision.

The no-answer/fail-safe draft defaults to `local_only`, `review_context=structural`,
`network_egress=false`, all five network channels disabled, no local model unless explicitly
selected, no external/local-model content categories, and
`agent_context_categories={categories:[bounded_structural_metadata, declared_file_type],
data_classes:[public_structural]}`.

That ceiling governs only material the requesting writer did not author. Because `agent_context` is
provenance-conditional, an agent always receives its own published prose and the deterministic
findings computed solely from it, on this default and with zero egress: that content is already in
the host's context, so withholding it protects nothing while breaking the
`check → respond → recheck` loop the product exists for. What the default withholds from an
unwidened agent is exactly what it did not author — another writer's or subagent's material,
imported Codex events, and semantic reviewer prose — plus every `sensitive_confidential` item and
the whole never-send set, which no provenance ever unlocks.

The default therefore lets an unwidened client receive IDs, status codes, counts, digests, declared
file types, and its own material, and omits other-authored task/finding/evidence prose until a human
explicitly widens that sink.

Ordinary human-readable CLI/UI rendering to an attached controlling terminal resolves to the
separate `local_human_view` sink and is not gated by this answer at all. A local human reading their
own terminal, on a vault they unlocked, is not a disclosure to a third party. `--json`, a piped or
redirected stream, a non-TTY invocation, and every MCP client resolve to `agent_context`. The wizard
states this plainly, because a user answering question 6 is choosing what reaches an *agent host*,
not what they themselves may read.

Question 1 maps to
`PrivacyPolicy.network_egress_permitted`: `false` forces all five channels denied; `true` enables
none and only permits later channel-specific choices. Choosing network egress is not consent to a
provider, channel, or content class. Choosing a provider is not consent to telemetry, diagnostics,
updates, or capability testing.

The recommended `assisted_review` recipe is deliberately different from that fail-safe draft. It
expands to `PrivacyProfile=trusted_provider`, `ReviewContextProfile=assisted`, workspace scope,
the canonical assisted `ReviewSelectionPolicy`, `request_confirmation=false`,
`include_finding_prose=true`, `include_exact_command_text=false`,
`require_current_provider_data_use_evidence=true`, data classes
`public_structural|ordinary_user_content`, external
categories `bounded_structural_metadata|declared_file_type|task_description|claim_text|
obligation_text|decision_excerpt|evidence_excerpt|finding_summary|command_metadata|diff_metadata|
repository_excerpt`, and exact agent-context selection
`{categories:[bounded_structural_metadata,declared_file_type,finding_summary],
data_classes:[ordinary_user_content,public_structural]}` in canonical sorted order. It excludes
`sensitive_confidential`, `transcript_excerpt`, broad source selection, exact command text, and
every never-send kind; it may carry allowed command metadata, while exact command text requires an
explicit `expanded` or `custom` selector. The released recipe records exact
byte/token ceilings from the support profile and keeps the v0.1 16 KiB/item and 256 KiB/case hard
caps.

That recipe is displayed as recommended only for an exact installed endpoint whose current,
versioned data-use record states training `prohibited`, retention `none|bounded`, and provider human
access `prohibited|restricted`. Known-broad, unknown, or stale posture removes the badge. The user
can still choose a supported endpoint through `custom`, but the UI must not call it no-training or
inherit the recommendation's claim.

Question 4 chooses only the context-selection strategy. It grants no category/class/scope or sink.
The answer includes the compiled selector so the review is exact: named profiles must match their
canonical expansions in `domain/privacy.md`; `custom` may edit sections, excerpt kinds, relevance,
finding-prose eligibility, exact-command eligibility, and caps inside the hard ceilings. `assisted` may select only
problem-local excerpts already captured or agent-published at
the frozen frontier. The current v0.1 service does not browse live Git or the filesystem, and the
review shows `not_recorded` rather than implying missing code was inspected.

Question 5 is external-LLM content only. Questions 6 and 7 are independent local-disclosure
ceilings: authorizing task/finding content for an external provider does not authorize an MCP host
or local runtime, and vice versa. `local_model_categories` must be empty when local models are off.
`trusted_human_control` is deliberately not a persistent category grant: an authenticated YZH1
preview may show the exact scope-valid nonsecret categories needed for that ceremony, while the
never-send set remains blocked. The UI warns that categories approved for `agent_context` may enter
the MCP/CLI/UI host agent's model context; Yoetz's local receipt does not prove that host's later
provider, network, retention, or training behavior.

For questions 5–7, an item is allowed only when both its category and data class are selected.
`data_classes` may contain only `public_structural`, `ordinary_user_content`, and
`sensitive_confidential`; `secret_or_cryptographic` is never selectable. Sensitive/confidential is
off by default, shown as its own high-impact widening, and requires strong YZH1 reauthentication.
Selecting a category alone never silently authorizes sensitive instances of it.

The four privacy profiles govern LLM inference/content disclosure only. `local_only` disables the
LLM network channel and external LLM-provider construction; it does not answer questions 9–12. A
future policy may therefore combine `local_only` with a bounded non-LLM row after enabling the
global ceiling. v0.1 has no production transports for those four rows: the review marks them
`unsupported` and off, `propose` rejects a true answer as `channel_unavailable` without changing
durable policy or making I/O, and a later implementation cannot activate an old draft/answer
without a fresh local-human capability/policy confirmation. Future channels use only reviewed
structural/synthetic schemas and cannot
carry task/user content. The review labels true zero-network operation as the composite
`local_only` + `network_egress=false` + all five channels disabled, subject to the separately stated
local-runtime caveat.

Every profile screen shows concrete synthetic examples:

> Allowed when selected: task description, chosen evidence excerpt, bounded structural metadata,
> and declared file type.

> Always blocked: credentials, unrelated files, complete transcripts, environment variables,
> encryption material, and content outside the approved scope.

`review` displays the global network ceiling, effective destination/purpose/scope/categories, every
channel independently, the review-context profile and exact compiled selector, recipe expansion,
editable data-use runtime guard, endpoint data-use profile ID/version/evidence digest/posture/
expiry, whether per-request preview applies, the non-overridable never-send set, and a
semantic diff from the current policy. Widening is highlighted and cannot commit via
`review` alone. It never describes a true ceiling with every channel denied as active egress, or a
`local_only` policy with a separately enabled-but-unavailable telemetry/update/diagnostic/capability
row as external LLM use or as an active v0.1 transport.

`tighten` commits only when the service independently proves every policy dimension is no broader.
`propose` never commits a widening. Loosening provider, destination, purpose, category, scope,
profile, compiled selector, data-use guard, or network channel returns `decision_required` with
`privacy_proposal_id` (`ppr_`), exact `sha256:` draft digest, policy version, expiry, and the exact
reviewed data-use profile ID/version/evidence digest when an external provider is selected—no
generic pending-request ID, challenge, proof or reusable authority. The proposal digest binds that
evidence record; any record change before commit invalidates the review and requires a new one. A
separate foreground
`HumanControlService` confidentially renders the pending proposal, reauthenticates the local human,
and commits internally. This ordinary setup schema has no decide/confirm method. Ordinary MCP,
agent, import, provider, and LLM schemas cannot obtain the preview or decision authority; the
same-UID threat limitation and strong reauthentication boundary are stated separately.

For `confirm_every_request`, the same renderer shows the exact post-minimization, post-redaction,
post-secret-scan excerpts and category/count summary immediately before dispatch. Approval is bound
to one physical dispatch of the outbound-case digest; editing any transmitted byte invalidates it.
Every physical retry requires a fresh preview/decision even for identical bytes. Resume before
authorization consumption continues the same dispatch and is not a retry. When the case is already
inside durable policy, exact foreground TTY consent is sufficient under resolved decision F-011
and is not cryptographic proof against arbitrary malicious same-UID automation.

After the `assisted_review` policy has been committed once, ordinary semantic checks and automatic
retries inside that exact standing policy do not prompt a human. Each physical attempt still gets a
fresh one-use authorization and receipt. Reviewer challenges are returned to the main agent as
ordinary semantic findings; the agent may respond, publish work/evidence or a revised claim, and
recheck without human involvement. Human presence remains required only for policy widening,
credential mutation, an explicit `confirm_every_request` attempt, or the existing human-only
finding waiver. Never-send and out-of-scope material cannot be approved at all.
The standard verification default is `semantic_if_configured`, so committing the assisted policy
and configuring its exact provider is sufficient for ordinary checks to invoke review; the user may
still explicitly request deterministic-only or semantic-required behavior.

Selecting a local model shows the exact runtime/profile and states that the process receives
plaintext. Unless its support cell proves enforceable no-network sandboxing, the runtime is an
explicitly trusted local component and Yoetz's AF_UNIX-only delivery is not evidence about that
process's later network behavior (F-013).

The future graphical UI may add navigation and accessibility affordances and may offer the same
user-selected recipes. It cannot hide, merge, or weaken a question, and it cannot treat a recipe as
an answered/committed widening until the expanded thirteen-answer review is explicitly accepted.
Yoetz owns policy validation and commit; the UI cannot write policy files directly.

## Errors and edge cases

- Closing, disconnecting, or crashing before completion commits nothing.
- A locked service may show status and confidential-unlock guidance but cannot expose policy content
  requiring decrypted state or accept ordinary-channel secrets.
- An unavailable provider profile remains selectable only as an explanatory unsupported value; it
  cannot be committed as ready.
- Headless setup may use a trusted local control protocol, but never an ordinary flag, environment
  variable, config secret, MCP tool, or stdin stream shared with an agent.
- Accessibility output must preserve blocked/allowed distinctions without relying on color alone.
- A UI cannot label a destination “trusted” without showing exact provider, model, endpoint-profile
  ID/version, and transport.
- A draft with `network_egress=false` and any enabled channel is invalid. A draft with
  `network_egress=true` and all channels disabled is valid but performs no network work.
- A proposed enabled non-LLM row is rendered `unsupported in v0.1` and cannot commit; upgrade-time
  transport availability is a widening that requires a fresh trusted local-human confirmation
  before activation.

## Invariants

1. Defaults set the global ceiling false and disclose nothing externally.
2. The global ceiling and each egress channel receive separate, comprehensible choices; the ceiling
   never implies channel consent.
3. Widening requires a fresh foreground local-human decision bound internally to the exact draft;
   ordinary setup only queues it.
4. Setup carries no keys, credentials, passphrases, or user-content payloads.
5. Yoetz, not the rendering surface, validates and commits policy.
6. CLI and future UI implement the same thirteen question IDs, typed answers, and
   `network_egress`-as-global-ceiling mapping.
7. A recipe is transparent draft generation, never consent or authority; upstream `assisted`
   recommendation eligibility is exact-profile and evidence-bound.
8. Selector expansion and provider-data-use evidence shown at review are digest-bound through the
   trusted decision; the user-controlled runtime guard decides whether later evidence expiry fences
   dispatch.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/subprocess/test_service_lock_and_confidential_unlock.py`
- `tests/packaging/test_privacy_docs_and_resources.py`

## Open questions

None.
