# tests/integration/observation/test_e2e_successor_acceptance.py — successor E2E gates

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):**
`specs/tests/integration/observation/test_acceptance_scenarios.py.md` |
**Imported by:** PR CI observation suites

## Purpose

Prove the successor observation/advice gates beyond PR #11: project-scoped `--workspace .`
hooks, independent multi-workspace session binds, and background verification supervisor wake
behavior.

## Public surface

Pytest module under `tests/integration/observation/`.

## Behavior

Scenarios cover installed hook command/timeout shape, two consented workspaces binding distinct
Codex sessions without cross-guessing, supervisor notify/drain, and unconsented `.` rejection.

## Errors and edge cases

Consent-inactive and missing hook workspace binding remain fail-closed.

## Invariants

1. No plaintext workspace paths asserted in status/log surfaces.
2. Hook observe timeouts stay within the three-second budget.

## Tests

This file is the test authority.

## Open questions

None.
