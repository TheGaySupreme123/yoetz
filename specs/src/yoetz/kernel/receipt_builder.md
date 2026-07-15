# src/yoetz/kernel/receipt_builder.py — canonical receipt document assembly

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`domain/receipts.md`, `domain/findings.md`, `kernel/projections.md`, `protocol/coverage.md`,
`protocol/canonical.md`, `version.md` | **Imported by:** `application/receipt.md`,
`adapters/sqlite/repository.md`, `cli/render.md`

## Purpose

This file turns the final derived work state into the immutable receipt document that Yoetz stores
and renders. The builder is where the system commits to one canonical explanation of what it knows,
what it cannot prove, and what remains open at the frozen frontier.

The builder does not read the ledger directly. It consumes the projection state that already
represents the ledger at a fixed frontier and then packages that state into a receipt document with
stable sections and stable wording boundaries.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `build_receipt(state, subject_frontier, receipt_id, task_id, session_id, generated_at, versions, redaction_profile, include)` | return the canonical immutable receipt document |

## Behavior

`build_receipt` is pure. It consumes:

- the current `ProjectionState`;
- the frozen `subject_frontier` the caller wants to describe;
- the preallocated receipt/task/session IDs and captured `generated_at` timestamp;
- the loaded `VersionManifest` or equivalent version slice;
- the requested redaction profile; and
- the registered canonical `include` detail policy.

It returns a `ReceiptDocument` with a stable ordering of sections and a stable conclusion code.

The builder first verifies that the supplied subject frontier matches the state it is asked to summarize. A
receipt is a statement about a fixed frontier; the builder must not silently summarize a different
frontier or a later projection.

The builder does not rank findings, re-evaluate policies, or fetch any new evidence. It packages
what the projection already knows into a canonical document and then weakens presentation only when
the requested redaction profile demands it.

The canonical receipt document contains, at minimum:

- the receipt/task/session IDs, generated-at timestamp, and subject frontier;
- the conclusion code from the public receipt vocabulary;
- the weakest material coverage;
- the active policy and engine versions;
- the projection and storage identities relevant to the result;
- the current findings and their dispositions;
- the open obligations or other unresolved work items that matter to the conclusion;
- the evidence and claim references needed to explain the outcome;
- a stable section list for the compact and full renderers.

In v0.1 the builder also keeps the following structural commitments stable:

- the canonical document contains only the subject frontier. The application result adds the
  post-append result frontier after the receipt event commits; the builder never guesses it;
- the section order is fixed and canonical;
- the document records the active version slice used to build it;
- the document carries enough coverage metadata for `receipt_weakest_coverage(document)` to compute
  the weakest material support without looking back at the ledger;
- the document separates canonical content from render-time truncation.

The conclusion is conservative and derived from the state:

- `no_unresolved_deterministic_findings` when there are no unresolved deterministic blockers and the
  coverage is strong enough to say so honestly;
- `unresolved_findings_remain` when one or more findings remain open or materially disputed;
- `insufficient_coverage` when the result cannot be stated honestly because the evidence is too weak,
  stale, redacted, or incomplete.

If the findings are absent but the coverage is still too weak to support a strong statement, the
builder prefers `insufficient_coverage` over a false sense of resolution. If the findings are
present and materially unresolved, the builder prefers `unresolved_findings_remain` over a weaker
neutral label.

`redaction_profile` changes presentation, not the underlying truth. In `full_local`, the builder can
carry the full allowed evidence and section detail. In `default_local_export`, it keeps the
canonical summary but hides user payload text that is not needed for support. In `redacted_share`, it
minimizes the body further so the receipt can be shared without leaking protected content. No
profile may strengthen the conclusion.

The supported v0.1 profiles are:

- `full_local` for local inspection and debugging;
- `default_local_export` for ordinary file export or local render;
- `redacted_share` for external sharing when payload text must be minimized.

The builder also defines the stable section order. The intended order is:

1. short summary;
2. outstanding obligations / work items;
3. findings and dispositions;
4. evidence and claim basis;
5. limitations and coverage gaps;
6. version and policy identity.

Every section is bounded and purpose-specific. The summary section names the conclusion and frontier.
The obligations section explains what is still open. The findings section lists the ranked blockers
or explains that none remain. The evidence section cites the support basis without echoing raw
payloads. The limitations section states the weakest coverage and any redaction gaps. The version
section records the exact release identities that produced the receipt.

`build_receipt` never writes to SQLite, never reads ambient time or randomness, and never re-ranks findings. It
assumes ranking already happened and packages the result into a canonical document.

## Errors and edge cases

- Frontier mismatch is an internal consistency error and must not be hidden.
- A receipt without a conclusion, coverage summary, or version identity is invalid.
- An unsupported redaction profile is rejected rather than approximated.
- The builder never introduces new evidence, claims, or findings that are not already in state.

## Invariants

1. A receipt is a frozen document, not a live view.
2. The same complete explicit inputs produce the same receipt document; changing the receipt ID or
   generation timestamp changes its canonical digest by design.
3. The builder never claims stronger certainty than the findings allow.
4. Redaction weakens visibility only; it never alters the canonical truth.
5. The receipt document is what gets hashed and stored, while renders are presentation only.

## Tests

- `specs/tests/unit.md` — stable section order, conclusion selection, and redaction behavior.
- `specs/tests/conformance.md` — receipt parity between memory and SQLite adapters.
- `specs/tests/packaging.md` — version and support identities embedded in the receipt are stable.
- `fixtures/receipts/` — golden canonical receipt documents and compact views.

## Open questions

None.
