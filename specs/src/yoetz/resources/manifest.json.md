# src/yoetz/resources/manifest.json — installed resource identity manifest

**Wave:** A/F | **ADRs:** ADR-002, ADR-003, ADR-006, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):**
root schema, migration, policy, fixture, guidance, and skill specs | **Imported by:** package startup/version,
`specs/scripts/verify_resource_manifest.py.md`, packaging and release workflows

## Purpose

The installed package must prove which reviewed non-code bytes it contains. This manifest binds
every runtime resource to a canonical source path, installed logical path, size, media type, and
SHA-256. It prevents stale copies, silent packaging omission, platform newline changes, unexpected
files, and runtime fallback to a developer checkout.

The manifest contains only public structural metadata. It never inventories local/private inputs,
user bundles, configuration, credentials, generated capability transcripts, or ignored files.

## Public surface

The future file is canonical JSON with this exact top-level shape and key order:

```text
{
  "schema": "yoetz.resource-manifest/1",
  "package": "yoetz",
  "resource_set_version": "0.1.0",
  "entries": [ResourceEntry...],
  "resource_set_digest": "sha256:<hex>"
}
```

Each `ResourceEntry` contains, in order:

- `logical_name`: POSIX relative runtime name;
- `source_path`: POSIX relative public-repository source;
- `package_path`: POSIX relative path below `src/yoetz/resources/`;
- `kind`: one of `json_schema|canonical_vector|migration|skill|guidance|
  compatibility_manifest|runtime_support`;
- `media_type`: frozen lowercase ASCII media type;
- `size`: non-negative JSON integer;
- `sha256`: lowercase `sha256:` digest;
- optional `contract_version`: bounded public version string.

Entries are sorted by ASCII `logical_name`. No optional key is serialized as null. The
`resource_set_digest` is the stable, self-excluding identity of the reviewed set. Its digest
material is the canonical JSON encoding of `schema`, `package`, `resource_set_version`, and the
sorted entries, except that the `runtime_support` entry contributes only `logical_name`,
`source_path`, `package_path`, `kind`, `media_type`, and optional `contract_version`; its `size` and
`sha256` are omitted. This one explicit exclusion lets the packaged support document bind the set
identity without requiring a hash fixed point. The checked-in manifest still carries and runtime
still verifies the support entry's actual size and SHA-256. The set digest is not a signature or a
digest of the manifest file.

## Behavior

### Included inventory

The v0.1 inventory contains exactly 73 entries:

- all 52 installed JSON Schema artifacts required for six-operation input/output, durable events,
  configuration, receipts, findings, version reporting, local-service control, and privacy/egress,
  plus the canonical schema inventory manifest (53 schema-namespace resources total);
- exactly these installed canonical fixture mirrors from `fixtures/canonical/`:
  `rfc8785-applicable.case.json`,
  `restricted-json-positive.case.json`,
  `restricted-json-rejections.case.json`,
  `utf16-property-order.case.json`,
  `unicode-normalization-distinct.case.json`,
  `publication-request-identity.case.json`,
  `accepted-entry-identity.case.json`,
  `identifiers.case.json`,
  `object-envelope.case.json`;
- `migrations/catalog/0001.sql`, `migrations/bundle/0001.sql`,
  `migrations/bundle/0002.sql`, and `migrations/bundle/0003.sql`;
- the canonical Codex `SKILL.md`, its compatibility manifest, and all four harness-neutral
  `guidance/` documents (which Codex installs byte-identically under `references/`);
- `support/runtime-support.json` as the installed write/integration support allowlist.

v0.1 deterministic policy rules and receipt rendering live in reviewed Python modules; there is no
external policy-data, prompt, receipt-template, or localization resource.

Files used only for tests, public documentation, licenses, SBOMs, release evidence, and full
adversarial/scale fixture corpora are not installed resources unless runtime code reads them. The
allowlist is explicit; discovering an extra file under the package resource directory is an error.

