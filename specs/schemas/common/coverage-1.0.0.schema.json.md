# schemas/common/coverage-1.0.0.schema.json — coverage value object schema

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz_core/protocol/coverage.md`, `src/yoetz_core/protocol/models.md`
**Imported by:** findings, receipts, and public-operation schemas

## Purpose

Describe the frozen coverage object used to label evidence strength and honesty bounds.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/common/coverage/1.0.0`.
- Owning model: `Coverage`.

## Behavior

Closed object with required fields:

- `publication_channels`
- `authorship_assurance`
- `artifact_observation`
- `evidence_immutability`
- `ledger_freshness`
- `check_types`
- `known_gaps` (array of bounded strings, possibly empty)

The ordered dimensions must use the published enum values from the coverage registry.
`publication_channels`, `check_types`, and `known_gaps` are canonical sorted-unique arrays; the
first two are nonempty exactly when the owning coverage model requires an observed channel/check.
No numeric scoring or averaging is allowed.

## Errors and edge cases

- Unknown enum values fail closed.
- Duplicate gaps or unsorted gap arrays fail.
- Extra properties fail.

## Invariants

1. Coverage is a closed honesty object.
2. Ordered dimensions only weaken.
3. Gap arrays are canonical.

## Tests

- `tests/unit/protocol/test_coverage.py`
- `tests/conformance/honesty/test_coverage_weakening.py`

## Open questions

None.
