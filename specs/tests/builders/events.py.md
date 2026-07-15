# tests/builders/events.py — explicit event-payload builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz/domain/events.md`, `specs/tests/builders/ids.py.md`,
`specs/tests/builders/clock.py.md` | **Imported by:** unit/property/integration/conformance tests

## Purpose

Build explicit event payloads and related envelopes for tests. The module exists so event cases can
be assembled without hiding any field that affects correctness.

## Public surface

- Builder helpers for the 16 public event families, plus known-unknown draft cases where needed by
  conformance tests.
- Builder helpers for canonical envelopes that pair an event payload with explicit IDs and
  timestamps.

## Behavior

Each helper requires explicit values for the event family’s correctness-relevant fields, including
IDs, frontier values, references, and coverage where applicable. The module does not infer plan
versions, causality, or subject bindings from context. It only packages the caller’s explicit
inputs into deterministic test data.

## Errors and edge cases

- Missing required fields or implicit family defaults fail closed.
- The module must not fabricate event history or resolve contradictions on its own.

## Invariants

1. Event builders are explicit.
2. No hidden correctness defaults are introduced.
3. Family-specific payload shape remains visible in tests.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`
- `specs/tests/conformance.md`

## Open questions

None.
