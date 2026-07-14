# .github/workflows/capability.yml — exact-version external integration capability gate

**Wave:** D/F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/capability.md`, `specs/scripts/generate_capability_matrix.py.md` | **Imported by:**
tagged release and public support matrix

## Purpose

Probe installed Yoetz artifacts against exact Codex, MCP, platform, key-backend, and optional
provider identities and produce redacted capability evidence. It proves only observed cells and
keeps strict-local mandatory tests separate from explicitly approved live-provider tests.

## Public surface

Workflow name `Capability Matrix`. Triggers:

- `workflow_dispatch` with candidate artifact, exact tested-set profile, and
  `live_provider=false|true`;
- weekly schedule for the protected default-branch candidate with `live_provider=false`;
- reusable `workflow_call` from release with immutable artifact digest and `live_provider` policy.

Jobs:

```text
prepare-capability-candidate
codex-mcp-matrix
platform-key-matrix
provider-fake
provider-live-approved
aggregate-capability-matrix
capability-required
```

The live job is absent unless explicitly requested and authorized; a skipped required-live policy
cell fails aggregation rather than being inferred.

## Behavior

### Permissions and candidate

Default `contents: read`, full-SHA actions, no persisted checkout credential. Non-live jobs receive
no secrets/OIDC/write permission. `provider-live-approved` alone targets a protected GitHub
environment with reviewer approval and only the named provider credential plus read-only artifact
access. Credentials are environment-only, never argv/config/artifact/cache/log.

Prepare exactly one clean candidate wheel/sdist or consume the release caller's immutable artifact.
Record and verify package/resource/commit digest. Every matrix job downloads that artifact by
digest, installs outside checkout, and rejects a source-tree import. No job installs a newer Yoetz
or external tool than the explicit policy cell.

Concurrency groups by candidate digest and profile. Scheduled newer runs may cancel only pending
older scheduled work, not a release-called or approved live job. Explicit time/request/spend/output
caps apply.

### Codex/MCP matrix

The matrix is generated from reviewed exact-version policy, not a semantic range. Each cell installs
the exact supported/tested Codex executable through a verified digest/locked source and proves its
reported version. Then, in isolated HOME/repository/config:

- register optional and required MCP forms and observe startup/degraded behavior;
- cold/warm startup within measured margin;
- negotiate exact MCP protocol/SDK and list exactly six tools with frozen schemas;
- execute the six-operation vertical slice, invalid input/application error/cancellation/
  response-loss idempotent retry;
- discover/integrate canonical skill without overwriting modified local content;
- exercise parent + two subagents, contradiction/evidence integration, resume/reattach;
- capture public `codex exec --json` fixtures and validate importer gaps/assurance;
- prove strict-local operation with provider network/secret denied.

The test harness captures raw private evidence only into an encrypted ephemeral evidence area; the
public `CapabilityEvidence` record contains digests and bounded observations. The job destroys temp
config/data/key material after report finalization.

MCP SDK/protocol cases exercise framing, validation ownership, structured results, `isError`, EOF,
cancellation, malformed frames, stdout purity, and any accepted protocol fallback. A documented API
shape without an observed installed run is not pass.

### Platform/key matrix

For every advertised platform, assert exact OS/CPU/ABI/Python/APSW/SQLite and filesystem identity,
then run disposable owner-permission/fsync/rename/directory-fsync/WAL/backup/key-backend probes.
Keyring uses a disposable test namespace and deletes it; missing/locked/headless are separate
outcomes. Pass on one backend/platform cannot support another.

### Provider fake and approved live

Fake provider cases are mandatory and cover success/refusal/malformed/rate-limit/timeout/
cancellation/late/stale result normalization without network.

The optional live job sends only a synthetic minimized case to the exact reviewed provider endpoint,
model, SDK, region/profile and has hard request/token/spend/time caps. It validates structured
success and safely reproducible failure behavior, provenance/usage, no automatic retry beyond
policy, and deterministic post-validation. Network allowlisting denies unrelated hosts. Raw response
is never public evidence and is destroyed after digest/redacted observation.

An outage/rate limit is inconclusive, not pass. Strict-local can still pass independently. If the
release claims the live profile, inconclusive blocks that claim or the release narrows support.

### Evidence aggregation

Each case emits one canonical `CapabilityEvidence` tied to artifact, platform, exact external
version, test revision, and private-evidence digest. Scan public records, upload with bounded
retention, then run `generate_capability_matrix.py` with reviewed policy. Aggregation rejects
missing/duplicate/conflicting/stale cells and never infers intermediate support.

Upload public `capability-matrix.json`/`.md` and evidence manifest. Raw transcripts, prompts,
payloads, configs, home paths, credentials, provider responses, user keychain material, or temp
repositories are never uploaded. `capability-required` verifies exact claim coverage.

## Errors and edge cases

- External install source unavailable, version/digest mismatch, runner drift, timeout, or outage is
  failed/inconclusive evidence, never an automatic pass/retry to another version.
- Secrets are masked but masking is not the boundary; outputs are allowlisted and scanned before
  upload.
- Fork/untrusted events cannot trigger environment approval or access provider credentials.
- External tools never touch a maintainer's real HOME/config/repository/keychain namespace.
- A newer stable external version observed after policy freeze is untested until separately added
  and run; it does not invalidate past evidence or expand the claim automatically.
- Cleanup failure fails the affected job and records only a bounded reason.

## Invariants

1. Capability pass always names exact candidate, platform, and external versions.
2. Strict-local capability requires no provider secret/network.
3. Live authority is isolated to one approved job with hard cost/network/output caps.
4. Untested, unsupported, failed, and inconclusive remain distinct.
5. Public evidence is structural/redacted; private transcripts are never uploaded.
6. Release claims equal passing matrix cells exactly.

## Tests

- Workflow policy tests assert trusted triggers, job permissions/environment, exact-version matrices,
  pinning, isolation, caps, and aggregation dependency.
- Harness negative controls expose wrong version, source import, config mutation, raw transcript,
  credential echo, provider outage, and missing evidence; each fails safely.
- `specs/tests/capability.md` owns case semantics and record shape.
- Generator unit/property tests own conservative aggregation.

## Open questions

None.

Exact Codex cells and any publishable live-provider profile are centralized empirical gates
E-002 and E-007; no continuous range or unapproved provider claim exists in v0.1.
