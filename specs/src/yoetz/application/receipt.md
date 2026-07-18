# src/yoetz/application/receipt.py — freeze, store, record, and render an honest receipt

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006, ADR-007 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/events.md`, `domain/receipts.md`, `domain/values.md`,
`kernel/projections.md`, `kernel/deterministic_checks.md`, `kernel/receipt_builder.md`,
`ports/ledger.md`, `ports/objects.md`,
`ports/clock.md`, `ports/ids.md`, `application/unit_of_work.md` | **Imported by:**
`application/service.md`

## Purpose

`receipt` creates an immutable, coverage-labeled account of one exact pre-receipt frontier. It
builds the canonical document from the recorded projection, stores that document encrypted,
appends one `receipt_recorded` event that refers to it, and returns a machine or derived human view
with the same conclusion and limitations. A receipt states what Yoetz can support at recorded
coverage; it is not proof of unobserved work, semantic correctness, or cryptographic authorship.

## Public surface

- `async execute_receipt(app: Application, request: ReceiptRequest) -> ReceiptResult` —
  implementation behind `Application.receipt`.
- Application-internal replay helper that reconstructs the original receipt result from a terminal
  operation's accepted event and verified receipt object.

Canonical document values and pure compact rendering belong to `domain/receipts.py`; canonical
assembly belongs to `kernel/receipt_builder.py`. CLI/MCP may choose presentation framing but may not
reword conclusions.

## Behavior

### Validate and freeze

1. Resolve the validated session/writer pair to one active task runtime. Validate
   `expected_frontier`, constrained actor/client, requested output `format` (`json|markdown|text`),
   `include` profile (`summary|standard|full`), and `redaction_profile` as exact closed protocol
   enums. Query the task-scoped importer as an
   optional bounded UX preflight; any observed pending import returns retryable
   `OPERATION_PENDING`. The authoritative receipt append repeats this predicate atomically. The
   three redaction profiles are
   `full_local`, `default_local_export`, and `redacted_share`; no unknown profile is approximated.
2. Compute the canonical logical request digest from protocol/schema/request/session/writer IDs,
   expected frontier, constrained actor/client, format/include/redaction profile, and active
   receipt-policy/version identities. Exclude allocated receipt/event/object IDs, clock metadata,
   encryption randomness, object commitments, and ledger-assigned fields.
3. Perform an optional bounded `lookup_operation` preflight. Same digest plus complete resolves the
   original receipt as described below; different digest is `IDEMPOTENCY_CONFLICT`. The final
   append repeats this lookup and is the correctness boundary.
4. Load or canonically replay the full projection at exactly `expected_frontier = F`. Verify both
   sequence and head digest and require projection lag zero at `F`. Unknown events, redactions,
   missing objects/keys, stale evidence, skipped/failed checks, and provider unavailability remain
   explicit inputs/gaps; the application never fills them from current conversation or later state.
   Read the authoritative accepted prefix through `F`, call
   `LedgerPort.load_case_availability(session_id, F, projection)`, and call the pure
   `build_deterministic_case(projection, records, availability)` to obtain the same typed
   availability/coverage/gap basis used by candidate status and check freeze.
5. From the replay/query-index facts, select exactly one latest current row per finding issue key and
   derive `ReceiptFindingState.resolved` with the shared scope/policy-execution/suppression/
   freshness rules. Load the exact readable applicable `CheckRecordedPayload`, if one exists. Map
   its skipped/failed policy and semantic terminal outcomes to registered root-bound or global
   `CaseGap` codes and fold their coverage. A response disposition alone never resolves a row.
   Construct the exact `ReceiptBuildContext`; the pure builder does not perform these reads or
   applicability decisions.

### Build and publish immutable objects

1. Allocate one `receipt_id` and capture `generated_at`, then call the pure
   `build_receipt` contract with the complete context, exact domain `ReceiptVersionSlice` mapped by
   the application from the active runtime manifest, requested redaction/include policy,
   receipt/task/session IDs, and captured timestamp. The builder has no Wave-F `version.py`
   dependency. It returns one immutable `ReceiptDocument` with stable section ordering and
   conclusion code.
   The mapper copies `package_name`, `package_version`, `protocol_version`, `engine_version`,
   `projection_version`, `object_format_version`, `catalog_schema_version`,
   `bundle_schema_version`, and `resource_manifest_digest` verbatim; converts the active built-in
   policies to sorted `PolicyVersionEntry` values; and converts the exact finding/receipt schema
   identities used by the document to sorted `SchemaVersionEntry` values. It ignores Python,
   SQLite, provider, platform, and capability fields that are not members of
   `ReceiptVersionSlice`; no stringified manifest blob enters the receipt.
2. The document includes at minimum: receipt/task/session IDs and `F`; the complete exact
   `ReceiptVersionSlice`, including its resource-manifest digest; current
   obligations and findings; readable responses; typed claim/evidence refs; weakest material
   coverage; unknown/redaction/unobserved/key/check gaps; redaction counts; and generation metadata.
   Check execution and semantic/provider outcomes are represented by their closed event facts and
   normalized gap/coverage effects; `ReceiptDocument` has no separate execution array or top-level
   provider-provenance summary. A retained semantic `Finding` still carries its domain-owned
   `SemanticProvenance` unchanged. The operation never hides accounting that is absent from those
   typed fields in free-form section prose.
3. Apply redaction while building the canonical document, not only while rendering. Removed
   source fields are counted in exact `ReceiptRedaction` rows; source unavailability/redaction also
   has the typed gap and coverage reduction supplied in context. `full_local`,
   `default_local_export`, and `redacted_share` use the exact kernel profile x include matrix. The
   resulting sections/redactions are part of canonical bytes, so a material transform changes the
   document and digest. No profile removes conclusion/frontier/suppression/coverage/gap truth or
   strengthens a conclusion.
4. Canonically encode the `ReceiptDocument`, compute its unkeyed canonical `receipt_digest`, and
   stage/finalize it as an encrypted receipt object. Size/canary/key/object checks complete before
   any append transaction. A failure here emits no receipt event.
5. Build `ReceiptRecordedPayload(receipt_id, subject_frontier=F, receipt_digest,
   receipt_object_id, conclusion_code, redaction_profile)`. Construct one engine-authored
   `receipt_recorded` event whose artifact references include the durable receipt object; encrypt
   and finalize the event payload object outside SQLite.

### Atomic record and result

1. Call `LedgerPort.append_batch` with one prepared event, `operation_kind=receipt`, the logical
   request digest, and `expected_frontier=F.sequence`. Its final transaction rechecks
   idempotency, requires no pending import for the session, verifies current bundle generation,
   exact head `F`, object inventory/reference integrity,
   and writer chain; appends the event, advances projections/head, stores the structural terminal
   operation result, and commits.
2. The receipt's `subject_frontier` is `F` and excludes its own event. `result_frontier` is the
   post-commit frontier and includes exactly that accepted `receipt_recorded` event. Return only
   after commit.
3. `json` returns the canonical machine document (or its typed structured representation) with
   the exact stored digest. Markdown/text is a deterministic derivative of that same document and
   must carry the identical conclusion and limitations; it is never hashed as an alternative
   receipt. `include` controls only the registered canonical section/detail policy and cannot hide
   required coverage or limitation sections.
4. Before ordinary-client serialization, the local-disclosure gate classifies every present JSON
   document content leaf. It returns the exact whole canonical document only if every category is
   authorized. If any document content leaf is blocked or unclassifiable, it fails the projection
   before emitting a success result; it never rewrites the document and never returns bytes that do
   not match `receipt_digest`. Markdown/text may use the ordinary `human_text` omission marker.

### Idempotent replay

Same request ID and logical digest returns the originally committed `receipt_id`, receipt object,
digest, document/render, accepted event, and frontiers. Replay uses the terminal operation's
accepted ingestion range/event ID, verifies the canonical `receipt_recorded` payload and object
reference, opens/decrypts the stored receipt object, verifies `receipt_digest`, decodes the exact
`ReceiptDocument`, and derives the requested render. It never calls the builder again and never
allocates a new ID. A mismatch is `STORAGE_CORRUPT`, not a reason to generate a replacement.

This replay path is necessary because `AppendResult` contains event acceptance summaries but not
the receipt ID/object/digest. The terminal operation envelope must retain enough structural
location data to find the one accepted receipt event without scanning an unbounded ledger.

### Honest conclusion

Conclusion is exactly `no_unresolved_deterministic_findings`,
`unresolved_findings_remain`, or `insufficient_coverage`. The strongest allowed wording is:

> No unresolved deterministic completion-integrity issue was detected at the recorded coverage.
> This receipt is not proof of unobserved work, complete semantic correctness, or cryptographic
> authorship.

Specific gaps (for example an untested platform, stale tree, unavailable semantic review, redacted
evidence, or unknown event) are appended prominently. The word `verified` must not replace the
weaker conclusion.

## Errors and edge cases

- Invalid format/include/redaction profile or bounds → `INVALID_REQUEST`; head/projection different
  from `expected_frontier` → `FRONTIER_CONFLICT`; same ID/different digest →
  `IDEMPOTENCY_CONFLICT`.
- A missing/locked key or unavailable/corrupt material cannot be treated as empty. If the receipt
  can represent the material structurally, it records an explicit unavailable gap; if the
  canonical document or its own encrypted objects cannot be created/read, return the mapped key/
  storage failure and append nothing.
- A concurrent event after freeze makes the final append fail its expected-frontier check. The
  already finalized receipt/event payload objects are safe orphans for delayed GC and are never
  acknowledged.
- Cancellation before commit leaves no acknowledged receipt. Cancellation during/after ambiguous
  commit is resolved by the operation row and same-ID replay.
- JSON and Markdown derivations that disagree in conclusion, limitations, receipt ID, frontier, or
  digest are an internal defect; neither is returned.

## Invariants

1. Every receipt object describes exactly one canonical pre-event frontier and is immutable.
2. The `receipt_recorded` event is committed only after the referenced receipt object is durable.
3. Same-ID replay returns the original stored document/digest, never a rebuilt live view.
4. Redaction/unavailability always remains visible and can only weaken coverage/conclusion.
5. Human rendering is derived from canonical JSON and cannot be stronger.
6. Acknowledgement follows the one-event terminal append commit.

## Tests

- `specs/tests/unit.md`: conclusion matrix, stable sections/order, request digest exclusions,
  format/render equivalence, redaction gap/coverage weakening, exact strongest wording.
- `specs/tests/conformance.md`: golden canonical receipt bytes/digests and memory/SQLite parity;
  subject-vs-result frontier; original-object replay.
- `specs/tests/integration.md`: concurrent append conflict, projection lag/replay, key/object
  unavailable/corrupt, unknown/redacted/stale inputs, terminal operation reconstruction.
- `specs/tests/subprocess.md`: kill at receipt object, event payload, append commit, and response
  boundaries; same-ID retry produces one event and original receipt.
- `specs/tests/packaging.md`: complete version/support identities and Markdown/JSON golden renders
  from installed resources.

## Open questions

None.
