# src/yoetz_core/resources/schemas/privacy/outbound-case-1.0.0.schema.json — installed outbound-case schema copy

**Wave:** B/E/F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports
(spec-tree):** `schemas/privacy/outbound-case-1.0.0.schema.json.md`, package resource manifest
**Imported by:** installed egress gateway, provider adapters, conformance and packaging tests

## Purpose

Specify the installed, byte-identical copy of the reviewed bounded outbound-case schema.

## Public surface

- Logical resource: `schemas/privacy/outbound-case-1.0.0.schema.json`.
- Installed path: `src/yoetz_core/resources/schemas/privacy/outbound-case-1.0.0.schema.json`.
- Media type and `$id` equal the reviewed root artifact.

## Behavior

The build copies reviewed root bytes unchanged and manifests exact path, size, and SHA-256. The
trusted service validates final cases with this verified local artifact before an adapter can see
them. Runtime generation and network `$ref` resolution are forbidden.

## Errors and edge cases

Missing, extra, symlinked, digest-mismatched, noncanonical, or divergent bytes fail service startup
before dispatch. No permissive fallback validator is allowed.

## Invariants

1. Installed bytes equal reviewed root bytes.
2. Validation is offline and manifest-closed.
3. The root artifact remains semantic owner.

## Tests

`tests/conformance/protocol/test_frozen_schemas.py`,
`tests/integration/privacy/test_egress_gateway.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`.

## Open questions

None.
