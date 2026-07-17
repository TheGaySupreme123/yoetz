# schemas/manifest.json — canonical schema inventory and byte-parity ledger

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/protocol/schemas.md`, `src/yoetz/version.md`, `specs/schemas/README.md`
**Imported by:** schema generation, release packaging, and parity tests

## Purpose

This file describes the canonical manifest that lists every reviewed schema artifact shipped in the
public release set. It is the inventory root for all schema bytes, IDs, sizes, and ownership data.

## Public surface

- `manifest.json` — canonical JSON object with exact top-level fields
  `manifest_schema: "yoetz.schema-manifest/1.0.0"`, `manifest_version: "1.0.0"`, and `members`.

## Behavior

The manifest is canonical JSON and records one entry per released schema artifact. Each entry has
exactly these fields:

- `path` — relative POSIX path;
- `$id` — the exact static-file schema URL;
- `schema_version`;
- `media_type: "application/schema+json"`;
- `byte_length` — exact raw-byte size;
- `sha256` — lowercase SHA-256;
- `owning_model` — the owning Python boundary model/helper name;
- `schema_kind`, one exact value from `request_result|event|config|version_manifest`;
- `artifact_role`, one exact value from `common-value|MCP input|MCP output|persisted-envelope|
  event-envelope|event-payload|configuration|finding|semantic-provenance|receipt-document|privacy-policy|
  outbound-case|privacy-audit|setup-contract|local-control|service-status|version-report`.

The mapping is closed and path-derived: `common/*` is `common-value` except
`common/operation-result-*`, which is `MCP output`; `operations/*-request-*` and
`operations/*-result-*` are `MCP input` and `MCP output`; `events/accepted-event-*` is
`persisted-envelope`, `events/event-draft-*` and `events/opaque-unknown-event-draft-*` are
`event-envelope`, and all other `events/*` are `event-payload`; the two `findings/*` artifacts
are `finding` and `semantic-provenance`; `config/*`, `receipts/*`, and `version/*` use
`configuration`, `receipt-document`, and `version-report`; the four privacy artifacts use their
four corresponding roles; control hello/request/result artifacts are `local-control`; and
service-status is `service-status`. Every one of the 52 entries therefore has exactly one
representable role; an unknown or path-incompatible role blocks generation.

Schema kind uses a separate exhaustive path map: `events/*` is `event`, `config/*` is `config`,
`version/*` is `version_manifest`, and `common/*|operations/*|findings/*|receipts/*|privacy/*|
service/*` is `request_result`. Every entry records this value, and generation/loading re-derives
it from the path; a mismatch or unknown prefix blocks release. Schema kind and artifact role are
orthogonal and neither substitutes for the other.

The v0.1 manifest lists exactly 52 `*.schema.json` members: the original 43 reviewed schemas plus
four privacy and five local-service/control schemas. The manifest never lists itself.

For every member, `$id` is not merely namespace-prefixed: it must equal
`https://schemas.yoetz.dev/0.1/` plus the exact relative `path`. Thus the manifest is also the
closed static-host route table. `https://schemas.yoetz.dev/0.1/manifest.json` serves these same
manifest bytes, while every gate and installed runtime resolves member IDs/refs from the local
manifest and packaged files with network retrieval disabled.

The manifest is the release-time source of truth for schema parity. It is produced from the reviewed
checked-in artifacts, not from runtime generation. The manifest must remain stable across source,
sdist, wheel, and installed copies.

## Errors and edge cases

- A missing or duplicate schema entry blocks release.
- A path/digest mismatch blocks packaging parity.
- A path/`$id` mismatch, unlisted absolute ref, unresolved local fragment, or ref resolution that
  attempts DNS/HTTP blocks the local gate and release.
- A manifest that is not canonical JSON fails the contract.

## Invariants

1. The manifest enumerates the reviewed schema set exactly.
2. Byte size and SHA-256 are part of the contract.
3. Runtime never rewrites the manifest.

## Tests

- `tests/packaging/test_build_artifacts.py`
- `tests/packaging/test_dependency_lock_and_licenses.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
