# Changelog

All notable user-visible changes to Yoetz are documented in this file. Format is a lightweight,
project-native heading style: one permanent `Unreleased` section above reverse-chronological
released versions.

## Unreleased

No unreleased changes yet.

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
