# tests/integration/application/test_import_review.py — Codex JSONL import and review flow

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/adapters/importers/codex_jsonl.md`, `src/yoetz/application/import_review.md`
**Imported by:** integration application tests

## Purpose

Prove the import/review support workflow preserves the source stream, rejects impossible stderr
capture state before persistence, bounds review selection, and keeps imported observations weak.

## Public surface

- `test_codex_jsonl_import_preserves_source_and_quarantine` — the raw source is retained and a
  crafted legacy stderr-present request fails before routing/capture.
- `test_review_selection_and_validation_are_bounded` — review selection errors fail closed.
- `test_imported_observations_use_codex_publication_channel` — imported observations keep the right
  channel and coverage.

## Behavior

The test uses reviewed Codex JSONL fixtures and asserts:

- source retention is exact;
- unsupported or malformed review selections and stderr-present requests are rejected before
  publish/capture;
- imported observations remain imported observations, not locally verified facts;
- the v0.1 capture result carries the exact stderr-absent constants.

## Errors and edge cases

- A selection that silently widens the review scope fails.
- An import that erases source identity fails.

## Invariants

1. Source preservation is exact.
2. Review bounds are explicit.
3. Imported observations do not become stronger than they are.

## Tests

- `tests/integration/application/test_import_review.py`

## Open questions

None.
