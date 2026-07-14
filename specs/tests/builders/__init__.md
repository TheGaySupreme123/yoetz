# tests/builders/__init__.py — explicit builder namespace package

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/builders/ids.py.md`, `specs/tests/builders/clock.py.md`,
`specs/tests/builders/events.py.md`, `specs/tests/builders/operations.py.md` |
**Imported by:** the suite’s unit/integration/conformance tests

## Purpose

Provide a small explicit namespace for shared test builders. The package exists so tests can import
well-named construction helpers without inventing correctness-relevant defaults in each file.

## Public surface

- No reexports are required; direct module imports are the default.
- The package contains exactly four explicit helper modules: `ids`, `clock`, `events`, and
  `operations`.

## Behavior

Importing the package must not create IDs, clocks, events, or operation payloads on its own. The
modules inside the package are the only place where builder logic lives, and every builder requires
explicit inputs for correctness-relevant values.

## Errors and edge cases

- Hidden defaults, implicit randomness, or module-level fixture fabrication are forbidden.
- The package must not grow additional helper modules without a reviewable spec amendment.

## Invariants

1. Builder logic stays explicit and local.
2. The package contributes no hidden data.
3. Only the four named helper modules belong here.

## Tests

- `specs/tests/unit.md`
- `specs/tests/integration.md`
- `specs/tests/conformance.md`

## Open questions

None.
