# src/yoetz/protocol/schemas.py — frozen JSON Schema catalog and version index

**Wave:** A/B | **ADRs:** ADR-002, ADR-003, ADR-007 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/errors.md` |
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
| `SCHEMA_MANIFEST_SCHEMA` | `str = "yoetz.schema-manifest/1.0.0"` |
| `SCHEMA_MANIFEST_VERSION` | `str = "1.0.0"` |
| `SCHEMA_MEMBER_COUNT` | `int = 52` |
| `load_schema_catalog()` | read the packaged schema bundle and return the frozen catalog |
| `schema_uri(name, version)` | build the canonical schema URI under the Yoetz namespace |
| `schema_path_for(name, version)` | return the relative packaged path for one schema document |
| `schema_document_for(name, version)` | load one document from the packaged bundle and validate it |
| `validate_schema_instance(name: str, version: str, value: JsonValue) -> None` | validate one canonical JSON value against one exact packaged schema using only the closed local registry |
| `validate_schema_document(document)` | enforce draft/version/id/path/digest invariants |
| `request_result_schema_versions(catalog)` | ASCII-sorted request/result schema version map |
| `event_schema_versions(catalog)` | ASCII-sorted 16-family event schema version map |

## Behavior

`SchemaDocument` is a frozen value object with exactly these fields, in this order:

```text
schema_kind: SchemaKind
artifact_role: SchemaArtifactRole
schema_name: str
schema_version: str
schema_id: str
relative_path: str
canonical_digest: str
schema_bytes: bytes
json_schema: Mapping[str, JsonValue]
```

`SchemaCatalog` is a frozen value object with exactly these fields, in this order:

```text
documents: tuple[SchemaDocument, ...]
by_path: Mapping[str, SchemaDocument]
by_id: Mapping[str, SchemaDocument]
by_name_version: Mapping[tuple[str, str], SchemaDocument]
request_result_versions: Mapping[str, str]
event_schema_versions: Mapping[str, str]
manifest_version: str
manifest_digest: str
```

`documents` and every mapping iterate in unsigned-ASCII key order. The maps are immutable views
whose values are the same `SchemaDocument` instances held in `documents`; callers cannot mutate a
backing dict through the catalog. `manifest_digest` is `sha256:<64 lowercase hex>` over the exact
packaged `manifest.json` bytes, not over a reconstructed object.

Before a `SchemaDocument` is exposed, its parsed schema tree is recursively frozen: every JSON
object becomes a `MappingProxyType` over a newly allocated dictionary whose keys were inserted in
canonical UTF-16 order, every JSON array becomes a tuple, and scalar values are retained unchanged.
No mutable `dict`, `list`, or `set` is reachable through `SchemaDocument.json_schema`. The private
plain parse trees used to construct `jsonschema`/`referencing` resources are never returned and are
discarded after catalog construction. The frozen view remains a canonical value and must satisfy
`canonical_encode(document.json_schema) == document.schema_bytes` for every member.

`SchemaKind` has exactly `request_result`, `event`, `config`, and `version_manifest`.
`SchemaArtifactRole` has exactly `common-value`, `MCP input`, `MCP output`,
`persisted-envelope`, `event-envelope`, `event-payload`, `configuration`, `finding`,
`semantic-provenance`, `receipt-document`, `privacy-policy`, `outbound-case`, `privacy-audit`,
`setup-contract`, `local-control`, `service-status`, and `version-report`. The two classifications
are orthogonal checked manifest claims. Schema kind is re-derived from the complete prefix map:
`events/ -> event`, `config/ -> config`, `version/ -> version_manifest`, and
`common/|operations/|findings/|receipts/|privacy/|service/ -> request_result`. Artifact role is
re-derived from the complete path rules in `specs/schemas/manifest.json.md`; neither value is
inferred from the other.

### Catalog identity and lookup

For a manifest member, `schema_name` is the basename with the exact suffix
`-{schema_version}.schema.json` removed. It must match
`^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`; therefore
`events/action-recorded-1.0.0.schema.json` has the identity
`("action-recorded", "1.0.0")`. `schema_version` is canonical three-component SemVer with no
leading zero, prerelease, or build spelling. No underscore alias, path-qualified alias, or inferred
nearest version exists.

`schema_path_for(name, version)` first requires the exact name grammar above and canonical version
grammar, then performs a `by_name_version` lookup in the loaded manifest. A slash, backslash,
`..`, percent escape, non-ASCII character, or non-canonical name/version fails before resource I/O.
An otherwise valid but absent pair is not synthesized. `schema_uri(name, version)` is exactly
`SCHEMA_NAMESPACE + schema_path_for(name, version)`. These HTTPS `$id` values are stable schema
identifiers and static-host routes; local validation resolves them from `by_id` only and never
retrieves them over HTTP.

`schema_document_for(name, version)` returns that catalog member after all manifest, byte, and
reference checks. It never falls back to a source-tree path, current working directory, generated
document, nearest version, DNS, or HTTP.

### Exact manifest load

`load_schema_catalog()` uses `importlib.resources.files("yoetz").joinpath("resources", "schemas")`
as its only resource root. It operates only through `Traversable`: it never derives `__file__`,
coerces the resource to `pathlib.Path`, calls `as_file`, or falls back to the checkout/current
working directory. The closed subtree contains exactly `manifest.json` plus the 52 manifest
members; an extra regular resource of any extension is a manifest-member mismatch. It reads
`manifest.json`, then performs these checks in order:

1. The bytes pass `strict_json_parse`, are a canonical JSON object, and have exactly the top-level
   keys `manifest_schema`, `manifest_version`, and `members`. The first two values equal
   `SCHEMA_MANIFEST_SCHEMA` and `SCHEMA_MANIFEST_VERSION`.
2. `members` has exactly `SCHEMA_MEMBER_COUNT` entries, strictly ascending by ASCII `path`, with no
   duplicate path. Every member has exactly `$id`, `artifact_role`, `byte_length`, `media_type`,
   `owning_model`, `path`, `schema_kind`, `schema_version`, and `sha256`; media type is exactly
   `application/schema+json`.
3. Every path is a relative canonical POSIX path under the resource root. Recursively enumerating
   every packaged descendant for which `Traversable.is_file()` is true yields exactly
   `{"manifest.json"}` union the manifest path set. A missing member or any extra regular resource,
   including one with a non-schema extension, raises `schema_manifest_member_mismatch`; the loader
   never filters the authority set with a filename glob.
4. Each member file's exact byte length and SHA-256 match the member, its bytes are canonical strict
   JSON for an object, and its `$schema` is exactly
   `https://json-schema.org/draft/2020-12/schema`. After that exact dialect check, the pinned
   `Draft202012Validator.check_schema(private_plain_schema)` must succeed against the library's
   bundled local metaschema. A `SchemaError` becomes `schema_bytes_invalid`; no validator text or
   schema content is exposed and no dialect URI is retrieved.
