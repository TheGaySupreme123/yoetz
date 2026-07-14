# fixtures/privacy/PRIV-006-policy-loosening.case.json — local-human-only policy widening

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
policy and setup schemas | **Imported by:** privacy unit, property, conformance and subprocess tests

## Purpose

Freeze monotone policy mutation: tightening applies safely; widening pauses for a trusted local-human
confirmation that MCP, agents, plugins, imports and providers cannot manufacture.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-006`, initial policy version `7`, with exact diffs:
trusted-provider to local-only, category removal, local-only to minimal-external, category addition,
provider/endpoint change, scope broadening and independent channel enablement.

## Behavior

The first two tightening diffs commit immediately with version compare-and-swap. Every widening
diff returns `decision_required` with pending `ppr_` UUIDv4, exact draft digest/version/expiry and no
authority. Forged MCP, agent, imported and provider assertions fail. A separate foreground
`HumanControlService` renders/reauthenticates/commits the exact diff internally without a serialized
proof. Changing any field after review invalidates the pending decision. Revocation or
tightening immediately prevents a not-yet-dispatched approved case from leaving.

## Errors and edge cases

Concurrent version change, expired pending decision, service-generation change, replayed request,
UI disconnect, crash before commit, and widening disguised as “rename” commit nothing. Enabling
telemetry is independently classified as widening even when LLM is already external.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. Non-human surfaces
may propose, never authorize, a broader disclosure boundary.

## Tests

`tests/unit/privacy/test_policy_and_contracts.py`,
`tests/property/test_egress_policy_properties.py`,
`tests/conformance/privacy/test_never_send_scope_and_channels.py`, and
`tests/subprocess/test_service_lock_and_confidential_unlock.py`.

## Open questions

None.
