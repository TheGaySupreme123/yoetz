# schemas/ — frozen public JSON Schema artifact set

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `domain/events.md`, `domain/findings.md`,
`domain/receipts.md`, `config/models.md` | **Imported by:** `protocol/schemas.md`,
MCP server, CLI validation fixtures, resource manifest, conformance and packaging tests

## Purpose

Specify every reviewed JSON Schema shipped at the repository root and copied byte-for-byte into the
wheel. These schemas are the language-neutral public contract. Pydantic may generate a candidate,
but no runtime-generated schema silently replaces a released artifact.

All schemas use JSON Schema Draft 2020-12, closed objects, explicit formats/patterns, and canonical
resource identities below `https://schemas.yoetz.dev/0.1/`. They describe accepted JSON
values; the stricter raw-byte parser still rejects duplicate keys, invalid UTF-8, BOM/NUL, floats,
unsafe integers, and lone surrogates before schema evaluation.

## Public surface

### Artifact layout

```text
schemas/
  manifest.json
  common/
    actor-assertion-1.0.0.schema.json
    client-info-1.0.0.schema.json
    coverage-1.0.0.schema.json
    frontier-1.0.0.schema.json
    public-error-1.0.0.schema.json
    operation-result-1.0.0.schema.json
    subject-state-ref-1.0.0.schema.json
  operations/
    start-request-1.0.0.schema.json
    start-result-1.0.0.schema.json
    publish-work-request-1.0.0.schema.json
    publish-work-result-1.0.0.schema.json
    check-request-1.0.0.schema.json
    check-result-1.0.0.schema.json
    respond-request-1.0.0.schema.json
    respond-result-1.0.0.schema.json
    status-request-1.0.0.schema.json
    status-result-1.0.0.schema.json
    receipt-request-1.0.0.schema.json
    receipt-result-1.0.0.schema.json
  events/
    event-draft-1.0.0.schema.json
    accepted-event-1.0.0.schema.json
    opaque-unknown-event-draft-1.0.0.schema.json
    session-opened-1.0.0.schema.json
    session-resumed-1.0.0.schema.json
    plan-published-1.0.0.schema.json
    obligation-published-1.0.0.schema.json
    assignment-recorded-1.0.0.schema.json
    decision-recorded-1.0.0.schema.json
    action-recorded-1.0.0.schema.json
    result-recorded-1.0.0.schema.json
    evidence-recorded-1.0.0.schema.json
    claim-recorded-1.0.0.schema.json
    plan-revised-1.0.0.schema.json
    finding-recorded-1.0.0.schema.json
    response-recorded-1.0.0.schema.json
    redaction-recorded-1.0.0.schema.json
    check-recorded-1.0.0.schema.json
    receipt-recorded-1.0.0.schema.json
  findings/
    finding-1.0.0.schema.json
    semantic-provenance-1.0.0.schema.json
  receipts/
    receipt-document-1.0.0.schema.json
  config/
    yoetz-config-1.0.0.schema.json
  privacy/
    egress-receipt-1.0.0.schema.json
    outbound-case-1.0.0.schema.json
    privacy-policy-1.0.0.schema.json
    setup-wizard-contract-1.0.0.schema.json
  service/
    control-hello-1.0.0.schema.json
    control-hello-result-1.0.0.schema.json
    control-request-1.0.0.schema.json
    control-result-1.0.0.schema.json
    service-status-1.0.0.schema.json
  version/
    version-manifest-1.0.0.schema.json
```

`manifest.json` is itself canonical JSON and lists for each artifact: relative POSIX path,
`$id`, schema version, media type, exact byte size, SHA-256 digest, owning Python boundary
model, exact `SchemaKind`, and exact artifact role. Schema kind follows the complete independent
path map in `protocol/schemas.py` (`events`, `config`, `version`, or the remaining
`request_result` prefixes). The closed role vocabulary covers common values, MCP
input/output, persisted envelopes, event envelopes/payloads, configuration, finding/provenance,
receipt documents, privacy policy/case/audit/setup, local control, service status, and version
reporting; `schemas/manifest.json` owns the exact tokens and path-to-role mapping.
The manifest lists exactly 52 `*.schema.json` artifacts and never lists itself; therefore the
`schemas/` directory's exact future-file inventory is 53 files including `manifest.json`.

### Identifier rules

- `$schema`: exactly `https://json-schema.org/draft/2020-12/schema`.
- `$id`: `https://schemas.yoetz.dev/0.1/<exact-relative-schema-path>`. The checked-in
  `schemas/` directory is the static document root mounted at `/0.1/`, so identity and hosting
  require no rewrite route. For example, `events/accepted-event-1.0.0.schema.json` is exactly
  `https://schemas.yoetz.dev/0.1/events/accepted-event-1.0.0.schema.json`.
