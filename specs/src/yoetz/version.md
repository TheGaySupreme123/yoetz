# src/yoetz/version.py — installed capability and version manifest

**Wave:** F | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/schemas.md` | **Imported by:** `__init__.md`, CLI `version`, MCP
runtime startup, receipts, release/capability probes

## Purpose

Expose the complete set of identities that determine compatibility, replay, packaging, and support.
One package version is insufficient: protocol, event schemas, engine, policies, projections, object
format, database schemas, SQLite source, MCP SDK, provider adapter, resource corpus, Python/ABI,
platform, and exact supported harness profiles evolve at different rates.

The manifest reports what is installed and observed. It does not by itself certify write safety;
startup diagnostics compare it with the reviewed packaged `runtime-support.json` allowlist.

## Public surface

- `VersionManifest` — frozen strict dataclass with the fields below.
- `ResourceIdentity(name, media_type, size_bytes, sha256_digest)` — frozen, sorted by ASCII name.
- `CapabilitySet(name, supported_versions, tested_versions, denied_versions)` — exact sorted sets;
  it has no range or interpolation semantics.
- `build_version_manifest(*, include_optional_probes: bool = False) -> VersionManifest`.
- `version_manifest_json(manifest, *, include_resources: bool = False) -> bytes` — canonical
  protocol JSON bytes plus one LF only at the CLI writer. Default output carries the resource
  manifest digest and counts; verbose resource mode adds the bounded full list.
- `verify_resource_manifest(manifest) -> tuple[StartupCheckResult, ...]` — digest/byte-parity
  checks; it does not mutate resources.
- Constants:
  - `PROTOCOL_VERSION = "0.1"`
  - `CONTROL_PROTOCOL_VERSION = "1.0"`
  - `PRIVACY_POLICY_SCHEMA_VERSION = "1.0.0"`
  - `EGRESS_RECEIPT_SCHEMA_VERSION = "1.0.0"`
  - `PRIVACY_CLASSIFIER_RULESET_VERSION = "privacy-classifier/0.1.0"`
  - `ENGINE_VERSION = "0.1.0"`
  - `PROJECTION_VERSION = "yoetz/0.1.0"`
  - `WORK_INTEGRITY_POLICY_VERSION = "work-integrity/0.1.0"`
  - `RESEARCH_EVIDENCE_POLICY_VERSION = "research-evidence/0.1.0"`
  - `OBJECT_FORMAT_VERSION = "yoetz-object/1"`
  - `CATALOG_SCHEMA_VERSION = "1"`, `BUNDLE_SCHEMA_VERSION = "1"`
  - `SQLITE_APPLICATION_ID = "0x594F4554"`.

`VersionManifest` contains exactly:

| Field | Meaning/source |
|---|---|
| `schema_version` | Manifest schema, initially `1.0.0` |
| `package_name`, `package_version` | `yoetz` and installed distribution metadata |
| `protocol_version`, `engine_version`, `projection_version` | constants above |
| `control_protocol_version`, `privacy_policy_schema_version`, `egress_receipt_schema_version`, `privacy_classifier_ruleset_version` | local-service/privacy compatibility constants above |
| `request_result_schema_versions` | ASCII-sorted operation/schema→SemVer map from frozen schemas |
| `event_schema_versions` | ASCII-sorted 16-family map, each initially `1.0.0` |
| `policy_versions` | ordered tuple of the two policy identifiers |
| `object_format_version` | fixed object envelope identity |
| `catalog_schema_version`, `bundle_schema_version`, `application_id` | storage identities |
| `python_implementation`, `python_version`, `python_abi` | `CPython`, exact runtime, SOABI |
| `os_name`, `os_version`, `machine`, `platform_tag` | normalized packaging identity |
| `apsw_version`, `sqlite_version`, `sqlite_source_id`, `sqlite_compile_options_digest` | runtime introspection or explicit unavailable state |
| `mcp_sdk_version`, `mcp_protocol_supported` | installed SDK metadata and the exact tested protocol-version set, not a claimed active negotiation |
| `provider_adapters` | tagged present/absent installed adapter identities; never credentials/endpoints |
| `service_capabilities` | exact tested local-control, keyring, secret-memory, and session-monitor capability identities; no ready/locked history or secrets |
| `codex_capability_profiles` | exact tested version/profile records from packaged runtime support; no range |
| `resource_manifest_digest`, `resource_counts`, `resources` | source/wheel artifact identities; full list only in explicit resource rendering |
| `build_identity` | reproducible release provenance identifier or `development-unavailable` |
| `support_status` | `supported_write`, `read_only_unsupported`, or `development_unverified` |
| `limitations` | ASCII-sorted bounded reason codes only |

## Behavior

### Construction order

1. Load distribution metadata without importing CLI/MCP/provider packages.
2. Capture CPython, ABI, OS, machine, and normalized wheel-platform identity.
3. Load packaged `resources/support/runtime-support.json`, verify it through the resource manifest,
   then validate its schema/canonical self-digest and artifact/resource binding.
4. Enumerate installed resources through `importlib.resources`, reject duplicates, and compute
   SHA-256 over exact bytes. Do not follow arbitrary filesystem links.
5. If APSW is installed, introspect its version, SQLite library version/source ID, amalgamation
   marker, and sorted compile options without opening a database. If absent, emit a tagged
   `{status: "absent"}` component and a limitation.
6. Read installed MCP/provider distribution versions only through metadata. Optional adapters are
   tagged `{status: "absent"}` rather than imported.
7. Compare observed runtime/platform/resource identities against the packaged support allowlist to
   derive `support_status` and limitations.
8. Return the frozen manifest with maps/sets normalized to ASCII-sorted tuples.

`include_optional_probes=False` is the default used by package import and `version --json`. When
true in `release-probe`, probe functions may import optional distributions and perform local,
non-secret capability checks explicitly enumerated by their adapters. It still performs no network,
key-store access, config loading, or database write.

### Canonical output

Convert the dataclass into the restricted JSON profile: counters as canonical integer strings where
the protocol requires, no floats or `null`, tagged `{status: "absent"}` records for unavailable
optional components, and ASCII-sorted set-like arrays. `version_manifest_json` uses
`canonical_encode`; human rendering is a derived CLI view and never the release evidence.

The full SQLite source ID is intentionally present in `version --json` and receipts because it is a
compatibility fact, but operational logs use only its digest. Filesystem paths, usernames, hostnames,
environment variables, installed config, provider endpoint/model choice, and key-backend account
names are absent.

### Resource parity

The packaged resource manifest enumerates exactly the 69 reviewed resources frozen by its owning
spec: 52 JSON Schema artifacts, the schema inventory manifest, 9 canonical compatibility fixtures,
2 migrations, 4 Codex skill files, and the runtime-support allowlist. For every entry:

1. read exact installed bytes;
2. enforce expected size and SHA-256;
3. reject missing, extra-in-required-namespace, duplicate, or path-traversal names;
4. in a source checkout/release build, separately prove byte equality to canonical root sources.

A mismatch makes write startup unsafe and returns a bounded diagnostic; `version --json` still
reports the mismatch without executing the damaged resource.

## Errors and edge cases

- Missing optional dependency: report absent/limitation, do not fail deterministic package
  inspection.
- Missing/corrupt required support or schema resource: manifest builds with `read_only_unsupported`;
  write startup later fails `STORAGE_UNSAFE` or `INTERNAL_ERROR` according to the startup gate.
- Unsupported Python patch/platform/ABI: import and version inspection work; writes fail closed.
- Unknown/newer installed SDK: report exact version as untested; do not silently extend the tested
  set.
- Metadata strings are bounded and normalized; arbitrary package metadata is never copied to logs
  or public error details.
- The build identity is not fabricated from file mtimes or a dirty Git checkout.

## Invariants

1. Equal installed bytes and runtime identities produce byte-identical canonical manifests.
2. Manifest construction has no network, config, key-store, database-write, subprocess, or user-data
   dependency.
3. Every receipt records all truth-affecting engine/policy/projection/schema identities.
4. Package version never substitutes for protocol or storage compatibility.
5. Unsupported is reported honestly; inspection capability is not described as write support.

## Tests

- `specs/tests/unit.md`: complete field/schema validation, deterministic sorting, no-float output,
  limitation derivation, unavailable optional dependencies.
- `specs/tests/subprocess.md`: vary hash seed/locale/TZ/cwd/HOME and assert identical canonical bytes
  for the same installed environment; deny network and filesystem writes.
- `specs/tests/packaging.md`: source, sdist, wheel, and installed resource byte parity; corrupt/remove/
  add resource cases; supported and unsupported platform fixtures.
- `specs/tests/capability.md`: every advertised Codex profile and MCP version exactly equals a
  passing capability-evidence cell.
- Receipt golden vectors include this manifest or its canonical digest as required by the ledger
  contract.

## Open questions

None.
