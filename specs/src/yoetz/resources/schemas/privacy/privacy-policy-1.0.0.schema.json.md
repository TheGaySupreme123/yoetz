# src/yoetz/resources/schemas/privacy/privacy-policy-1.0.0.schema.json — installed privacy-policy schema copy

**Wave:** B/C/E/F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports
(spec-tree):** `schemas/privacy/privacy-policy-1.0.0.schema.json.md`, package resource manifest
**Imported by:** installed policy validation, setup, conformance and packaging tests

## Purpose

Specify the installed, byte-identical copy of the reviewed effective privacy-policy schema.

## Public surface

- Logical resource: `schemas/privacy/privacy-policy-1.0.0.schema.json`.
- Installed path: `src/yoetz/resources/schemas/privacy/privacy-policy-1.0.0.schema.json`.
- Media type and `$id` equal the reviewed root artifact.

## Behavior

The build copies root schema bytes unchanged and records path, size and SHA-256 in both schema and
installed-resource manifests. Runtime resolves it only from the verified local resource registry;
it cannot generate, fetch, widen, or patch the schema.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, size/digest-mismatched, or semantically divergent bytes
block build, installation verification, and service readiness before policy is accepted.

## Invariants

1. Installed bytes equal reviewed root bytes.
2. Resolution is offline and manifest-closed.
3. The root artifact remains semantic owner.

## Tests

`tests/conformance/protocol/test_frozen_schemas.py`,
`tests/conformance/compatibility/test_resource_manifest.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`.

## Open questions

None.
