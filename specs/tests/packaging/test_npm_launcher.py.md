# tests/packaging/test_npm_launcher.py — npm launcher packaging gate

**Wave:** F | **ADRs:** ADR-007, ADR-012 | **Imports (spec-tree):**
`specs/support/npm-launcher/package.json.md`, `specs/support/npm-launcher/bin/yoetz.js.md` |
**Imported by:** `specs/tests/packaging.md`

## Purpose

Locks the launcher's delegation-only shape and its deliberately unpublished state.

## Public surface

Pytest module; no exports.

## Behavior

Covers: `package.json` has name `yoetz`, `private: true`, the exact `bin` map, the Apache-2.0
license, and none of the dependency/script keys; the launcher version equals
`yoetz.__version__`; `bin/yoetz.js` exists, is executable, and starts with the exact Node
shebang; with a recorded fake `uv`/`uvx` shim on a controlled PATH, the launcher invokes exactly
`yoetz==<version> <argv...>` and propagates the child's exit code; with an empty PATH it exits 1
with stderr guidance naming `uv` and the official install URL. The two Node-executing tests
skip cleanly when `node` is absent, because Node is contributor-only tooling (ADR-007).

## Errors and edge cases

Shims live in `tmp_path`; nothing installs or publishes anything.

## Invariants

1. A dependency key, a `private: false` flip, or a version drift fails this gate.

## Tests

Self; indexed by `specs/tests/packaging.md`.

## Open questions

None.
