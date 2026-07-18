# src/yoetz/kernel/receipt_builder.py — canonical receipt document assembly

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`domain/receipts.md`, `domain/findings.md`, `domain/events.md` (`CheckRecordedPayload`),
`kernel/projections.md`, `kernel/deterministic_checks.md` (`CaseAvailabilityFacts`, `CaseGap`),
`protocol/coverage.md`, `protocol/canonical.md` | **Imported by:** `application/receipt.md`,
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
| `ReceiptFindingState` | frozen `(finding_id, resolved)` state for one current issue row |
| `ReceiptBuildContext` | frozen complete projection/availability/coverage/gap/finding/check input |
| `build_receipt(context, receipt_id, task_id, session_id, generated_at, versions, redaction_profile, include)` | return the canonical immutable receipt document |

## Behavior

`build_receipt` is pure. It consumes:

- one complete `ReceiptBuildContext` at the frozen subject frontier;
- the preallocated receipt/task/session IDs and captured `generated_at` timestamp;
- the exact domain-owned `ReceiptVersionSlice` (never a broad runtime `VersionManifest`);
- the requested redaction profile; and
- the registered canonical `include` detail policy: `summary`, `standard`, or `full`.

It returns a `ReceiptDocument` with a stable ordering of sections and a stable conclusion code.

`ReceiptFindingState` is exactly `(finding_id: FindingId, resolved: bool)`. `bool` is nominally
boolean, not an integer substitute. `ReceiptBuildContext` is exactly:

```text
ReceiptBuildContext(projection: ProjectionState,
                    subject_frontier: Frontier,
                    availability: CaseAvailabilityFacts,
                    coverage: Coverage,
                    gaps: tuple[CaseGap, ...],
                    finding_states: tuple[ReceiptFindingState, ...],
                    applicable_check: CheckRecordedPayload | None)
```

The application, not this builder, owns construction. `finding_states` contains exactly one latest
current finding row per issue key under the shared status applicability rules, ordered by the full
registered finding `rank_key`; every ID resolves to a readable current `projection.findings` row.
`resolved` is computed by the sole rule in `specs/INTERFACES.md`: a later check must include that
finding's frontier, run the matching policy to completion, suppress nothing, be current and
gap-free, and use whole-case scope or directly intersect a selected claim/obligation root. A later
same-issue row makes the older row noncurrent and starts unresolved; it is not itself resolution.
Acknowledged/rejected/waived response disposition never resolves a row. Tombstoned/unreadable
finding rows are absent from the tuple but contribute a typed gap and weakened coverage.
`applicable_check` is the exact readable
check payload whose scope, policy executions, subject frontier, and semantic facts still apply to
the material state, or `None`; the builder never recovers it from
`ProjectionState.latest_tested_state` alone.

The context is self-validating: its frontier equals
`Frontier(sequence=projection.frontier, head_digest=projection.head_digest)`; availability is the
same exact snapshot used to derive its gaps; every gap tuple is sorted/unique and its code occurs in
`coverage.known_gaps`; and `coverage` is no stronger than projection, finding, check, availability,
and gap material. When `applicable_check` is present, its returned IDs/suppression/verdict/coverage
match `projection.latest_tested_state`; its policies and executions are one-to-one in canonical pack
order. An invalid context is a construction defect, never repaired by dropping material.

The builder first verifies that `context.subject_frontier` matches the projection it is asked to summarize. A
receipt is a statement about a fixed frontier; the builder must not silently summarize a different
frontier or a later projection.

The builder does not rank findings, re-evaluate policies, or fetch any new evidence. It packages
the already-normalized context into a canonical document. It never imports `ports/ledger.py`, reads
a runtime `VersionManifest`, or guesses policy/semantic accounting from projection fields.

The canonical receipt document contains, at minimum:

- the receipt/task/session IDs, generated-at timestamp, and subject frontier;
- the conclusion code from the public receipt vocabulary;
- the weakest material coverage;
- the complete caller-supplied `ReceiptVersionSlice`: package, protocol, engine, projection,
  object-format, catalog-schema, bundle-schema, policy, schema, and resource-manifest identities;
- the current findings and their dispositions;
- the open obligations or other unresolved work items that matter to the conclusion;
- the evidence and claim references needed to explain the outcome;
- a stable section list for the compact and full renderers.

Attempted/skipped/failed policy and semantic/provider outcomes enter only through the exact
`applicable_check`, retained finding provenance, and application-normalized coverage/gaps. The
receipt schema has no separate execution/provenance array, so the builder maps those facts only to
existing typed finding fields, registered limitation gap codes, and fixed section text. It never
invents a second execution record or serializes opaque provider errors. If an application cannot
map a promised accounting fact into the existing document shape, it must omit that promise from
the receipt operation rather than hiding it in prose.

