# ADR-007 — Packaging, platforms, and release policy

**Status:** Working decision for spec drafting (2026-07-13). Ratification requires clean-VM
install/upgrade/uninstall evidence from built artifacts.
**Owning public specs:** repository-file specs, `specs/src/yoetz_core/version.md`, packaged resource
specs, packaging/capability tests, and release workflow/script specs.

## Decisions

1. **Interpreter:** one exact CPython line, `>=3.14,<3.15` in metadata, tested/advertised patch
   `3.14.6` (refreshed at release lock). Writes gate on the exact tested
   patch/distribution/OS/ABI allowlist; import/`version`/read-only inspection work on any 3.14.
2. **Project & build tooling:** `uv` (pinned `0.11.28` at lock) for env/lock/run/tool-install/
   build; build backend `uv_build`; committed `uv.lock`; conventional `src/` layout.
3. **CLI framework:** Typer (pinned `0.26.8`), `pretty_exceptions_enable=False`,
   `no_args_is_help=True`; no Click-internal coupling. Async bridge: exactly one top-level
   `anyio.run(...)` per process entry (CLI command, MCP bridge, or foreground service); no nested
   event-loop helpers.
4. **Console script:** distribution and executable both `yoetz-core`
   (`yoetz_core.cli.app:main`). `python -m yoetz_core` delegates to the same entry.
5. **Wheel strategy:** yoetz-core itself is a pure-Python wheel; platform specificity comes from
   the pinned APSW dependency wheels. Advertised platform matrix v0.1: macOS 11.0+ arm64
   (`macosx_11_0_arm64`) and glibc 2.28+ x86-64 (`manylinux_2_28_x86_64`). No musl, Windows, or
   macOS x86-64 claims. Primary install:
   `uv tool install --managed-python --python 3.14.6 "yoetz-core==0.1.0"`.
6. **Keys and optional extras:** the certified standard install includes direct pinned
   `cryptography` (AES-GCM, RFC 3394 AES Key Wrap, HKDF/HMAC) and `keyring` plus the approved
   macOS/Linux secure-backend dependencies because object/vault crypto and OS-keyring-first service
   startup are v0.1 core behavior, not semantic extras. Optional extras are `semantic-openai` (openai) and
   `portable-recovery` (argon2-cffi). Explicit passphrase-backed vault setup also requires the
   reviewed Argon2id implementation; absence leaves the service locked rather than downgrading.
7. **Type/lint stack:** Ruff `0.15.21` (format+lint, line length 100), official npm Pyright
   `1.1.411` via dev-only `package.json` + `npx --no-install pyright`, strict mode.
8. **Release artifacts:** sdist + wheel, SHA-256 checksums, CycloneDX SBOM via `uv export`,
   dependency lock, support matrix, conformance summary, known limitations, changelog, security
   policy. Sigstore signing deferred until a documented verification command exists (a signature
   without a verifier is not a gate).
9. **Install/upgrade/uninstall:** documented and tested: `uv tool install/upgrade/uninstall`, the
   foreground `yoetz-core service run` entrypoint suitable for a user-selected external
   supervisor, `codex mcp add/remove yoetz`, and data-retention behavior on uninstall. Native
   launchd/systemd-user installer commands are deferred; uninstall never deletes bundles/vault/
   keyring entries.
10. **Diagnostics:** `yoetz-core version --json` emits the full `VersionManifest`; startup safety
    validation is mandatory but the public `doctor` command stays v0.2.

## Consequences

Every dependency update is a reviewed PR + package patch release rerunning contract/storage/
packaging/capability matrices; version pins in the docs are refreshed at ADR acceptance and
again at release lock.
