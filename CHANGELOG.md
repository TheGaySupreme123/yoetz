# Changelog

All notable user-visible changes to Yoetz are documented in this file. Format is a lightweight,
project-native heading style: one permanent `Unreleased` section above reverse-chronological
released versions.

## Unreleased

### Added

- First-party Codex **live observation and advice** as a required v0.1 capability (ADR-010
  amendment): dual-source ingest (hooks primary + selective session-stream reconciliation), local
  `ObservationPort` control (`yoetz observe status|grant|pause|resume|revoke|reconcile`), unified
  `yoetz hooks observe`, project-level observation consent via private workspace commitment,
  automatic session↔task attachment without depending on MCP `start`, descriptor-safe workspace
  inspection, approved-check runner, and deterministic `AdviceSnapshot` guidance (optional semantic
  review remains additive). Still exactly six MCP tools; observation is CLI/service control only.
  Sensitive evidence stays encrypted; no unencrypted transcript spool; `hook_observed` requires real
  observation evidence under active consent.

- TOML alternate settings surface and owner-declared OpenAI-compatible endpoints (ADR-014 /
  issue #2): `config.toml` may bind Official OpenAI (`openai-responses`) or
  `owner-declared-openai-responses` with constrained `[provider.owner_declared_endpoint].https_origin`
  (HTTPS host+optional port only; no secrets, headers, or free `base_url`). Wizard and menu collect
  the same nonsecret choice; `yoetz provider endpoint` writes it. Owner-declared data-use defaults
  to `unknown` (never inherits `assisted`). Privacy desired-state TOML via
  `yoetz privacy export-desired` / `apply-desired` classifies tighten vs widen and never silently
  widens egress. Credentials, vault unlock, MCP registration, and widening decide remain
  ceremony-only.

- Interactive control menu (ADR-013): bare `yoetz` on an interactive terminal now opens a
  navigable menu (first-run still gets the setup wizard once, then lands in the menu), and the
  new `yoetz menu` command opens it explicitly. The menu shows a status overview (service
  reachability, vault mode, Codex MCP registration, first-run posture) and dispatches to the
  existing operations — setup wizard, harness MCP/skill integration, provider-credential
  ceremonies, privacy posture reads, and service unlock/lock/stop — with every preview/confirm
  gate and confidential ceremony unchanged. Non-TTY, piped, and CI invocations keep the
  historical help output byte-for-byte.

- First-run setup wizard (ADR-012): bare `yoetz` on an interactive terminal with no completion
  marker launches `yoetz setup run` — Codex PATH discovery with an explicit choice when several
  installs exist, preview-and-confirm MCP registration (`codex mcp get` first; foreign entries
  preserved, never replaced; success verified by re-reading state), a service reachability check,
  and printed next steps for the privacy-setup and provider-credential ceremonies, which remain
  human-driven. `yoetz setup status` reports the same posture read-only; every non-TTY bare
  invocation still prints help.
- `yoetz integrate <harness> mcp status|preview|install`: digest-bound, preview-gated MCP server
  registration as a first-class command, backed by the new sibling `HarnessMcpPort` and Codex
  discovery/registration adapters.
- Publish-ready npm launcher at `support/npm-launcher/` for a future `npx yoetz`: a
  dependency-free delegator to the exact pinned `uvx yoetz==<version>`, kept deliberately
  unpublished (`"private": true`) until a separate release decision.
- A README Getting started section documenting the install and first-run path.

### Changed

- Strengthened contribution intake: issue-first process with duplicate search, design gates for
  protocol/privacy/storage/release/ADR work, mandatory PR checklist, and required disposition of
  human and code-review-agent comments (`CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`,
  `.github/pull_request_template.md`).
- Added root `AGENTS.md`, root `CODEOWNERS` for trust boundaries, and a SECURITY threat-model /
  out-of-scope table.

## 0.1.0 — Public alpha

Initial public alpha release.

### Added

- The six-operation protocol (`start`, `publish_work`, `check`, `respond`, `status`, `receipt`) over
  both the CLI and MCP, with identical request/result contracts and a shared canonical
  encoding/idempotency model.
- A persistent, per-user local service that is the sole owner of encryption keys, decrypted state,
  and SQLite writer connections, reached over an authenticated local control protocol; CLI and MCP
  are bounded clients of it.
- Local encrypted object storage (`yoetz-object/1`) and an installation vault with OS-keyring and
  explicit passphrase initialization modes.
- Generation-fenced single-writer durability for the installation catalog and every task bundle,
  built on APSW/SQLite with WAL and verified build/PRAGMA checks.
- A centrally enforced privacy and data-egress protocol: classification, policy resolution, local
  minimization/redaction/secret scanning, optional human preview/approval, and a structural
  `EgressReceipt` for every reserved decision and physical attempt. The default installation is
  zero-egress (`local_only`, global network ceiling off, all five channels disabled).
- An optional, privacy-gated semantic review path behind the same gateway, with a reviewed OpenAI
  Responses adapter, a local-model AF_UNIX profile, and a scripted fake provider for testing.
- Codex integration as the first harness adapter: an explicit trusted-project skill install/status/
  remove flow, an MCP stdio bridge, and a JSONL transcript importer for an exact tested Codex
  version range.
- Backup, restore, and forward-only migration support with frontier-pinned manifests and verified
  route switches; see [`docs/runbooks/`](docs/runbooks/) for the operator procedures.
- Public protocol documentation under [`docs/protocol/`](docs/protocol/) and the evidence-bound
  claim map at [`docs/public-claims.json`](docs/public-claims.json).

### Known limitations

- Independent security review of the vault, key hierarchy, and privacy gateway is a release gate
  that has not yet completed — see `docs/public-claims.json` for exactly which claims currently have
  evidence.
- v0.1 ships no production transport for the four non-LLM egress channels (telemetry, crash
  diagnostics, update checks, capability testing); they exist only as denied policy vocabulary.
- Native `launchd`/`systemd-user` service installation and headless passphrase unlock are not
  included; see [`docs/protocol/local-service-security.md`](docs/protocol/local-service-security.md).
- The advertised platform matrix is macOS 11.0+ arm64 and glibc 2.28+ x86-64 only; other platforms
  are untested.

### Security

- This is the first public release; see [`SECURITY.md`](SECURITY.md) for how to report a
  vulnerability. There is no prior version to carry a security fix forward from.
