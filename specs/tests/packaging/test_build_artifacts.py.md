# tests/packaging/test_build_artifacts.py — clean double-build and candidate identity

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** repository build metadata, lockfile,
resource scripts and release workflow specs | **Imported by:** package/release construction gate

## Purpose

Prove sdist/wheel are built from a clean public export with locked tools/dependencies and characterize
the exact reproducibility scope before any artifact is tested or published.

## Public surface

Cases build twice from independently created normalized source exports, compare artifact inventory/
metadata/bytes, verify build isolation/no source shortcuts, and produce an immutable candidate
manifest used by sibling packaging tests.

## Behavior

Export only allowlisted tracked public files at exact commit/tag into two different canary-bearing
paths. Verify clean lock/schema/resource/boundary gates, then invoke pinned `uv build --no-sources`
with fixed locale/timezone and no private index/path. Capture build tool/Python/OS/arch and output
digests; dependency inputs come from locked hashes.

Require one expected sdist and wheel per target policy, valid names/tags/versions, no unexpected
build output. Compare archive member sets, owned file bytes, metadata fields, ordering/timestamps/
permissions, and whole-artifact digest according to frozen reproducible-build policy. Any permitted
third-party metadata variance is enumerated field-by-field; arbitrary byte ignore is forbidden.

Build subprocess/network trace must not access checkout parent, home, private URL, Git metadata not
declared as provenance, or undeclared package source. Candidate manifest binds selected bytes.

## Errors and edge cases

- Dirty export, lock drift, generated drift, second-build mismatch outside policy, build warning
  affecting identity, or missing wheel fails.
- A source-tree import or local path embedded in output fails even when build succeeds.
- Build timeout/outage is incomplete evidence, never reproducible pass.

## Invariants

1. All downstream tests consume one digest-bound candidate set.
2. Build inputs are public, locked, clean, and path-independent.
3. Reproducibility exceptions are explicit and bounded.
4. Test never modifies source or publishes output.

## Tests

Negative fixtures vary checkout path, hash seed, timestamp, dirty file, resource byte, private index,
and unpinned build tool. Only approved normalized dimensions may preserve pass.

## Open questions

None.

E-008 is the sole central reproducibility gate.
