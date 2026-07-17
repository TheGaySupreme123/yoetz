# schemas/findings/finding-1.0.0.schema.json — finding schema

**Wave:** B | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/findings.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** check/result schemas, receipts, and parity fixtures

## Purpose

Describe the canonical public finding value used by checks, receipts, and ranking.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/findings/finding-1.0.0.schema.json`.
- Owning model: `Finding`.

## Behavior

Closed object with required fields:

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
- `provenance` only when origin is semantic.

The schema enforces bounded text, stable refs, explicit coverage, and the semantic provenance gate.

## Errors and edge cases

- Semantic provenance missing on semantic findings fails.
- Unknown kinds or extra keys fail.

## Invariants

1. Coverage is explicit.
2. Semantic provenance is gated by origin.
3. Finding IDs are required.

## Tests

- `tests/unit/domain/test_findings.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
