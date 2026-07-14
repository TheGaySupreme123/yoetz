# fixtures/privacy/PRIV-002-confirm-every-request.case.json — per-request local-human approval

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
schemas and setup contract | **Imported by:** privacy conformance, gateway integration and subprocess
tests

## Purpose

Freeze exact preview, authority, persistence, and retry behavior for `confirm_every_request`.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-002`, policy
`pvy_22222222-2222-4222-8222-222222222222`, request scope `req_23232323-2323-4232-8232-232323232323`,
provider `provider.example`, endpoint profile `ep_example_v1`, model `model-example-1`, purpose
`semantic-review`, categories `task_description` and `evidence_excerpt`, fixed internal
case ID, two proposal/authorization/dispatch ID sets, one total retry deadline, and UTC expiries.

## Behavior

The MCP-originated candidate pauses at nonterminal audit state `awaiting_human` and returns only
bounded proposal/request IDs plus expiry; neither waiting nor later `approved` state creates a
finished `EgressReceipt`. An MCP-supplied consent assertion and an
agent message claiming approval are both rejected with `local_human_required` and no network. The
trusted authenticated local control session displays exact post-minimization excerpts, removals,
destination, purpose, scope and counts, then records request-specific approval bound to case/policy/
scope digests and one physical dispatch. A crash after approval but before authorization consumption
resumes that same dispatch with no second prompt; it is not a retry and still dispatches at most
once. After a retry-eligible first physical timeout, identical bytes enter a new
`awaiting_human` proposal with a fresh exact preview/decision, authorization, dispatch ID, credential
handle, and receipt. Without the second foreground decision there is no second I/O and semantic
work completes incomplete when the total deadline expires. Approval permits exactly that second
attempt; denial prevents it. Changed bytes, policy, scope, destination, purpose, or expiry also
requires a new preview. Every attempt receipt names `per_request_local_human` and contains no
plaintext. Authorization consumption enters internal `receipt_pending`; only the real terminal
attempt outcome finalizes the receipt. The first timeout receipt requires
`safe_failure_reason=provider_timeout`; any completed retry receipt forbids `safe_failure_reason`.

## Errors and edge cases

Expired pending decision, changed scope/model/category, replay in another service generation, and approval
after expiry each deny before adapter construction. Response loss does not duplicate dispatch when
the durable attempt already proves it occurred and never silently converts the first decision into
retry consent.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. Approval authority
is unavailable to MCP/agent and binds exactly one physical dispatch. Pre-consumption resume preserves
that dispatch, while every later physical retry requires a fresh foreground preview/decision even
for byte-identical content.

## Tests

`tests/conformance/privacy/test_privacy_profiles.py`,
`tests/integration/privacy/test_egress_gateway.py`, and
`tests/subprocess/test_service_lock_and_confidential_unlock.py`.

## Open questions

None.
