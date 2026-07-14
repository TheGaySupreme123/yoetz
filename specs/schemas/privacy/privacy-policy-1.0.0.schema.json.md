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
- `$id`: `https://schemas.yoetz.dev/core/0.1/privacy/privacy-policy/1.0.0`.
- Media type: `application/vnd.yoetz.privacy-policy+json`.
- Closed root fields: `schema_version`, `policy_id`, `version`, `policy_digest`, `profile`,
  `network_egress_permitted`, `effective_scope`, `channel_policies`, `local_model_enabled`, optional
  `local_model_binding`, `local_sink_category_ceilings`, `never_send`, `created_at`, and optional
  `supersedes_policy_digest`.

Exact enums:

- scope kind: `machine|workspace|task|request`;
- LLM profile: `local_only|confirm_every_request|minimal_external|trusted_provider`;
- egress channel: `llm_inference|product_telemetry|crash_diagnostics|update_checks|
  capability_testing` (all five are the same `EgressChannel` vocabulary even though LLM has a
  richer typed subsection);
- data class: `public_structural|ordinary_user_content|sensitive_confidential|
  secret_or_cryptographic`;
- data category: `bounded_structural_metadata|declared_file_type|task_description|claim_text|
  obligation_text|decision_excerpt|evidence_excerpt|finding_summary|command_metadata|diff_metadata|
  repository_excerpt|transcript_excerpt|diagnostic_metadata`;
- protected local sink: `local_model|agent_context|trusted_human_control`.

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

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/property/test_egress_policy_properties.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/packaging/test_privacy_docs_and_resources.py`

## Open questions

None.
