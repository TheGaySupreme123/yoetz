# tests/unit/domain/test_receipts.py — receipt document and compact render behavior

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/domain/receipts.md`, `src/yoetz/protocol/coverage.py`,
`src/yoetz/domain/findings.py`
**Imported by:** the domain and kernel unit suite

## Purpose

Lock the receipt document’s canonical structure and the compact render’s honesty rules.

## Public surface

- `test_receipt_document_requires_frontier_and_versions` — canonical documents reject missing core
  provenance.
- `test_receipt_conclusion_vocab_is_conservative` — the public conclusion set stays small.
- `test_verdict_conclusion_correspondence_is_exhaustive` — all four check verdicts, including both
  `incomplete_check` branches, obey the unchanged-frontier correspondence.
- `test_suppressed_latest_check_cannot_claim_clear` — nonzero suppression yields unresolved or
  insufficient, never the strongest conclusion.
- `test_receipt_weakest_coverage_matches_supports` — document coverage is the weakest support.
- `test_render_receipt_compact_never_outruns_evidence` — compact text is no stronger than the
  document.
- `test_section_order_and_redaction_notes_are_stable` — presentation order and redaction behavior
  stay fixed.
- `test_receipt_response_preserves_evidence_and_result_refs` — response basis accepts the exact
  sorted-unique `EvidenceId | ResultId` union and preserves each ID kind through the codec.
- `test_receipt_section_items_are_required_and_exact` — missing `items` is invalid, while explicit
  `items=[]` decodes to the required empty tuple and re-encodes as `items=[]`.
- `test_semantic_review_not_configured_receipt_states_not_run` — not-configured gap carries the
  explicit “semantic relevance review was not run” compact disclosure, distinct from blocked-by-policy.
- `test_semantic_relevance_review_not_run_gap_shares_not_run_wording` — failure/timeout gap family
  shares the same truthful not-run wording.

## Behavior

The suite proves:

- receipt documents are frozen and versioned;
- receipt conclusions do not claim verification;
- every verdict/conclusion correspondence and capped-check branch is exhaustive;
- weakest coverage is derived from supports, not guessed;
- compact rendering can omit detail but cannot strengthen the statement;
- redaction leaves an honest trace in the document;
- response records preserve both evidence and result references without a lossy conversion;
- every section carries an explicit `items` array, including the empty-array case, so exact inverse
  encoding cannot collapse absence into emptiness.

## Errors and edge cases

- A render that sounds stronger than the document fails.
- A document with missing provenance or coverage summary fails.
- A response evidence reference with any ID kind other than `evd_` or `res_`, an unsorted union, or
  a duplicate member fails.
- A section with absent `items` fails; explicit `items=[]` succeeds and remains present after the
  codec round trip.

## Invariants

1. Receipt documents are immutable truth records.
2. Rendered views are always weaker or equal to the document.
3. Coverage is computed from supports, not prose.
4. Receipt response evidence is lossless across evidence/result ID kinds.
5. Required empty section items never collapse into an omitted field.

## Tests

- `tests/unit/domain/test_receipts.py`

## Open questions

None.
