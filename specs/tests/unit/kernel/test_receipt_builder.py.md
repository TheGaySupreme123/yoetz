# tests/unit/kernel/test_receipt_builder.py — canonical receipt assembly rules

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/kernel/receipt_builder.md`, `src/yoetz/domain/receipts.md`,
`src/yoetz/kernel/ranking.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the receipt builder so it packages the frozen state into one canonical document and never
re-ranks, re-fetches, or strengthens the result.

## Public surface

- `test_frontier_mismatch_is_rejected` — the builder must summarize the supplied frontier only.
- `test_conclusion_selection_matches_state_strength` — conclusion choice is conservative.
- `test_suppressed_findings_block_clear_conclusion_until_fresh_check` — capped identities are not
  forgotten after visible responses.
- `test_section_order_is_canonical` — section ordering stays fixed.
- `test_redaction_profiles_only_weaken_visibility` — redaction changes presentation, not truth.
- `test_builder_never_adds_new_findings_or_evidence` — the builder is a packaging step only.

## Behavior

The suite proves:

- receipt assembly is pure;
- the subject frontier and result frontier are explicit;
- section order and version identity are stable;
- redaction profiles weaken the visible text without changing the canonical document;
- the builder consumes existing findings and coverage only.
- a nonzero latest suppressed count is retained as structural uncertainty until a newer zero-count
  check replaces it.

## Errors and edge cases

- A receipt whose conclusion outruns the findings fails the test.
- A builder that fetches fresh evidence fails the test.

## Invariants

1. Receipt building is packaging, not analysis.
2. Redaction never strengthens claims.
3. Frontier mismatches are explicit defects.

## Tests

- `tests/unit/kernel/test_receipt_builder.py`

## Open questions

None.
