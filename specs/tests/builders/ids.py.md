# tests/builders/ids.py — explicit identifier builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/INTERFACES.md`, `specs/tests/fixture_loader.py.md`, `specs/tests/builders/__init__.md` |
**Imported by:** unit/property/integration/conformance fixtures

## Purpose

Provide deterministic ID construction helpers for test data. The helpers must make the ID kind and
seed explicit so tests do not hide correctness-relevant choices.

## Public surface

- Builder helpers for the public Yoetz ID families used by the suite: session, request, operation,
  writer, entry, event, obligation, claim, and finding IDs.
- Validation helpers for hostile or malformed ID inputs used in negative tests.

## Behavior

Every helper requires explicit caller-supplied inputs for the kind-specific pieces that affect the
result. The module never invents a seed, falls back to ambient randomness, or shares mutable global
state. It produces canonical IDs only, and the same explicit inputs always yield the same output.

## Errors and edge cases

- Missing required inputs, wrong kind, or malformed seed data fails closed.
- The module must not normalize hostile input into a different valid ID.

## Invariants

1. ID builders are deterministic.
2. No hidden defaults stand in for correctness-relevant values.
3. Hostile input is rejected, not repaired.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`

## Open questions

None.
