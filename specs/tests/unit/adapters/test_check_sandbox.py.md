# tests/unit/adapters/test_check_sandbox.py

**Wave:** D | **ADRs:** ADR-009 | **Imports (spec-tree):** `adapters/check_sandbox.py.md`,
`adapters/approved_checks.py.md`

## Purpose

Prove enforcing sandbox preparation, honest unavailable behavior, and `/bin/true`-equivalent
success under macOS Seatbelt when available.

## Public surface

Pytest unit cases over platform launch adapters and the approved-check runner.

## Behavior

- Platform default sandbox
- Unsupported never claims network isolation from env alone
- Approved true succeeds inside enforcing sandbox (Darwin)
- Sandbox unavailable rejects with `sandbox_unavailable`

## Errors and edge cases

Darwin execution requires an environment that permits nested Seatbelt.

## Invariants

No test treats an env marker as network isolation.

## Tests

This file.

## Open questions

None.
