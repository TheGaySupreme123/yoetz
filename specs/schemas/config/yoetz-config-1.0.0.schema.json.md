# schemas/config/yoetz-config-1.0.0.schema.json — configuration schema

**Wave:** C/F | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/config/models.md`, `src/yoetz/config/load.md`
**Imported by:** trusted service startup, configuration-schema tests, and packaging tests

## Purpose

Describe the strict service-generation configuration object after model default materialization.
Ordinary CLI/MCP/UI clients do not receive or parse this object.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/config/yoetz-config/1.0.0`.
- Owning model: `YoetzConfig`.

## Behavior

Closed object with these materialized required top-level fields:

- `schema_version`;
- `profile`;
- `storage`;
- `verification`;
- `logging`;
- `privacy`;

and optional `provider` or `local_model` when the selected profile allows that exact capability.
`local_model` is the closed `LocalModelProfileConfig`: exact profile/endpoint/model/protocol/schema/
capability identifiers plus a bounded timeout. It contains no socket path, URL, host, port,
command, executable, arguments, environment, header, download, discovery, secret, or extension
mapping. `strict-local` may name this local capability but forbids `provider`; `local-openai` may
name `provider` but forbids `local_model`; `test-fake` permits neither.

`privacy` is the closed `PrivacyBootstrapConfig` shape owned by `config/privacy.py`: one reviewed
first-run machine seed, explicit `network_egress_permitted` global ceiling, initial local-model
choice, and exactly five explicit initial channel choices (`llm_inference`, `product_telemetry`,
`crash_diagnostics`, `update_checks`, and `capability_testing`). The schema's `required` array
includes `privacy`; that property's `default` annotation is the complete safe bootstrap
(`local_only`, `network_egress_permitted=false`, all five channels denied, local model disabled).
The config loader first lets the owning strict model materialize defaults and then
validates/serializes this required canonical shape; JSON Schema `default` is an annotation and is
never treated as an authorization or an implicit validator mutation.

Nested objects retain their own closed shapes and strict enums. Secret-like fields are forbidden.
This schema is the service config contract and must not permit ad hoc keys. The seed is used only
when no durable policy exists; changing config never tightens, widens, resets, or replaces a stored
policy.

## Errors and edge cases

- Unknown top-level or nested keys fail.
- Wrong profile/cross-field combinations fail.
- Any local-model locator, launch instruction, secret-like key, or tuple not later found in the
  installed exact-profile registry fails before adapter construction; the schema never permits a
  caller-chosen endpoint.
- Missing `privacy` after model default materialization, a true global ceiling, or any other
  permissive/non-closed privacy seed
  fails startup; the implementation never substitutes an egress-enabled value.

## Invariants

1. Configuration is strict.
2. Secrets are forbidden.
3. Profiles gate capabilities.
4. The canonical validated object always contains the safe-defaulted first-run seed with its global
   network ceiling false and all five channels denied.
5. Local-model config is capability selection, never local-disclosure authorization.

## Tests

- `tests/unit/config/test_models.py`
- `tests/capability/test_privacy_provider_and_local_model_profiles.py`
- `tests/conformance/compatibility/test_resource_manifest.py`

## Open questions

None.
