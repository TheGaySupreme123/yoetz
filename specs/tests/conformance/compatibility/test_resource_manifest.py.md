# tests/conformance/compatibility/test_resource_manifest.py — resource manifest parity

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/version.md`, `specs/schemas/README.md`, `specs/fixtures/README.md`
**Imported by:** conformance compatibility tests

## Purpose

Prove the packaged resource manifest matches the checked-in reviewed resource set byte-for-byte.

## Public surface

- `test_root_resource_bytes_match_manifest` — installed bytes equal reviewed source bytes.
- `test_missing_extra_duplicate_and_traversal_cases_fail` — manifest validation is closed.
- `test_public_resource_list_matches_release_artifact` — the public set is complete and stable.

## Behavior

The test asserts:

- resource names, sizes, and SHA-256 digests match the manifest;
- the inventory has exactly 71 entries: 52 JSON Schemas, one schema manifest, nine canonical
  fixtures, two migrations, the Codex skill plus compatibility manifest, four harness-neutral
  guidance resources, and one runtime-support allowlist;
- missing, extra, duplicate, or traversal-named resources fail;
- the published resource set is exactly the reviewed set, no more and no less.

## Errors and edge cases

- A byte mismatch that is tolerated fails the test.

## Invariants

1. Resource parity is exact.
2. Manifest validation is closed.
3. Public resources are stable.

## Tests

- `tests/conformance/compatibility/test_resource_manifest.py`

## Open questions

None.
