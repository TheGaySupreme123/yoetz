# schemas/version/version-manifest-1.0.0.schema.json — installed version manifest schema

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/version.md`, `specs/schemas/README.md`
**Imported by:** `version --json`, receipts, and release evidence

## Purpose

Describe the frozen manifest of installed package, runtime, and resource identities.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/version/version-manifest/1.0.0`.
- Owning model: `VersionManifest`.

## Behavior

Closed object with required fields mirroring the version manifest:

- package/workflow-protocol/local-control-protocol/engine/projection/policy/object/storage
  identities;
- privacy-policy schema, egress-receipt schema, and privacy-classifier ruleset identities;
- request/result and event schema-version maps;
- platform/runtime identities;
- resource list and resource-manifest digest;
- exact local-service capability identities, support status, and limitations.

Optional capability entries are required tagged unions whose unavailable branch is exactly
`{"status":"absent"}` plus any schema-required bounded reason; `null` is forbidden. Codex support
is an exact sorted set of version/profile records, never minimum/maximum range fields. Extra
properties are forbidden.

## Errors and edge cases

- Missing required version or resource identity fails.
- Unsupported or unbounded provenance strings fail.

## Invariants

1. Version manifest is deterministic.
2. Resource identity is explicit.
3. Absence is bounded.

## Tests

- `tests/unit/version/test_manifest.py`
- `tests/conformance/compatibility/test_resource_manifest.py`

## Open questions

None.
