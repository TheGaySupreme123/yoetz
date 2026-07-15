# src/yoetz/resources/schemas/privacy/setup-wizard-contract-1.0.0.schema.json — installed privacy-setup schema copy

**Wave:** C/E/F | **ADRs:** ADR-004, ADR-006, ADR-007, ADR-009 | **Imports
(spec-tree):** `schemas/privacy/setup-wizard-contract-1.0.0.schema.json.md`, package resource manifest
**Imported by:** trusted setup renderers, future UI, conformance and packaging tests

## Purpose

Specify the installed, byte-identical copy of the reviewed privacy setup-wizard contract schema.

## Public surface

- Logical resource: `schemas/privacy/setup-wizard-contract-1.0.0.schema.json`.
- Installed path: `src/yoetz/resources/schemas/privacy/setup-wizard-contract-1.0.0.schema.json`.
- Media type and `$id` equal the reviewed root artifact.

## Behavior

The build copies reviewed root bytes unchanged and manifests exact path, size, and SHA-256. Trusted
local setup surfaces use the verified artifact; graphical and CLI renderers cannot carry shadow
fields or looser message branches.

## Errors and edge cases

Missing, extra, symlinked, noncanonical, digest-mismatched, or divergent bytes fail setup readiness.
There is no fallback schema that accepts secrets or arbitrary free text.

## Invariants

1. Installed bytes equal reviewed root bytes.
2. Resolution is offline and manifest-closed.
3. The root artifact remains semantic owner.

## Tests

`tests/conformance/protocol/test_frozen_schemas.py`,
`tests/subprocess/test_service_lock_and_confidential_unlock.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`.

## Open questions

None.