In v0.1 the builder also keeps the following structural commitments stable:

- the canonical document contains only the subject frontier. The application result adds the
  post-append result frontier after the receipt event commits; the builder never guesses it;
- the section order is fixed and canonical;
- the document records the active version slice used to build it;
- the document carries enough coverage metadata for `receipt_weakest_coverage(document)` to compute
  the weakest material support without looking back at the ledger;
- the document separates canonical content from render-time truncation.

Conclusion selection is exact. Let `U` be the context's unresolved `finding_states` whose current
finding has `FINDING_KIND_TRAITS[kind].actionable=true`, and let `A` be `applicable_check`:

1. if `U` is nonempty, emit `unresolved_findings_remain` regardless of weaker coverage;
2. otherwise, if `A is None`, emit `insufficient_coverage` and require a material
   `check_not_recorded|check_not_applicable|check_payload_unavailable` gap as appropriate;
3. otherwise, if `A.suppressed_count > 0`, emit `insufficient_coverage`;
4. otherwise, `A.verdict=insufficient_coverage|incomplete_check` emits
   `insufficient_coverage`;
5. otherwise, `A.verdict=action_required` is valid only when `U` was nonempty and therefore cannot
   reach this step; and
6. `A.verdict=no_issue_detected` emits `no_unresolved_deterministic_findings` only when all of its
   selected policy executions are `run/completed`, `context.gaps` is empty, and
   `context.coverage.known_gaps` is empty. Any failure of those conditions emits
   `insufficient_coverage`.

This uses the check verdict as an applicability/consistency fact but does not copy it into the
receipt vocabulary. A nonactionable `ledger_stale_or_incomplete`, a skipped/failed pack, semantic
unavailability, later redaction, or any rootless material gap prevents the strong conclusion through
steps 2-6. Responses never set `resolved` and never clear capped uncertainty. The document copies
`A.suppressed_count` exactly, or zero when `A is None`; it never fabricates suppressed identities.

Before profile/include handling, the builder deterministically selects the truth-bearing top-level
material. `findings` is the ordered current finding rows named by `finding_states`; `responses` is
the readable current response for those IDs; `obligations` is the current plan's obligation set plus
any obligation root named by those findings, preserving exact effective status; `claim_refs` is the
sorted union of current completion claims and claim roots used by findings/gaps; and `evidence_refs`
is the sorted union of evidence directly referenced by those claims, obligations, results, and
responses. Missing/unreadable selected rows are omitted only with a matching typed gap and weakened
coverage. Bounds overflow fails; nothing is first-N truncated.

`include` changes canonical sections only, never those selected top-level tuples:

| `include` | Exact section-key tuple |
|---|---|
| `summary` | `(summary, limitations_and_coverage, version_and_policy_identity)` |
| `standard` | `(summary, outstanding_work, findings_and_dispositions, limitations_and_coverage, version_and_policy_identity)` |
| `full` | `(summary, outstanding_work, findings_and_dispositions, evidence_and_claim_basis, limitations_and_coverage, version_and_policy_identity)` |

The profile is then applied to top-level content fields before sections are generated:

| Profile | Findings | Obligation `summary` | Responses | Gap `detail` | Always retained |
|---|---|---|---|---|---|
| `full_local` | every selected row | retained when present | every selected row with exact conditional reason/waiver fields | retained when present | identities, conclusion, suppression, versions, coverage, gap codes/roots |
| `default_local_export` | every selected row | set to `None` | every selected row; reasons are support material and remain | set to `None` | same |
| `redacted_share` | deterministic rows only; semantic rows omitted | set to `None` | acknowledged rows only with `reason=None`; rejected/waived rows omitted because their required reason cannot be blanked | set to `None` | same, plus all selected claim/evidence refs |

Every actually removed protected content leaf is counted once; omitted structural IDs, enum tokens,
and relation fields do not masquerade as content redactions. Cleared obligation summaries emit/merge
`(obligation_text, policy_redacted)`; cleared gap details and omitted response rows emit/merge
`(finding_detail, policy_redacted)` for the removed required reason; omitted semantic finding rows
count both their required summary and detail as two `finding_detail` leaves. Effective
captured-object redaction emits/merges
`(evidence_content, source_redacted)` once per unique redacted object. Other effective event
redactions use the category of the current projected family (`claim_text`, `finding_detail`, or
`obligation_text`), falling back only to `repository_content` for action/result/plan material. Rows
sort by unsigned ASCII `(category, reason)` and zero counts are omitted. Non-redaction
unavailability creates gaps, not `ReceiptRedaction`. Section omission by `include` is not counted as
redaction because the selected top-level truth remains present; `include_profile_omitted` is retained
only for backward-read compatibility and is not emitted by the generation-1 builder.

