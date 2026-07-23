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
- package-data inclusion for `py.typed`, all 73 entries in the reviewed runtime resource manifest,
  and the manifest itself (74 runtime resource files total): schemas, canonical fixtures,
  migrations, Codex skill files, and the runtime-support allowlist;
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

The dependency section must be consistent with the import graph in the spec tree. At the
2026-07-17 implementation lock it has the following implementable shape (ordering is lexical):

- `[build-system].requires = ["uv_build>=0.11.29,<0.12"]` and
  `[build-system].build-backend = "uv_build"`;
- `[project].requires-python = ">=3.14,<3.15"`;
- `[project].dependencies` contains exactly `anyio==4.14.2`, `apsw==3.53.3.1`,
  `cryptography==49.0.0`, `jsonschema==4.26.0`, `keyring==25.7.0`, `mcp==1.28.1`,
  `platformdirs==4.10.0`, `pydantic==2.13.4`, and `typer==0.27.0`;
- `[project.optional-dependencies].semantic-openai` contains exactly `httpx==0.28.1` and
  `openai==2.46.0`;
- `[project.optional-dependencies].portable-recovery` contains exactly
  `argon2-cffi==25.1.0`;
- `[dependency-groups].dev` contains exactly `hypothesis==6.156.6`, `pytest==9.1.1`,
  `pytest-timeout==2.4.0`, and `ruff==0.15.22`;
- `[tool.uv].required-version = "==0.11.29"` and `[tool.uv].prerelease = "disallow"`.

The standard project table also declares the `yoetz = "yoetz.cli.app:main"` console script. The
Ruff tables freeze line length 100. `[tool.pyright]` freezes `pythonVersion = "3.14"`,
`typeCheckingMode = "strict"`, `venvPath = "."`, and `venv = ".venv"` so the npm-owned Pyright
process resolves the same uv-managed project and development dependencies exercised by pytest;
the Pyright executable and version remain npm-owned. Dependency groups cannot be merged into one
another, and exact pins cannot be weakened to compatible or minimum ranges. The runtime
dependencies, optional capability groups, and build/test tooling declared here must align with the
behavior described in the application, CLI, adapter, and packaging specs.

The v0.1 standard runtime includes direct pinned `cryptography` for AES-GCM, RFC 3394 AES Key Wrap,
HKDF and HMAC, plus the approved `keyring`/platform secure-backend stack resolved in `uv.lock`.
Exact versions refresh at E-001/release lock and every advertised wheel must support the frozen
known-answer suite. Optional dependency groups are exactly `semantic-openai` and
`portable-recovery`, as frozen by ADR-007.
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

None. Apache-2.0 and the development-only npm Pyright choice are frozen. The direct identities
above are the 2026-07-17 implementation lock; E-001 remains the exact release-refresh and
toolchain-compatibility gate.
