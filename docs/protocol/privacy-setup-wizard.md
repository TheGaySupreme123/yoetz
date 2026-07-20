# Privacy setup and policy-change contract

This page freezes the experience and security contract for creating or widening Yoetz privacy
policy, independent of any one graphical toolkit. The v0.1 CLI trusted-control flow satisfies this
contract today; a future desktop UI must present the same questions, examples, preview, and
confirmation semantics — it cannot hide, merge, or weaken a question, and Yoetz (not the rendering
surface) always validates and commits policy.

## Setup session shape

A setup session exposes closed actions `begin`, `answer`, `review`, `propose`, `tighten`, and
`cancel`, moving through closed states `recipe`, `network`, `local_models`, `provider`,
`review_context`, `categories`, `agent_context_categories`, `local_model_categories`,
`request_confirmation`, `channel_consents`, `scope`, `review`, `decision_required`, `complete`, and
`cancelled`.

## The thirteen questions

After an optional recipe is expanded into visible answers, setup asks exactly these thirteen
questions. The `question_id` token and answer type are protocol, not UI copy — a future UI localizes
labels but cannot rename or reshape them.

| # | `question_id` | Meaning | Answer type |
|---:|---|---|---|
| 1 | `network_egress` | Global ceiling: whether any Yoetz network egress may be permitted at all | boolean |
| 2 | `local_models` | Whether local models are permitted | boolean |
| 3 | `external_provider` | Which external provider/model/endpoint profile is trusted, and whether current data-use evidence is required | `{binding, require_current_provider_data_use_evidence}` or `none` |
| 4 | `review_context` | Which deterministic selection strategy builds semantic review cases | `{profile, selection}` |
| 5 | `content_categories` | Which categories/classes may be sent to the external LLM binding | `{categories, data_classes}` |
| 6 | `agent_context_categories` | Which categories/classes results may release to an agent-capable host, for material that host did not author | `{categories, data_classes}` |
| 7 | `local_model_categories` | Which categories/classes the selected local model runtime may receive | `{categories, data_classes}` |
| 8 | `request_confirmation` | Whether every external LLM request needs preview/confirmation | boolean |
| 9 | `product_telemetry` | Whether product telemetry is permitted | boolean |
| 10 | `crash_diagnostics` | Whether crash diagnostics are permitted | boolean |
| 11 | `update_checks` | Whether update checks are permitted | boolean |
| 12 | `capability_testing` | Whether capability testing is permitted | boolean |
| 13 | `authorization_scope` | Machine, workspace, task, or this request | full-ancestor-chain scope |

The setup UI never accepts a credential, key, recovery secret, provider token, or vault passphrase.

## Recipes are transparent drafts, never consent

The CLI may offer five convenience labels — `private`, `metadata-only`, `assisted-review`
(recommended when an eligible endpoint exists), `expanded-review`, and `custom` — mapping exactly to
protocol `recipe_hint` tokens `private`, `metadata_only`, `assisted_review`, `expanded_review`, and
`custom`. Choosing a recipe expands it into all thirteen ordinary typed answers, which the CLI shows
and the user may edit; the label is echoed only while the answers still exactly match the recipe,
and reverts to `custom` after any edit. **Selecting a recipe never commits a widening, provisions a
credential, or skips the review/decision step.**

### The fail-safe default draft

With no answers, the draft is `local_only`, `review_context=structural`, `network_egress=false`, all
five network channels disabled, no local model, no external or local-model content categories, and
`agent_context_categories={categories:[bounded_structural_metadata, declared_file_type],
data_classes:[public_structural]}`. Under this default, an unwidened agent still receives its own
published prose and the deterministic findings computed solely from it — withholding a writer's own
words protects nothing and would break the `check → respond → recheck` loop. What the default
withholds is exactly what the agent did not author: another writer's or subagent's material,
imported events, and semantic reviewer prose, plus every `sensitive_confidential` item and the
entire never-send set, which no provenance ever unlocks. Ordinary human-readable rendering to an
attached controlling terminal is the separate `local_human_view` sink and is not gated by question 6
at all — reading your own unlocked terminal is not a third-party disclosure.

### The recommended `assisted_review` recipe

Deliberately different from the default: `PrivacyProfile=trusted_provider`,
`ReviewContextProfile=assisted`, workspace scope, `request_confirmation=false`,
`include_finding_prose=true`, `include_exact_command_text=false`,
`require_current_provider_data_use_evidence=true`, data classes
`public_structural|ordinary_user_content`, and the canonical `assisted` external category set
(bounded structural metadata, declared file type, task description, claim text, obligation text,
decision excerpt, evidence excerpt, finding summary, command metadata, diff metadata, repository
excerpt). It excludes `sensitive_confidential`, transcript excerpts, broad source selection, and
exact command text; it keeps the v0.1 16 KiB/item and 256 KiB/case hard caps. It is displayed as
recommended only for an exact installed endpoint whose current, versioned data-use record states
training `prohibited`, retention `none|bounded`, and provider human access `prohibited|restricted`.
Unknown or stale posture removes the badge; a user can still pick that endpoint through `custom`,
but the UI must not carry the recommendation's claim into a custom policy.

