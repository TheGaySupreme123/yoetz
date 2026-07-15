# fixtures/privacy/PRIV-001-local-only.case.json — local-only and local-model privacy profile

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas and data-egress protocol | **Imported by:** privacy conformance, integration, capability and
zero-egress tests

## Purpose

Freeze the positive `local_only` behavior, including a separately configured local model that uses
the same disclosure fences without external network access.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-001`, with variants
`deterministic_only` and `local_model`. It fixes policy
`pvy_11111111-1111-4111-8111-111111111111`,
machine scope, all five network channels disabled, local runtime `local-static-v1`, synthetic task
and evidence excerpts, an unrelated transcript canary, exact release-cell OS keyring/presence IPC
allowlists, and deterministic network/control probes.

## Behavior

`deterministic_only` completes useful deterministic work and constructs no provider/local-model
adapter. `local_model` classifies, minimizes, bounds, and never-send-scans the selected task and
evidence; it removes the unrelated transcript and secret-shaped canary, then calls only the injected
in-process local fake. Both variants permit exact service/confidential local endpoints and the
release-cell OS credential/user-presence/session-lifecycle routes (allowlisted Linux AF_UNIX
session-bus Secret Service peers/methods and, separately, system-bus `org.freedesktop.login1`
peers/methods, or measured macOS native notifications); the
local-model variant additionally permits
only its exact approved AF_UNIX profile. Arbitrary AF_UNIX, bus names/methods/peers, and local
proxies fail. The fixture records zero AF_INET/AF_INET6 sockets, DNS, HTTP(S), redirects, telemetry,
crash upload, update, capability-test, or model-download attempts. No network egress receipt claims
a local call. Evidence names the exact platform/release profile, Yoetz-owned processes, lifecycle
from startup through `locked|ready` and the operation, and each allowlisted local peer. It does not
attest the external OS agent or separate model runtime.

## Errors and edge cases

Fake provider credentials, proxy variables, agent requests for extra context, and redirect fixtures
do not change composition. Any external adapter construction, canary in local-model input, or secret
in MCP/agent output fails the case.

## Invariants

The fixture is canonical, synthetic, offline, deterministic, path-free, and test/sdist-only. Local
model permission is independent of external network and does not weaken classification or
never-send rules.

## Tests

`tests/conformance/privacy/test_privacy_profiles.py`,
`tests/conformance/honesty/test_strict_local_zero_egress.py`, and
`tests/capability/test_privacy_provider_and_local_model_profiles.py`.

## Open questions

None.
