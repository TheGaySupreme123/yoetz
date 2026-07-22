# src/yoetz/domain/receipts.py — immutable receipt documents and rendered outcomes

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`protocol/coverage.md`, `protocol/errors.md`, `protocol/models.md` (`ReceiptRedactionProfile`),
`domain/values.md`, `domain/findings.md`
**Imported by:** `domain/events.md`, `kernel/receipt_builder.md`, `application/receipt.md`, `cli/render.md`,
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
| `ReceiptVersionSlice`, `PolicyVersionEntry`, `SchemaVersionEntry` | frozen version provenance records |
| `ReceiptObligation`, `ReceiptResponse`, `ReceiptGap`, `ReceiptRedaction` | frozen canonical support records |
| `ReceiptSection` | frozen canonical section record |
| `receipt_document_from_json(value)` / `receipt_document_to_json(document)` | exact receipt schema codecs |
| `render_receipt_compact(document) -> str` | bounded compact text for CLI/MCP |
| `receipt_weakest_coverage(document)` | computes the weakest material coverage across the receipt |

## Behavior

### Exact schema-shaped records

Every enum here is `str`-valued and every record is `@dataclass(frozen=True, slots=True)`. The
nominal enums owned by this module are `ReceiptConclusion`, `ReceiptObligationStatus`
(`open|resolved|superseded|waived`), `ReceiptRedactionCategory`
(`claim_text|evidence_content|finding_detail|obligation_text|repository_content|transcript_content`),
`ReceiptRedactionReason`
(`include_profile_omitted|never_send_redacted|policy_redacted|source_redacted`), and
`ReceiptSectionKey` (`summary|outstanding_work|findings_and_dispositions|
evidence_and_claim_basis|limitations_and_coverage|version_and_policy_identity`).
`ResponseDisposition` and `WaiverScope` come from `domain/findings.py`;
`ReceiptRedactionProfile` comes from `protocol/models.py`.

The nested canonical records have exactly these fields:

```text
PolicyVersionEntry(policy_id: str, policy_version: str)
SchemaVersionEntry(schema_id: str, schema_version: str)
ReceiptVersionSlice(package_name: Literal["yoetz"], package_version: str,
                    protocol_version: str, engine_version: str,
                    projection_version: str, object_format_version: str,
                    catalog_schema_version: str, bundle_schema_version: str,
                    policy_versions: tuple[PolicyVersionEntry, ...],
                    schema_versions: tuple[SchemaVersionEntry, ...],
                    resource_manifest_digest: str)
ReceiptObligation(obligation_id: ObligationId, status: ReceiptObligationStatus,
                  source_refs: tuple[EventId | ObligationId | ClaimId, ...],
                  summary: str | None = None)
ReceiptResponse(finding_id: FindingId, finding_frontier: Frontier,
                disposition: ResponseDisposition,
                evidence_refs: tuple[EvidenceId | ResultId, ...],
                reason: str | None = None,
                waiver_scope: WaiverScope | None = None,
                waiver_expiry: Timestamp | None = None)
ReceiptGap(code: str,
           subject_refs: tuple[EventId | ObligationId | ClaimId, ...],
           detail: str | None = None)
ReceiptRedaction(category: ReceiptRedactionCategory,
                  reason: ReceiptRedactionReason,
                  count: int)
ReceiptSection(key: ReceiptSectionKey, title: str, body: str,
               items: tuple[str, ...], coverage_note: str | None = None)
```

`ReceiptDocument` has exactly these fields:

```text
schema_version: Literal["1.0.0"]                # init=False
receipt_id: ReceiptId
task_id: TaskId
session_id: SessionId
generated_at: Timestamp
subject_frontier: Frontier
conclusion: ReceiptConclusion
suppressed_finding_count: int
versions: ReceiptVersionSlice
coverage: Coverage
findings: tuple[Finding, ...]
obligations: tuple[ReceiptObligation, ...]
responses: tuple[ReceiptResponse, ...]
claim_refs: tuple[ClaimId, ...]
evidence_refs: tuple[EvidenceId, ...]
gaps: tuple[ReceiptGap, ...]
redactions: tuple[ReceiptRedaction, ...]
sections: tuple[ReceiptSection, ...]
```

