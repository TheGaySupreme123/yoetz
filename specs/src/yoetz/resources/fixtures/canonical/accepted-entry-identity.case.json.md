# src/yoetz/resources/fixtures/canonical/accepted-entry-identity.case.json — installed canonical fixture copy

**Wave:** A/F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/fixtures/README.md`, `specs/src/yoetz/resources/manifest.json.md` | **Imported by:**
canonical-vector tests and packaging parity checks

## Purpose

Specify the packaged byte-identical copy of the reviewed accepted-entry identity canonical fixture.

## Public surface

- Logical resource: `fixtures/canonical/accepted-entry-identity.case.json`.
- Installed package path:
  `src/yoetz/resources/fixtures/canonical/accepted-entry-identity.case.json`.

## Behavior

The build copies the reviewed root fixture byte-for-byte into the package resource tree. The
installed file remains canonical JSON and participates in the reviewed v0.1 runtime compatibility
subset.

## Errors and edge cases

- Digest mismatch, missing file, traversal path, or unexpected extra resource fails closed.
- The fixture must not be regenerated at runtime or normalized from a different source copy.

## Invariants

1. Source and packaged bytes are identical.
2. No runtime generation or network access is involved.
3. The fixture belongs to the reviewed v0.1 installed canonical subset.

## Tests

- `specs/tests/unit/protocol/test_canonical_vectors.py.md`
- `specs/tests/property/test_canonical_properties.py.md`
- `specs/tests/packaging/test_resource_byte_parity.py.md`

## Open questions

None.
