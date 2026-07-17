# tests/unit/application/test_service_facade.py — ready-only application facade unit suite

**Wave:** D | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz/application/service.md` | **Imported by:** test runner

## Purpose

Verify only a current ready service context constructs/uses/closes Application.

## Public surface

Delegation, generation, cancellation, incomplete-semantic, error fencing, privacy-token identity,
and close tests.

## Behavior

Assert six methods delegate once, semantic failure returns deterministic incomplete_check,
relock/stale context denies, and close orders resources. Assert the six privacy facade names equal
the six registered control tokens exactly; receipt list/get delegate only to the internal
`PrivacyAuditPort.list_receipts`/`get_receipt` methods and expose no aliases. The page fields map
one-to-one; get maps `PrivacyReceiptView` to `found` and `None` to `not_found` without nullable wire
output.

## Errors and edge cases

Partial startup, result validation defect, disconnect, cancellation around shielded commit.

## Invariants

1. No per-client RuntimeFactory exists.
2. Application never escapes daemon composition.

## Tests

This file is the executable owner.

## Open questions

None.
