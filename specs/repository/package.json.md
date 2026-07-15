# package.json — development-only official Pyright tool declaration

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** `pyproject.toml.md`, `uv.lock.md` |
**Imported by:** local type-check command, PR CI, release evidence

## Purpose

Pin the official npm distribution of Pyright without introducing a JavaScript runtime, build,
package, or production dependency into Yoetz.

## Public surface

A private npm package manifest with `name: "yoetz-dev-tools"`, `private: true`, no publish
configuration, one `typecheck` script invoking `pyright`, and exactly one development dependency:
the ADR-007 Pyright version refreshed to the newest supported stable release at implementation lock.

## Behavior

`npm ci` installs the checked lock exactly for development/CI. `npm run typecheck` checks the
Python source and tests using the strict Pyright configuration owned by `pyproject.toml`; it does
not generate files or access the network after dependencies are installed. There are no runtime
dependencies, lifecycle hooks, workspaces, bundled files, or package publication fields.

## Errors and edge cases

An unpinned range, added dependency, lifecycle script, registry override, package publication
setting, or mismatch with `package-lock.json` fails the dependency-policy gate. npm is optional for
end users and absent from wheel/runtime requirements. The package is never published as `yoetz`
and does not provide an application `bin`; a future `npx yoetz` launcher requires a separate owner
spec and release contract.

## Invariants

This file exists solely for the official Pyright CLI. It cannot become a second application
toolchain, execute generated code, or alter released Python artifacts.

## Tests

`tests/packaging/test_dependency_lock_and_licenses.py`, PR type-check workflow, and
`tests/packaging/test_wheel_and_sdist_contents.py` (sdist policy and wheel exclusion).

## Open questions

None. F-005 is resolved in favor of the official development-only npm Pyright package; E-001
freezes its exact supported release.
