# schemas/privacy/setup-wizard-contract-1.0.0.schema.json — trusted privacy setup UI messages

**Wave:** C/E (contract), post-v0.1 (optional graphical surface) | **ADRs:** ADR-004, ADR-006 and
ADR-009 | **Imports (spec-tree):** privacy-policy schema | **Imported by:** trusted
local CLI control flow, future UI, policy mutation service, setup conformance tests

## Purpose

Freeze the secret-free ordinary-control messages used to render, review, propose, or tighten a
privacy policy. The schema
keeps CLI and future UI behavior equivalent while preserving Yoetz as the sole policy authority.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/privacy/setup-wizard-contract/1.0.0`.
- Media type: `application/vnd.yoetz.privacy-setup+json`.
- A closed discriminated union of client actions `begin|answer|review|propose|tighten|cancel` and
  service views `question|policy_review|decision_required|complete|cancelled|setup_error`.

## Behavior

Every message has `schema_version: "1.0.0"`, `session_id` matching `psw_` plus a canonical
lowercase UUIDv4,
`message_type`, `sequence` as canonical decimal string, and `expires_at`. Unknown union branches or
branch fields fail.

Client actions:

- `begin`: current policy digest/version or explicit first-run marker; no answers.
- `answer`: exactly one of the following twelve stable question IDs with its sole accepted answer
  shape:

  | `question_id` | answer type |
  |---|---|
  | `network_egress` | JSON boolean |
  | `local_models` | JSON boolean |
  | `external_provider` | exact closed external `ProviderBinding` (`provider_id`, `model_id`, `endpoint_profile_id`, `endpoint_profile_version`, `transport: external`) or the fixed token `none` |
  | `content_categories` | closed `{categories, data_classes}` selection object |
  | `agent_context_categories` | closed `{categories, data_classes}` selection object |
  | `local_model_categories` | closed `{categories, data_classes}` selection object |
  | `request_confirmation` | JSON boolean |
  | `product_telemetry` | JSON boolean |
  | `crash_diagnostics` | JSON boolean |
  | `update_checks` | JSON boolean |
  | `capability_testing` | JSON boolean |
  | `authorization_scope` | the closed full-ancestor-chain `AuthorizationScope` object from the privacy-policy schema |

  No generic string, free-form object, alternate question spelling, or implicit coercion is
  accepted.

`content_categories` applies only to the bound external `llm_inference` provider.
`agent_context_categories` independently controls content released in ordinary CLI/MCP/UI results
and never authorizes an external provider. `local_model_categories` independently controls the
named local runtime and must be empty when `local_models=false`. No answer silently copies
categories between sinks. `trusted_human_control` is not a persistent wizard category grant:
during an authenticated foreground YZH1 ceremony it may show any scope-valid nonsecret category
required by that exact preview, while the never-send set remains non-overridable.

Each selection object has exactly `categories` (ASCII-sorted unique closed `DataCategory[]`) and
`data_classes` (ASCII-sorted unique subset of `public_structural|ordinary_user_content|
sensitive_confidential`). `secret_or_cryptographic` is schema-invalid. Category and class must both
authorize an item. Defaults are structural only for `agent_context` and empty for external/local
model. Adding `sensitive_confidential` is a separately highlighted widening, requires strong YZH1
reauthentication, and never changes the never-send rule.
- `review`: exact draft digest and expected current policy version.
- `propose`: exact draft digest and expected current version; widening can only create a pending
  decision, never commit.
- `tighten`: exact draft digest and expected version; commits only after server proves the diff
  cannot widen any dimension.
- `cancel`: bounded reason `user_cancelled|timeout|surface_closed`.

`network_egress` maps only to the candidate policy's global
`network_egress_permitted` ceiling. `false` forces all five channel answers disabled; `true`
enables none and merely allows later independently affirmative channel choices. The four
`PrivacyProfile` values are derived from the provider/category/confirmation choices for LLM
inference only; they never encode consent for the four non-LLM channels.

Service `question` uses only those twelve exact IDs and their exact answer types, display kind, closed
choices, safe synthetic allowed/blocked examples, current non-secret selection, and whether the
answer can widen disclosure. It cannot carry repository/task content.

`policy_review` carries a complete candidate privacy-policy object, candidate digest, semantic diff
arrays `tightens|loosens|unchanged`, the global network ceiling, independent five-channel
summaries including exact `available|unsupported` capability state, never-send constants, and
`local_human_decision_required`. `decision_required` carries only
`privacy_proposal_id` (a pending `ppr_` UUIDv4), exact draft digest, expected policy version and
expiry. It carries no generic pending-request alias, challenge, authorization reference, proof,
decision method or reusable token. A separate foreground `HumanControlService` confidentially
renders the pending proposal, reauthenticates the local human and commits internally; that protocol
is not represented here. `complete` carries committed policy ID/version/digest and effective
scope. Error contains only `stale_version|expired|invalid_answer|unsupported_profile|
local_human_required|channel_unavailable|service_locked|internal_error` plus safe correlation ID.

Every draft, candidate, current-policy, and proposal digest uses
`sha256:<64 lowercase hex>`. The `authorization_scope` answer preserves `installation_id` and every
applicable workspace/task/request ancestor exactly; it never accepts a generic scope string.

The union has no string field capable of carrying a credential, passphrase, key, endpoint URL,
prompt, content excerpt, path, environment value, transcript, or free-form agent message. The
confidential vault-unlock channel is a separate bounded binary control protocol and intentionally
has no JSON representation in this schema.

## Errors and edge cases

- Stale sequence, expired pending decision, draft-digest change, or version race commits nothing.
- MCP/agent callers cannot access setup/preview methods, obtain pending plaintext, decide a proposal,
  or submit any confirmation authority.
- `begin` while locked returns the safe `service_locked` view and no unlock field.
- A tightening-only draft may complete without confirmation only after server-side semantic diff.
- A graphical UI cannot invent extra IDs/answer shapes, default an unanswered widening choice,
treat `network_egress=true` as channel consent, treat `local_only` as a decision for non-LLM
  channels, or combine channel consents.
- v0.1 marks all four non-LLM channel capabilities `unsupported` and off. Answering true may be
  represented in a draft but `propose` returns `setup_error/channel_unavailable`, persists no
  enabled row, and triggers no I/O. A future capability transition cannot activate an old draft or
  answer; it requires a new trusted local-human confirmation.
- Normal CLI stdin shared with an agent is not a trusted control channel.

## Invariants

1. The setup contract contains policy choices and evidence, never secrets or user content.
2. Ordinary setup can queue exact widening but contains no method that can decide or commit it.
3. MCP, agents, imports, and providers cannot create local-human confirmation authority.
4. The global ceiling and five network channel choices remain independently visible; the ceiling
   grants nothing by itself.
5. Cancel/crash/staleness leaves the prior policy unchanged.
6. CLI and future UI render the same service-owned contract, including the twelve exact question IDs
   and answer types.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/subprocess/test_service_lock_and_confidential_unlock.py`
- `tests/packaging/test_privacy_docs_and_resources.py`

## Open questions

None.
