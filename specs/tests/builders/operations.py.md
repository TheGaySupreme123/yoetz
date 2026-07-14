# tests/builders/operations.py — explicit operation-request/result builder helpers

**Wave:** D–F | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz_core/protocol/models.md`, `specs/tests/builders/ids.py.md`,
`specs/tests/builders/clock.py.md` | **Imported by:** unit/property/integration/conformance tests

## Purpose

Build explicit operation request and result values for the six public workflow operations. The
module keeps request IDs, frontiers, and outcome fields visible instead of inferred.

## Public surface

- Builder helpers for `start`, `publish_work`, `check`, `respond`, `status`, and `receipt`
  request/result cases.
- Helper constructors for operation envelopes, request identities, and derived test variants.

## Behavior

The helpers require explicit caller input for fields that affect correctness, including request
IDs, session/task IDs, expected frontiers, coverage, and selected outcome branches. They do not
invent a result, choose a fallback frontier, or synthesize success from partial inputs.

## Errors and edge cases

- Missing request identity, frontier, or outcome inputs fails closed.
- The module must not collapse request/result distinctions into one hidden default form.

## Invariants

1. Operation builders are explicit.
2. Outcome branches stay visible in the test data.
3. No hidden correctness defaults are introduced.

## Tests

- `specs/tests/unit.md`
- `specs/tests/property.md`
- `specs/tests/integration.md`
- `specs/tests/conformance.md`

## Open questions

None.