## What each question governs

Question 1 maps to the global `network_egress_permitted` ceiling: `false` forces all five channels
denied; `true` enables none by itself and only unlocks later channel-specific choices. Choosing
network egress is not consent to a provider, channel, or content class; choosing a provider is not
consent to telemetry, diagnostics, updates, or capability testing.

Question 4 chooses only the context-selection *strategy*, granting no category, class, scope, or
sink by itself. `assisted` may select only problem-local excerpts already captured or
agent-published at the frozen frontier — v0.1 has no live Git or filesystem browser, and the review
shows `not_recorded` rather than implying missing code was inspected.

Question 5 governs external-LLM content only. Questions 6 and 7 are **independent** local-disclosure
ceilings — authorizing content for an external provider does not authorize an MCP host or a local
model, and vice versa; `local_model_categories` must stay empty while local models are off.

For questions 5–7, an item is allowed only when **both** its category and its data class are
selected. `data_classes` may contain only `public_structural`, `ordinary_user_content`, and
`sensitive_confidential` — `secret_or_cryptographic` is never selectable at all.
`sensitive_confidential` is off by default, shown as its own high-impact widening, and requires
strong reauthentication; selecting a category alone never silently authorizes its sensitive
instances.

Questions 9–12 are the four non-LLM channels. **v0.1 ships no production transport for any of
them:** the review marks each `unsupported` and off, and `propose` rejects a `true` answer with
`channel_unavailable` without changing durable policy or making any I/O. A later Yoetz release
cannot silently activate an old stored draft or answer — it always requires a fresh local-human
capability confirmation first.

## Review, propose, and commit

`review` displays the global ceiling, effective destination/purpose/scope/categories, every channel
independently, the review-context profile and its exact compiled selector, the recipe expansion, the
editable data-use runtime guard, the endpoint's data-use profile ID/version/evidence digest, whether
per-request preview applies, the complete non-overridable never-send set, and a semantic diff from
the current policy. Widening is visually highlighted and **cannot commit through `review` alone.**

`tighten` commits immediately, but only when the service independently proves every policy dimension
is no broader than the current one. `propose` never commits a widening by itself: loosening any
dimension returns `decision_required` with a `privacy_proposal_id`, an exact draft digest, the
current policy version, an expiry, and — when an external provider is selected — the exact reviewed
data-use profile ID/version/evidence digest. A separate foreground human-control surface then
confidentially renders the pending proposal, reauthenticates the local human, and commits
internally; the ordinary setup schema itself has no decide/confirm method, and no MCP, agent,
import, or provider schema can reach that authority.

For `confirm_every_request`, the same renderer shows the exact post-minimization, post-redaction,
post-secret-scan excerpts immediately before every dispatch. Approval binds one physical dispatch of
the outbound-case digest; every retry — even of identical bytes — needs a fresh preview and
decision, while resume before authorization consumption continues the same dispatch rather than
creating a new one.

After a standing policy like `assisted_review` is committed once, ordinary checks, automatic
retries inside that policy, reviewer challenges, and agent responses/rechecks proceed without a
human prompt. Each physical attempt still gets its own fresh one-use authorization and receipt.
Human presence remains required only for policy widening, credential mutation, an explicit
`confirm_every_request` attempt, or a finding waiver. Never-send and out-of-scope material cannot be
approved under any profile.

## Errors and edge cases

- Closing, disconnecting, or crashing before `review`/`tighten`/decision commits nothing.
- A locked service may show status and confidential-unlock guidance but cannot expose policy
  content that requires decrypted state, and never accepts an ordinary-channel secret.
- An unavailable provider profile is selectable only as an explanatory `unsupported` value — never
  committable as ready.
- Headless setup may use the trusted local control protocol, never an ordinary flag, environment
  variable, config secret, MCP tool, or stdin stream shared with an agent.
- `network_egress=false` with any enabled channel is an invalid draft; `network_egress=true` with
  every channel disabled is valid and performs no network work at all.
- A proposed enabled non-LLM row always renders `unsupported in v0.1` and cannot commit.

## See also

- [`data-egress-and-privacy.md`](data-egress-and-privacy.md) — the enforced policy/dispatch
  protocol this wizard configures.
- [`../../PRIVACY.md`](../../PRIVACY.md) — the user-facing privacy summary.
- `schemas/privacy/setup-wizard-contract-1.0.0.schema.json` — the wire schema for this contract.
- `yoetz privacy setup` / `yoetz privacy show` / `yoetz privacy propose` / `yoetz privacy tighten`
  — the v0.1 CLI surface implementing this contract.
