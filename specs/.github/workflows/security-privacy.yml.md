# .github/workflows/security-privacy.yml — dependency, code, artifact, and plaintext-boundary gate

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/scripts/scan_public_boundary.py.md`, `specs/src/yoetz_core/observability/privacy.md`,
`specs/tests/packaging.md` | **Imported by:** PR policy and tagged release

## Purpose

Run the public alpha's security/privacy evidence as separate fail-closed gates: dependency and
license review, static/security tests, migration/resource/workflow hardening, secret/private-boundary
scan, artifact allowlist, and synthetic-canary plaintext sweep through runtime storage/transport/
diagnostic surfaces.

Passing is bounded evidence for the tested threat model, not a declaration of complete security,
formal verification, SOC 2, or absence of all vulnerabilities.

## Public surface

Workflow name `Security and Privacy`. Triggers:

- `pull_request` when code, dependencies, workflows, resources, packaging, security/privacy tests,
  or release policy change;
- weekly schedule on protected default branch;
- `workflow_dispatch` for exact candidate SHA/artifact;
- reusable `workflow_call` from tagged release with immutable artifact digest.

Jobs:

```text
dependency-and-license
static-and-workflow-policy
source-and-artifact-boundary
runtime-plaintext-canary
security-evidence
security-required
```

## Behavior

### Authority and supply-chain posture

Default permissions `contents: read`; fork PRs have no secrets/write/cache promotion. Use no
`pull_request_target`. Full-SHA pin every action and verify locked security tool versions/hashes.
Jobs have explicit time/memory/output caps and isolated temp HOME/app-data. Network is denied during
runtime privacy tests; dependency/vulnerability jobs use only the minimum named registry/advisory
endpoints or a pre-generated reviewed database snapshot.

The release caller supplies candidate artifact by digest. PR/source mode builds once from clean
export and scans that artifact. Never use `pip install latest`, arbitrary curl-to-shell, package
lifecycle scripts, untrusted SARIF transformers, or unreviewed binary downloads.

### Dependency and license gate

Verify `uv.lock`, `package-lock.json`, hashes, direct/transitive inventories, platform marker
resolution, no private/direct local/Git/path dependency, and artifact metadata agreement. Run the
locked vulnerability scanner against source lock and built/install environment; record advisory DB
identity/freshness. Unknown/unreviewed critical/high findings block. Lower severities require an
explicit bounded public disposition with owner and expiry; the workflow itself cannot waive.

Generate/verify CycloneDX SBOM and license inventory. Every shipped distribution/native component
appears once with version/hash/source/license where available. Missing/unknown/incompatible license,
copied code without attribution, or SBOM/artifact mismatch blocks.

### Static and workflow policy

Run locked Python static/security rules, type/lint, banned API/import/layer checks, and targeted
tests for canonical parsing, SQL identifier/parameter discipline, cryptographic envelope/key
separation, path traversal/symlink handling, subprocess argument boundaries, stdout protocol purity,
exception redaction, and unsafe deserialization/network defaults.

Lint every workflow for minimum permissions, no unpinned actions, no `pull_request_target`, no
untrusted expression-to-shell interpolation, no broad secret/environment dump, explicit timeouts,
trusted release environments, immutable artifact passing, and job-level OIDC/write only where
needed. Migration/resource files are digest/append-only checked.

### Source and artifact boundary

Run `scan_public_boundary.py` independently over clean source export, sdist, wheel, wheel metadata,
SBOM/provenance, schemas/migrations/skills/fixtures/docs, and public evidence. Compare artifact
members to exact allowlists. Reject local/private planning material, transcripts, paths, secrets,
customer/tenant/production identifiers, DB/WAL/SHM/log/cache/debug/editor files, source maps,
unexpected binary/native code, and unapproved package/import/URL names.

A negative-control artifact embeds a per-run synthetic canary in filename, text, binary, metadata,
and safe encoded form. The scanner must fail and the canary must not occur in logs/reports. The
negative control is destroyed and never uploaded.

### Runtime plaintext-canary sweep

Install the candidate outside checkout. Seed unique synthetic markers into task title, refs,
payload, plan/obligation/action/result/evidence/claim text, imported JSONL, provider fake case/result,
filename/path/URL/command-like values, secret/config fields, and hostile exception text. Execute
start/publish/check/respond/status/receipt/import/review, crash/reopen, checkpoint, backup, migration,
diagnostic/release probe, CLI errors, and MCP invalid/error paths.

After clean close, scan every allowed temp surface byte-for-byte: catalog/ledger DB, WAL/SHM,
SQLite temp/journal, filenames/directories, logs/stderr/stdout structural channels, diagnostic and
backup manifests, crash/fault reports, process argument capture, test/evidence output. Encrypted
object ciphertext may not contain the plaintext canary; authenticated decrypt in the test confirms
intended content remains recoverable. The only plaintext test input copy is isolated from scanned
product output and deleted.

Verify owner-only permission/symlink/hard-link fences, no raw key in any surface, locked/missing key
fails closed, and no support bundle copies encrypted user objects by default. Any incomplete surface
enumeration or scanner failure blocks.

### Evidence

Produce canonical structural reports: candidate/runtime/tool/advisory-policy digests, case/rule IDs,
counts, pass/fail/incomplete, bounded reason codes, SBOM/license/vulnerability summary, and artifact
scan digests. Reports contain no matched text, canary, user content, raw paths, environment,
tracebacks, SQL, payloads, or object bytes and scan themselves before short-retention upload.

`security-evidence` verifies completeness/candidate equality. `security-required` uses `if: always()`
and fails on skipped/cancelled/failed/incomplete predecessor or an expired disposition.

## Errors and edge cases

- Vulnerability feed outage/staleness is incomplete evidence and blocks a release claim; PR policy
  may use the last locked snapshot only when freshness policy permits.
- Static scanner noise is fixed or dispositioned in reviewed policy, never shell-ignored.
- Binary/archive/parser limit or unknown file type is incomplete scan and blocks.
- Synthetic canaries are not real secrets and never justify scanning production/user data.
- Runtime scan teardown kills only suite-owned child processes and never inspects a maintainer's real
  home/keychain/repositories.
- Security reports are not uploaded if their own boundary scan fails.

## Invariants

1. No unscanned source/artifact/runtime plaintext surface can accompany a passing gate.
2. Findings/reports never disclose matched content or secrets.
3. Dependency, source, built artifact, and runtime privacy are independent required gates.
4. Fork code receives no secrets or write authority.
5. Exceptions/dispositions are exact, reviewed, expiring, and cannot silently change severity.
6. Passing language remains scoped to tested threat model and artifact identity.

## Tests

- Workflow policy tests verify triggers, permissions, action pins, trusted environments, timeouts,
  scanner inputs, negative controls, and aggregation.
- Synthetic vulnerable dependency/license/private file/unpinned action/secret/path/canary cases all
  block with redacted output.
- `specs/tests/conformance.md`, `specs/tests/integration.md`, and `specs/tests/packaging.md` own the
  runtime/artifact privacy matrices.

## Open questions

None.

Exact security-tool and disposition policy is centralized under E-008; unresolved critical or
high findings remain non-waivable by workflow input.
