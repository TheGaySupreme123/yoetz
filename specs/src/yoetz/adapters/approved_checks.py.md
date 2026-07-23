# src/yoetz/adapters/approved_checks.py — commitment-bound approved check runner

**Wave:** D | **ADRs:** ADR-009, ADR-010 | **Imports (spec-tree):** `ports/subject_state.md`,
`protocol/canonical.md` | **Imported by:** observation advice and independent verification tests

## Purpose

Execute only commands explicitly approved by project policy or user grant. Approvals are
commitment records over exact argv (not freeform shell). Results bind to the observed
subject-state digest so a later edit makes prior success stale.

## Public surface

- `ApprovedCheckRunner`, `ApprovedCheckApproval`, `ApprovedCheckCommand`, `ApprovedCheckResult`
- `approval_commitment(...)`
- Status/outcome enums: passed/failed/rejected/timeout/stale

## Behavior

Fixed argv, `shell=False`, sanitized environment, bounded time/output. Network denied unless a
separate authorization path exists (v0.1 runner rejects `allow_network=True`). Stdout/stderr are
digested then wiped; secret-like command output never appears in advice/status. Subject-state
mismatch yields `stale`.

## Errors and edge cases

Fail closed on consent, mapping, and validation errors; never leak secrets.

## Invariants

1. No freeform shell.
2. Approval is a commitment, not prose.
3. Output bytes are never retained for advice delivery.
4. Results bind to exact subject-state digest.

## Tests

`tests/unit/adapters/test_approved_checks.py`

## Open questions

None.