- `$ref`: the absolute static-file `$id`, optionally followed by a fragment. Runtime, generators,
  and tests resolve it through the frozen local registry only; no gate or installed operation may
  issue DNS or HTTP merely because the identifier is an HTTPS URL.
- URL-shaped fixture values that are not `$id` or `$ref` are ordinary opaque test data, not schema
  routes. In particular REP-004's future-version schema URI is deliberately absent from the v0.1
  catalog and is preserved without any registry, filesystem, DNS, or HTTP dereference.
- `$defs`: stable lower-snake-case logical names; no generator-specific numeric suffixes.
- `title` and `description` are documentation only and excluded from behavioral
  comparison only during candidate normalization; released bytes, including descriptions, remain
  frozen.

The common operation-result definitions are a separately public schema for language-neutral
clients.

### Static hosting and offline closure

The complete checked-in `schemas/` tree is directly deployable without renaming: its contents are
served byte-for-byte below `https://schemas.yoetz.dev/0.1/`, including
`https://schemas.yoetz.dev/0.1/manifest.json`. Every schema response uses
`application/schema+json; charset=utf-8`, public CORS, `nosniff`, and immutable caching because a
released versioned schema path is never rewritten. The manifest response uses `application/json`,
a digest ETag, and bounded revalidation caching; it advances atomically only to a reviewed
superset/new release inventory and remains bound to the listed member bytes.

