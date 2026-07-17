# scripts/generate_schemas.py — deterministic public JSON Schema generator

**Wave:** A/F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz/protocol/models.md`, `specs/src/yoetz/protocol/schemas.md`,
`specs/src/yoetz/protocol/canonical.md` | **Imported by:** PR CI, resource verification,
release construction

## Purpose

Generate and verify the reviewable JSON Schema files for all public six-operation request/result
models and durable event payloads. The script makes schema drift visible in source control and
prevents framework defaults, nondeterministic definition names, or local environment state from
silently changing the public protocol.

It is a repository maintainer tool, not runtime code. Installed users never need it.

## Public surface

The future script exposes:

- `build_schema_documents() -> tuple[SchemaDocument, ...]` — pure deterministic generation;
- `validate_schema_document(document) -> None` — dialect and Yoetz policy checks;
- `render_schema(document) -> bytes` — canonical pretty JSON bytes;
- `compare_tree(expected, root) -> SchemaDiff` — missing/extra/changed inventory;
- `write_tree(expected, root) -> None` — atomic explicit update;
- `main(argv: Sequence[str] | None = None) -> int`.

Command contract:

```text
uv run --locked python scripts/generate_schemas.py --check
uv run --locked python scripts/generate_schemas.py --write
```

Exactly one mode is required. Optional `--output-root` exists for tests only and must resolve below
an explicitly supplied temporary directory; production/CI uses repository `schemas/`. Exit `0`
means exact parity, `1` means generated diff/validation failure, and `2` means invocation error.

## Behavior

### Model discovery

Import only the pure protocol schema registry. Do not import application, adapter, CLI, MCP,
provider, key, storage, or package-resource modules. The registry returns an explicit ordered tuple;
the script never discovers subclasses by walking modules or relies on import side effects.

The v0.1 inventory includes request and result schemas for `start`, `publish_work`, `check`,
`respond`, `status`, and `receipt`; shared public envelope/error/coverage/frontier schemas; and every
versioned durable event payload accepted by the protocol registry. File destinations are explicit
registry data. Two models cannot claim one path or schema `$id`.

### Normalization

For each model, request JSON Schema 2020-12 from the pinned model library and apply only the frozen
Yoetz normalization pipeline:

1. replace framework-generated definition anchors with registry-owned stable names;
2. set exact `$schema`, `$id`, `title`, and version annotations;
3. resolve or sort `$defs`, properties, required lists, enum values, and union alternatives by
   contract-defined ASCII keys without changing semantic order where order is meaningful;
4. ensure every object has `additionalProperties: false` unless a named map type explicitly owns
   open keys;
5. represent identifiers, digests, timestamps, safe integers, and media types with shared frozen
   `$defs`, not slightly different inline patterns;
6. remove framework implementation metadata that is not part of the public contract;
7. reject floats, implicit coercion, nullable-by-default, unbounded strings/arrays, or noncanonical
   integer ranges;
8. preserve descriptions only from public protocol docstrings that passed the boundary scan.

Normalization is deterministic and idempotent. It cannot read Git, clock, network, hostname,
locale, environment, checkout path, or installed optional extras.

### Validation

Validate every generated document against the chosen dialect metaschema available in the locked
development environment, then run Yoetz-specific checks:

- `$id`, destination, media-type/schema-name, and registry version agree;
- every `$id` is exactly `SCHEMA_NAMESPACE + destination_relative_path` and is therefore a direct
  static-file URL;
- every `$ref` is an absolute canonical member URL (plus optional fragment), resolves exactly once
  through the candidate local registry, and succeeds while DNS/socket/HTTP access is denied;
- regexes are anchored, ASCII-explicit where required, and below complexity/length caps;
- arrays and strings have explicit bounds; integers stay within canonical safe/SQLite ranges;
- discriminated unions use stable literal tags and reject unknown variants;
- request schemas exclude ledger-assigned fields; result schemas include the required structural
  identity/coverage/error fields;
- descriptions contain no local paths, private terms, secrets, example payloads, or unstable claims.

Compile representative valid/invalid vectors through both model validation and schema validation.
Any disagreement is a generator failure.

### Rendering and tree comparison

Render canonical compact UTF-8 without BOM, insignificant whitespace, or a terminal newline, using
UTF-16 code-unit object-key order. The reviewed root uses one schema per explicit registry
destination plus a generated public index.
`--check` reads raw bytes under `schemas/`, rejects symlinks and unexpected files, and prints only a
bounded path/status diff. It never rewrites.

The same check treats `schemas/` as the document root for `/0.1/`: for every manifest member it
derives the URL path, opens the corresponding local file without a network client, and proves the
root and packaged mirror bytes agree. A loopback static-server integration test may exercise HTTP
path/content-type/header behavior, but schema/ref validation itself remains file-backed and runs
with network denied.

`--write` stages all output in a sibling temporary directory, fsyncs files, verifies the staged
tree, then atomically replaces only generator-owned schema files. It does not delete an unknown
file; instead it fails and asks the maintainer to classify it. After write, it reruns `--check`.
Packaged resource synchronization and manifest update are separate explicit steps, so one command
cannot hide both schema and packaging drift.

### Output discipline

stdout contains a compact deterministic summary suitable for CI; details go to sanitized stderr.
No generated schema or diagnostic includes or captures exception tracebacks. `--json` may emit a
bounded structural report with counts, paths, and expected/observed digests, never schema contents.

## Errors and edge cases

- Duplicate model/path/`$id`, unresolved refs, invalid dialect, or model/schema vector disagreement
  exits `1` without writes.
- A missing locked dependency or wrong generator/model-library version fails; it does not emit
  best-effort schemas.
- Dirty unrelated files are never reset or overwritten.
- Locale/hash seed/order differences must not affect bytes; parity is checked under multiple seeds.
- Output-root traversal, symlink, absolute path, or case collision is rejected.
- Generated descriptions are scanned for public-boundary canaries before write.

## Invariants

1. Reviewed schema bytes are a deterministic projection of the frozen protocol registry.
2. `--check` is read-only and fails on any missing, changed, or extra owned file.
3. `--write` is explicit, atomic per generated tree, and cannot overwrite unknown files.
4. Runtime/adapters/network/environment cannot influence public schema bytes.
5. Model validation and JSON Schema accept/reject the same published vectors.

## Tests

- `specs/tests/unit.md`: normalization ordering, stable anchors, bounds, local refs, rendering.
- `specs/tests/property.md`: shuffled input/model order and hash seeds produce identical bytes.
- `specs/tests/integration.md`: clean/check/write/drift/unknown/symlink/collision trees.
- `specs/tests/packaging.md`: reviewed schema and embedded resource byte equality.
- PR CI runs `--check` from a clean checkout before tests/build.

## Open questions

None.
