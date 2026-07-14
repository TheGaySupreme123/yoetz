# tests/conftest.py — shared pytest fixtures for immutable test support

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/fixture_loader.py.md`, `specs/tests/builders/__init__.md`,
`specs/tests/builders/ids.py.md`, `specs/tests/builders/clock.py.md`,
`specs/tests/builders/events.py.md`, `specs/tests/builders/operations.py.md` |
**Imported by:** the unit, integration, conformance, packaging, and subprocess suites

## Purpose

Centralize the shared pytest fixtures used by the suite so every test module consumes the same
read-only fixture corpus and the same explicit builder namespaces.

## Public surface

- Pytest fixtures exposing the shared read-only fixture loader.
- Pytest fixtures exposing the explicit builder namespaces for IDs, clock values, events, and
  operations.
- No runtime application/bootstrap helpers, no hidden global state, and no private test-only
  convenience API beyond those shared fixtures.

## Behavior

The module only wires reusable fixtures and collection hooks. It does not read user configuration,
touch the network, build runtime state, or manufacture correctness-relevant defaults. All
fixture-producing helpers are deterministic and process-local.

## Errors and edge cases

- Import-time side effects, filesystem writes, or implicit fixture generation are forbidden.
- The module must not create alternate copies of reviewed fixtures or builders.

## Invariants

1. Shared test support is centralized.
2. Fixture access is read-only and manifest-bound.
3. Builder helpers remain explicit and deterministic.

## Tests

- `specs/tests/unit.md`
- `specs/tests/integration.md`
- `specs/tests/conformance.md`
- `specs/tests/packaging.md`

## Open questions

None.
