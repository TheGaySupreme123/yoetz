# schemas/privacy/outbound-case-1.0.0.schema.json — validated bounded outbound request case

**Wave:** B/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):**
privacy-policy schema, canonical protocol, data-egress protocol | **Imported by:** outbound gateway,
provider adapters, preview renderer, fake/live adapter tests

## Purpose

Freeze the only payload shape a network adapter may receive. The case proves classification,
effective-policy evaluation, scope binding, minimization, redaction, secret scanning, and optional
human authorization have completed before any adapter can perform I/O.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/privacy/outbound-case/1.0.0`.
- Media type: `application/vnd.yoetz.outbound-case+json`.
- Closed root fields: `schema_version`, `case_id`, `request_id`, `authorization_id`, `channel`,
  `purpose`, `scope`, `destination`, `policy`, `content_items`, `approved_categories`,
  `blocked_categories`, `minimization`, `secret_scan`, `limits`, `canonical_content_digest`,
  `created_at`, and optional `human_authorization`.

## Behavior

IDs are `cas_`, existing `req_`, and `aut_` plus canonical lowercase UUIDv4. `channel` is one of all
five network channels including `llm_inference`. `scope` is the same closed full-ancestor-chain
object as the policy schema: every kind carries `installation_id`; workspace/task/request carry
`workspace_ref_commitment`; task/request carry `task_id`; request carries `request_id`.
Machine-scoped taskless telemetry/update/capability cases therefore require no task ID without
discarding their installation ancestor. `purpose` is a bounded lower-kebab token from the
effective policy.

For `llm_inference`, `destination` is exactly `ProviderBinding`: the five closed fields
`provider_id`, `model_id`, `endpoint_profile_id`, `endpoint_profile_version`, and `transport`, with
`transport: external` for this network case. It contains no alternate destination alias, URL,
socket path, credential, header, or arbitrary option. Non-LLM branches use their separately typed
closed destination profile and prohibit provider/model fields. `policy` carries policy ID/version/
digest and authorization scope digest.

`content_items` is an ordered tuple of at most 64 closed items. Each item has `item_id`, one allowed
content category, a bounded opaque `source_ref`, UTF-8 `content` of at most 16 KiB, exact UTF-8 byte
count, content digest, and transformations. A transformation is `selected|minimized|redacted` with
bounded reason code and before/after byte counts; it contains no removed text. Whole canonical
transmission content is at most 256 KiB. Items cannot contain filesystem paths or unrestricted
references. The schema grants no `source_ref` dereference operation and composition supplies no
repository/database/filesystem/environment resolver. v0.1's reviewed bundled in-process adapter is
trusted to honor that contract; the schema is not an OS sandbox against malicious Python code.

`approved_categories` and `blocked_categories` are sorted unique. Never-send and out-of-scope are
allowed only in `blocked_categories`, never on an item. `minimization` contains candidate/included/
removed counts and bytes. `secret_scan` fixes registry version, scanner profile digest, matches
removed, blocked-item count, and `passed:true`. A scan match on an indivisible/uncertain item prevents
case construction rather than recording its value.

`limits` records per-item/whole-case byte and token ceilings and actual totals. The
`canonical_content_digest` commits to exactly the provider-renderable logical content before
provider-specific wrapping. The deterministic adapter later renders the exact final application
request body; the gateway commits to those body bytes in the receipt before authentication-header
injection and transport I/O. Credential metadata and HTTP/TLS framing are not commitment input.
Every unkeyed policy, scope, case, content, scanner-profile, or schema digest uses
`sha256:<64 lowercase hex>`; only installation-keyed commitments use `hmac-sha256:`.

`authorization_id` references the service-internal single-use `EgressAuthorization` bound to exact
case/channel/provider/model/endpoint/purpose/scope/policy/version/expiry/service generation. That
authority is never serialized to CLI/MCP/provider output; the case contains no proof token,
challenge, passphrase, credential, preview plaintext, or human identity.

For a separately configured local model, an equivalent protected `local_model` sink path applies
the same classification, minimization, scope, size, and never-send checks without network dispatch.
A local disclosure is not serialized as this network outbound case and receives a
`LocalDisclosureReceipt`, never an `EgressReceipt`; it cannot trigger model download or redirects.

## Errors and edge cases

- Empty/over-limit cases, byte-count/digest mismatch, unprefixed digest, incomplete scope chain,
  unknown/duplicate/unsorted fields, forbidden category, expired/stale authorization, or
  destination-policy mismatch fail before I/O.
- MCP/agent assertions cannot populate `authorization_id`; only a service-validated reference is
  accepted.
- Redirect targets are not destinations; redirects are disabled and never authorized transitively.
- Retry preserves canonical content/destination/policy commitment but uses a new `dsp_` dispatch ID and
  revalidates authorization expiry.
- Reviewed bundled provider renderers are contractually limited to wrapping the case without
  adding, fetching, summarizing, or substituting content; third-party/dynamic adapters are absent.

## Invariants

1. A provider adapter receives only this already-approved bounded object.
2. Every transmitted content byte has category, scope, source, transform, and digest evidence.
3. Never-send/out-of-scope data cannot be represented as an included item.
4. Human authorization is bound to exact case, policy, scope, and expiry.
5. Adapter input/composition grants no repository/database/filesystem/environment capability; this
   is a least-authority API claim, not process isolation.
6. A local-model case uses identical disclosure fences and grants the Core adapter no IP-network
   capability; a separate runtime's ambient authority remains the explicit F-013 limitation.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/property/test_egress_policy_properties.py`
- `tests/integration/privacy/test_egress_gateway.py`
- `tests/conformance/privacy/test_privacy_profiles.py`
- `tests/capability/test_privacy_provider_and_local_model_profiles.py`

## Open questions

None.
