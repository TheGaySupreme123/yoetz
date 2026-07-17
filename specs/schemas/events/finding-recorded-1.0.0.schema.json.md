# schemas/events/finding-recorded-1.0.0.schema.json — finding-recorded payload schema

**Wave:** B | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/findings.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** check, replay, and receipt tests

## Purpose

Describe the engine-authored finding payload mirrored field-for-field from the finding model.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/finding-recorded-1.0.0.schema.json`.
- Owning model: `Finding`.

## Behavior

Closed payload object with:

- `finding_id`;
- `kind`;
- `origin`;
- `priority`;
- `summary`;
- `detail`;
- `subject_refs`;
- `policy_id`;
- `policy_version`;
- `subject_frontier`;
- `coverage`;
- `provenance` when semantic.

The schema delegates the nested finding/coverage/provenance validation to their own schemas.

## Errors and edge cases

- Semantic provenance missing on semantic origin fails.
- Unknown finding kind fails.

## Invariants

1. Finding payload matches the domain model.
2. Coverage is explicit.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
