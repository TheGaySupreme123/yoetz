# src/yoetz/resources/schemas/privacy/egress-receipt-1.0.0.schema.json — installed egress-receipt schema copy

**Wave:** B/E/F | **ADRs:** ADR-004, ADR-006, ADR-007, ADR-009 | **Imports
(spec-tree):** `schemas/privacy/egress-receipt-1.0.0.schema.json.md`, package resource manifest
**Imported by:** PrivacyAuditPort adapter, receipt inspection, conformance and packaging tests

## Purpose

Specify the installed, byte-identical copy of the reviewed structural egress-receipt schema.

## Public surface

- Logical resource: `schemas/privacy/egress-receipt-1.0.0.schema.json`.
- Installed path: `src/yoetz/resources/schemas/privacy/egress-receipt-1.0.0.schema.json`.
- Media type and `$id` equal the reviewed root artifact.

## Behavior

The build copies reviewed root bytes unchanged and manifests exact path, size, and SHA-256. All
durable privacy-audit receipt adapters validate against the verified installed artifact, including
receipts without task/session identity.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, digest-mismatched, or semantically divergent bytes fail
startup before outbound dispatch. No task-event schema substitutes for this receipt.

## Invariants

1. Installed bytes equal reviewed root bytes.
2. Resolution is offline and manifest-closed.
3. The root artifact remains semantic owner.

## Tests

`tests/conformance/protocol/test_frozen_schemas.py`,
`tests/integration/privacy/test_egress_gateway.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`.

## Open questions

None.
