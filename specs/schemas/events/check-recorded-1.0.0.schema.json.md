# schemas/events/check-recorded-1.0.0.schema.json — check-recorded payload schema

**Wave:** D/E | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/findings.md`, `src/yoetz/protocol/coverage.md`
**Imported by:** check and replay tests

## Purpose

Describe the payload that records a completed check and its verdict metadata.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/events/check-recorded/1.0.0`.
- Owning model: `CheckRecordedPayload`.

## Behavior

Closed payload object with:

- `mode`;
- `policies`;
- `subject_frontier`;
- `verdict`;
- `returned_finding_ids`;
- `suppressed_count`;
- `coverage`;
- `semantic_status`;
- `semantic_reason`;
- optional final `semantic_provenance` under the presence rules in `domain/events.md`;
- `engine_version`;
- `projection_version`.

The schema keeps the selected findings, component-wise weakest material `Coverage`, and
status/reason explicit and bounded. The
status/reason pair uses the closed matrix in `ports/semantic.md`; no free-form reason is allowed.
Predispatch outcomes forbid provenance, while attempted outcomes may carry only receipt-finalized
`SemanticProvenance`.

## Errors and edge cases

- Unknown semantic status fails.
- Missing, malformed, or stronger-than-the-checked-input coverage fails.
- Unknown semantic reason, an invalid status/reason pair, or provisional/predispatch provenance fails.
- Hidden or extra findings fail.

## Invariants

1. Check result is explicit.
2. Semantic status is closed.
3. Extra keys are forbidden.
4. Semantic incompleteness has one exact machine-readable cause.
5. Recorded coverage is the conservative coverage summary consumed by replay/projections.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/operations/test_check_contract.py`

## Open questions

None.
