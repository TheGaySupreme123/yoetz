# tests/unit/application/test_observation_check_policy.py — policy parser gates

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/src/yoetz/application/observation_check_policy.md` |
**Imported by:** unit suite

## Purpose

Prove exact-byte digest identity, strict schema/argv, and no-follow project loading.

## Public surface

Pytest unit cases.

## Behavior

Whitespace changes the digest; unknown/free-form fields reject; symlinked `.yoetz` rejects; a
fixed in-workspace file parses.

## Errors and edge cases

All invalid cases raise bounded `ProtocolValueError`.

## Invariants

No test executes a shell.

## Tests

This file.

## Open questions

None.
