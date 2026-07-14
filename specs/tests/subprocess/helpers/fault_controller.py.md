# tests/subprocess/helpers/fault_controller.py — deterministic crash-boundary coordinator

**Wave:** C–F | **ADRs:** ADR-001, ADR-003, ADR-004 | **Imports (spec-tree):**
`specs/tests/subprocess/helpers/child.py.md` | **Imported by:** kill-matrix and reopen/retry tests

## Purpose

Coordinate abrupt termination at named durability boundaries without timing guesses. The helper
arms exactly one test-only marker, waits for the installed child to prove it reached that boundary,
then faults or kills only the owned process group.

## Public surface

- `FaultPoint`: closed enum of the 16 named boundaries from `tests/subprocess.md`.
- `FaultMode`: `sigkill|abrupt_exit|injected_io_error|broken_pipe` where applicable.
- `FaultPlan`: point, mode, operation/request identity, deadline, expected pre/post-commit class.
- `FaultObservation`: armed/reached/terminated timestamps, marker ordinal, exit, reason code.
- `arm_fault(child_spec, plan) -> ArmedChild`.
- `wait_and_trigger(armed) -> FaultObservation`.
- `assert_fault_hooks_unavailable_in_release(installed_env)`.

## Behavior

Pass the selected marker through a dedicated inherited test descriptor or test-build capability,
never public CLI input/environment in release artifacts. Marker messages are fixed structural
tokens containing point and ordinal only. Authenticate association with the child's unique temp-root
nonce; payloads, paths, keys, SQL, object IDs, and exception strings are absent.

Require exactly one `armed`, then one `reached`, message. Trigger the selected fault immediately
after acknowledgement and before any `continued` marker. Pre-commit points 1–9 and post/delivery/
maintenance/semantic points 10–16 retain their contract classification; the test's durable-state
oracle, not this helper, decides applied outcome.

If marker is not reached by deadline, terminate the owned group and fail; do not fall back to a
sleep-based kill. After termination, fsync/close the controller transcript and return only bounded
structural observation. One bundle is faulted serially; parallel cases require separate roots.

## Errors and edge cases

- Duplicate/out-of-order/unknown marker, wrong nonce, child exit before marker, deadline, or cleanup
  failure is a harness failure.
- A fault mode unsupported on the certified platform is not silently replaced; the matrix marks the
  cell unsupported and release claims fail/narrow.
- Release wheel must reject/omit hook activation even when test variables/descriptors are forged.
- Controller never reads or modifies SQLite/object state.

## Invariants

1. Each case faults once at an acknowledged semantic boundary.
2. No timing estimate establishes boundary reach.
3. Only the suite-owned child group is terminated.
4. Fault hooks cannot activate in production configuration/artifacts.

## Tests

Self-tests expose every marker in a tiny instrumented child, prove reach exactly once, deadline and
wrong-nonce failures, pre/post classification, group-scoped kill, and release-hook denial. Reports
are privacy-scanned before retention.

## Open questions

None.
