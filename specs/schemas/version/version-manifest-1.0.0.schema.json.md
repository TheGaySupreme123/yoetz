# schemas/version/version-manifest-1.0.0.schema.json — installed version manifest schema

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/version.md`, `specs/schemas/README.md`
**Imported by:** `version --json`, receipts, and release evidence

## Purpose

Describe the frozen manifest of installed package, runtime, and resource identities.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/version/version-manifest-1.0.0.schema.json`.
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

The implementation-locked capability/resource wire records are closed:

- version, source-ID, and digest components are `{status:"present", version|source_id|digest}` or
  exactly `{status:"absent"}`;
- an installed MCP SDK is descriptive metadata only. `mcp_protocol_supported` remains the exact
  evidence-backed tested set and may be empty while the SDK component is present; that development
  state carries the `mcp_capability_unverified` limitation and never infers protocol support from
  installation;
- provider adapters are `{name,status:"present",adapter_version,[sdk_distribution],[sdk_version]}`
  or `{name,status:"absent"}`;
- `CapabilitySet` is `{name,supported_versions,tested_versions,denied_versions}` with bounded
  sorted-unique version sets; Codex profiles record exact version/profile identity, integration
  modes, and trigger/observation status;
- subject-state capability is `{status:"present",cells:[...]}` or exactly absent, with each cell
  binding profile/platform/Git/capture-format identities and capability digest;
- `ResourceIdentity` is `{name,media_type,size_bytes,sha256_digest}`, where size is a canonical
  decimal string. Resource counts are exactly 53 schemas, 9 canonical vectors, 4 migrations,
  2 skill resources, 4 guidance resources, and 1 runtime-support resource, totaling 73; the
  resource list is either intentionally omitted-content empty or exactly 73 identities.

The manifest carries exactly 31 request/result schema-version entries and 16 event entries, the
two policy identities in canonical `research-evidence`, `work-integrity` order, and bounded
collections (providers/service 16, Codex profiles 64, subject-state cells 32, limitations 64).

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
