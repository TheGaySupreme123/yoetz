# fixtures/privacy/PRIV-007-cross-scope.case.json — authorization scope non-transferability

**Wave:** C/E | **ADRs:** ADR-006, ADR-009 | **Imports (spec-tree):** privacy
policy/outbound-case schemas | **Imported by:** privacy property, gateway and conformance tests

## Purpose

Freeze that machine/workspace/task/request scopes intersect and that an authorization cannot be
reused across a sibling, parent, unrelated workspace, or changed request.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-007`, with machine policy, workspace
`wsp_alpha`, tasks `tsk_alpha_1`/`tsk_alpha_2`, workspace `wsp_beta`, and requests `req_alpha_a`/
`req_alpha_b`. Exact authorizations bind one scope each.

## Behavior

The `tsk_alpha_1` authorization approves only its selected case. Reuse for sibling task, workspace
parent, `wsp_beta`, another request, broader category, endpoint or purpose returns `scope_mismatch`
before adapter construction. Machine permission does not widen a narrower workspace/task denial;
effective policy is intersection. Receipts record opaque scope refs/digest and never filesystem path.

## Errors and edge cases

Prefix collisions, Unicode lookalikes, claimed parentage, moved workspace path, symlink, imported
task ID, and agent-supplied scope assertion cannot establish scope. Unknown ancestry denies.

## Invariants

The fixture is canonical, synthetic, offline, deterministic and test/sdist-only. Scope authority is
explicit durable service state, not string/path inference.

## Tests

`tests/property/test_egress_policy_properties.py`,
`tests/integration/privacy/test_egress_gateway.py`, and
`tests/conformance/privacy/test_never_send_scope_and_channels.py`.

## Open questions

None.
