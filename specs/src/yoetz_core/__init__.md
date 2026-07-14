# src/yoetz_core/__init__.py — side-effect-free package boundary

**Wave:** F | **ADRs:** ADR-007 | **Imports (spec-tree):** `version.md` (lazy/type-only) |
**Imported by:** users inspecting the installed package

## Purpose

Define the deliberately tiny import surface of `yoetz_core`. Importing the package must be safe in
build tools, metadata probes, read-only diagnostics, unsupported runtimes, and applications that do
not intend to start the ledger. It must not instantiate the runtime or imply that a writable storage
configuration is supported.

## Public surface

- `__version__: str` — the installed distribution version obtained from package metadata.
- `get_version_manifest() -> VersionManifest` — lazily imports and delegates to
  `version.build_version_manifest()`.
- `__all__ = ("__version__", "get_version_manifest")`.
- The distribution includes a zero-byte `py.typed` marker beside this file; it is a packaged
  resource, not an importable name.

No domain types, adapters, configuration globals, SDK objects, or singleton application instance
are re-exported.

## Behavior

At module import, resolve only the installed package version through `importlib.metadata.version`.
When running from a valid editable/source checkout whose distribution metadata is unavailable, use
the fixed development marker `"0.0.0+uninstalled"`; never inspect Git, spawn a process, or read a
project file to synthesize a version.

`get_version_manifest` performs its `version` module import inside the function so ordinary package
import does not import APSW, MCP, Pydantic, keyring, provider SDKs, configuration, logging, or
platform probing. The returned manifest is descriptive; write-readiness remains a separate startup
gate.

## Errors and edge cases

- Unexpected metadata failures other than distribution-not-found propagate as bounded import
  failures; they are not caught and stringified.
- An unsupported Python patch or platform may still import this module and inspect `__version__`.
- Calling `get_version_manifest` may return unsupported/unavailable capability fields, but it must
  never open a database, access a key store, read user config, or perform network I/O.

## Invariants

1. `import yoetz_core` has no filesystem writes, network calls, subprocesses, logging configuration,
   background threads, event loops, or environment mutation.
2. The import surface does not expose a second route around application validation.
3. `__version__` is the distribution identity, not the protocol or engine identity.
4. Import succeeds on any CPython 3.14 environment even when write support will later fail closed.

## Tests

- `specs/tests/unit.md`: exported names and development fallback.
- `specs/tests/subprocess.md`: import under empty HOME, denied network, readonly cwd, missing
  optional extras, hostile config files, and unsupported platform markers; assert no created files,
  stdout, or stderr.
- `specs/tests/packaging.md`: wheel and sdist expose identical `__version__`, `__all__`, and
  `py.typed`; installed import does not depend on the source checkout.

## Open questions

None.
