# src/yoetz/application/observation_verification.py — inspect + approved-check orchestration

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/workspace_inspect.py.md`,
`adapters/approved_checks.py.md`, `kernel/policies/observation_advice.md` |
**Imported by:** observation advice refresh paths and composition tests

## Purpose

Convert observed changed-file commitments into bounded inspection requests and run standing
approved checks bound to exact subject-state digests.

## Public surface

- `orchestrate_changed_path_inspection(...)`
- `run_bound_approved_check(...)`
- `ObservationVerificationJob`
- `ObservationVerificationRepository`
- `ObservationVerificationWorker.enqueue_if_changed(...)`, `run_once()`
- `ObservationVerificationSupervisor` — generation-fenced background drain loop owned by ready
  composition: `start()`, `stop()`, `register()`, `notify()`. Hook ingest only enqueues and wakes;
  approved checks never run inside the hook RPC budget. Ready composition creates the supervisor with
  the current `service_generation`, starts it when the ready `Application` is opened, and stops it on
  application/`ServiceReadyContext` close before privacy/runtime teardown. The loop always parks on
  its wake event (short timeout) so an empty handle set cannot busy-loop and starve shutdown.

## Behavior

After each normalized completed tool action, ready composition decrypts the authenticated workspace
locator, reopens it descriptor-safe/no-follow, loads the exact raw policy bytes, and captures a
structural Git subject digest. Untrusted/changed policy executes nothing. An unchanged digest
enqueues nothing. A changed digest enqueues one job per exact approved command.

The repository permits one running lease per workspace. A newer digest marks older pending jobs
stale and becomes latest pending work; an already running job may finish but is non-current if the
post-run digest changed. Cache identity is workspace + trusted policy digest + approval commitment
+ subject-state digest. Service generation or lease expiry returns abandoned running jobs to
pending; completed results are immutable.

Pre-run subject-state mismatch returns `STALE` without executing. Exact argv runs with
`shell=False`, sanitized environment, bounded time/output, and enforcing no-network sandbox.
Network-requiring checks remain rejected as `network_check_unsupported` absent separate reviewed
authority. Redacted output goes directly to an encrypted observation-content object. Public advice
receives only result/evidence commitments, currentness, safe derived facts, and limitations.

## Errors and edge cases

Missing locator/policy/trust/state capture records a bounded limitation and executes nothing.
Runner failure, timeout, state drift, service-generation change, or lease expiry cannot count as a
current pass.

## Invariants

1. Exact argv only; no shell or inherited environment.
2. One running job per workspace and immutable completed results.
3. Public facts contain no raw output or absolute path.

## Tests

Covered by approved-check, verification-repository, migration, observation-advice, and real ready
composition tests.

## Open questions

None.
