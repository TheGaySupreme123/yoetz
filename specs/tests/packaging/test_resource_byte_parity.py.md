# tests/packaging/test_resource_byte_parity.py — reviewed/source/embedded/installed resource equality

**Wave:** A/F | **ADRs:** ADR-002, ADR-003, ADR-006, ADR-007 | **Imports (spec-tree):** resource
manifest and verifier specs | **Imported by:** startup/package/release integrity gate

## Purpose

Prove each runtime-read schema, canonical fixture mirror, migration, policy, skill/reference,
compatibility manifest, and template is the exact reviewed byte set in source, package tree, wheel,
and clean install.

## Public surface

Cases compare full inventory, path/source/destination, kind/media/version, size/SHA-256, set
digest, canonical text policy, installed resource API, and corruption/missing/extra/collision
behavior. The canonical fixture subset is the nine reviewed `fixtures/canonical/*.case.json`
resources; the larger adversarial/replay/import/receipt/backward corpora remain test/sdist-only.

## Behavior

Run verifier `--check`, independently recompute manifest from canonical sources, inspect embedded
tree and wheel members, then install candidate and read resources through package APIs from
unrelated cwd. Require four-way byte equality and manifest self/set digests. Exercise
migration/schema/skill/fixture loader to prove verification occurs before decode/execute/use.

Mutation matrix removes/adds/duplicates/one-byte changes resource or manifest; changes newline/BOM;
uses traversal/backslash/case collision/symlink; mismatches source/package path, media kind, size,
version, self digest. Build must fail for source drift; patched installed artifact fails closed
before affected operation and never falls back to checkout/network.

## Errors and edge cases

- Manifest itself is verified separately rather than recursively listed.
- Wheel resource may lack persistent filesystem path; compare bytes via standard traversal.
- Test/private fixture corpora are excluded unless explicitly runtime-read.
- Error evidence cannot print resource content or installation path.

## Invariants

1. One reviewed source byte sequence maps to one installed logical resource.
2. Extra and missing resources both fail.
3. Verification precedes use.
4. Runtime has no source-tree/network replacement path.

## Tests

Run on source, editable, wheel, sdist-rebuilt wheel, and offline-installed artifact across advertised
platforms. Mutation artifacts remain synthetic and unpublished.

## Open questions

None.