All tuple ordering, uniqueness, sizes, text limits, conditional response fields, section shapes,
version identities, and conclusion/suppression constraints match
`schemas/receipts/receipt-document-1.0.0.schema.json`. Domain counts are `int` but not `bool`.
`suppressed_finding_count` remains a JSON integer; `ReceiptRedaction.count` is rendered as the
schema's canonical unsigned-decimal string. Frontier sequences remain decimal strings.

Section keys must be exactly one of the three registered sequences: summary include =
`(summary, limitations_and_coverage, version_and_policy_identity)`; standard =
`(summary, outstanding_work, findings_and_dispositions, limitations_and_coverage,
version_and_policy_identity)`; full adds `evidence_and_claim_basis` between findings and
limitations. There is no arbitrary section order or duplicate key.

`ReceiptDocument` is the canonical immutable record written by the receipt builder and stored as an
encrypted object. It contains the fixed frontier, protocol/engine/policy/version provenance, the
resolution of obligations, findings, responses, evidence references, and the coverage/gap summary
needed to explain the result honestly. In v0.1 the document is a frozen value with these logical
fields:

- `receipt_id`, `task_id`, `session_id`, and `generated_at` (all allocated/captured before the pure
  builder runs and included in the canonical digest);
- the `subject_frontier` being described;
- the receipt conclusion;
- `suppressed_finding_count: int`, the exact nonnegative count from the applicable latest check
  (zero when none were suppressed); it never carries invented identities;
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

`ReceiptConclusion` and `CheckVerdict` are deliberately non-isomorphic: the former describes a
durable projection at a frontier, while the latter also records whether one check execution was
incomplete. A receipt builder derives its conclusion from the application-supplied frozen
projection/finding/check/coverage/gap context rather than copying a prior verdict. When a recorded
check and a receipt describe the same unchanged subject frontier, the required correspondence is:

| `CheckVerdict` | Required `ReceiptConclusion` |
|---|---|
| `action_required` | `unresolved_findings_remain` |
| `no_issue_detected` | `no_unresolved_deterministic_findings` |
| `insufficient_coverage` | `insufficient_coverage` |
| `incomplete_check` | `unresolved_findings_remain` when the projection has any unresolved actionable finding at that frontier; otherwise `insufficient_coverage` |

If projection facts changed after the check, the receipt describes its own supplied frontier and
no cross-frontier equality is implied.

An applicable latest check with `suppressed_count > 0` cannot support
`no_unresolved_deterministic_findings`, because the projection intentionally lacks the omitted
finding identities. While a visible actionable finding remains it yields
`unresolved_findings_remain`; otherwise it yields `insufficient_coverage` until a newer applicable
check records zero suppression. A receipt exposes the count, never invented suppressed IDs.

`ReceiptResponse.evidence_refs` preserves the exact response basis accepted by
`ResponseRecordedPayload`: each sorted-unique member is either an `EvidenceId` or a `ResultId`.
There is no result-to-evidence coercion, lookup, or lossy projection at receipt construction.

`ReceiptSection` is canonical document content, not a separate presentation wrapper. It carries its
registered key, short title, bounded body, required bullet-item tuple (which may be empty), and
optional local coverage note. `items` has no dataclass default: absence is invalid, while an explicit
empty tuple encodes as `"items": []`. There is no `ReceiptRender` type in v0.1: it had no schema,
no stable fields, and no consumer that needed a second object graph.

### JSON codecs

`receipt_document_from_json(value)` accepts only the closed object frozen by
`receipt-document-1.0.0.schema.json`, constructs every nested nominal record, parses frontier and
redaction-count decimal strings, and rejects all missing, extra, conditionally invalid, unsorted,
or duplicate values. `receipt_document_to_json(document)` is its exact inverse, omits only optional
members whose value is `None`, always emits every section's required `items` array, preserves
`EvidenceId | ResultId` response evidence without rewriting its ID kind, and delegates findings and
coverage to their one owning codecs. It does not serialize adapters, Pydantic models, datetimes, or
mutable containers.

For each valid schema value `x`, canonical encoding of
`receipt_document_to_json(receipt_document_from_json(x))` equals canonical encoding of `x`. The
receipt digest is the digest of this canonical document object; `receipt_id` and `generated_at`
remain inside it.

