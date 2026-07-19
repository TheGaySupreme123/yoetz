# src/yoetz/resources/support/runtime-support.json — installed runtime support allowlist

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009, ADR-010, ADR-011 | **Imports (spec-tree):**
`support/runtime-support.json.md`, `resources/manifest.json.md` | **Imported by:** installed
version/startup gates and packaging tests

## Purpose

Package the exact reviewed runtime-support allowlist so an installed artifact never consults a
checkout, network, or mutable user file to decide whether writes or optional integrations are
supported.

## Public surface

The future file is byte-identical to root `support/runtime-support.json` and has the complete
`yoetz.runtime-support/1` shape, exact cell sets, typed external/absent evidence references,
limitations, and self-digest
owned by that source spec, including exact trigger-hook and structural subject-state cells.

## Behavior

The resource generator copies reviewed source bytes without decoding or newline normalization and
registers this path as `runtime_support` in the package resource manifest. Its size and byte digest
are deliberately excluded only from the stable resource-set digest material, while remaining
mandatory in the concrete manifest entry. Runtime loads it through
`importlib.resources`, verifies resource size/SHA-256 and the inner canonical self-digest, then
constructs immutable support values. It performs no fallback to root source and no online refresh.

## Errors and edge cases

Missing, extra, mismatched, noncanonical, wrong-artifact, unknown-version, or case-colliding bytes
make write admission `read_only_unsupported`/`STORAGE_UNSAFE` as the startup gate specifies. Version
inspection still returns a bounded manifest-corrupt limitation and never executes damaged data.

## Invariants

1. Source, sdist, wheel, and installed support bytes are identical.
2. The packaged copy cannot widen or override the reviewed root source.
3. Loading is offline, bounded, path-safe, and side-effect-free.
4. The support manifest is evidence identity, not a cryptographic signature.

## Tests

`tests/packaging/test_resource_byte_parity.py`, `tests/packaging/test_platform_and_sqlite_gate.py`,
`tests/packaging/test_version_manifest.py`, and `tests/conformance/compatibility/test_resource_manifest.py`.

## Open questions

None.
