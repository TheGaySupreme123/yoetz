# .github/workflows/release.yml — approval-gated tagged public release pipeline

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
repository build/resource/script/workflow specs and every release-gate test spec | **Imported by:**
public package and GitHub release process

## Purpose

Build one immutable candidate from a clean version tag, prove it across advertised environments,
assemble honest release evidence, and only then publish the exact tested bytes through protected
environments. The pipeline separates construction, verification, approval, publication, and
post-publication download verification so no rebuild occurs between them.

## Public surface

Workflow name `Tagged Release`. Triggers:

- `push` of annotated/signed release tags matching `v[0-9]+.[0-9]+.[0-9]+` with optional approved
  prerelease suffix policy;
- `workflow_dispatch` only in `dry_run=true` mode for an existing exact tag. Manual input cannot
  publish.

Required jobs/stages:

```text
validate-release-source
build-candidate
verify-linux-x86_64
verify-macos-arm64
fault-release-profile
capability-release-profile
security-privacy-release
assemble-release-evidence
approve-publication
publish-pypi
publish-github-release
publish-schema-site
verify-published
release-required
```

Publication jobs reference protected environments and immutable candidate/evidence digests.

## Behavior

### Permissions and trust boundaries

Top-level permissions are `contents: read`. Build/test/evidence jobs have no secrets, OIDC, or
write. `publish-pypi` alone receives `id-token: write` in the protected `pypi` environment for
Trusted Publishing; no long-lived package token. `publish-github-release` alone receives bounded
`contents: write` after approval. Provenance attestation gets job-scoped `id-token: write` only if
the implemented end-user verification path is part of release policy. Every action is full-SHA
pinned.

Tag code is trusted only after validation; no fork/PR event reaches publication. Environments
require human reviewers, prevent self-approval where supported, restrict branches/tags, and expose
no production/user/provider credentials. Live-provider capability, if claimed, runs in its own
separately approved environment before publication.

Concurrency group is the normalized version/tag with `cancel-in-progress: false`. A second run for
the same tag must consume/verify the same candidate digests or fail; it cannot overwrite/rebuild a
different version silently.

### Validate release source

Checkout the exact tag without persisted credentials and verify:

- tag parses and package version equals it exactly; commit is reachable from protected release
  branch and meets signed-tag/commit policy;
- source export is clean and contains only public allowlisted files;
- lockfiles are current; `uv run --locked python scripts/verify_spec_manifest.py --check` proves the
  public specification/future-file ownership tree is complete and self-contained; schema/resource
  generators pass `--check`;
- migration/protocol/vector append-only policy and changelog/security/license/contributor metadata;
- no private index/path/Git dependency, ignored/private file, local path, transcript, secret, or
  release canary;
- release policy/support/known-limitation entries are complete and no public claim exceeds planned
  matrices.

Create a canonical source manifest/tar export tied to tag/commit. Dry-run follows every stage except
protected publication and marks evidence `dry_run`.

The release spec-manifest check is rerun inside the clean exported candidate before build, with
ignored/private drafting roots absent. Missing/duplicate ownership, index-only gaps, extension-
mapping ambiguity, malformed headings, unlisted spec, or a private normative dependency is terminal;
release cannot use a manifest generated or patched during CI.

### Build exactly once

Use the locked build runner/tool/Python to run resource/schema/boundary checks and
`uv build --no-sources`. Build sdist/wheel twice from normalized clean exports to evaluate the
documented reproducibility scope. Select one candidate set and compute digests; if permitted
metadata differs, record/justify at field level rather than choosing silently. Inspect allowlisted
members and metadata before upload.

Generate candidate manifest with tag/commit, lock/build tool, artifact sizes/digests/tags, resource
set, and build runner identity. Upload immutable short-retention internal artifacts with attestation
from this workflow when configured. Every downstream job verifies digest and never rebuilds.

### Platform verification

Verify on exact advertised environments, initially certified macOS arm64 and glibc/manylinux 2.28
x86-64. Runner/container labels/images are digest/revision locked, and each job asserts OS/CPU/ABI/
filesystem/Python/APSW/SQLite source/options before making support claims.

Install candidate in a clean environment with documented managed Python path. Run version/startup,
resource/schema/migration identity, all non-live test layers required for platform, strict-local six
operations, MCP/CLI subprocess, clean install/upgrade/rollback/uninstall/data retention/offline
reinstall, backup/restore, key backend states, and installed-artifact boundary scan. Unsupported
runner identity or unavailable platform fails; another platform cannot substitute.

### Reusable full gates

Invoke the exact candidate digest through:

- `nightly-fault.yml` release profile for full crash/property/1M-resource evidence;
- `capability.yml` release profile for the reviewed exact Codex/MCP/platform/key and, only when
  claimed/approved, live-provider cells;
- `security-privacy.yml` release profile for dependency/license/static/source/artifact/runtime
  plaintext gates.

Reusable workflows return signed/digest-bound structural manifests, not mutable status text. A
freshness policy may accept a prior run only if it used identical candidate bytes/tool policies/
platform images and lies within the reviewed interval; default release behavior runs fresh.

