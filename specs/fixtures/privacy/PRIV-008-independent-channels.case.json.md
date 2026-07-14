# fixtures/privacy/PRIV-008-independent-channels.case.json — independent egress-channel consent

**Wave:** C/E/F | **ADRs:** ADR-006, ADR-007, ADR-009 | **Imports (spec-tree):**
privacy policy/receipt schemas | **Imported by:** zero-egress, privacy conformance, capability and
public-claim tests

## Purpose

Freeze that LLM inference, telemetry, crash diagnostics, update checks and capability testing never
share implied consent, destinations, content permissions, or receipts.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-008`, with five exact channel-isolation
cases, an all-disabled control, and rejected ceiling conflicts. The LLM case uses
`minimal_external`, `network_egress_permitted=true`, and only `llm_inference` enabled. Each of the
four non-LLM cases starts from `local_only` with a true ceiling/all channels off and proposes
enabling exactly one currently unsupported row. The control uses a false ceiling/all channels off.
It fixes synthetic destinations, machine/task scopes, structural payloads, a task-content/crash
canary, policy-transition outcomes, decision-receipt shapes, and network-attempt expectations.

## Behavior

The LLM-only variant permits one exact task-scoped inference attempt/attempt receipt and proves the
other four channels make no attempt. Each non-LLM proposal is rejected by the v0.1 application as
`channel_unavailable`: the prior durable policy remains all-off, no authorization is consumed, no
adapter/transport is constructed, and no DNS/socket attempt occurs. A forced/imported enabled row
is also fenced at use time and produces one pre-dispatch structural decision receipt with
`outcome=channel_unavailable`, `safe_failure_reason=channel_unavailable`, and no authorization ID,
dispatch ID/time, request commitment, or attempt-only request-body count. It is not an attempt
receipt and cannot be confused with an ambiguous transport failure. Its reserved subject is a
structural `PreDispatchAuditDecision` with no prepared/encrypted content object. No task/user-content or canary
appears anywhere.

The all-disabled control uses `profile=local_only`, `network_egress_permitted=false`, and all five
disabled channels. The exact service/confidential endpoints, release-cell OS credential/user-
presence/session-lifecycle security IPC, and an
optional separately approved local-model AF_UNIX profile are local non-egress. All peers/routes are
allowlisted; arbitrary AF_UNIX/bus/proxy use fails. It records zero AF_INET/AF_INET6/DNS/redirect
attempts. Negative cases pair a false ceiling with each proposed
enabled channel and fail schema/application validation before construction or I/O. A true ceiling
with all five channels disabled is valid and makes zero attempts, proving the ceiling is not
consent.

## Errors and edge cases

Shared hostname, SDK client, HTTP pool, endpoint profile prefix, setup screen, or operation does not
merge authorization. Crash handling cannot opportunistically upload because LLM is enabled. Update
checks cannot download/run code under a check-only consent. Neither `local_only` nor a true global
ceiling silently enables a non-LLM channel. Installing a later capability cannot activate an old
proposal or crafted policy; it requires a fresh exact local-human policy transition.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. The only v0.1
physical attempt is the explicitly enabled LLM case. Non-LLM rows prove independent vocabulary and
fail-closed unavailability through decision receipts only. True zero network is asserted only for
the false-ceiling all-disabled control, not every `local_only` policy token.

## Tests

`tests/conformance/privacy/test_never_send_scope_and_channels.py`,
`tests/conformance/honesty/test_strict_local_zero_egress.py`, and
`tests/capability/test_privacy_provider_and_local_model_profiles.py`.

## Open questions

None.
