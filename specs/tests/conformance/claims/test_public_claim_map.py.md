# tests/conformance/claims/test_public_claim_map.py — public claim map evidence

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/README.md`, `tests/conformance/honesty/test_adversarial_cases.py.md`
**Imported by:** conformance claim-evidence tests

## Purpose

Bind public README/help/skill/support claims to concrete evidence and conformance coverage.

## Public surface

- `test_claim_entries_cover_public_statements` — every public statement has a map entry.
- `test_claim_words_do_not_outrun_evidence` — wording stays within the documented coverage.
- `test_skipped_or_unsupported_claims_are_flagged` — unsupported claims are explicit.

## Behavior

The test checks the reviewed claim map and asserts:

- each public claim points to owning specs and supporting conformance tests;
- wording is bounded by the evidence/coverage qualifiers;
- privacy claims distinguish final application request-body commitments from auth/framing and bind
  one credential callback per physical attempt;
- structural-receipt claims cover every successfully reserved terminal decision and physical
  attempt, identify initial reservation failure as the sole pre-dispatch no-receipt exception, and
  reject pending/approved/receipt-repair state as a finished outcome;
- privacy-audit storage claims bind content-bearing proposals to owning task-bundle encrypted
  objects and do not imply v0.1 has taskless content-encryption storage;
- adapter claims are limited to closed reviewed-bundled composition with no injected ambient
  handles and explicitly disclaim OS/process sandbox isolation;
- the v0.1 diagnostic claim permits only bounded structural identity and proves raw traceback
  capture is absent rather than merely owner-only/disabled by default;
- any missing claim or unsupported claim is reported explicitly.

## Errors and edge cases

- A public claim without a map entry fails.

## Invariants

1. Public claims are evidence-mapped.
2. Unsupported claims are explicit.
3. Wording stays within the claim map.

## Tests

- `tests/conformance/claims/test_public_claim_map.py`

## Open questions

None.
