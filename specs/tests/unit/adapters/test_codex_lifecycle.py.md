# tests/unit/adapters/test_codex_lifecycle.py — mapping store unit tests

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):**
`src/yoetz/adapters/integrations/codex_lifecycle.py.md` | **Imported by:** adapter unit suite

## Purpose

Lock allowlisted mapping round-trip, fail-closed reads, atomic 0600 writes, forbidden token
rejection, clear, and single-flight lock coalescing.

## Public surface

pytest cases covering store/load/clear/lock behaviors.

## Behavior

Uses an injected temporary state root. Asserts exact schema rejection, oversized-file absence, path
separator rejection, and that concurrent lock acquisition admits exactly one owner.

## Errors and edge cases

Malformed JSON and wrong versions are absent, never partially trusted.

## Invariants

1. Fail closed on schema drift.
2. Private atomic writes.

## Tests

This file.

## Open questions

None.