Hosting is a publication surface, never a runtime dependency. The local gate constructs a closed
URI-to-file registry from `schemas/manifest.json`, proves for every member that
`$id == SCHEMA_NAMESPACE + relative_path`, resolves every fragment with network disabled, and
compares the root bytes with `src/yoetz/resources/schemas/`. Release staging serves the same tree
from a loopback static server with external network/DNS disabled and fetches every canonical URL
path from those checked-in bytes before publication. The tagged release may later expose the same
canonical paths at the immutable `/0.1/` prefix, but that hosted publication is separate from the
runtime and gate-resolution path. A missing host, DNS failure, or CDN outage never prevents an
installed Yoetz operation from validating locally; it blocks only a release claiming hosted
availability.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
schemas/common/actor-assertion-1.0.0.schema.json
schemas/common/client-info-1.0.0.schema.json
schemas/common/coverage-1.0.0.schema.json
schemas/common/frontier-1.0.0.schema.json
schemas/common/operation-result-1.0.0.schema.json
schemas/common/public-error-1.0.0.schema.json
schemas/common/subject-state-ref-1.0.0.schema.json
schemas/config/yoetz-config-1.0.0.schema.json
schemas/events/accepted-event-1.0.0.schema.json
schemas/events/action-recorded-1.0.0.schema.json
schemas/events/assignment-recorded-1.0.0.schema.json
schemas/events/check-recorded-1.0.0.schema.json
schemas/events/claim-recorded-1.0.0.schema.json
schemas/events/decision-recorded-1.0.0.schema.json
schemas/events/event-draft-1.0.0.schema.json
schemas/events/evidence-recorded-1.0.0.schema.json
schemas/events/finding-recorded-1.0.0.schema.json
schemas/events/obligation-published-1.0.0.schema.json
schemas/events/opaque-unknown-event-draft-1.0.0.schema.json
schemas/events/plan-published-1.0.0.schema.json
schemas/events/plan-revised-1.0.0.schema.json
schemas/events/receipt-recorded-1.0.0.schema.json
schemas/events/redaction-recorded-1.0.0.schema.json
schemas/events/response-recorded-1.0.0.schema.json
schemas/events/result-recorded-1.0.0.schema.json
schemas/events/session-opened-1.0.0.schema.json
schemas/events/session-resumed-1.0.0.schema.json
schemas/findings/finding-1.0.0.schema.json
schemas/findings/semantic-provenance-1.0.0.schema.json
schemas/manifest.json
schemas/operations/check-request-1.0.0.schema.json
schemas/operations/check-result-1.0.0.schema.json
schemas/operations/publish-work-request-1.0.0.schema.json
schemas/operations/publish-work-result-1.0.0.schema.json
schemas/operations/receipt-request-1.0.0.schema.json
schemas/operations/receipt-result-1.0.0.schema.json
schemas/operations/respond-request-1.0.0.schema.json
schemas/operations/respond-result-1.0.0.schema.json
schemas/operations/start-request-1.0.0.schema.json
schemas/operations/start-result-1.0.0.schema.json
schemas/operations/status-request-1.0.0.schema.json
schemas/operations/status-result-1.0.0.schema.json
schemas/privacy/egress-receipt-1.0.0.schema.json
schemas/privacy/outbound-case-1.0.0.schema.json
schemas/privacy/privacy-policy-1.0.0.schema.json
schemas/privacy/setup-wizard-contract-1.0.0.schema.json
schemas/receipts/receipt-document-1.0.0.schema.json
schemas/service/control-hello-1.0.0.schema.json
schemas/service/control-hello-result-1.0.0.schema.json
schemas/service/control-request-1.0.0.schema.json
schemas/service/control-result-1.0.0.schema.json
schemas/service/service-status-1.0.0.schema.json
schemas/version/version-manifest-1.0.0.schema.json
```

## Behavior

### Closed and exact schemas

Every object uses `additionalProperties: false`; composed objects use
`unevaluatedProperties: false` where required to close `allOf`/union branches. Required
fields exactly match the protocol models. Optional fields distinguish absent from explicit
`null`; `null` is accepted only when the contract names it.

Sequence/frontier/int64 identity fields that their owning schema explicitly declares as strings use
canonical decimal spelling with the exact `"0" | [1-9][0-9]*` pattern and documented bounds.
Bounded measurements and counts that are not identities remain JSON integers where the frozen
schema says so: examples include `payload_ref.plaintext_size`, manifest `byte_length`, and request
body byte counts. They reject booleans, strings, non-integral numbers, and values outside their
schema bounds. Floats and integers outside the restricted canonical JSON safe range remain
forbidden globally; the contract does not convert every safe bounded integer into a string.

The accepted-event artifact validates the full persisted/schema record: it includes
`entry_digest` and excludes the decoded in-memory `payload` handle. Entry-digest computation uses a
separate preimage view that removes `entry_digest` as well; that intentionally incomplete view is
canonicalized and hashed but is not itself validated as a full accepted-event document.

`event-draft` has two disjoint branches:

1. a known family/version branch referencing the exact payload schema; unknown fields fail;
2. a bounded opaque unknown-event branch preserving schema name/version and an encrypted payload
   candidate, while forbidding collision with a known family/version.

The discriminator is validated by Yoetz code rather than relying on nonstandard OpenAPI behavior.

### Output fallback compatibility

Each of the six operation result schemas is a union of:

- its operation-specific `ok: true` result; and
- the common `ok: false` public-error result.

All six must admit the same prebuilt, request-independent `INTERNAL_ERROR` fallback with
nullable/absent request identity exactly as specified by the MCP contract. A schema change that
rejects the fallback blocks server startup and release.

### Generate, review, freeze

1. Boundary models generate candidate Draft 2020-12 schemas with deterministic ref templates.
2. A normalizer removes only explicitly non-contractual generator noise, resolves stable
   `$defs` names, sorts object members canonically, and never widens constraints.
3. Maintainers review the semantic diff against ADR/INTERFACES/owning-file-spec authority.
4. Accepted candidates are written to root `schemas/`, and `manifest.json` is
   regenerated.
5. CI regenerates into a temporary directory and requires exact semantic parity and, after
   canonical formatting, exact bytes. Runtime never rewrites the checked-in copies.
6. Build copies the exact reviewed bytes into `yoetz/resources/schemas/`; packaging
   tests prove source/wheel/installed equality.

### Compatibility policy

- First public versions are `1.0.0` even though the product protocol is `0.1`.
- Backward-compatible additive behavior requires a new schema artifact/version; released files are
  immutable.
- Breaking changes require a new major schema version and protocol compatibility decision.
- Unknown request/result schema versions fail closed. Unknown accepted event schemas remain
  preservable as opaque data.
- Golden fixtures for every released version remain readable for the support window stated in the
  release manifest.

## Errors and edge cases

- Duplicate `$id`, path, or digest entry blocks build/startup.
- Runtime resolver lookup of an unknown `$ref` fails locally; it never fetches the URL.
- A model-generated candidate that widens a frozen schema fails CI even if positive tests pass.
- A frozen schema that no longer matches its model fails parity startup before stdin is accepted.
- Schema validation errors are mapped to bounded allowlisted locations/reasons; input values and
  arbitrary validator messages never cross the public error boundary.
- `format` checks are explicitly registered and tested; unsupported formats are not silently
  treated as annotations.

## Invariants

1. Public schema bytes are reviewed, immutable release artifacts.
2. Source, sdist, wheel, and installed copies are byte-identical.
3. Every public model has one owning schema; no CLI-only or MCP-only shadow contract exists.
4. All outputs admit the nested last-resort error result.
5. Runtime schema resolution is offline and closed over the manifest.
6. Raw JSON safety constraints remain enforced before schema/model validation.

## Tests

- Positive, negative, boundary, unknown-field, bad-version, pattern, max-size, and union-disjointness
  vectors for every artifact.
- Model→candidate→frozen parity; frozen→validator→model agreement for every fixture.
- Six fallback-admission tests and MCP tool-discovery schema snapshots.
- Offline `$ref` resolution with DNS/socket denial.
- Released-old-schema backward-read corpus.
- Packaging byte-parity and manifest corruption/missing/extra/duplicate cases.

## Open questions

None.
