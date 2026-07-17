# fixtures/privacy/PRIV-006-policy-loosening.case.json — local-human-only policy widening

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
policy and setup schemas | **Imported by:** privacy unit, property, conformance and subprocess tests

## Purpose

Freeze monotone policy mutation: tightening applies safely; widening pauses for a trusted local-human
confirmation that MCP, agents, plugins, imports and providers cannot manufacture.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-006`, initial policy version `7`, with exact diffs:
trusted-provider to local-only, category removal, local-only to minimal-external for an installed
eligible provider capability, category addition, provider/endpoint change, scope broadening, and
proposed independent-channel enablement when that channel has no installed v0.1 transport.

## Behavior

The first two tightening diffs commit immediately with version compare-and-swap. A widening that
targets an installed, policy-addressable capability returns `decision_required` with pending `ppr_`
UUIDv4, exact draft digest/version/expiry and no authority. Forged MCP, agent, imported and provider
assertions fail. A separate foreground `HumanControlService` renders/reauthenticates/commits the
exact diff internally without a serialized proof. Changing any field after review invalidates the
pending decision. Revocation or tightening immediately prevents a not-yet-dispatched approved case
from leaving.

Proposed enablement of `product_telemetry`, `crash_diagnostics`, `update_checks`, or
`capability_testing` in v0.1 is not an authorizable widening because no production transport is
installed. It returns `channel_unavailable`, creates no `ppr_` decision or dormant consent, leaves
the prior durable policy unchanged, constructs no adapter, and performs no DNS/socket I/O. A later
reviewed transport still requires a fresh local-human policy transition after installation. This
case is behaviorally aligned with `PRIV-008`; human authority cannot manufacture an absent
capability.

## Errors and edge cases

Concurrent version change, expired pending decision, service-generation change, replayed request,
UI disconnect, crash before commit, and widening disguised as “rename” commit nothing. Enabling an
installed telemetry capability would be independently classified as widening even when LLM is
already external; the unavailable v0.1 capability is rejected before any decision is created.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. Non-human surfaces
may propose, never authorize, a broader disclosure boundary.

## Tests

`tests/unit/privacy/test_policy_and_contracts.py`,
`tests/property/test_egress_policy_properties.py`,
`tests/conformance/privacy/test_never_send_scope_and_channels.py`, and
`tests/subprocess/test_service_lock_and_confidential_unlock.py`. Conformance asserts that supported
widening returns `decision_required` while unavailable-channel enablement returns
`channel_unavailable` exactly as ADR-009 and `PRIV-008` require.

## Open questions

None.
