# src/yoetz/resources/schemas/manifest.json — installed byte-identical schema manifest copy

**Wave:** A/F | **ADRs:** ADR-002, ADR-003, ADR-006, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):**
`specs/schemas/manifest.json.md`, `specs/src/yoetz/resources/manifest.json.md` |
**Imported by:** packaging/release verification and startup integrity checks

## Purpose

Specify the packaged copy of the reviewed schema manifest. It binds the installed schema bytes to
the reviewed root inventory and must remain byte-identical to the source manifest.

## Public surface

- Logical resource: `schemas/manifest.json`.
- Installed package path: `src/yoetz/resources/schemas/manifest.json`.

## Behavior

The build copies the reviewed manifest into the package resource tree without semantic rewriting.
The runtime only reads the installed bytes after the resource-manifest check has passed.

## Errors and edge cases

- Digest mismatch, missing file, traversal path, or unexpected extra resource fails closed.
- A manifest that is not canonical JSON or whose entry set differs from the reviewed source is
  invalid.

## Invariants

1. Packaged bytes equal reviewed source bytes.
2. The installed file never regenerates itself.
3. Manifest parity is checked before schema use.

## Tests

- `specs/tests/packaging.md`
- `specs/tests/conformance/protocol/test_frozen_schemas.py.md`

## Open questions

None.
