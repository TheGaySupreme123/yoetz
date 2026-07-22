# tests/unit/adapters/test_approved_checks.py — approved-check binding coverage

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Owner:** `adapters/approved_checks.py.md`

Covers subject-state binding/staleness, unapproved argv rejection, and absence of secret-like
command output in results.

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
