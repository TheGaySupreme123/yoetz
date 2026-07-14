# src/yoetz_core/resources/schemas/common/actor-assertion-1.0.0.schema.json — installed byte-identical schema copy

**Wave:** A/F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/schemas/common/actor-assertion-1.0.0.schema.json.md`,
`specs/src/yoetz_core/resources/manifest.json.md` | **Imported by:** packaging and runtime schema
verification

## Purpose

Specify the packaged copy of the reviewed `actor_assertion` schema. The installed file is a
byte-for-byte mirror of the root schema artifact.

## Public surface

- Logical resource: `schemas/common/actor-assertion-1.0.0.schema.json`.
- Installed package path: `src/yoetz_core/resources/schemas/common/actor-assertion-1.0.0.schema.json`.

## Behavior

The build copies the reviewed root schema into the package resource tree unchanged. Runtime loads
verify the manifest digest and size before decoding or validating against this schema.

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
