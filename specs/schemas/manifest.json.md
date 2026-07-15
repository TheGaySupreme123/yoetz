# schemas/manifest.json — canonical schema inventory and byte-parity ledger

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz/protocol/schemas.md`, `src/yoetz/version.md`, `specs/schemas/README.md`
**Imported by:** schema generation, release packaging, and parity tests

## Purpose

This file describes the canonical manifest that lists every reviewed schema artifact shipped in the
public release set. It is the inventory root for all schema bytes, IDs, sizes, and ownership data.

## Public surface

- `manifest.json` — canonical JSON object containing the reviewed schema inventory.

## Behavior

The manifest is canonical JSON and records one entry per released schema artifact. Each entry
includes:

- relative POSIX path;
- `$id`;
- schema version;
- media type;
- exact byte size;
- SHA-256 digest;
- owning Python boundary model;
- artifact role (`MCP input`, `MCP output`, `persisted-envelope`, `configuration`,
  `privacy-policy`, `outbound-case`, `privacy-audit`, `setup-contract`, `local-control`, or
  `service-status`).

The v0.1 manifest lists exactly 52 `*.schema.json` members: the original 43 reviewed schemas plus
four privacy and five local-service/control schemas. The manifest never lists itself.

The manifest is the release-time source of truth for schema parity. It is produced from the reviewed
checked-in artifacts, not from runtime generation. The manifest must remain stable across source,
sdist, wheel, and installed copies.

## Errors and edge cases

- A missing or duplicate schema entry blocks release.
- A path/digest mismatch blocks packaging parity.
- A manifest that is not canonical JSON fails the contract.

## Invariants

1. The manifest enumerates the reviewed schema set exactly.
2. Byte size and SHA-256 are part of the contract.
3. Runtime never rewrites the manifest.

## Tests

- `tests/packaging/test_build_artifacts.py`
- `tests/packaging/test_dependency_lock_and_licenses.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
