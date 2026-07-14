# tests/builders/clock.py — explicit clock and timestamp builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/builders/__init__.md`, `specs/tests/fixture_loader.py.md` |
**Imported by:** unit/property/integration/conformance fixtures

## Purpose

Provide explicit, deterministic clock helpers for tests. The module makes time values caller-owned
instead of sourcing them implicitly from the environment or wall clock.

## Public surface

- Builder helpers for canonical UTC timestamps and monotonic timestamps used in test data.
- A frozen clock helper for tests that need repeatable time progression.

## Behavior

The helpers accept explicit timestamps or explicit step sequences and return deterministic values.
They do not read the live system clock unless a test intentionally asks for a live-adjacent
observation in an integration fixture. They never guess a default `now`, never capture ambient
timezone state silently, and never mutate shared clock state across tests.

## Errors and edge cases

- Missing explicit time inputs fail closed.
- Ambiguous timezone or malformed timestamp values are rejected.

## Invariants

1. Test time is explicit.
2. No helper silently supplies `now`.
3. Clock values are reproducible under seed/order changes.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`

## Open questions

None.