### Assemble evidence and approval

Generate SBOM/provenance candidate records, then call `generate_release_evidence.py --write` with
all test/capability/security/platform manifests. Run `--check` and public-boundary scan. Evidence
must state exact support matrix, unsupported/untested cells, known limitations, migration/resource/
protocol/engine/policy/projection/object versions, checksums, and truthful signature/provenance
status.

`approve-publication` is an environment gate over candidate and evidence digest summary. Approval
authorizes publication of those exact bytes only. There is no workflow input to skip tests, waive
scan, broaden support, change version, or substitute artifact.

### Publish

`publish-pypi` uses PyPI Trusted Publishing and uploads the prebuilt sdist/wheels by digest with
skip-existing disabled. Before upload, query/verify that version is absent; an existing different
digest is terminal conflict. TestPyPI, if used, is a separate pre-release workflow and never a
source for public bytes.

`publish-github-release` creates release notes from reviewed changelog/limitations plus links to
evidence, then attaches the same artifacts, `SHA256SUMS`, SBOM, provenance/attestation, capability
matrix, support matrix, known limitations, and verification instructions. Prerelease tags are
marked prerelease; release notes do not strengthen evidence wording.

`publish-schema-site` receives only the protected schema-host publication authority. It deploys
the already-tested `schemas/` bytes without regeneration or renaming at
`https://schemas.yoetz.dev/0.1/`, serving each immutable `*.schema.json` path as
`application/schema+json; charset=utf-8`, the manifest as `application/json`, public CORS,
`X-Content-Type-Options: nosniff`, immutable member caching, and digest-ETag manifest
revalidation. It may not overwrite a different digest at an existing versioned schema path; the
manifest changes only by an atomic reviewed inventory publication. Provider credentials or origin
details never enter artifacts/logs.

Publication order and partial failure policy are explicit: PyPI is immutable and primary package
publication; if GitHub release attachment fails after PyPI succeeds, do not rebuild/yank
automatically. Resume same workflow using identical digests and report bounded incident status.
Yank/delete is a separate human incident process, never automatic rollback.

### Verify published bytes

After registry/CDN availability with bounded retry, download artifacts from PyPI and GitHub into
fresh Linux and macOS environments by exact version/filename and fetch every schema/manifest URL
from the published `/0.1/` tree. Verify hashes against evidence,
metadata/resource manifest/SBOM/provenance, install with network denied from captured wheelhouse,
compare hosted schema bytes to the candidate and installed local registry, run `version --json`,
startup and strict-local smoke, and compare package bytes to candidate. Only then does
`release-required` succeed and the release become announceable.

No workflow step sends announcement, email, Slack, issue, or social post; external communication is
a separately authorized action.

## Errors and edge cases

- Any required job skipped/cancelled/failed/incomplete blocks approval/publication.
- Tag/version/commit/artifact/evidence mismatch, existing registry version, or private-boundary hit
  is terminal; never mutate the tag or version in workflow.
- Registry/runner/advisory/provider outage is incomplete evidence or publish failure; do not rebuild
  or switch endpoints/artifacts.
- Post-PyPI partial failure resumes attachments/verification with identical bytes. Never silently
  republish, overwrite, or claim rollback.
- Secrets/identity tokens are job-scoped, never printed/cached/artifacted, and unavailable to test
  code before protected environment entry.
- GitHub artifacts are transport, not publication authority; every consumer verifies digest.
- Manual dry run cannot acquire publish permissions even if a maintainer supplies false inputs.

## Invariants

1. Build once; every test, approval, publication, and verification names the same digests.
2. Only a validated tag plus protected environment approval can publish.
3. Test jobs are secret-free/read-only; publication authority is isolated and minimal.
4. Public claims never exceed passing exact platform/capability/security evidence.
5. No failure/skip/incomplete can be waived by workflow input.
6. Published bytes are downloaded and reverified before success/announcement.
7. The workflow never rebuilds, overwrites an existing version, auto-yanks, or externally announces.

## Tests

- Workflow policy tests assert trigger/tag grammar, manual dry-run isolation, job permissions,
  environment order, action SHA pins, artifact-digest edges, no rebuild downstream, and `if: always`
  aggregation behavior.
- A local/emulated dry run uses synthetic artifacts and fake registries to exercise success,
  existing-version conflict, partial publication, resume, and downloaded-byte mismatch.
- Negative controls force failed/skipped reusable gate, wrong platform, changed resource, secret
  exposure, evidence mismatch, and unapproved publish; each blocks before authority use.
- A release dry-run removes one future-file row, introduces a duplicate owner and replaces one public
  dependency with a private drafting reference; `verify_spec_manifest.py --check` must block before
  candidate construction.
- `specs/tests/packaging.md` owns artifact lifecycle and installed verification.

## Open questions

None.

Artifact formats and publication environments are centralized under E-008. Signing remains
an explicit v0.2 deferral; v0.1 still requires checksums, SBOM, and truthful CI provenance.
