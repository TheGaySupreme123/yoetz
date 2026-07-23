# .github/workflows/pr-ci.yml — bounded pull-request correctness gate

**Wave:** F | **ADRs:** ADR-002, ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
protocol/resource/script specs and unit/conformance/integration/subprocess/packaging test specs |
**Imported by:** branch protection and tagged-release prerequisite policy

## Purpose

Provide fast, deterministic evidence that a public change preserves contracts, style/types, pure
truth behavior, adapter parity, bounded durable/transport behavior, package construction, and the
public/private boundary. It gives reviewers several independently diagnosable jobs and never uses
production credentials or live providers.

## Public surface

Future workflow name: `PR CI`. Triggers:

- `pull_request` for opened/synchronize/reopened/ready-for-review against protected branches;
- `push` to the protected default branch after merge;
- `workflow_dispatch` for maintainers, with no behavior-changing input.

Required check names:

```text
contracts-and-resources
format-lint-type
unit-and-conformance
sqlite-integration
cli-mcp-subprocess
package-and-boundary
pr-ci-required
```

`pr-ci-required` is the stable branch-protection aggregation job and succeeds only when every
applicable predecessor succeeds; a cancelled/skipped required predecessor fails aggregation.

## Behavior

### Workflow security and concurrency

Set top-level `permissions: contents: read`. Jobs receive no secrets, OIDC, package, action, issue,
pull-request-write, or security-event permission. Pull requests from forks execute untrusted code
only with the read-only token and no privileged cache/write path. Never use `pull_request_target`.

Concurrency group is `pr-ci-${event.pull_request.number || ref}` with `cancel-in-progress: true`.
Job and step timeouts are explicit. Shell is noninteractive UTF-8 with fail-fast pipeline settings.
All third-party actions are pinned to reviewed full commit SHA with a human version comment; moving
tags are forbidden. Dependency update automation changes those pins in review.

Checkout uses no persisted credentials, fetch depth sufficient for boundary/metadata checks but no
submodules/LFS unless explicitly introduced. The workflow verifies the checkout contains no
symlink/path escape in owned resource/release roots.

### Shared environment

Use the project-locked Python patch and `uv` version declared by repository build metadata. Install
`uv` from a digest/SHA-pinned action or verified standalone artifact, then `uv sync --locked`.
JavaScript tooling uses the locked Node major/patch and `npm ci --ignore-scripts`; no npm package
lifecycle script executes. Caches are keyed by runner OS/arch plus lockfile digest, are read-only for
forks, and are performance only: a cache miss or poisoned/mismatched payload cannot alter lock
resolution or artifact identity.

Set deterministic controls (`PYTHONHASHSEED` matrix where applicable, UTC, `C`/UTF-8 locale) and
isolate HOME/XDG/Yoetz app data inside the job temp directory. Strict-local tests deny provider
network use and use fake key/object/provider adapters or disposable platform stores only.

### `contracts-and-resources`

On a small Linux runner:

1. check dependency locks without modifying them;
2. run `uv run --locked python scripts/verify_spec_manifest.py --check` to prove one-to-one future-
   file ownership, complete file specs/index coverage and public self-containment without opening
   ignored drafting inputs;
3. run `scripts/generate_schemas.py --check`;
4. run `scripts/verify_resource_manifest.py --check`;
5. validate every public schema, canonical vector, migration, skill link, and manifest; prove each
   schema `$id` is the direct `/0.1/<relative-path>` static-host URL, resolve every `$ref` from the
   local manifest with DNS/sockets denied, and verify root/package mirrors byte-for-byte;
6. verify migration/vector files are append-only against the protected-base export and, once a
   release tag exists, verify versioned `*.schema.json` artifacts against the most recent reachable
   release tag; the atomic `schemas/manifest.json` inventory may advance as specified by ADR-007;
7. run the public-boundary scanner on the checkout's public candidate inventory;
8. run focused canonical/schema/protocol unit vectors under at least two hash seeds.

The base comparison must not execute code from the base through an unsafe path. Structural diffs
are bounded; no private ignored file is read or uploaded.

The spec-manifest gate runs from the public checkout alone. A missing `FILE_MANIFEST`, unowned future
file, index-only child gap, shorthand/extension ambiguity, missing mandatory section, or normative
reference to private drafting material fails before build/test. CI cannot classify or auto-generate
manifest rows on behalf of reviewers.

### `format-lint-type`

Run exactly the locked commands:

```text
uv run --locked ruff format --check .
uv run --locked ruff check .
npm ci --ignore-scripts
npx --no-install pyright
```

