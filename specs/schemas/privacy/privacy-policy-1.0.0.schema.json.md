# schemas/privacy/privacy-policy-1.0.0.schema.json — effective privacy and disclosure policy

**Wave:** B/C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
`docs/protocol/data-egress-and-privacy.md` | **Imported by:** configuration, trusted setup,
privacy policy engine, outbound gateway, public docs and conformance fixtures

## Purpose

Freeze the language-neutral policy that centrally constrains external network egress and protected
local disclosure sinks. It makes profiles, scope, channel independence, and never-send rules data
that every surface and adapter must obey identically.

## Public surface

- `$schema`: JSON Schema Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/privacy/privacy-policy-1.0.0.schema.json`.
- Owning model: `PrivacyPolicy`.
- Media type: `application/vnd.yoetz.privacy-policy+json`.
- Closed root fields: `schema_version`, `policy_id`, `version`, `policy_digest`, `profile`,
  `review_context_profile`, `review_selection`,
  `require_current_provider_data_use_evidence`, `network_egress_permitted`, `effective_scope`,
  `channel_policies`, `local_model_enabled`, optional
  `local_model_binding`, `local_sink_category_ceilings`, `never_send`, `created_at`, and optional
  `supersedes_policy_digest`.

Exact enums:

- scope kind: `machine|workspace|task|request`;
- LLM profile: `local_only|confirm_every_request|minimal_external|trusted_provider`;
- review-context profile: `structural|goal_aware|assisted|expanded|custom`;
- egress channel: `llm_inference|product_telemetry|crash_diagnostics|update_checks|
  capability_testing` (all five are the same `EgressChannel` vocabulary even though LLM has a
  richer typed subsection);
- data class: `public_structural|ordinary_user_content|sensitive_confidential|
  secret_or_cryptographic`;
- data category: `bounded_structural_metadata|declared_file_type|task_description|claim_text|
  obligation_text|decision_excerpt|evidence_excerpt|finding_summary|command_metadata|diff_metadata|
  repository_excerpt|transcript_excerpt|diagnostic_metadata`;
- protected local sink: `local_model|agent_context|local_human_view|trusted_human_control`;
- disclosure provenance: `self_authored|engine_derived_from_self_authored|other_writer|imported`.

## Behavior

The schema is a closed object. `schema_version` is `1.0.0`; `policy_id` matches
`pvy_` plus a canonical lowercase UUIDv4; `version` is a canonical positive decimal string; every
unkeyed digest is `sha256:<64 lowercase hex>`; timestamps are canonical UTC.

`effective_scope` is one closed discriminated shape that preserves its complete ancestor chain:

- `machine`: `{kind, installation_id}` with canonical `ins_` ID;
- `workspace`: machine fields plus `workspace_ref_commitment` rendered
  `hmac-sha256:<64 lowercase hex>`;
- `task`: workspace fields plus canonical `task_id` (`tsk_`);
- `request`: task fields plus canonical `request_id` (`req_`).

Fields belonging to a deeper kind are forbidden at a shallower kind. There is no generic
single-reference shortcut, raw workspace identifier, path, repository name, task title, or content
excerpt.

`network_egress_permitted` is the global boolean network ceiling. `false` requires all five channel
policies disabled. `true` grants no channel and no content permission by itself.

`review_context_profile` is required and orthogonal to `profile`. It selects candidate material
before policy enforcement: `structural` admits no user prose; `goal_aware` admits allowed goal,
obligation, claim, decision, and finding prose but no excerpts; `assisted` may additionally select
bounded problem-local evidence/test/failure/diff/repository excerpts already recorded in the frozen
case; `expanded` may relevance-rank all recorded items allowed by the policy; `custom` uses the
exact user-selected sections/kinds/relevance/booleans/budgets. Separate policy fields still control
categories, classes, and scope. It never widens a channel policy or provides
live repository/filesystem access. `structural` is the only valid first-run value in the built-in
zero-egress seed. The upstream CLI recommendation expands to `assisted` only through an explicit
policy transition.

`review_selection` is the exact closed `ReviewSelectionPolicy` compiled from that profile. It has
sorted unique `sections` drawn from `goal|obligations|claims|decisions|timeline|
deterministic_assessments|change_observations|coverage|targeted_excerpts|omissions`; sorted unique
`excerpt_kinds` from `evidence|test|failure|diff|command|repository`; `relevance:
linked_subjects_only|linked_then_in_scope`; `include_finding_prose`; `include_exact_command_text`;
and nonnegative integer
caps `max_timeline_items<=64`, `max_assessments<=64`, `max_change_observations<=32`,
`max_excerpts<=16`, `max_omissions<=64`, `max_excerpt_bytes<=16_384`, and
`max_total_excerpt_bytes<=131_072`. Named profiles must equal the canonical expansions in
`domain/privacy.md`; `custom` persists the exact user selection inside those ceilings. Selection
grants no category/class/scope/provider/channel. Effective overlay intersection uses set
intersection, logical-AND finding-prose and command eligibility, the stricter relevance, and
minimum caps; a result
that is not a named expansion is labeled `custom`.

`require_current_provider_data_use_evidence` is a required boolean. It is false when no external
provider is bound. When true, dispatch is additionally fenced on a current installed record with
training `prohibited`, retention `none|bounded`, and provider human access
`prohibited|restricted`. The upstream assisted recipe sets it true. An informed custom policy may
set it false; that is an explicit loosening and cannot inherit the upstream recommendation claim.

`channel_policies` contains exactly one closed policy for each of the five `EgressChannel` values.
Each fixes enabled state, sorted allowed `DataCategory` and `DataClass` sets, optional exact
`ProviderBinding`, purposes, scope ceiling, preview flag, byte/token ceilings and expiry ceiling. A
binding is a closed object with exactly `provider_id`, `model_id`, `endpoint_profile_id`,
`endpoint_profile_version`, and `transport` (`external|local_af_unix`), with no URL/socket path or
credential. `PrivacyProfile` branches govern the `llm_inference` policy only:

- `local_only`: `llm_inference` disabled and no external LLM binding; an optional separately
  configured local runtime may execute with the same classifier/minimizer/never-send fences. When
  `network_egress_permitted=true`, the schema can represent each of the four non-LLM rows
  independently under its exact bounded structural/synthetic shape; this does not permit user/task
  content or construct an external LLM provider, and the v0.1 application rejects enabling those
  unsupported rows;
- `confirm_every_request`: an LLM external binding, preview true, and at least one category;
- `minimal_external`: an LLM external binding, preview optional only when a stricter scope requires it,
  and data classes excluding `sensitive_confidential`;
- `trusted_provider`: an LLM external binding, explicit nonempty purpose/category sets; sensitive content
  may be automatically eligible only within its exact explicit authorization.

`confirm_every_request` may include `sensitive_confidential` only when the policy names its category
and data class and the exact post-minimization excerpt receives request-bound local-human preview
approval. It is not exclusive to `trusted_provider`.

The upstream `assisted` recipe uses `trusted_provider`, workspace scope,
`preview_required=false`, `require_current_provider_data_use_evidence=true`, data classes
`public_structural|ordinary_user_content`, and allowed
categories `bounded_structural_metadata|declared_file_type|task_description|claim_text|
obligation_text|decision_excerpt|evidence_excerpt|finding_summary|command_metadata|diff_metadata|
repository_excerpt`. It excludes `sensitive_confidential` and `transcript_excerpt`, and its
`agent_context` ceiling is exactly
`{categories:[bounded_structural_metadata,declared_file_type,finding_summary],
data_classes:[ordinary_user_content,public_structural]}` (each array ASCII-sorted in canonical
bytes) so the main agent can receive the reviewer challenge. A reviewer challenge is
provider-derived and therefore never self-authored, so this grant — not provenance — is what admits
it; the recipe must carry it explicitly. Those recipe facts are
checked by setup/conformance; they are not implicit schema defaults.

`agent_context` is the one provenance-conditional sink: its category set governs material the
requesting writer did not author, while `self_authored` and `engine_derived_from_self_authored`
items are admitted at the policy's data classes without a category grant. `local_human_view` admits
every non-never-send category at `public_structural|ordinary_user_content` and is not
provenance-conditional. Neither mechanism admits `sensitive_confidential` or any never-send kind,
which remain absolute at every sink and under every provenance.

Every external LLM profile requires `network_egress_permitted=true` and an enabled
`llm_inference` policy. Disabled channel policies forbid binding/categories/classes/purposes.
Telemetry and update checks allow only `bounded_structural_metadata`; v0.1 crash diagnostics allow
only reviewed bounded structural diagnostic metadata; capability testing is synthetic-only and
forbids user-content categories. All four non-LLM policies exclude `ordinary_user_content`,
`sensitive_confidential`, and every task/user-content category. No child inherits the global
ceiling, LLM binding, profile consent, or another child's enabled state.

The four non-LLM rows are forward-compatible policy vocabulary in v0.1, not advertised runtime
capabilities. No production telemetry, crash-upload, update-check, or capability-test transport is
owned in v0.1. An enabled row remains structurally representable for schema/conformance purposes,
but the v0.1 policy-transition application rejects it and stores no dormant consent. A
forced/imported enabled row resolves before dispatch with outcome/reason
`channel_unavailable/channel_unavailable`, with no authorization consumption, dispatch fields,
request commitment, DNS, or socket I/O. Future support must add an exact use-case/adapter owner,
reviewed schema, and fresh local-human transition; it cannot appear through a generic HTTP fallback
or activation of old intent.

`local_sink_category_ceilings` has exactly `local_model`, `agent_context`, and
`trusted_human_control`, with category/class ceilings and `secret_or_cryptographic` excluded. These
are disclosure sinks but not network
channels; network receipts and consent are never used to imply that any sink may receive secrets.
`agent_context` is the fence applied before MCP rendering; MCP is not itself a sink enum.

`never_send` is an exact constant ASCII-sorted array:
`api_credential`, `authentication_token`, `complete_transcript`, `cookie`, `credential_file`,
`encryption_key`, `hidden_auth_configuration`, `keyring_content`, `out_of_scope_file`, `password`,
`private_certificate`, `raw_database`, `raw_stderr`, `recovery_or_unlock_secret`,
`unrelated_environment`, and `unrestricted_log`. Profiles/scopes cannot omit or override an entry.

The policy object contains no credentials, endpoint URL, passphrase, key, prompt, content excerpt,
filesystem path, environment name/value, raw authorization challenge, or decrypted vault state.

## Errors and edge cases

- Unknown fields, enum values, duplicate/unsorted sets, floats, noncanonical integers/times, nulls,
  incomplete/over-complete scope ancestor chains, path-like scope values, unprefixed digests, or
  invalid commitments fail.
- A profile/global-ceiling/binding/preview/category mismatch fails schema validation before policy
  evaluation. A false ceiling with any enabled channel is invalid.
- A missing/unknown review-context value, or a preset/profile/category/class combination that
  claims wider selection than its closed meaning, is invalid.
- A named profile whose `review_selection` is not its exact expansion, an invalid custom selector,
  or a true provider-data-use guard without an external binding is invalid.
- Enabling one channel cannot imply, default, or materialize another.
- A local runtime profile cannot contain a remote URL or network permission.
- Removing a never-send constant or admitting `secret_or_cryptographic` to a local sink is invalid.
- Schema validity does not itself authorize a policy widening; the service separately requires a
  fresh local-human decision and version compare-and-swap.

## Invariants

1. The schema can express narrower policy but cannot express permission to send never-send data.
2. The global ceiling grants nothing; LLM and four non-LLM network channels remain independent.
3. Privacy profiles govern LLM disclosure only. `local_only` forbids external LLM/user-content
   egress but may coexist with separately enabled bounded structural non-LLM channels.
4. Provider authorization is bound to exact provider/model/endpoint-profile ID and version,
   transport, purposes, categories, and the full scope ancestor chain.
5. Local-model, agent-context, and trusted-human-control sinks cannot receive never-send material;
   the agent-context fence runs before MCP rendering.
6. No secret or user-content value is a policy field.
7. Source and installed schema bytes are identical and resolved offline.
8. Review-context selection can only narrow candidate material; the channel policy remains the
   independent disclosure authority.
9. Provider data-use evidence authorizes nothing; only the explicit editable guard determines
   whether its currency is a runtime precondition.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/property/test_egress_policy_properties.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/packaging/test_privacy_docs_and_resources.py`

## Open questions

None.
