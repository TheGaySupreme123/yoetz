# src/yoetz/protocol/schemas.py — frozen JSON Schema catalog and version index

**Wave:** A | **ADRs:** ADR-002, ADR-003, ADR-007 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/models.md`, `domain/events.md`, `config/models.md` |
**Imported by:** `version.md`, package schema-integrity checks, packaging tests,
schema validation tooling

## Purpose

This file is the index for every committed JSON Schema that Yoetz ships or validates against.
It is the bridge between the human-written spec tree and the machine-readable schema artifacts
under `specs/schemas/`. Without it, the version manifest cannot report schema identities, the
packaging checks cannot prove byte parity, and the codebase would have no single place to say
which schema files are canonical and which are generated or optional.

The schema catalog is release evidence, not runtime policy. Runtime code may consume the catalog to
validate installed artifacts, but it must never treat the schema index as a dynamic discovery
mechanism or a network-backed registry.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `SchemaKind` | enum of `request_result`, `event`, `config`, `version_manifest` |
| `SchemaArtifactRole` | closed 17-value release/packaging role enum registered in `specs/INTERFACES.md` |
| `SchemaDocument` | frozen dataclass describing one committed schema artifact |
| `SchemaCatalog` | frozen dataclass holding the full schema index and normalized version maps |
| `SCHEMA_NAMESPACE` | `str = "https://schemas.yoetz.dev/0.1/"` |
| `load_schema_catalog()` | read the packaged schema bundle and return the frozen catalog |
| `schema_uri(name, version)` | build the canonical schema URI under the Yoetz namespace |
| `schema_path_for(name, version)` | return the relative packaged path for one schema document |
| `schema_document_for(name, version)` | load one document from the packaged bundle and validate it |
| `validate_schema_document(document)` | enforce draft/version/id/path/digest invariants |
| `request_result_schema_versions(catalog)` | ASCII-sorted request/result schema version map |
| `event_schema_versions(catalog)` | ASCII-sorted 16-family event schema version map |

## Behavior

`SchemaDocument` is a frozen value object with exactly these fields:
`schema_kind`, `artifact_role`, `schema_name`, `schema_version`, `schema_id`, `relative_path`,
`canonical_digest`, `schema_bytes`, and `json_schema`.

`SchemaKind` and `SchemaArtifactRole` are orthogonal. `SchemaKind` is the broad runtime validation
family used to build version maps and select validators. `SchemaArtifactRole` is the exact
release/packaging purpose recorded by the reviewed schema manifest, including distinctions such as
MCP input versus output and accepted envelope versus event payload. Neither enum is derived from
the other. `specs/INTERFACES.md` registers the nominal role vocabulary; the reviewed
`specs/schemas/manifest.json.md` owns the exact path-to-role assignment and must enumerate the same
closed values. `SchemaDocument.artifact_role` uses the nominal enum, never an unchecked manifest
string.

`SchemaKind` is determined by a second complete path map, independently of artifact role:

- `events/*` -> `event`;
- `config/*` -> `config`;
- `version/*` -> `version_manifest`; and
- `common/*`, `operations/*`, `findings/*`, `receipts/*`, `privacy/*`, and `service/*` ->
  `request_result`.

Those prefixes exhaust all 52 v0.1 schema artifacts. Any other prefix is invalid. The manifest
records the resulting `schema_kind` explicitly and the loader re-derives it from this map; there is
no `support_manifest` kind or phantom support-manifest schema in v0.1. Runtime support is the
separate reviewed JSON allowlist verified through the resource manifest, not part of this JSON
Schema catalog.

The catalog only includes schemas that are either:

- directly referenced by the public protocol surface;
- needed by the package/version manifest;
- or required by release-time validation.

The catalog does not invent schemas. It reads only packaged files and normalizes them into stable
ASCII-sorted maps. The expected bundle includes, at minimum:

- request/result schemas for the public operations and helper request/result envelopes;
- the 16 event-family schemas at version `1.0.0`;
- the config schema;
- the version-manifest schema.

`schema_uri(name, version)` delegates to `schema_path_for` and returns
`SCHEMA_NAMESPACE + relative_path`. The exact checked-in path therefore is also the exact static
host path; the URI is stable and must not depend on install paths, Git state, or package metadata.

`schema_path_for(name, version)` maps a schema name/version pair to a relative artifact path under
the checked-in `schemas/` root and packaged `yoetz/resources/schemas/` mirror. The path is
canonical, lowercase, and traversal-free. A schema name with `..`,
path separators, or a non-canonical spelling is rejected before any file access.

`schema_document_for(name, version)` loads one packaged JSON Schema, parses it strictly, validates
its `$schema`, `$id`, version, and canonical digest, and returns the frozen document. It never
falls back to a nearest match or a generated substitute.

`load_schema_catalog()` reads the entire packaged bundle, validates each document individually, then
checks catalog-wide invariants:

- no duplicate `(schema_kind, schema_name, schema_version)` entries;
- every manifest `artifact_role` parses as `SchemaArtifactRole` and equals the exact role required
  for its relative path by the reviewed manifest mapping;
- every version map is ASCII-sorted by schema name;
- every `event_schema_versions` entry is exactly `1.0.0` in v0.1;
- every request/result schema version is recorded as the exact SemVer string frozen in the bundle;
- every `$id` lives under `SCHEMA_NAMESPACE`;
- every `$id` equals `SCHEMA_NAMESPACE + relative_path` byte-for-byte;
- every absolute `$ref` and fragment resolves through the in-memory catalog with no network
  retriever or fallback;
- every document is draft 2020-12;
- the canonical digest recorded in the manifest matches the exact packaged bytes.

The catalog is read-only. It is a packaging and verification surface, not a mutation API. Its
validator registry is created exclusively from manifest members; a missing URI raises the bounded
unknown-schema failure and never invokes DNS, sockets, HTTP, or a library default retriever.

`request_result_schema_versions(catalog)` and `event_schema_versions(catalog)` return the normalized
maps used by `version.py`. The maps are stable across hash seeds and installation order because the
catalog sorts them before exposing them.

## Errors and edge cases

- A missing schema file is a packaging failure, not a silent omission.
- A duplicate schema name/version pair is a release-blocking inconsistency.
- A `$id` outside the Yoetz namespace, a wrong schema draft, or a wrong schema version is invalid.
- An unknown artifact role or a role incompatible with its manifest path is invalid even when the
  schema bytes themselves validate.
- A path that escapes `specs/schemas/` is rejected as unsafe before read.
- A digest mismatch means the package is corrupt or stale and must fail closed.
- This file never validates runtime payloads directly; it validates the schema artifacts that
  runtime validators consume.

## Invariants

1. Schema identity is stable, namespaced, and release-visible.
2. The catalog is deterministic and load-order independent.
3. Packaged schema bytes are the authority for schema identity, not generated docs.
4. No schema document may depend on runtime state, config files, or network discovery.
5. Version-manifest reporting and schema bundle validation use the same normalized catalog.
6. Broad `SchemaKind` classification never substitutes for the exact manifest artifact role.

## Tests

- `specs/tests/packaging.md` — schema bundle byte parity, missing/extra/duplicate paths, digest
  mismatch, and traversal rejection.
- `specs/tests/unit.md` — URI/path normalization, duplicate detection, and version-map ordering.
- `specs/tests/capability.md` — version manifest includes the expected schema maps.
- `fixtures/canonical/` — frozen positive and negative schema-manifest vectors.

## Open questions

None.
