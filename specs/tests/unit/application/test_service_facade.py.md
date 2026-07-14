# tests/unit/application/test_service_facade.py — ready-only application facade unit suite

**Wave:** D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz_core/application/service.md` | **Imported by:** test runner

## Purpose

Verify only a current ready service context constructs/uses/closes Application.

## Public surface

Delegation, generation, cancellation, incomplete-semantic, error fencing, and close tests.

## Behavior

Assert six methods delegate once, semantic failure returns deterministic incomplete_check, relock/stale context denies, close orders resources.

## Errors and edge cases

Partial startup, result validation defect, disconnect, cancellation around shielded commit.

## Invariants

1. No per-client RuntimeFactory exists.
2. Application never escapes daemon composition.

## Tests

This file is the executable owner.

## Open questions

None.
