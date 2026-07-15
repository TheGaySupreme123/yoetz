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
`cancel`, and closed states `network`, `local_models`, `provider`, `categories`,
`agent_context_categories`, `local_model_categories`,
`request_confirmation`, `channel_consents`, `scope`, `review`, `decision_required`, `complete`, and
`cancelled`.

It asks exactly the following twelve questions; the token and answer shape are protocol, not UI copy:

| Order | `question_id` | Meaning | Answer type |
|---:|---|---|---|
| 1 | `network_egress` | global ceiling: whether any Yoetz network egress may be permitted | boolean |
| 2 | `local_models` | whether local models are permitted | boolean |
| 3 | `external_provider` | which external provider/model/endpoint profile is trusted | exact external `ProviderBinding` or fixed token `none` |
| 4 | `content_categories` | which categories/classes may be sent to the external LLM binding | closed `{categories: DataCategory[], data_classes: DataClass[]}` |
| 5 | `agent_context_categories` | which categories/classes ordinary CLI/MCP/UI results may release to an agent-capable host | same closed selection object |
| 6 | `local_model_categories` | which categories/classes the selected local model runtime may receive | same closed selection object |
| 7 | `request_confirmation` | whether every external LLM request needs preview/confirmation | boolean |
| 8 | `product_telemetry` | whether product telemetry is permitted | boolean |
| 9 | `crash_diagnostics` | whether crash diagnostics are permitted | boolean |
| 10 | `update_checks` | whether update checks are permitted | boolean |
| 11 | `capability_testing` | whether capability testing is permitted | boolean |
| 12 | `authorization_scope` | machine, workspace, task, or this request | closed full-ancestor-chain `AuthorizationScope` |

The provider answer's closed fields are `provider_id`, `model_id`, `endpoint_profile_id`,
`endpoint_profile_version`, and `transport: external`. The scope answer always carries
`installation_id`; adds `workspace_ref_commitment` at workspace and below; adds `task_id` at task
and below; and adds `request_id` only at request scope. UI labels may be localized, but IDs and
answer types cannot change.

The UI renders policy-diff and request-preview view models from the setup contract schema. It never
accepts credentials, keys, recovery secrets, provider tokens, or vault passphrases.

## Behavior

`begin` reads the current policy version and available non-secret provider/local-runtime profiles
from the trusted service. It does not probe the network. Answers produce a draft only; no egress or
provider construction occurs during setup.

The wizard defaults to `local_only`, `network_egress=false`, all five network channels disabled,
no local model unless explicitly selected, no external/local-model content categories, and
`agent_context_categories={categories:[bounded_structural_metadata, declared_file_type],
data_classes:[public_structural]}`. This lets clients
receive IDs, status codes, counts, digests, and declared file types but omits task/finding/evidence
prose until a human explicitly widens that sink. Question 1 maps to
`PrivacyPolicy.network_egress_permitted`: `false` forces all five channels denied; `true` enables
none and only permits later channel-specific choices. Choosing network egress is not consent to a
provider, channel, or content class. Choosing a provider is not consent to telemetry, diagnostics,
updates, or capability testing.

Question 4 is external-LLM content only. Questions 5 and 6 are independent local-disclosure
ceilings: authorizing task/finding content for an external provider does not authorize an MCP host
or local runtime, and vice versa. `local_model_categories` must be empty when local models are off.
`trusted_human_control` is deliberately not a persistent category grant: an authenticated YZH1
preview may show the exact scope-valid nonsecret categories needed for that ceremony, while the
never-send set remains blocked. The UI warns that categories approved for `agent_context` may enter
the MCP/CLI/UI host agent's model context; Yoetz's local receipt does not prove that host's later
provider, network, retention, or training behavior.

For questions 4–6, an item is allowed only when both its category and data class are selected.
`data_classes` may contain only `public_structural`, `ordinary_user_content`, and
`sensitive_confidential`; `secret_or_cryptographic` is never selectable. Sensitive/confidential is
off by default, shown as its own high-impact widening, and requires strong YZH1 reauthentication.
Selecting a category alone never silently authorizes sensitive instances of it.

The four privacy profiles govern LLM inference/content disclosure only. `local_only` disables the
LLM network channel and external LLM-provider construction; it does not answer questions 8–11. A
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
channel independently, whether per-request preview applies, the non-overridable never-send set,
and a semantic diff from the current policy. Widening is highlighted and cannot commit via
`review` alone. It never describes a true ceiling with every channel denied as active egress, or a
`local_only` policy with a separately enabled-but-unavailable telemetry/update/diagnostic/capability
row as external LLM use or as an active v0.1 transport.

`tighten` commits only when the service independently proves every policy dimension is no broader.
`propose` never commits a widening. Loosening provider, destination, purpose, category, scope,
profile, or network channel returns `decision_required` with `privacy_proposal_id` (`ppr_`), exact
`sha256:` draft digest, policy version and expiry—no generic pending-request ID, challenge, proof or
reusable authority. A separate foreground
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

Selecting a local model shows the exact runtime/profile and states that the process receives
plaintext. Unless its support cell proves enforceable no-network sandboxing, the runtime is an
explicitly trusted local component and Yoetz's AF_UNIX-only delivery is not evidence about that
process's later network behavior (F-013).

The future graphical UI may add navigation and accessibility affordances but cannot skip, merge,
preselect, or weaken a question. Yoetz owns policy validation and commit; the UI cannot write policy
files directly.

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
6. CLI and future UI implement the same twelve question IDs, typed answers, and
   `network_egress`-as-global-ceiling mapping.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/subprocess/test_service_lock_and_confidential_unlock.py`
- `tests/packaging/test_privacy_docs_and_resources.py`

## Open questions

None.