The full adversarial, replay, import, receipt, and backward-read corpora remain test/sdist-only.

### Path and source rules

All paths are NFC-normalized UTF-8 POSIX relative paths. They cannot be empty, absolute, contain
`.`/`..`, backslash, control character, repeated slash, leading/trailing slash, or a segment that
changes under ASCII case folding relative to another path. `source_path` must be inside an
allowlisted public source root (`schemas/`, `migrations/`, `skills/`, `guidance/`, `fixtures/`, or
`support/`).
No other source root is valid in v0.1. It cannot point into ignored/local planning material,
`.git`, editor state, temp
directories, build outputs, transcripts, home paths, or a symlink.

`package_path` must equal the logical destination mapping approved for its kind and must resolve
within `src/yoetz/resources/`. Exactly one entry names every packaged file other than the
manifest itself. Logical/source/package paths are independently unique and case-collision free.

### Digest generation

The generator/verifier reads raw bytes; it does not decode or normalize before size/digest. SHA-256
is used for content identity, not secrecy. It validates kind-specific text rules separately:
UTF-8 without BOM, LF-only and final LF for SQL/Markdown/JSON text; canonical JSON for JSON files;
bounded size per kind.

Generation is deterministic: the same reviewed tree produces identical manifest bytes across OS,
locale, timezone, Python hash seed, and checkout path. No timestamp, user, hostname, build path,
Git branch, dirty flag, or environment value appears in this file. Release commit/package identity
belongs in the version/release-evidence manifest instead.

### Runtime verification

Startup loads this file through package resources with a hard byte cap, rejects duplicate JSON
keys/floats/non-canonical integers/unknown fields, recomputes its set digest, and validates the
manifest schema before trusting any entry. Resource loads verify entry size and digest before
decoding or execution. A full release/startup probe verifies the complete inventory; an individual
runtime load may verify the named entry after the manifest itself is trusted.

`yoetz version --json` reports the resource-set version and digest. It does not print local
paths or enumerate content whose name is not already public. Strict-local operation never contacts
the network to refresh resources.

### Update protocol

A resource change is one review unit: update canonical source, synchronize exact packaged copy,
regenerate manifest, and update any golden vectors/version declarations. A released migration or
protocol vector is not edited in place; add a new versioned path. Code review can reproduce the
manifest with the locked script and must see a clean diff afterward.

## Errors and edge cases

- Missing/extra/duplicate/traversing/case-colliding/symlinked resource: build and release failure.
- Source/packaged size or digest mismatch: build failure; installed runtime fails closed for any
  operation requiring the resource.
- Invalid/noncanonical manifest JSON or self-digest: package integrity failure before resource use.
- Unknown `kind`, media type, key, or source root: failure; no forward-compatible ignore behavior.
- A wheel loader that cannot expose bytes through standard resource traversal is unsupported.
- Diagnostic output uses logical name and bounded reason/digest only, never bytes or absolute paths.

## Invariants

1. Every runtime-read non-code file is manifest-bound to one reviewed public source.
2. Every manifest entry has byte-identical source and package copies.
3. Extra package resources fail verification rather than becoming ambient inputs.
4. Manifest generation has no machine-, time-, secret-, or checkout-dependent field.
5. Runtime performs no network or developer-tree fallback.
6. The set digest is reproducible but is never described as a signature or authenticity proof.

## Tests

- `specs/tests/unit.md`: manifest schema, canonical set digest, path/media/kind validators.
- `specs/tests/property.md`: arbitrary hostile paths/duplicates/orderings and deterministic output.
- `specs/tests/integration.md`: installed resource API and per-entry pre-use verification.
- `specs/tests/packaging.md`: full source/package inventory parity, mutation matrix, clean install,
  wheel/sdist contents, and reproducible generation.
- `specs/tests/capability.md`: installed skill and compatibility resource identity.

## Open questions

None. The runtime-support self-exclusion is the sole v0.1 set-identity exclusion; adding another
self-referential installed resource requires a new manifest schema.
