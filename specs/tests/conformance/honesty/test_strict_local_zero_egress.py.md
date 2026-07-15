# tests/conformance/honesty/test_strict_local_zero_egress.py — strict-local zero-egress contract

**Wave:** C–E | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/config/models.md`, `src/yoetz/application/service.md`,
`src/yoetz/adapters/providers/fake.md`
**Imported by:** conformance honesty tests

## Purpose

Prove strict-local mode performs no network egress and does not instantiate provider clients.

## Public surface

- `test_zero_egress_in_strict_local` — no DNS, AF_INET/AF_INET6, HTTP(S), redirect, telemetry,
  crash-upload, update-check, capability-test, or model-download egress occurs; the exact
  authenticated local service/confidential endpoints and release-cell allowlisted OS
  credential/user-presence/session-lifecycle security IPC remain allowed.
- `test_strict_local_still_supports_deterministic_operations` — deterministic workflows still work.
- `test_fake_credentials_are_not_read_or_reported` — credential-like environment values are ignored.

## Behavior

The test runs the full strict-local workflow under external-egress denial and asserts:

- provider modules/clients are not constructed;
- deterministic operations still return useful public results;
- fake credential-looking environment values are neither read nor echoed;
- no log, error, receipt, MCP result, or agent-context disclosure suggests semantic review occurred;
- permitted local IPC is closed to the configured authenticated service/confidential endpoints,
  optional exact approved local-model AF_UNIX profile, and release-cell OS credential/user-presence/
  session-lifecycle routes (for example allowlisted Linux AF_UNIX session-bus Secret Service
  peers/methods and, separately, system-bus `org.freedesktop.login1` peers/methods, or measured
  macOS native notifications); none can act as a proxy or external transit;
- LLM inference, telemetry, crash diagnostics, update checks and capability testing each record zero
  external attempts, proving one disabled channel is not inferred from another.

## Errors and edge cases

- Any AF_INET/AF_INET6, DNS, redirect, external subprocess, telemetry, crash-upload, update-check,
  capability-test, or model-download attempt in strict-local fails the test.
- An unexpected AF_UNIX peer/path/credential or use of the local socket as a proxy fails the test.
- An unallowlisted D-Bus name/method/peer or platform IPC route fails. Evidence names the exact
  platform/release profile, Yoetz-owned processes, startup-through-`locked|ready`/operation interval,
  and local peers; it does not attest external OS agents or a model runtime.

## Invariants

1. Strict-local plus the false global ceiling/all-disabled channel policy means zero external egress
   while permitting only exact release-profiled local IPC.
2. Deterministic operations remain useful.
3. Credential-like noise is ignored.

## Tests

- `tests/conformance/honesty/test_strict_local_zero_egress.py`

## Open questions

None.
