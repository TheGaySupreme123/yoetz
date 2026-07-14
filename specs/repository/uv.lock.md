# uv.lock — frozen dependency resolution and artifact identity

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/version.md`, `tests/packaging.md`, `tests/subprocess.md`
**Imported by:** `uv`, release builds, reproducible install tests, and offline verification

## Purpose

This file freezes the exact dependency graph used to build and install the project. It is the
release-time answer to “what did we actually ship?” and the install-time answer to “what bytes and
versions are expected on a clean machine?”

## Public surface

The file contains a canonical lock graph with:

- resolved package names and versions;
- source and wheel hashes;
- platform markers and target tags;
- dependency edges;
- extras/groups used by the supported install modes;
- any build-time packages needed to reproduce the wheel and sdist.

## Behavior

`uv.lock` must be machine-generated, reviewable, and reproducible. It is not hand-edited. It must
resolve exactly for the supported targets and produce the same install set the release tests use.

The lock file serves three separate jobs:

1. reproducible build input for the release pipeline;
2. offline install source for clean-machine verification;
3. supply-chain evidence for packaging and provenance tests.

The locked graph must remain compatible with the version and packaging manifests:

- every runtime dependency pin must agree with the build artifacts;
- every optional dependency group must resolve to the same package identities across supported
  targets;
- hash mismatches or source drift must fail the release rather than being silently accepted;
- no unreviewed transitive dependency may appear in a release build.

The file may contain multiple target-specific resolution entries, but each target entry must be
deterministic and tied to a reviewed support matrix. If a platform or Python patch level is not in
scope, the lock should make that limitation explicit rather than pretending support.

One committed `uv.lock` covers the reviewed runtime, development, release, and both optional
capability groups (`semantic-openai`, `portable-recovery`). Target markers partition the one graph;
v0.1 does not maintain per-mode lockfiles.

## Errors and edge cases

- A missing lockfile blocks reproducible release builds.
- A dependency hash mismatch blocks offline reinstall validation.
- A package that resolves on one supported target but not another must be treated as a support
  decision, not as a flaky install.
- A lockfile that drifts from the release manifest or wheel contents is invalid.

## Invariants

1. The lockfile is the frozen install truth for the release set.
2. The same lock graph produces the same artifact identities.
3. The lockfile does not widen support implicitly.
4. Offline verification must be possible from the lock plus captured artifacts.
5. The lockfile is reviewed as release evidence, not as an implementation convenience.

## Tests

- `tests/packaging/test_dependency_lock_and_licenses.py` — lock resolution and hash parity.
- `tests/packaging/test_offline_reinstall.py` — clean install and reinstall from locked inputs.
- `tests/packaging/test_checksums_sbom_and_provenance.py` — lock graph appears in supply-chain
  evidence.

## Open questions

None.

E-001 is the sole central dependency-pin gate.
