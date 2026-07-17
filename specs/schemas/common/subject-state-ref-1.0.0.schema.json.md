# schemas/common/subject-state-ref-1.0.0.schema.json — subject state reference schema

**Wave:** A/B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/domain/events.md`, `src/yoetz/domain/findings.md`
**Imported by:** exactly the action-recorded, result-recorded, evidence-recorded, and
claim-recorded event payload schemas

## Purpose

Describe the bounded reference object that binds claims and actions to repository/artifact state.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/common/subject-state-ref/1.0.0`.
- Owning model: `SubjectStateRef`.

## Behavior

Closed object with optional fields:

- `tree_digest`
- `diff_digest`
- `described_state`

At least one digest-like reference must be present when the owning contract requires a freshness
anchor. The explanatory state text is optional and never participates in deterministic equality.
Extra keys are forbidden.

The four v0.1 event owners of this common freshness-anchor shape are exactly
`action-recorded-1.0.0`, `result-recorded-1.0.0`, `evidence-recorded-1.0.0`, and
`claim-recorded-1.0.0`. Other schemas may reference those events or compare their anchors, but no
other event payload acquires a `subject_state` field implicitly through this common schema.

## Errors and edge cases

- Unbounded descriptive text fails if it exceeds the schema limit.
- A reference missing all anchor material fails when the owning schema requires one.

## Invariants

1. Subject-state refs are bounded anchors.
2. Descriptive state is non-authoritative.
3. Extra keys are forbidden.

## Tests

- `tests/unit/domain/test_event_payloads.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
