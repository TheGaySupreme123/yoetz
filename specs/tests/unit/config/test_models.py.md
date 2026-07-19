# tests/unit/config/test_models.py — strict configuration model validation

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/config/models.md`
**Imported by:** the config unit suite

## Purpose

Lock the strict config model, profile table, and secret rejection rules.

## Public surface

- `test_defaults_and_schema_version` — default values and schema version are fixed.
- `test_profile_capability_matrix` — each profile maps to the documented network/semantic policy.
- `test_strict_local_forbids_external_but_accepts_exact_local_selection` — strict-local keeps the
  Yoetz path network-denied and can select only the closed nonsecret local-model tuple; it calls the
  separate runtime offline only when exact runtime sandbox evidence supports that claim.
- `test_external_and_fake_profiles_forbid_local_model` — v0.1 never implicitly selects between
  semantic sinks.
- `test_local_model_locator_and_launch_keys_are_rejected` — no path/URL/host/port/command/env/
  download/discovery key is representable.
- `test_secret_keys_are_rejected` — secret-like keys fail closed before becoming config values.
- `test_unknown_keys_are_rejected` — no unreviewed config fields leak in.
- `test_provider_data_use_profile_is_installed_evidence` — data-use posture is selected only through
  the exact installed endpoint profile and cannot be self-asserted in user config.
- `test_privacy_seed_delegate_is_atomic_idempotent_and_never_overwrites` — concurrent equivalent
  generation-1 seeds converge on the same row; a different complete policy conflicts without
  overwrite.

## Behavior

The suite proves:

- model validation is strict and frozen;
- profile capability rows are table-driven;
- semantic/provider settings obey the cross-field rules;
- review-context selection is explicit but cannot authorize provider/category/class/scope policy;
- provider data-use version/evidence is support-manifest metadata, not user-authored configuration;
- local model fields are exact structural identifiers only and merely select installed capability;
- secret-like names are rejected even when values are absent;
- unknown keys and wrong types fail before runtime startup.
- config delegates an already-materialized denied policy to the store's atomic seed transition and
  never manufactures identity, time, scope, or digest.

## Errors and edge cases

- A config model that coerces types fails.
- A secret key name that survives validation fails.
- A local endpoint locator/launch instruction or arbitrary options mapping that survives fails.
- A user config key claiming no-training, retention, provider-human-access, evidence digest, or
  recommendation eligibility fails.

## Invariants

1. Configuration is strict and deterministic.
2. Profiles are capability gates.
3. Secrets do not belong in normal config.
4. Local-model configuration cannot choose transport or authorize disclosure.
5. Configuration cannot manufacture upstream provider evidence.

## Tests

- `tests/unit/config/test_models.py`

## Open questions

None.
