# tests/builders/clock.py — explicit clock and timestamp builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/builders/__init__.md`, `specs/tests/fixture_loader.py.md` |
**Imported by:** unit/property/integration/conformance fixtures

## Purpose

Provide explicit, deterministic clock helpers for tests. The module makes time values caller-owned
instead of sourcing them implicitly from the environment or wall clock.

## Public surface

- Builder helpers for canonical UTC timestamps and finite, nonnegative monotonic samples used in
  test data.
- A scripted `ClockPort` helper with explicit wall and monotonic step sequences.

## Behavior

The helpers accept explicit timestamps or explicit step sequences and return deterministic values.
The scripted clock consumes the next caller-supplied value independently for `now_utc()` and
`monotonic_seconds()`; each monotonic sequence is finite, nonnegative, and nondecreasing. Exhausting
either sequence fails instead of repeating or guessing a value. Tests construct
`Deadline(expires_at_utc, monotonic_deadline)` explicitly and pass a captured scripted monotonic
sample into `remaining_seconds(sample)` or `expired(sample)`.

They do not read the live system clock unless a test intentionally asks for a live-adjacent
observation in an integration fixture. They never guess a default `now`, never capture ambient
timezone state silently, never patch global clocks as the source of expected values, and never
mutate shared clock state across tests.

## Errors and edge cases

- Missing explicit time inputs fail closed.
- Ambiguous timezone or malformed timestamp values are rejected.
- NaN, infinity, negative, decreasing, or wrong-typed monotonic sequence members are rejected
  before the clock is used.

## Invariants

1. Test time is explicit.
2. No helper silently supplies `now`.
3. Clock values are reproducible under seed/order changes.
4. Deadline tests cannot pass by consulting an ambient wall or monotonic clock.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`

## Open questions

None.