5. Filename-derived name/version, `$id == SCHEMA_NAMESPACE + path`, manifest kind, path-derived
   kind, manifest role, and path-derived role all agree. IDs and `(schema_name, schema_version)`
   pairs are unique.
6. Every local fragment and absolute `$ref` resolves against the already loaded `by_id` documents
   using the exact closed-registry algorithm below.
7. The complete normalized maps described below exist; otherwise catalog construction fails rather
   than returning a partial object.

The function performs no model imports, environment/config reads, source-tree discovery, socket
calls, or writes. Repeated calls may return the same immutable cached object, but must return equal
content independent of import order, hash seed, current directory, or installation layout.

### Closed reference registry

Catalog construction creates a private `referencing.Registry` as
`Registry(retrieve=_deny_retrieve)`, where `_deny_retrieve(uri)` always raises `NoSuchResource(uri)`
and performs no I/O. It seeds that registry with exactly the 52 pairs
`schema_id -> DRAFT202012.create_resource(private_plain_schema)`. The draft metaschema URI is only
the frozen dialect identifier; support comes from the pinned local library and that URI is never
retrieved. Runtime instance validators also share one module-private
`FormatChecker(formats=("date-time",))`; `date-time` is the only format asserted by the frozen
v0.1 schema bundle. No optional checker discovery or plugin registration is permitted.

Before returning the catalog, the loader statically walks every value in every private plain schema
and checks every `$ref`, including references in conditional or otherwise unreachable branches. A
reference is admissible only when it is either a fragment of the current document or an absolute
URI whose defragmented base is an exact `by_id` key, with no query component. The loader then calls
`registry.resolver(current_schema_id).lookup(ref)` so the fragment itself must resolve. Relative
references, scheme-relative references, non-Yoetz absolute references, missing manifest IDs,
queries, and unresolved fragments all raise `schema_reference_unresolved`. Runtime validators use
this same closed registry. They never call `jsonschema.validate`, deprecated `RefResolver`, or a
default registry with a retrieval path.

### Runtime instance validation

`validate_schema_instance(name, version, value)` is the single public schema-instance boundary:

1. It resolves the exact catalog identity through `schema_document_for(name, version)`; name,
   version, and not-found failures propagate unchanged.
2. It calls `ensure_canonical_value(value)` before validation. Canonical-value failures propagate
   their existing exact reason rather than being relabeled as schema failures.
3. It selects the corresponding private plain schema from the immutable loader state and constructs
   a pinned `Draft202012Validator` with the same private closed `Registry` and exact local format
   checker described above. It never validates through the recursively frozen public mapping.
4. Any instance-validation error, including a required/additional-property, type, bound,
   conditional, reference, or checked `date-time` format failure, raises only
   `ProtocolValueError("schema_instance_invalid")`. The exception never echoes an instance value,
   JSON path, schema path, validator message, URL, or library exception text.
