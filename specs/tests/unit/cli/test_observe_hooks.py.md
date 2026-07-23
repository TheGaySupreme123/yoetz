# tests/unit/cli/test_observe_hooks.py

Covers every supported hook payload mapping, unknown future events, consent gating, pre/post
pairing, unpaired gaps, Yoetz self-tool advice suppression, and malformed stdin exit-0 behavior.

## Purpose

Document owned behavior for this module.

## Public surface

See module exports and call sites in the owned path.

## Behavior

Follow the owned implementation and linked ADRs.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No plaintext transcript spool.
2. No seventh MCP tool.
3. Coverage-qualified advice only.

## Tests

Covered by the owning unit/integration/capability suites for this path.

## Open questions

None.
