# tests/unit/service/test_runtime_context.py — ready-service runtime-context unit suite

**Wave:** C | **ADRs:** ADR-001, ADR-008 | **Imports (spec-tree):** `src/yoetz/ports/runtime.md` | **Imported by:** test runner

## Purpose

Verify generation/capability/route values replacing obsolete per-client runtime scopes.

## Public surface

Construction, least-authority, route/provision validation, stale/closed task facade tests.

## Behavior

Assert only ready matching service/vault/catalog generations admit route/provision; client kinds never enter runtime values.

## Errors and edge cases

Stale generation, access escalation, mismatched writer/session, closed runtime, relock.

## Invariants

1. No `RuntimeScopeKind`/one-shot CLI/MCP ownership remains.
2. Runtime values expose no key/path.

## Tests

This file is the executable owner.

## Open questions

None.
