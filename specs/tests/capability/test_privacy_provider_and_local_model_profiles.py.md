# tests/capability/test_privacy_provider_and_local_model_profiles.py — exact privacy runtime capability evidence

**Wave:** E/F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports (spec-tree):**
privacy profiles/gateway, exact provider/local-runtime/platform support manifests | **Imported by:**
capability suite, public claims and release evidence

## Purpose

Collect artifact-bound evidence that advertised external provider and local-model runtime cells honor
privacy boundaries; no untested provider/model/platform range is inferred.

## Public surface

Parameterized exact cells name package/resource digest, OS/runtime, service/control version,
provider SDK/provider/model/endpoint profile or local runtime/model, policy profile, network harness,
fixtures, timestamps and normalized outcome. Live external cases are explicit opt-in.

## Behavior

For each advertised external cell, send one approved synthetic minimal case as an explicitly
operator-authorized `llm_inference` attempt—not as product `capability_testing` traffic—and validate destination/
exact final-body request commitment/receipt, per-attempt custom credential transport with no real
key in SDK client/default headers, refusal/timeout/invalid degradation, redirect denial, composition
that passes no repository/storage/environment handles, reviewed bundled-adapter avoidance of forbidden
APIs, and zero non-LLM channels. This is not a same-UID sandbox claim. For each local-model cell, prove no AF_INET/AF_INET6/DNS/redirect/
download/subprocess, exact config-to-installed-registry tuple resolution, service-owned socket-handle
resolution with owner/mode/peer/generation checks, identical classification/minimization/never-send
behavior, and a local disclosure receipt. An empty registry and a config tuple absent from the
installed registry must report unavailable without opening any socket.
The no-network probe applies to the Yoetz adapter process. The separate AF_UNIX runtime is a trusted
disclosure sink; a runtime-wide no-network result requires artifact-bound sandbox/network-denial
evidence for that exact runtime profile. Otherwise the cell records that runtime egress is
unverified and no broader public claim is allowed.
Run confirm-every-request only with local trusted preview authority; never automate approval through
MCP/agent. Unsupported/unconfigured cells record unsupported, never pass.

The v0.1 capability manifest lists `product_telemetry`, `crash_diagnostics`, `update_checks`, and
`capability_testing` as `unsupported`, with no adapter/profile cell. Proposing any row on returns
`channel_unavailable`, changes no durable policy, and makes no network attempt. A simulated future
manifest addition still leaves the row off until a fresh exact local-human transition; no stored
v0.1 choice or setup answer becomes dormant consent.
The optional release suite's explicitly approved synthetic provider call remains governed by the
separate `llm_inference` row and all of its preview/consent rules; naming a test “capability” grants
no network or disclosure authority.

## Errors and edge cases

The harness never receives or injects credential bytes. A trusted release operator provisions the
isolated ready service through approved confidential ingress/keyring handling before the test; the
harness receives only the non-secret configured profile ID and readiness status. If that setup is
unavailable, the cell is unsupported/inconclusive. Unexpected provider text, identifiers, URLs,
latency detail and response bodies are discarded or normalized into the bounded structural receipt;
capability evidence cannot promote a profile or model not named exactly.

## Invariants

1. Evidence is exact-cell, artifact-bound and synthetic.
2. External and local model paths satisfy the same content fences.
3. Live provider permission enables no other network channel.
4. Skipped/inconclusive cells support no public claim.
5. Evidence contains no credentials or request/response plaintext.
6. Config supplies no endpoint locator; every opened local socket derives from the installed exact
   profile and trusted service resolver.
7. AF_UNIX transport alone is not evidence that the model runtime has no network authority.
8. No production non-LLM transport cell is inferred from policy vocabulary, and a future cell does
   not activate without fresh local-human authority.

## Tests

Run only through the release capability harness with explicit live opt-in; local-runtime cells run
offline. Evidence is consumed by public-claim and support-manifest generation.

## Open questions

None.
