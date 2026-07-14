# .github/workflows/nightly-fault.yml — supervised crash, state-machine, and scale evidence

**Wave:** C/F | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`specs/tests/property.md`, `specs/tests/subprocess.md`, `specs/tests/integration.md` |
**Imported by:** release eligibility and durability support evidence

## Purpose

Run the expensive deterministic failure and bounded-resource cases intentionally excluded from
ordinary PR latency: full process-kill matrix, lease/generation races, migration/restore interruption,
projection replay variants, long property state machines, and 10K/100K/1M event resource probes.

## Public surface

Workflow name `Nightly Fault and Scale`. Triggers:

- nightly UTC cron at one documented off-peak time;
- `workflow_dispatch` with `profile=standard|release` and an optional exact candidate SHA;
- reusable `workflow_call` from tagged release with `profile=release` and candidate artifact digest.

Jobs:

```text
fault-matrix-linux
fault-matrix-macos
property-state-machine
replay-and-resource
nightly-evidence
nightly-required
```

Release profile requires every job; standard nightly may mark the 1M-event case as scheduled but
still fails if it starts and does not complete honestly.

## Behavior

### Authority, isolation, and concurrency

Default permissions are `contents: read`; no secrets, OIDC, publication, issue, or pull-request
write. Full-SHA pin every action. Scheduled runs use the protected default-branch SHA; manual runs
validate the supplied SHA belongs to the repository and check out without persisted credentials.

Concurrency group includes candidate SHA and profile, with at most one run per platform/profile.
Do not cancel a run already executing durability boundaries merely because a new nightly starts;
queue it. Each job has explicit wall-time, disk, memory, process, descriptor, output, and worker
caps. Temp HOME/app-data roots are unique and jobs kill only child process groups bearing their own
marker. Before tests, assert no other suite-owned process or bundle is active.

### Candidate construction

For scheduled/manual source runs, build one clean wheel/sdist in a setup job and pass immutable
digest-verified artifacts to test jobs. For release-call runs, consume only the supplied candidate
artifact from the calling workflow. Test jobs install outside checkout and record package/resource/
Python/APSW/SQLite/OS/filesystem identities. They never install `latest` or compile an alternate
SQLite path.

### Platform fault matrix

On certified Linux x86-64 and macOS arm64 environments, run every semantic kill boundary from the
subprocess spec for publication, check/semantic, checkpoint, backup, migration/restore, and response
serialization. For each boundary:

1. create an isolated deterministic fixture and record request identity;
2. child executes installed executable with one exact fault marker;
3. controller terminates/faults only that child at the acknowledged marker;
4. reopen through normal startup/recovery, never test-only repair;
5. retry the same request and resolve pre-commit vs outcome-unknown post-commit;
6. verify chains/objects/operations/semantic attempts/frontiers;
7. discard projection cache and full replay;
8. compare with in-memory reference state and expected receipt wording;
9. privacy-scan DB/WAL/SHM/temp/log/report surfaces using synthetic canaries.

Run owner-generation races: live owner rejects second writer; killed owner is proven stale; two
successors race and exactly one advances generation. Exercise wall-clock anomalies only through the
injected clock; generation fencing remains authority.

Automatic test retry is disabled. A failing seed is retained in redacted structural form and the
job fails.

### Property state machine

Run the extended Hypothesis/reference-machine profile over append/retry/idempotency conflict,
unknown events, redaction, branch/merge, stale finding response, semantic delay/late result, kill/
reopen, migration/rebuild, and receipt. Execute multiple fixed hash seeds and randomized seeds;
record seeds and shrink to a bounded operation-code trace containing no payload text.

The release profile reaches the reviewed example/step budget on both adapters. Deadline/cancellation
does not count as pass. Any divergence persists a sanitized operation-code trace and digest.

### Replay and resource probes

Generate deterministic synthetic structural/payload objects inside temp encrypted bundles and run:

- incremental vs empty full replay under page-size/hash-seed variants;
- 10K and 100K entries each nightly; 1M entries in release profile and designated weekly cadence;
- append/check/status/receipt latency distributions under supported client count;
- idle/peak RSS, WAL/database/object growth, checkpoint, backup/restore time;
- bounded-memory query paths and no accidental full transcript/materialization;
- cancellation and cleanup at each scale.

Budgets are reviewed data. A budget miss is `fail` or `inconclusive` under an infrastructure reason,
never silently skipped. Results are measurements tied to runner identity, not universal performance
claims.

### Evidence output

Each job emits canonical redacted JSON with candidate/runtime/platform identity, case/seed IDs,
counts, outcomes, duration/resource buckets, frontier/digest comparisons, and bounded failure code.
Never upload bundles, DB/WAL/SHM, encrypted objects, raw stdout/stderr, prompts, payloads, temp roots,
or crash dumps. Run boundary scan on reports before digest-bound artifact upload with short retention.

`nightly-evidence` validates completeness and candidate equality, then produces a nightly manifest.
`nightly-required` succeeds only if every required platform/case passed. Scheduled failure notifies
through repository-configured GitHub status/issue workflow only if separately authorized; this file
itself performs no external messaging.

## Errors and edge cases

- Runner outage/timeout/disk pressure produces incomplete evidence and a failed required job.
- Unsupported platform/source identity fails before fault claims; it cannot be substituted with a
  different runner.
- A kill controller timeout kills its own process group and reports failure; broad process-name kill
  is forbidden.
- 1M profile is never run uncontrolled on a maintainer machine and never parallelizes writers to
  the same bundle.
- Artifact/evidence digest mismatch or privacy-scan failure blocks upload/aggregation.
- Scheduled runs cannot consume live provider or production key credentials.

## Invariants

1. Every durability boundary is observed on installed candidate bytes.
2. No automatic retry turns a flaky failure green.
3. Process control is scoped to the job's own children and data roots.
4. Resource probes are capped and tied to exact platform identity.
5. Uploaded evidence is structural/redacted and complete or the gate fails.
6. Release eligibility requires a passing, fresh release-profile run for each advertised platform.

## Tests

- Workflow policy tests assert permissions, pinning, trigger/input validation, non-cancelling
  concurrency, limits, and artifact dependency graph.
- Fault-controller self-tests prove each marker is reachable once and production config cannot
  enable markers.
- Negative controls force child hang, seed divergence, budget miss, stale owner, report canary, and
  missing platform evidence; all must fail safely.
- `specs/tests/subprocess.md` and `specs/tests/property.md` define test semantics.

## Open questions

None.

Exact fault, timing, memory, disk, and example budgets are centralized empirical gates E-004,
E-005, and E-008 and do not block the natural-language implementation freeze.
