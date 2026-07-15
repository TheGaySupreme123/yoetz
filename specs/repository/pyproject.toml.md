# pyproject.toml — declarative build, package, and tool configuration

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/version.md`, `src/yoetz/__init__.md`, `tests/packaging.md`,
`tests/subprocess.md`, `schemas/README.md`
**Imported by:** the release build, editable installs, `uv`, packaging tests, and metadata probes

## Purpose

This file is the canonical declarative source for building, installing, and packaging the public
Python distribution. It tells the build backend what the project is, where the package lives,
which Python versions are supported, which optional capabilities exist, and which files are
intended to ship.

Without this file, the release process would need ad hoc setup logic and the installed package
identity could drift from the checked-in source tree.

## Public surface

The file must define, at minimum:

- project metadata for the `yoetz` distribution;
- `license = "Apache-2.0"` as the exact SPDX license expression;
- the Python version support floor/ceiling for v0.1;
- the `src/` package layout;
- the build backend and build-system requirements;
- runtime dependency declarations;
- optional dependency groups for non-default capabilities;
- console-script entry points and module invocation parity;
- package-data inclusion for `py.typed` and every entry in the reviewed 69-file runtime resource
  manifest: schemas, canonical fixtures, migrations, Codex skill files, and the runtime-support
  allowlist;
- tool configuration used by the release and test pipeline when that configuration is part of the
  public build contract.

## Behavior

`pyproject.toml` is authoritative for packaging metadata. It must describe the package in a way
that yields the same installed identity the runtime reports through `__init__.__version__` and the
version manifest.

The file must keep the build contract declarative:

- the build backend must be reproducible and compatible with `uv`-driven builds;
- the package must install from the `src/` layout only;
- the project metadata must advertise the package name and supported Python range;
- the wheel and sdist must include exactly the public files assigned to each artifact by the
  resource-manifest and packaging specs; every installed runtime resource is manifest-bound;
- the release artifact must exclude private planning docs, transcripts, tests, and other
  non-public authoring material.

The dependency section must be consistent with the import graph in the spec tree. In particular,
the runtime dependencies required by the public package, the optional capability groups, and the
build/test tooling declared here must align with the behavior described in the application, CLI,
adapter, and packaging specs.

The v0.1 standard runtime includes direct pinned `cryptography` for AES-GCM, RFC 3394 AES Key Wrap,
HKDF and HMAC, plus the approved `keyring`/platform secure-backend stack. Exact versions refresh at
E-001/release lock and every advertised wheel must support the frozen known-answer suite. Optional
dependency groups are exactly `semantic-openai` and `portable-recovery`, as frozen by ADR-007.
Python build/test/Ruff configuration lives in
`pyproject.toml`; the development-only npm Pyright pin and invocation live in the separate root
`package.json` and lock so Node never becomes a runtime dependency.

The file may include tool tables for linting, formatting, or testing only when those tools are part
of the public release contract. Tool config does not override the file-level specs; it only makes
the build and test environment reproducible.

## Errors and edge cases

- Missing or conflicting project metadata blocks a build.
- A dependency declared here but excluded from the release artifact is a packaging defect.
- A file that is required by the package metadata but absent from the source tree fails the
  packaging gate.
- Tool configuration must not smuggle private repo-specific assumptions into the public build.

## Invariants

1. `pyproject.toml` is the single declarative source of truth for the release build.
2. The installed distribution identity must match the runtime version manifest.
3. Packaging metadata does not depend on local checkout state.
4. Public resources are included explicitly, not by accident.
5. Optional capabilities remain optional after build and install.

## Tests

- `tests/packaging/test_build_artifacts.py` — build backend, project metadata, and console entry
  point validation.
- `tests/packaging/test_wheel_and_sdist_contents.py` — package-data inclusion and exclusion rules.
- `tests/packaging/test_version_manifest.py` — installed metadata matches the runtime version
  manifest.
- `tests/subprocess/test_module_entrypoint_parity.py` — installed invocation paths are consistent.

## Open questions

None. Apache-2.0 and the development-only npm Pyright choice are frozen; E-001 remains the exact
development-toolchain version gate.
