# tests/unit/config/test_privacy_desired.py — ADR-014 privacy desired-state tests

**Wave:** C | **ADRs:** ADR-009, ADR-014 | **Imports (spec-tree):** `config/privacy_desired.md`,
`application/privacy_policy.md` | **Imported by:** none

## Purpose

Prove privacy desired-state TOML round-trip and that widen is never classified as tighten.

## Public surface

None — pytest module.

## Behavior

Write/load canonical policy bytes; assert `is_privacy_tightening` rejects network-enabling widen
and accepts the reverse tighten.

## Errors and edge cases

Malformed documents are covered by `ConfigError` in the owning module; this file focuses on the
happy path and widen classification.

## Invariants

Widen ≠ tighten.

## Tests

This file is the test.

## Open questions

None.
