# src/yoetz_core/resources/schemas/common/operation-result-1.0.0.schema.json — installed byte-identical schema copy

**Wave:** A/F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/schemas/common/operation-result-1.0.0.schema.json.md`,
`specs/src/yoetz_core/resources/manifest.json.md` | **Imported by:** packaging and runtime schema
verification

## Purpose

Specify the packaged copy of the reviewed operation-result wrapper schema. The installed file
mirrors the reviewed source byte-for-byte.

## Public surface

- Logical resource: `schemas/common/operation-result-1.0.0.schema.json`.
- Installed package path: `src/yoetz_core/resources/schemas/common/operation-result-1.0.0.schema.json`.

## Behavior

The build copies the reviewed root schema into the package resource tree unchanged. Runtime loads
verify manifest size and digest before this schema is used.

## Errors and edge cases

- Missing or digest-mismatched bytes fail packaging or startup verification.
- Traversal, symlink, or unexpected extra resource files fail closed.

## Invariants

1. Installed bytes equal reviewed bytes.
2. No runtime network or local regeneration is allowed.
3. The schema stays owned by the reviewed source file.

## Tests

- `specs/tests/packaging.md`
- `specs/tests/conformance/protocol/test_frozen_schemas.py.md`

## Open questions

None.