5. It returns `None` after successful validation and exposes neither the private schema tree,
   registry, resolver, nor validator.

Validation is wholly local: DNS, HTTP, environment configuration, a source checkout, and the
default referencing registry are never consulted. Catalog construction has already resolved every
reference, while `_deny_retrieve` remains the fail-closed backstop if an implementation defect tries
to leave the 52-resource registry.

### Inner and outer manifest ordering

This module self-verifies the inner schema manifest and its 52 members without importing
`version.py`. Later startup/release resource verification independently cross-checks
`SchemaCatalog.manifest_digest` against the outer resource-manifest entry for
`schemas/manifest.json`. Both checks must pass before write readiness; the schema catalog cannot
depend on the outer verifier to bootstrap its own trusted bytes.

### Version maps

`request_result_versions` contains exactly the 31 manifest members whose `schema_kind` is
`request_result`, keyed by their hyphenated `schema_name`; values are their exact manifest versions.
`event_schema_versions` contains exactly the 16 members whose `artifact_role` is `event-payload`,
keyed by converting that member's hyphenated `schema_name` to lower snake case (for example,
`action-recorded -> action_recorded`); every v0.1 value is `1.0.0`. It excludes
`accepted-event`, `event-draft`, and `opaque-unknown-event-draft` even though those artifacts have
`SchemaKind.EVENT`. The two public helper functions return these immutable catalog fields; they do
not rebuild, filter, or register values dynamically.

## Errors and edge cases

Every bounded failure raises `ProtocolValueError` with one exact member of the central immutable
reason registry. The mapping is:

| Reason | Exact failure family |
|---|---|
| `schema_manifest_missing` | packaged `manifest.json` resource absent |
| `schema_manifest_invalid` | manifest parse/canonical/top-level schema or version failure |
| `schema_manifest_duplicate_path` | repeated member path |
| `schema_manifest_member_mismatch` | wrong member keys/media type/length, or packaged member set differs |
| `schema_path_unsafe` | absolute, traversal, separator, escape, or otherwise unsafe lookup/member path |
| `schema_name_invalid` | non-canonical API name or version spelling |
| `schema_not_found` | valid `(name, version)` lookup is absent from the complete catalog |
| `schema_bytes_invalid` | member bytes are not canonical strict JSON for an object, or the object fails the pinned Draft 2020-12 metaschema check |
| `schema_digest_mismatch` | member byte SHA-256 does not match the manifest |
| `schema_id_mismatch` | document/manifest `$id` differs from namespace plus path |
| `schema_draft_unsupported` | `$schema` is not the frozen 2020-12 URI |
| `schema_version_mismatch` | filename, manifest, and derived schema version disagree |
| `schema_kind_mismatch` | known kind disagrees with the path-derived kind |
| `schema_artifact_role_invalid` | role token is outside the closed enum |
| `schema_artifact_role_mismatch` | known role disagrees with the path-derived role |
| `schema_reference_unresolved` | any local fragment or absolute `$ref` is absent from the local registry |
| `schema_catalog_incomplete` | either exact normalized version map cannot be built |
| `schema_duplicate_identity` | duplicate `$id` or `(schema_name, schema_version)` |
| `schema_instance_invalid` | a selected canonical JSON instance fails its exact frozen packaged schema |

The loader reports the first failure in the load order above and never embeds schema bytes, paths
outside the safe manifest-relative form, parser text, or network errors in the reason. Runtime
payload validation belongs to the selecting boundary and is performed through
`validate_schema_instance`; no consumer creates a looser or network-capable validator.

## Invariants

1. Schema identity is stable, namespaced, and release-visible.
2. The catalog is deterministic and load-order independent.
3. Packaged schema bytes plus their packaged manifest are the authority for schema identity, not
   generated docs or a live schema website.
4. No schema document may depend on runtime state, config files, current working directory, or
   network discovery; the full integrity gate runs offline from installed resources.
5. Version-manifest reporting and schema bundle validation use the same normalized catalog.
6. Broad `SchemaKind` classification never substitutes for the exact manifest artifact role.
7. No mutable container is reachable through a returned schema document or catalog.
8. Artifact metaschema checks and runtime instance validation are reproducible with networking
   disabled and use only the pinned validator plus the exact packaged registry.

## Tests

- `specs/tests/packaging.md` — schema bundle byte parity, missing/extra/duplicate paths (including an
  extra non-schema resource), digest mismatch, Draft 2020-12 metaschema rejection, and traversal
  rejection.
- `specs/tests/unit.md` — URI/path normalization, duplicate detection, version-map ordering, and
  closed local instance validation.
- `specs/tests/capability.md` — version manifest includes the expected schema maps.
- `fixtures/canonical/` — frozen positive and negative schema-manifest vectors.

## Open questions

None.
