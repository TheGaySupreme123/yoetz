# tests/conformance/adapters/test_start_catalog_port.py — start catalog port parity

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):**
`src/yoetz/ports/start_catalog.md`, `tests/conformance/adapters/test_ledger_port.py.md`
**Imported by:** conformance adapter tests

## Purpose

Prove the in-memory and SQLite start-catalog ports expose identical reserve/resume/complete and
quarantine semantics.

## Public surface

- `test_reserve_resume_complete_parity` — the normal state machine matches.
- `test_quarantine_and_reclaim_parity` — failure and reclaim paths match.
- `test_generation_and_route_identity_parity` — IDs and route state are equal where they should be.

## Behavior

The test drives both backends through the same start sequences and asserts:

- identical public states and outcomes;
- identical quarantine semantics;
- identical generation-based authority rules;
- diagnostic row/handle differences do not affect the oracle.

## Errors and edge cases

- A backend that advances past a quarantined state fails.

## Invariants

1. Start-catalog behavior is public and identical.
2. Generation and route identity stay exact.
3. Quarantine is consistent.

## Tests

- `tests/conformance/adapters/test_start_catalog_port.py`

## Open questions

None.
