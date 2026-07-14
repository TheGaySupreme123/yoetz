# src/yoetz_core/domain/receipts.py — immutable receipt documents and rendered outcomes

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`protocol/coverage.md`, `protocol/errors.md`, `domain/values.md`, `domain/events.md`,
`domain/findings.md`
**Imported by:** `kernel/receipt_builder.md`, `application/receipt.md`, `cli/render.md`,
`adapters/sqlite/repository.md`

## Purpose

Receipts are the durable, coverage-labeled account of what Yoetz believes at a fixed frontier.
This file defines the immutable receipt document values and the helper types used to present them.
Without it, the receipt layer would drift into free-form summary text and lose its role as a stable
record of evidence, gaps, limitations, and version provenance.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `ReceiptConclusion` | enum of the public receipt conclusion vocabulary |
| `ReceiptDocument` | frozen dataclass holding the canonical receipt payload |
| `ReceiptSection` | frozen section wrapper used for rendering |
| `ReceiptRender` | frozen output structure for compact/human views |
| `render_receipt_compact(document)` | bounded text summary for CLI/MCP |
| `receipt_weakest_coverage(document)` | computes the weakest material coverage across the receipt |

## Behavior

`ReceiptDocument` is the canonical immutable record written by the receipt builder and stored as an
encrypted object. It contains the fixed frontier, protocol/engine/policy/version provenance, the
resolution of obligations, findings, responses, evidence references, and the coverage/gap summary
needed to explain the result honestly. In v0.1 the document is a frozen value with these logical
fields:

- `receipt_id`, `task_id`, `session_id`, and `generated_at` (all allocated/captured before the pure
  builder runs and included in the canonical digest);
- the `subject_frontier` being described;
- the receipt conclusion;
- the active version slice used to build the receipt;
- the weakest material coverage for the document as a whole;
- the ordered findings that informed the conclusion;
- the open obligations or unresolved work items that remain material;
- the response records and any waiver scopes or expiries;
- the claim and evidence references used to justify the result;
- the coverage gaps and redaction notes needed to explain why the result is bounded.

`ReceiptConclusion` is deliberately conservative. The wording rules in the contract and ledger
spec only allow a receipt to state the current state of evidence and coverage, not to claim proof of
correctness. The public vocabulary is intentionally small and stable:

- `no_unresolved_deterministic_findings`;
- `unresolved_findings_remain`;
- `insufficient_coverage`.

`ReceiptSection` and `ReceiptRender` separate canonical content from presentation. `ReceiptSection`
is the stable unit of presentation inside a document. It carries a section key, a short title, the
bounded human-readable body, optional bullet items, and the section’s local coverage note when the
body is redacted or derived from weak evidence. `ReceiptRender` is the presentation wrapper returned
by the compact renderer; it may hold the final headline, section list, truncation metadata, and a
redaction flag, but it does not change the canonical document.

`render_receipt_compact(document)` produces a short, bounded English-only summary in v0.1. It must
not invent stronger wording than the underlying receipt document supports. The compact view may
mention:

- the fixed frontier;
- the highest-level conclusion;
- unresolved obligations/findings;
- coverage gaps and redactions;
- whether semantic evaluation was unavailable or not requested.

The compact render is intentionally weaker than the underlying document when the document carries
more detail than the chosen surface needs.

`receipt_weakest_coverage(document)` computes the component-wise weakest material coverage from the
document’s supporting facts. It is used by the CLI, MCP, and tests to ensure the rendered wording
never outruns the evidence.

## Errors and edge cases

- A receipt that lacks its receipt/task/session identity, generation time, subject frontier,
  version provenance, or coverage summary is invalid.
- `ReceiptDocument` deliberately has no post-append `result_frontier`: that frontier contains the
  digest of the event that commits this document and would create a hash self-reference. The
  operation result carries the post-commit frontier.
- Redacted or missing supporting material weakens the document; it does not disappear.
- A receipt never claims “verified” in place of a weaker conclusion.
- Rendering functions never expose raw payloads, secrets, or unbounded evidence text.
- A render may omit detail for a bounded surface, but it may not invent a stronger conclusion.

## Invariants

1. Receipt documents are immutable and replayable.
2. The compact render can be weaker than the document, never stronger.
3. Receipt coverage is the weakest material coverage of the receipt’s supports.
4. The receipt object itself does not perform I/O.
5. Export/render details never change the canonical document.
6. The canonical receipt digest commits to `receipt_id` and `generated_at`; neither is envelope-only
   metadata.

## Tests

- `tests/unit/domain/test_receipts.py` — document validation and weakest-coverage computation.
- `tests/unit/domain/test_receipts.py` — compact wording rules and no-stronger-than-evidence checks.
- `tests/conformance/operations/test_receipt_contract.py` — golden canonical receipt documents and
  compact text across public surfaces.

## Open questions

None.
