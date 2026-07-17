# schemas/operations/publish-work-result-1.0.0.schema.json — publish-work result schema

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004 | **Imports (spec-tree):**
`src/yoetz/application/publish_work.md`, `src/yoetz/protocol/errors.md`
**Imported by:** CLI, MCP, and parity tests

## Purpose

Describe the public result shape for publish-work, including accepted summaries and public errors.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/operations/publish-work-result-1.0.0.schema.json`.
- Owning model: `PublishWorkResultModel`.

## Behavior

Union of success and common public-error branches. The success branch carries:

- accepted event summaries;
- frontiers and digest/identity fields needed to replay the batch;
- bounded warnings and coverage/gap notes;
- exact outcome (`accepted` or replayed equivalent).

The schema must admit the same last-resort error fallback as the other operations.

Accepted IDs, frontiers, counts, schema/version tokens, and digests are structural. Any echoed
event summary, label, statement, reason, reference, warning prose, or user-origin text is content-
bearing and admits only its exact original type or the common omission marker. The success branch
requires the common `agent_context` privacy projection and durable local-disclosure receipt.

## Errors and edge cases

- Partial acceptance is not allowed as a success shape.
- Missing fallback parity fails release.

## Invariants

1. Batch acceptance is atomic.
2. Public-error fallback is shared.
3. Success payload stays bounded.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/operations/test_publish_work_contract.py`

## Open questions

None.
