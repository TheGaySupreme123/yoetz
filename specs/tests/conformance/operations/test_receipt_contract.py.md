# tests/conformance/operations/test_receipt_contract.py — receipt public contract

**Wave:** D | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/application/receipt.md`, `src/yoetz_core/domain/receipts.md`, reviewed receipt
fixtures and the fixture manifest
**Imported by:** conformance operations tests

## Purpose

Prove the receipt operation returns the same canonical document and render surfaces everywhere.

## Public surface

- `test_receipt_request_result_parity` — JSON/structured receipt results match.
- `test_frontier_and_own_event_exclusion_parity` — the subject frontier is exact.
- `test_receipt_wording_is_weaker_than_document` — human text never outruns the document.
- `test_reviewed_receipt_vectors_match_exact_document_and_compact_bytes` — each golden fixture
  produces the frozen canonical document and compact rendering byte-for-byte.

## Behavior

The test checks:

- the same frozen frontier yields the same receipt document;
- own-event exclusion is consistent;
- redaction/profile differences only affect presentation;
- human wording stays weaker than the canonical receipt document;
- reviewed current, imported-partial, redacted-gap, semantic-advisory, unresolved, and waiver-expiry
  vectors match exact canonical document digests and compact text bytes on every surface.

## Errors and edge cases

- A wrapper that upgrades the receipt conclusion fails.
- Recomputing the expected vector with production code instead of loading reviewed bytes fails the
  independence requirement.

## Invariants

1. Receipt identity is surface-neutral.
2. Presentation is weaker than the document.
3. Frontier handling is exact.
4. Golden document and compact-render bytes come from reviewed fixtures, not self-generated oracles.

## Tests

- `tests/conformance/operations/test_receipt_contract.py`

## Open questions

None.