`render_receipt_compact(document) -> str` produces one newline-free, bounded English-only summary
in v0.1. It returns the string itself, not a wrapper, tuple, section list, or bytes. The exact
sentence templates and their precedence are frozen by `fixtures/receipts/*`.
It must
not invent stronger wording than the underlying receipt document supports. The compact view may
mention:

- the fixed frontier;
- the highest-level conclusion;
- unresolved obligations/findings;
- coverage gaps and redactions;
- whether semantic evaluation was unavailable or not requested.

Structural semantic-relevance gap codes are distinct:

- `optional_semantic_review_blocked_by_policy` — optional review was blocked before dispatch by
  network-egress policy (no provider attempt);
- `semantic_review_not_configured` — the semantic evaluator/provider was not configured;
- `semantic_relevance_review_not_run` — optional semantic evaluation failed, timed out, or otherwise
  did not complete.

When either not-configured or not-run gap is present, compact wording states that no unresolved
deterministic issue was found in the published record and that **semantic relevance review was not
run**, and must not reuse the blocked-by-policy sentence. Limitations section bodies for those gaps
begin with `Semantic relevance review was not run.` before listing coverage-limited codes.

The compact render is intentionally weaker than the underlying document when the document carries
more detail than the chosen surface needs. It is presentation only and is never hashed as the
receipt document.

`receipt_weakest_coverage(document)` is the exact left fold of `protocol.coverage.weakest`, starting
with `document.coverage` and then visiting `document.findings` in stored order using each
`finding.coverage`. With no findings it returns `document.coverage`. It never derives coverage
from prose, section presence, a conclusion token, or list length. Construction also requires every
`ReceiptGap.code` to occur in `document.coverage.known_gaps` and requires
`receipt_weakest_coverage(document) == document.coverage`; therefore a top-level coverage value
cannot be stronger than a carried finding and no explicit gap can disappear from the coverage
summary.

## Errors and edge cases

The exact `ProtocolValueError` reasons first raised by this module are:
`invalid_receipt_conclusion`, `invalid_receipt_version_slice`,
`invalid_receipt_obligation`, `invalid_receipt_response`, `invalid_receipt_gap`,
`invalid_receipt_redaction`, `invalid_receipt_section`, `invalid_receipt_section_order`,
`invalid_receipt_document`, `receipt_coverage_mismatch`, `receipt_gap_not_in_coverage`, and
`receipt_json_shape_invalid`. This closed inventory must appear in
`protocol.errors.PROTOCOL_REASON_CODES`. Imported ID, timestamp, frontier, finding, coverage, and
canonical-set validators propagate their owning reason unchanged.

- A receipt that lacks its receipt/task/session identity, generation time, subject frontier,
  version provenance, or coverage summary is invalid.
- `ReceiptDocument` deliberately has no post-append `result_frontier`: that frontier contains the
  digest of the event that commits this document and would create a hash self-reference. The
  operation result carries the post-commit frontier.
- Redacted or missing supporting material weakens the document; it does not disappear.
- A response may cite either recorded evidence or a recorded result; any other ID kind is invalid,
  and a result reference is never silently discarded or converted.
- A receipt section missing `items` is invalid. `items=[]` is the one explicit empty representation
  and round-trips byte-identically.
- A receipt never claims “verified” in place of a weaker conclusion.
- Rendering functions never expose raw payloads, secrets, or unbounded evidence text.
- A render may omit detail for a bounded surface, but it may not invent a stronger conclusion.

## Invariants

1. Receipt documents are immutable and replayable.
2. The compact render can be weaker than the document, never stronger.
3. Receipt coverage is the weakest material coverage of the receipt’s supports.
4. The receipt object itself does not perform I/O.
5. Export format and render-time truncation never change the canonical document. The receipt
   request's `include` and redaction profile are canonical build inputs and may change it under the
   frozen builder matrix.
6. The canonical receipt digest commits to `receipt_id` and `generated_at`; neither is envelope-only
   metadata.

## Tests

- `tests/unit/domain/test_receipts.py` — document validation and weakest-coverage computation.
- `tests/unit/domain/test_receipts.py` — compact wording rules, the exhaustive
  `CheckVerdict`-to-`ReceiptConclusion` correspondence (including both `incomplete_check`
  branches), and no-stronger-than-evidence checks.
- `tests/conformance/operations/test_receipt_contract.py` — golden canonical receipt documents and
  compact text across public surfaces.

## Open questions

None.
