# tests/capability/test_provider_profile_live.py — approved bounded live semantic-provider probe

**Wave:** E/F | **ADRs:** ADR-004, ADR-006, ADR-007, ADR-008, ADR-009 |
**Imports (spec-tree):** capability evidence, semantic port/OpenAI adapter/check specs |
**Imported by:** optional provider support matrix

## Purpose

Observe one explicitly approved provider endpoint/model/SDK profile with synthetic minimized data,
hard cost/network/time caps, provenance capture, and deterministic post-validation. It is optional
and never part of strict-local correctness. Although this is release capability evidence, its one
provider dispatch is deliberately classified and authorized as `llm_inference`; it is not the
unsupported v0.1 product `capability_testing` network channel.

## Public surface

Markers require `live_provider`. Cases cover structured success and safely observable refusal,
invalid output, rate limit, timeout/cancellation where the provider/test environment can reproduce
them without uncontrolled spend. Each endpoint/model/SDK/region is a distinct evidence cell.

## Behavior

Run only in an approved isolated capability environment with an already-ready trusted service. A
trusted release operator provisions a disposable provider credential through approved confidential
ingress and OS-keyring/vault handling outside the test process; CI is eligible only when that exact
procedure is supported. The operator must explicitly enable and approve the named `llm_inference`
cell under its active egress policy; the test marker or harness cannot supply consent or turn on the
unsupported `capability_testing` channel. Verify candidate/profile/model/SDK and non-secret `configured` profile
status without testing or revealing credential presence. Allow network only to the named endpoint;
set `trust_env=False`, no proxy/netrc/env auth, redirects off, exact HTTPS host/port/path/method/
TLS/SNI/certificate validation, adapter retries zero, request `max_output_tokens=2048`, response
body/encoding cap `1_048_576`, and spend/wall-time caps to frozen policy. Submit a public synthetic
case containing no repository/user data and verify minimized payload digest.

Capture the custom transport's structural proof that the actual SDK request body bytes equal the
deterministic rendered body, the receipt commitment covers those bytes with the exact trailing-NUL
privacy-audit domain, and the real credential was consumed by one body/profile/deadline-bound
header-injection callback after equality with the gateway's precomputed commitment. Inspect the
per-attempt SDK client/default headers before disposal and
prove they contain only the fixed nonsecret sentinel, never the real key. Retry creates a new client,
transport, dispatch, and handle.

Capture only the bounded provider request ID/provenance/usage fields, parsed judgment when valid,
and the structural egress receipt. Raw provider response bytes are never retained, even as
ephemeral test evidence. Normalize through the production adapter, then apply deterministic
schema/freshness/finding fences and compare the bounded outcome. Refusal/rate limit/outage is
refusal/inconclusive/fail, never pass; release narrows the optional claim if a required cell lacks
evidence.

## Errors and edge cases

- Unapproved/manual developer execution skips as unauthorized but cannot count as passing release
  evidence.
- Credential never enters the test process, environment, argv, config artifact, cache, logs,
  reports, or exception; the service exposes only opaque configured/unavailable status.
- Unexpected extra request/retry, host, proxy, redirect, cost/token/body cap, content encoding, or
  cleanup failure fails immediately. Poisoned proxy/netrc/transport environment is a negative test.
- Body digest/commitment/profile/destination/TLS/deadline mismatch, credential callback reuse/
  retention, a stock transport, raw/chunked/compressed response over the cap, or real credential
  retention in the SDK client fails before the cell can pass.
- Live output cannot strengthen deterministic coverage or select after stale frontier.

## Invariants

1. Only synthetic minimized data leaves CI.
2. One approved profile and exact candidate are tested.
3. Spend/request/network/time are hard bounded.
4. Semantic evidence remains advisory and freshness-fenced.
5. Strict-local support is independent.

## Tests

Fake provider covers the exhaustive failure matrix elsewhere. This live file emits redacted evidence
and proves its own negative secret/canary/output scan before retention.

## Open questions

None.

E-007 is the sole central provider-profile gate.
