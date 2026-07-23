# tests/unit/kernel/test_observation_advice_policies.py — observation advice rules

**Wave:** D | **ADRs:** ADR-005, ADR-010 | **Owner:** `kernel/policies/observation_advice.md`

Unit coverage for each observation-advice rule against synthetic envelopes and optional
inspect/check/composition facts.

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
