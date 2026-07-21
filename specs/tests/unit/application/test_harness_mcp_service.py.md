# tests/unit/application/test_harness_mcp_service.py — registration service unit gate

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`specs/src/yoetz/application/harness_mcp.py.md` | **Imported by:** `specs/tests/unit.md`

## Purpose

Locks the service-layer confirmation and diagnostic contract over a fake port.

## Public surface

Pytest module; no exports.

## Behavior

Covers: `status`/`preview` record success diagnostics with matching phases; `register` without
explicit acceptance raises `confirmation_required` before the port sees any command and records
the failure; an accepted registration passes the exact confirmation digest through to the port
command and records the execute success with that digest; a port failure is recorded with its
reason and re-raised; the confirmation channel is the closed
`interactive|noninteractive_flag` set; a non-`HarnessBinary` input is rejected with
`ValueError`.

## Errors and edge cases

The fake port asserts zero mutation on refused paths via its recorded command list.

## Invariants

1. No test reaches a real adapter or subprocess.

## Tests

Self; indexed by `specs/tests/unit.md`.

## Open questions

None.