Also validate build metadata, workflow YAML, JSON/Markdown hygiene, import-layer rules, and that no
adapter/framework type crosses the domain/port boundary. No formatter writes in CI.

### `unit-and-conformance`

Run pure unit, bounded property examples, and adapter conformance excluding fault/soak/live markers:

```text
uv run --locked pytest tests/unit tests/property tests/conformance \
  -m "not fault and not soak and not live" -q --timeout=120
```

Split or shard only by stable explicit test groups; test order, worker count, and hash seed are
recorded. In-memory and SQLite conformance runs use the same public fixtures and reference results.
No test outcome is retried automatically into green. A flaky failure is a failure.

### `sqlite-integration`

Run on the primary Linux CI platform with the exact locked APSW/SQLite distribution. Assert source
ID, compile options, filesystem probe, WAL/FULL synchronization, and single-writer assumptions
before tests. Run bounded integration cases for migration `0001`, object durability, projection
replay, recovery, backup/restore, six-operation vertical slice, privacy sweep, and key-unavailable
fail-closed behavior. Exclude the full kill/scale matrix, which belongs nightly.

Artifacts are not uploaded on success. On failure, upload only the canonical sanitized structural
test report after running the public-boundary scanner; never upload databases, WAL/SHM, objects,
temp HOME, logs with payloads, or arbitrary pytest output.

### `cli-mcp-subprocess`

Build/install the candidate wheel in an isolated environment, then run bounded subprocess cases:
console/module parity, stdout/stderr/exit contract, MCP initialize/list/six tools, line framing,
partial write/backpressure, cancellation/signals, second-writer rejection, and representative
pre/post-commit retry boundaries. Assert MCP stdout is protocol-only JSONL. Full 16-point kill
matrix and long platform cases remain nightly.

### `package-and-boundary`

Run `uv build --no-sources` from a clean exported candidate. Inspect sdist/wheel allowlists,
metadata, entry points, migration/schema/skill resource bytes, resource manifest, licenses, and
absence of unexpected native Yoetz binaries. Install the wheel outside the checkout and run import,
help, `version --json`, strict-local vertical slice, and resource verification.

Run `scan_public_boundary.py` independently on checkout, sdist, wheel, metadata/SBOM if generated,
and sanitized test reports. Seed synthetic canaries in a negative-control artifact and prove the
scanner blocks it without logging the canary. Never publish/upload this PR artifact as a release.

### Aggregation

`pr-ci-required` uses `if: always()` and checks the exact predecessor result map. It emits a bounded
summary of job outcomes and commit digest. It cannot waive a failure based on labels, actor, draft
status, retry count, or manual approval.

## Errors and edge cases

- Lock drift, generated-resource drift, unsupported runner/runtime identity, or incomplete scan
  fails before semantic tests/build claims.
- A network/package-registry outage fails the affected dependency setup; it is not a code pass.
- Job cancellation due to newer commit yields cancelled, never success for the old SHA.
- Draft PRs may run reduced billing policy only if their checks are not used as merge evidence; the
  required ready-for-review run always executes the full bounded set.
- No workflow command prints environment, tokens, package-index credentials, canaries, home paths,
  payloads, or raw failing artifact bytes.
- GitHub-hosted runner drift is recorded and asserted; a materially changed image invalidates rather
  than silently broadens platform evidence.

## Invariants

1. Required PR checks execute candidate code with read-only, secret-free authority.
2. Lock/resource/schema/public-boundary drift is always visible.
3. Passing PR CI is bounded correctness evidence, not full fault/capability/release evidence.
4. Built artifact tests never import the source checkout accidentally.
5. Required aggregation cannot convert skipped/cancelled/failed into success.
6. No user/private payload artifact is uploaded.

## Tests

- Workflow lint and policy tests assert triggers, permissions, pinning, timeouts, concurrency, and
  required job graph.
- A synthetic PR changes generated schema/resource without regeneration and must fail.
- Synthetic spec-tree cases omit/duplicate an owner, list an index child without a file spec, use an
  ambiguous `.py`/`.md` mapping, remove a mandatory heading and cite a private drafting path; each
  must fail the manifest gate before build.
- Fork simulation proves no secret/write permission or privileged cache upload.
- Negative control seeds private canary/stdout noise/lock drift and verifies bounded failure.
- `specs/tests/packaging.md` and `specs/tests/subprocess.md` own command-level assertions.

## Open questions

None.

Exact runner identities and job budgets are centralized under E-001 and E-008.
