# src/yoetz/adapters/providers/fake.py — scripted semantic evaluator fake

**Wave:** E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** `ports/semantic.md`,
`domain/privacy.md`
**Imported by:** unit, integration, conformance, and capability tests only

## Purpose

This file provides a deterministic test double for exercising the real privacy gateway, semantic
coordinator, and post-validation path without a live model. Production strict-local, denied, and
disabled paths construct no evaluator and never use this fake as a fallback.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `ScriptedFakeSemanticEvaluator` | scripted implementation of `SemanticEvaluatorPort` |
| `FakeSemanticScript` | immutable sequence of scripted provider outcomes and delays |
| `scripted_success(...)` | emit a parsed judgment and provisional provider-attempt provenance |
| `scripted_refusal(...)` | emit a refusal result |
| `scripted_timeout(...)` | emit a timeout result |
| `scripted_invalid(...)` | emit malformed or schema-invalid output |
| `scripted_late(...)` | emit a late-arriving result |

## Behavior

Only the explicit `test-fake` test composition may register the fake behind
`PolicyEnforcingOutboundGateway`; installed production startup profiles cannot import or construct
it. It accepts only an `ApprovedOutboundCase`, consumes a fixed script of outcomes, and returns them
in order. It is deterministic by construction: the same script, case, and deadline produce the same
result sequence. Privacy denial/disabled semantic behavior is tested by proving no evaluator call,
not by calling the fake to simulate absence.

### Script model

`FakeSemanticScript` is an immutable sequence of steps. Each step may specify:

- a delay before resolution;
- a parsed success judgment;
- a refusal;
- a timeout;
- malformed or schema-invalid output;
- a late-arriving result;
- a provider-unavailable style failure.

The script is consumed exactly once per `evaluate(...)` call. If the script is exhausted, the fake
must fail loudly in test code rather than silently repeating the final value.

### Deterministic timing

If a step includes a delay, the fake resolves it deterministically. In async tests that can mean
awaiting the delay; in unit tests it can mean using an injected scheduler or clock hook. The fake
itself must not introduce nondeterministic wall-clock dependence.

### Result fidelity

The fake must be able to produce every semantic outcome the coordinator needs to handle:

1. a valid success judgment that later fails post-validation;
2. a refusal;
3. a timeout at the deadline boundary;
4. an invalid output or wrong schema;
5. a late result after lease loss;
6. a provider-unavailable error class.

The fake returns the same closed semantic result variants as the live adapter so the coordinator can
exercise the real state machine.

### Adversarial cases

The conformance suite uses this fake to simulate:

- invented IDs;
- out-of-case evidence references;
- stale frontier claims;
- duplicate response arrivals;
- refusal after a valid earlier attempt;
- coverage inflation;
- invalid JSON;
- wrong schema;
- late results after another state has already been committed.

This file should keep those cases declarative so tests can read like the public contract rather than
provider-specific plumbing.

### Failure semantics

The fake returns `SemanticResultUnavailable` wherever the live adapter would classify an expected
provider/transport/auth capability failure. It raises only injected internal defects used to test
the sanitizing boundary.
Everything else should be returned as a semantic result variant so the coordinator can classify it
in the same way it would classify a live provider.

The fake must never:

- reach out to the network;
- depend on the OpenAI SDK;
- mutate the case input;
- invent richer provenance than the live adapter would have;
- bypass the coordinator’s post-validation path.
- bypass privacy classification/authorization/receipt merely because it is a test fake.
- appear in a strict-local or other production service composition.

### Bounded provenance

The fake’s `ProviderAttemptProvenance` is enough for retry/status tests and no more. It preserves
the same provisional fields and bounded value rules as the live adapter but never includes an
authorization/reservation/receipt ID or final `SemanticProvenance`; the real coordinator must
finalize it after the fake path's real privacy receipt becomes durable.

The fake must support the adversarial cases the conformance suite needs:

- invented IDs;
- coverage inflation;
- stale frontier handling;
- malformed output;
- refusal;
- timeout;
- invalid JSON or wrong schema;
- late arrival after lease loss.

The fake may also script success cases that look superficially plausible but fail the deterministic
post-validation rules, so the coordinator’s validation fence is exercised end-to-end.

The fake should preserve enough provenance for the coordinator to verify retry and audit behavior,
but it must never reach out to a network or depend on the real OpenAI SDK.

## Errors and edge cases

- Exhausting the script is a test failure, not a silent repetition.
- A malformed scripted result should fail in the same public category the live adapter would use.
- Late results should remain visible as late, not silently folded into success.
- A scripted output that exceeds the semantic port’s size limits is invalid.
- If script timing conflicts with the deadline, the deadline wins and the fake returns a timeout or
  deadline-classified failure rather than success.

## Invariants

1. Deterministic script, deterministic outputs.
2. No network.
3. The fake matches the semantic port contract exactly enough for the coordinator to be unaware it
   is fake.
4. Adversarial cases remain first-class.
5. The fake never silently succeeds on exhausted input.
6. Provenance remains bounded and explicit.
7. Tests claiming privacy coverage instantiate it only behind the real gateway.

## Tests

- `tests/integration/providers/test_fake_provider_coordinator.py` — script ordering, the full
  adversarial outcome/timing matrix, bounded provenance, and isolation.

## Open questions

None.
