# ADR-007 — Packaging, platforms, and release policy

**Status:** Implementation lock (2026-07-17). Release ratification still requires clean-VM
install/upgrade/uninstall evidence from built artifacts and the E-001 release refresh.
**Implemented by:** repository build metadata, `src/yoetz/version.py`, packaged resource
manifests, the packaging/capability suites, and the release workflows under `.github/workflows/`.

## Decisions

1. **Interpreter:** one exact CPython line, `>=3.14,<3.15` in metadata, tested/advertised patch
   `3.14.6` (refreshed at release lock). Writes gate on the exact tested
   patch/distribution/OS/ABI allowlist; import/`version`/read-only inspection work on any 3.14.
2. **Project & build tooling:** `uv==0.11.29` for env/lock/run/tool-install/build; build backend
   requirement `uv_build>=0.11.29,<0.12`; prereleases are disallowed; committed `uv.lock`;
   conventional `src/` layout.
3. **CLI framework:** Typer `0.27.0`, `pretty_exceptions_enable=False`; no Click-internal
   coupling. Async bridge: exactly one top-level `anyio.run(...)` per process entry (CLI command,
   MCP bridge, or foreground service); no nested event-loop helpers. Amended by ADR-012: bare
   invocation prints help in every case except the bounded first-run TTY exception on the root
   command only (interactive terminal, setup marker absent), which launches `yoetz setup run`.
4. **Console script:** distribution and executable both `yoetz`
   (`yoetz.cli.app:main`). `python -m yoetz` delegates to the same entry.
5. **Wheel strategy:** yoetz itself is a pure-Python wheel; platform specificity comes from
   the pinned APSW dependency wheels. Advertised platform matrix v0.1: macOS 11.0+ arm64
   (`macosx_11_0_arm64`) and glibc 2.28+ x86-64 (`manylinux_2_28_x86_64`). No musl, Windows, or
   macOS x86-64 claims. Primary install:
   `uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"`.
6. **Keys, semantic readiness, and compatibility extras:** the certified standard install includes
   direct pinned
   `cryptography` (AES-GCM, RFC 3394 AES Key Wrap, HKDF/HMAC) and `keyring` plus the approved
   macOS/Linux secure-backend dependencies because object/vault crypto and OS-keyring-first service
   startup are v0.1 core behavior. Founder-authorized amendment (2026-07-29): `argon2-cffi`,
   `httpx`, and `openai` are also standard direct dependencies because first run offers both
   passphrase storage and semantic review and must not offer a path the installed artifact cannot
   execute. `semantic-openai` and `portable-recovery` remain compatibility extras for existing
   install commands, with the same exact pins; they add no dependency absent from the standard
   install. A malformed or incomplete environment still fails closed rather than downgrading.
7. **Type/lint stack and npm boundary:** Ruff `0.15.22` (format+lint, line length 100), official
   npm Pyright `1.1.411` via a development-only private `package.json`, strict mode. The locked
   contributor/CI toolchain is Node `26.5.0` with npm `12.0.1`; `npm ci --ignore-scripts` followed
   by `npm run typecheck` is the reproducible invocation. Node/npm are not end-user runtime
   requirements. Amended by ADR-012: the npm launcher now exists at `support/npm-launcher/` with
   its own provenance/delegation/upgrade contract — a dependency-free delegator to
   `uvx yoetz==<version>` — and remains deliberately unpublished (`"private": true`); publishing
   is a separate future release decision.
8. **Release artifacts:** sdist + wheel, SHA-256 checksums, CycloneDX SBOM via `uv export`,
   dependency lock, support matrix, conformance summary, known limitations, changelog, security
   policy. Sigstore signing deferred until a documented verification command exists (a signature
   without a verifier is not a gate).
9. **Install/upgrade/uninstall:** documented and tested: `uv tool install/upgrade/uninstall`, the
   foreground `yoetz service run` entrypoint suitable for a user-selected external
   supervisor, `codex mcp add/remove yoetz`, and data-retention behavior on uninstall. Native
   launchd/systemd-user installer commands are deferred; uninstall never deletes bundles/vault/
   keyring entries. Amended by ADR-012: the `codex mcp get`/`codex mcp add` sequence is also
   available as the preview-gated `yoetz integrate <harness> mcp` commands and the `yoetz setup`
   wizard; the manual commands remain valid and the runbook's preservation rules are unchanged.
10. **Diagnostics:** `yoetz version --json` emits the full `VersionManifest`; startup safety
    validation is mandatory but the public `doctor` command stays v0.2.
11. **Public schema hosting without runtime coupling:** the checked-in `schemas/` tree is mounted
    byte-for-byte at `https://schemas.yoetz.dev/0.1/`; each `$id` is the direct URL formed by
    appending its exact relative file path. Released versioned schema paths are immutable. The
    manifest advances atomically with digest/ETag binding. PR/release gates resolve all refs from
    the local manifest with network denied, and installed Yoetz always uses packaged mirrors;
    hosted availability is independently verified release evidence, never an operational
    dependency.

## Implementation-lock identities

The 2026-07-17 implementation lock freezes the direct dependency declarations below. All are
exact pins except the intentionally bounded build-backend requirement. The generated locks also
freeze every transitive distribution, source, artifact hash, marker, and license.

- Runtime: `anyio==4.14.2`, `apsw==3.53.3.1`, `argon2-cffi==25.1.0`,
  `cryptography==49.0.0`, `httpx==0.28.1`, `jsonschema==4.26.0`, `keyring==25.7.0`,
  `mcp==1.28.1`, `openai==2.46.0`, `platformdirs==4.10.0`, `pydantic==2.13.4`,
  `textual==8.2.8`, and `typer==0.27.0`.
- Compatibility extras: `semantic-openai` repeats `httpx==0.28.1` and `openai==2.46.0`;
  `portable-recovery` repeats `argon2-cffi==25.1.0`.
- Development/test: `hypothesis==6.156.6`, `pytest==9.1.1`,
  `pytest-timeout==2.4.0`, and `ruff==0.15.22`; Pyright remains npm-owned as above.
- Runtime candidate: CPython `3.14.6`, APSW `3.53.3.1`, and SQLite `3.53.3` with exact source ID
  `2026-06-26 20:14:12 d4c0e51e4aeb96955b99185ab9cde75c339e2c29c3f3f12428d364a10d782c62`.

These identities select what implementation and capability testing exercise; they do not create
a supported runtime cell. Only passing, artifact-bound release evidence may populate
`runtime-support.json`.

## Consequences

Every dependency update is a reviewed PR + package patch release rerunning contract/storage/
packaging/capability matrices. E-001 refreshes these implementation pins at release lock and must
record whether each selected identity stayed fixed or changed; a newer version alone never widens
the support allowlist.
