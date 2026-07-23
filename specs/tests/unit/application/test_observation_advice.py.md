# tests/unit/application/test_observation_advice.py — advice snapshot wiring

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Owner:** `application/observation_advice.md`

Covers zero-cooperative-publication advice, suppression reissue, deterministic-only vs configured
semantic path, hook refresh wiring, and secret-like output absence from advice/status.

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
