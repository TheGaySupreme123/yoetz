# tests/integration/storage/test_owner_generation.py — owner generation and write authority

**Wave:** C | **ADRs:** ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz_core/adapters/sqlite/connection.md`, `src/yoetz_core/adapters/sqlite/repository.md`
**Imported by:** integration storage tests

## Purpose

Prove only the current owner generation can append or checkpoint, even when lease timing looks
plausible.

## Public surface

- `test_current_generation_can_write` — the live owner succeeds.
- `test_stale_generation_is_rejected` — stale ownership cannot write or checkpoint.
- `test_expired_lease_and_generation_are_both_respected` — both signals matter.
- `test_cli_style_and_mcp_style_ownership_match` — ownership semantics are identical across
  callers.

## Behavior

The test uses controlled connections to race owner generation changes and asserts:

- generation, not wall clock alone, decides write authority;
- stale owners fail even with a not-yet-expired lease;
- current owners can write and checkpoint normally;
- ownership semantics are the same for CLI-like and MCP-like callers.

## Errors and edge cases

- A stale generation that can still checkpoint fails the test.
- A lease-only check that ignores generation fails the test.

## Invariants

1. Current generation is the write authority.
2. Lease and generation are both enforced.
3. Ownership semantics do not depend on caller surface.

## Tests

- `tests/integration/storage/test_owner_generation.py`

## Open questions

None.