Sections are generated after that transform and may use only retained structural values. Exact
titles are `Summary`, `Outstanding work`, `Findings`, `Evidence basis`, `Limitations`, and
`Versions and policy` in the key order above. Items are only canonical IDs, gap codes, or version
identities already present in the document; they sort by the owning tuple order and never copy a
description, claim statement, response reason, finding text, path, URL, or evidence bytes. Bodies
and coverage notes come from one immutable template registry keyed only by conclusion, structural
counts, gap-code tuple, applicable policy/semantic statuses, retained response/frontier facts,
retained semantic-finding provenance, and version identities. The generic fallback rows are exact:
`count_phrase(0, singular, plural) = "no {plural}"`,
`count_phrase(1, singular, plural) = "one {singular}"`, and otherwise it is
`"{canonical decimal} {plural}"`.

- summary: `No unresolved deterministic findings were recorded at frontier {sequence}.`,
  `Coverage is insufficient at frontier {sequence}.`, or
  `{Capitalized count_phrase(N, "actionable finding", "actionable findings")} remain unresolved at
  frontier {sequence}.`;
- outstanding work: `No open obligations are recorded.`, `One obligation remains open.`, or
  `{N} obligations remain open.`;
- findings: `No findings remain open.`,
  `No actionable finding is selected, but weak coverage prevents the strong conclusion.`,
  `One actionable finding remains unresolved.`, or
  `{N} actionable findings remain unresolved.`;
- evidence basis:
  `The receipt retains {count_phrase(C, "claim reference", "claim references")} and
  {count_phrase(E, "evidence reference", "evidence references")}.`;
- limitations: `Coverage is bounded to the recorded evidence and is not proof of correctness.`
  when there is no gap/redaction;
  `Visibility is reduced by recorded redactions; coverage is not proof of correctness.` when there
  are redactions but no gap; otherwise `Coverage is limited by: {codes}.`, where `codes` is the
  comma-space join of the exact sorted material gap-code tuple; and
- versions: `Engine {engine_version}; protocol {protocol_version}; {policy rows}.`, where each
  policy row is `{policy_id} {policy_version}` in `ReceiptVersionSlice.policy_versions` order and
  rows are separated by semicolon-space.

Fallback items are respectively empty, open-obligation IDs, unresolved actionable finding IDs,
claim refs followed by evidence refs, exact gap codes, and empty; fallback `coverage_note` is
`None`. More specific reviewed rows may mention only the additional typed key facts listed above.
The exact templates and items for every released v0.1 vector are the already-frozen
`receipt_document.sections` values in `fixtures/receipts/*.case.json`; changing them requires a new
fixture/version rather than an implementation-local paraphrase.

Because sections and redaction rows are fields of `ReceiptDocument`, any profile/include choice that
changes them changes canonical bytes and `receipt_digest`; redaction is not a render-only rewrite.
Two profiles may coincide only when the matrix removes no leaf. In every case conclusion, subject
frontier, suppression count, weakest coverage, and material gap codes remain equal or weaker and
never disappear.

`build_receipt` never writes to SQLite, never reads ambient time or randomness, and never re-ranks findings. It
assumes ranking already happened and packages the result into a canonical document.

## Errors and edge cases

- Frontier mismatch is an internal consistency error and must not be hidden.
- A receipt without a conclusion, coverage summary, or version identity is invalid.
- An unsupported redaction profile is rejected rather than approximated.
- The builder never introduces new evidence, claims, or findings that are not already in context.

## Invariants

1. A receipt is a frozen document, not a live view.
2. The same complete explicit inputs produce the same receipt document; changing the receipt ID or
   generation timestamp changes its canonical digest by design.
3. The builder never claims stronger certainty than the findings allow.
4. Redaction changes the canonical document whenever the frozen matrix removes content, while
   preserving or weakening every truth-bearing conclusion/coverage/gap fact.
5. The receipt document is what gets hashed and stored, while renders are presentation only.

## Tests

- `specs/tests/unit.md` — stable section order, conclusion selection, and redaction behavior.
- `specs/tests/conformance.md` — receipt parity between memory and SQLite adapters.
- `specs/tests/packaging.md` — version and support identities embedded in the receipt are stable.
- `fixtures/receipts/` — golden canonical receipt documents and compact views.

## Open questions

None.
