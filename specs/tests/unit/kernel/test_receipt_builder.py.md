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
- `test_redaction_profiles_change_canonical_bytes_without_changing_truth` — the exact field matrix
  changes document/digest while preserving conclusion/frontier/suppression and non-strengthening coverage.
- `test_profile_by_include_matrix_is_exhaustive` — all nine combinations select exact top-level
  fields, section keys, redaction rows, and fixed-template inputs.
- `test_context_requires_explicit_availability_and_applicable_check` — no projection-only fallback
  invents captured-object availability, resolution, or check accounting.
- `test_receipt_version_slice_is_exact` — the builder accepts the exact 11-field
  `ReceiptVersionSlice` (including `resource_manifest_digest`), not `VersionManifest` or a
  mapping.
- `test_builder_never_adds_new_findings_or_evidence` — the builder is a packaging step only.

## Behavior

The suite proves:

- receipt assembly is pure;
- the subject frontier and result frontier are explicit;
- section order and version identity are stable;
- redaction profiles transform the canonical document before hashing; whenever the matrix removes a
  field or changes a section/redaction row, canonical bytes and digest change;
- conclusion, subject frontier, suppression count, material gap codes, and weakest coverage are
  invariant or weaker across the same context's profile variants;
- `include` changes only the exact section tuple and leaves selected top-level truth-bearing tuples
  unchanged;
- `full_local`, `default_local_export`, and `redacted_share` apply the exact finding/obligation/
  response/gap matrix and merge redaction counts by `(category, reason)`;
- the builder consumes existing findings and coverage only.
- a nonzero latest suppressed count is retained as structural uncertainty until a newer zero-count
  check replaces it.
- `ReceiptFindingState.resolved` and `applicable_check` are explicit context facts; response
  disposition and `ProjectionState.latest_tested_state` alone cannot substitute for them.
- `unavailable_at_freeze` and recorded redaction produce distinct typed gaps/redaction accounting.
- receipt `versions` follows the local-catalog 11-field `ReceiptVersionSlice` contract, including
  `resource_manifest_digest`, rather than a stripped `VersionManifest`.

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
