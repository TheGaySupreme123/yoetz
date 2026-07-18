# src/yoetz/kernel/deterministic_checks.py — deterministic work-policy evaluation

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/projections.md`, `kernel/reducers.md` (`ReplayIndex` builder only), `domain/events.md`, `domain/findings.md`, `domain/values.md`,
`protocol/coverage.md`, `protocol/errors.md` | **Runtime-dispatched packs:**
`kernel/policies/work_integrity.md`, `kernel/policies/research_evidence.md` | **Imported by:** `application/check.md`,
`application/status.md`, `adapters/sqlite/repository.md`, `kernel/ranking.md`

## Purpose

This file contains the deterministic policy engine. It is the non-LLM part of Yoetz’s checking
loop and the first line of defense against bad work claims. It only looks at the frozen case and
the versioned policy packs. It never looks at provider output, network state, or SQLite rows.

The engine exists so the system can explain failures even when semantic evaluation is unavailable,
delayed, or refused. Deterministic checks are not a fallback in the weak sense; they are a primary
trust layer.

Because the engine is pure, one evaluation serves two callers with different durability. `check`
freezes a case, allocates finding IDs, records events, and reaches a verdict; `status` with
`view=candidate_findings` runs the identical packs over the identical case shape and records
nothing. The engine cannot tell the callers apart and is given no way to, which is why a candidate
and a recorded finding at the same frontier can never disagree.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PolicyPack` | frozen data-only `(policy_id, policy_version)` selector |
| `UnavailableCapturedObject` | frozen captured-object availability identity `(source_event_id, object_id)` |
| `CaseAvailabilityFacts` | frozen, plaintext-free event/captured-object unavailability snapshot |
| `FindingBasisRef` | exact typed internal ref union `evt|obl|clm|act|res|evd|fnd` |
| `FrozenSourceAvailability` | enum `available`, `not_recorded`, `unavailable_at_freeze`, `redacted_at_source` |
| `FindingFact` | frozen fact code plus exact sorted subject refs |
| `FindingBasis` | internal frozen rule/fact/state/source-availability/coverage explanation for one candidate |
| `DeterministicAssessment` | one `CandidateFinding` paired with its exact `FindingBasis` |
| `DeterministicPolicyResult` | frozen assessments-only result for one completed pack invocation |
| `DeterministicFindingTemplate` | frozen `(summary, next_action)` text owned once for every deterministic kind |
| `DETERMINISTIC_FINDING_TEMPLATES` | immutable complete `FindingKind -> DeterministicFindingTemplate` registry |
| `render_deterministic_finding_text(kind, subject_refs)` | render the exact ID-only summary/detail pair |
| `finding_basis_to_status_json(assessment)` | controlled projection to `status-result#/$defs/finding_basis` |
| `CaseGap` | frozen typed projection/case gap `(marker, code, subject_refs)` |
| `DeterministicCase` | pure frozen policy input with projection, frontier, availability facts, allowed IDs, coverage index, and typed gaps |
| `build_deterministic_case(projection, records, availability)` | derive the pure case from one exact accepted-record prefix and its frozen availability snapshot |
| `run_deterministic_policies(case, policy)` | evaluate one `DeterministicCase` and return `DeterministicPolicyResult` |

## Behavior

`PolicyPack` is exactly the frozen data-only pair `policy_id: str` and `policy_version: str`. It is
a selector for one built-in pack, not a callback-bearing object or dynamic ruleset. The two policy
modules construct the only supported values. The engine rejects any other pair and verifies the
selected module's exact exported constants before evaluation.

`DeterministicPolicyResult` is exactly
`(assessments: tuple[DeterministicAssessment, ...])`. It is returned only after the selected pack
completed normally; an empty tuple is a successful no-finding evaluation. It has no pack identity,
outcome, reason, or ran/skipped/failed tuple. `application/check.py` owns whether a selected pack is
invoked and creates the sole `ports.ledger.CheckPolicyExecution` record around that invocation.
Unknown/tampered pack identities raise an internal policy-wiring error rather than returning a
fabricated failed result.

`UnavailableCapturedObject` is exactly `(source_event_id: EventId, object_id: ObjectId)`.
`CaseAvailabilityFacts` is exactly `(unavailable_event_ids: tuple[EventId, ...],
unavailable_captured_objects: tuple[UnavailableCapturedObject, ...])`. The event tuple is sorted
unique by unsigned ASCII ID bytes; captured-object rows are unique and sort by source-event bytes,
then object-ID bytes. It contains no reason string: missing object, missing key, and locked key have
the same deterministic meaning, while a recorded redaction remains a distinct ledger fact. The
shared `LedgerPort.load_case_availability(session_id, frontier, projection)` operation obtains this immutable snapshot outside every
write transaction. Durable check freeze and candidate status use that same operation, and the
canonical availability digest participates in the durable check dependency digest.

The event cause table is closed. A current source whose accepted envelope is
`logically_redacted|erased_claimed`, or which is an effective replay-index redaction target, is
recorded redaction and is excluded from `unavailable_event_ids`. A current source whose payload is
null with envelope state `key_unavailable`, or with `present` because its object/key cannot be
opened at the snapshot, is non-redaction unavailability and is included. A readable `present`
source is excluded. No other combination is valid.

`CaseGap` is exactly `(marker: str, code: str,
subject_refs: tuple[EventId | ObligationId | ClaimId, ...])`. `marker` is the
canonical projection marker or the exact case-only unavailable marker below; `code` is its bounded machine class;
`subject_refs` is sorted unique and contains only public event/obligation/claim roots. Empty refs
are allowed only for a genuine task/global limitation. A rootless gap weakens the check's material
coverage and receipt but cannot create a finding because the public finding contract requires at
least one real subject.

`FindingBasisRef` is exactly
`EventId | ObligationId | ClaimId | ActionId | ResultId | EvidenceId | FindingId`.
`DeterministicCase` is exactly the frozen value `(projection: ProjectionState, frontier: Frontier,
availability: CaseAvailabilityFacts, allowed_ids: frozenset[FindingBasisRef], coverage_by_ref:
immutable Mapping[FindingBasisRef, Coverage], gaps: tuple[CaseGap, ...])`. `ProjectionState`
deliberately stores its frontier as the separate pair
`frontier: int` and `head_digest: str`; it does not contain a nested `Frontier`. The one exact
relation is therefore
`case.frontier == Frontier(sequence=case.projection.frontier,
head_digest=case.projection.head_digest)`. Both sequence and digest must match. Comparing only the
integer or comparing a `Frontier` object directly to `projection.frontier` is a programming defect.
The index contains the current visible logical IDs and their source event IDs, plus accepted event
IDs needed as nonempty gap roots. Each value starts from the exact accepted-envelope coverage for
that source and is weakened by the closed component table below; no default is inferred from a
channel or record kind.

`build_deterministic_case(projection, records, availability)` is pure. The caller supplies the authoritative
ledger-ordered prefix whose final accepted record has `ingestion_sequence == projection.frontier`
and `entry_digest == projection.head_digest`; the empty prefix is valid only for `(0, "genesis")`.
The helper constructs the `Frontier` from that exact integer/digest pair, builds the same
envelope-only `ReplayIndex` used by reducers, joins each current projection record's
`source_event_id` to its accepted envelope, builds the exact coverage index, derives the allowed-ID
set, validates the frozen availability facts, and converts projection/case gap markers to typed
`CaseGap` values. A prefix
ending at the right integer with a different digest fails rather than being treated as the same
case. The frozen marker grammar is:

- `unknown_event:<event_id>:<schema>@<version>` -> code `unknown_event`, subject refs containing
  that unknown event ID;
- `redacted_event:<event_id>` -> code `redacted_event`, subject refs containing the source/target
  event ID, for accepted-envelope `logically_redacted|erased_claimed` or an effective replay-index
  event/object target;
- `redacted_object:<object_id>` -> code `redacted_object`, subject refs containing exactly the
  causative redaction event with the lowest ledger ingestion sequence in the same prefix whose
  locator targets that object;
- `missing_ref:<source_event_id>:<target_logical_id>` -> code `missing_ref`, subject refs
  containing the visible source event ID; the target logical ID is retained in the marker for
  audit but is never guessed into a public finding root;
- case-only `unavailable_event:<event_id>` -> code `event_payload_unavailable`, rooted at that event,
  only when a current record payload is unavailable without any effective redaction target; and
- case-only `unavailable_captured_object:<source_event_id>:<object_id>` -> code
  `captured_object_unavailable`, rooted at the evidence source event, only when captured content is
  unavailable without a matching redaction marker.

Availability validation is closed at the pure boundary. `unavailable_event_ids` must equal the
current projection-source event IDs selected by the event cause table above; readable,
non-current, unknown, or effectively redacted sources in that tuple are defects.
Every `unavailable_captured_objects` row must resolve to the exact current evidence
`(source_event_id, captured_object_id)` pair in `ReplayIndex.evidence_sources_by_object` and must
have no effective object-redaction target. A row for evidence with no captured object, a stale or
unknown source, the wrong object ID, or a redacted object is a defect. Because accepted records do
not encode live captured-object readability, the pure helper cannot prove that an adapter omitted
an unavailable pair or inserted an actually readable pair; exact probe completeness belongs to
`LedgerPort.load_case_availability` conformance and its object/key-generation fence. The helper
performs no probe, preserves the supplied canonical tuple exactly, and never fabricates a fact.

An object-only payload deletion is an effective event target in the reducer index and therefore
uses `redacted_event`, not `event_payload_unavailable`. Multiple redaction events targeting one
object retain the first causative event by ledger ingestion sequence as the exact single public
root; later events remain auditable ledger facts and neither event-ID ordering nor iteration order
can replace it. A retained object marker with no causative locator or a
case-only unavailable marker that also has a matching redaction is corruption. Any other reducer-
owned marker names its causative event ID explicitly. A pre-existing
`Coverage.known_gaps` entry with no recorded root becomes a global `CaseGap` with empty refs.

Coverage weakening is exact and component-wise. `min(x, cap)` below means the lower registered
ordinal in that field; the three tuple/set dimensions are not treated as strength scores.

| Condition on one indexed ref | `publication_channels` | `authorship_assurance` | `artifact_observation` | `evidence_immutability` | `ledger_freshness` | `check_types` | token added to `known_gaps` |
|---|---|---|---|---|---|---|---|
| event payload effectively redacted | unchanged | unchanged | `published_only` | `min(base, metadata_only)` | `min(base, redacted_gap)` | unchanged | `redacted_event` |
| event payload unavailable without redaction | unchanged | unchanged | `published_only` | `min(base, metadata_only)` | `min(base, redacted_gap)` | unchanged | `event_payload_unavailable` |
| evidence captured object redacted | unchanged | unchanged | `published_only` | `min(base, content_digest)` | `min(base, redacted_gap)` | unchanged | `redacted_object` |
| evidence captured object unavailable without redaction | unchanged | unchanged | `published_only` | `min(base, content_digest)` | `min(base, redacted_gap)` | unchanged | `captured_object_unavailable` |
| visible source has an unresolved typed ref | unchanged | unchanged | unchanged | unchanged | `min(base, partial)` | unchanged | `missing_ref` |
| opaque unknown event | unchanged | unchanged | unchanged | unchanged | `min(base, partial)` | unchanged | `unknown_event` |

`published_only` is already the weakest artifact-observation value, so spelling it directly cannot
strengthen a base. If several rows apply, every ordered cap is applied, and the existing gaps plus
all applicable tokens are emitted as one sorted unique tuple. A ref targeted by an object-only
payload redaction receives both `redacted_event` and `redacted_object`; a captured-content target
receives `redacted_object` without losing its readable evidence metadata. These six tokens are the
only kernel-added `Coverage.known_gaps` values in generation 1. The resulting value must still fit
the registered 64-token `Coverage` bound; an over-cap accepted prefix fails case freezing as
corruption instead of truncating or dropping a gap.

This sidecar deliberately does not add fields to `ProjectionState` or `projection_snapshot`: the
existing generation-1 golden snapshots remain byte-stable. The ledger head already commits the
accepted envelopes from which the index is derived, and freeze/resume persists the complete case
inside the authenticated encrypted resume object. Candidate-status and durable check paths MUST
obtain the same availability shape and call this same helper over the same prefix. The common
`ProjectionRecord.redacted` bit remains true for every null-payload tombstone for generation-1
snapshot compatibility; actual redaction versus non-redaction unavailability is decided only by
the envelope/index/availability equality above.

`run_deterministic_policies(case, policy)` is pure. It inspects the `ProjectionState` contained in
the supplied `DeterministicCase`, applies the selected built-in rule set, and returns ordered
`DeterministicAssessment` values. It does not rank the findings, allocate IDs, build a receipt, or
decide whether semantic evaluation should run next.

To keep the shared types in their declared owning module without adding an unowned
`kernel/policy_types.py`, this module never imports either policy module at module import time.
`run_deterministic_policies` performs the two explicit imports inside its dispatch body after all
types above are defined; `TYPE_CHECKING` imports are allowed. Each policy module imports the shared
types from this module. Directly importing either pack and importing this engine first are therefore
both cycle-safe.

`FrozenSourceAvailability` is the `str` enum `available`, `not_recorded`,
`unavailable_at_freeze`, or `redacted_at_source`. For a rule comparison its exact precedence is:
any material compared source with envelope `logically_redacted|erased_claimed` or an effective
recorded redaction target -> `redacted_at_source`; else any
material compared source in `CaseAvailabilityFacts` -> `unavailable_at_freeze`; else any required
source ID or typed state absent from the frozen case -> `not_recorded`; else `available`. A basis
with no source comparison uses `available`. This fact is about the frozen source, not later
disclosure.

`FindingBasis` contains only canonical, machine-readable facts already in the deterministic case. Its exact
fields are: `rule_id: str`; `observed_facts: tuple[FindingFact, ...]`;
`required_but_missing_facts: tuple[FindingFact, ...]`;
`subject_state_relation: SubjectStateRelation`; `source_availability:
FrozenSourceAvailability`; `coverage_gaps: tuple[str, ...]`; and
`supporting_refs: tuple[FindingBasisRef, ...]`. `SubjectStateRelation` is imported from
`domain.values`; this module defines no parallel three-string type. `FindingFact` is exactly
`(fact_code: str, subject_refs: tuple[FindingBasisRef, ...])`. `rule_id` is namespaced and must equal
`f"{candidate.policy_id}/{candidate.kind.value}"`; the internal value is therefore for example
`work-integrity/completion_with_open_obligations`, never the bare kind. `observed_facts` contains
1..33 entries and `required_but_missing_facts` contains 0..33. Within each tuple a fact code occurs
at most once. Each ref tuple is sorted unique and contains 1..`MAX_REF_LIST` frozen-case IDs;
`supporting_refs`, the union of all observed refs, and the tuple of coverage gaps are independently
sorted unique and each has at most `MAX_REF_LIST`. Fact tuples sort by unsigned UTF-8 `fact_code`
then canonical ref bytes. Fact codes come only from the selected pack's registry, never provider/
config text. The basis contains no hidden reasoning, ambient repository data, policy-selection
result, or new conclusion prose.

`finding_basis_to_status_json(assessment)` is the sole controlled projection to the frozen
`status-result#/$defs/finding_basis` object. It does not mutate or replace the encrypted internal
basis. The mapping is exact:

| Status field | Source |
|---|---|
| `rule_id` | `assessment.candidate.kind.value`, after verifying the namespaced internal rule ID above |
| `observed_fact_codes` | sorted unique `fact_code` values from `observed_facts` |
| `observed_refs` | sorted unique union of every `observed_facts[*].subject_refs` |
| `required_missing_fact_codes` | sorted unique `fact_code` values from `required_but_missing_facts` |
| `subject_state_relation` | the same enum value |
| `frozen_source_availability` | `available -> available`, `not_recorded -> not_recorded`, `unavailable_at_freeze -> unavailable`, `redacted_at_source -> redacted` |
| `coverage_gaps` | the same sorted tuple |
| `evidence_refs` | sorted unique `supporting_refs` whose canonical prefix is `evd_` or `res_` |

The status basis is intentionally a controlled public projection, not a second internal basis:
its schema flattens fact/ref associations and does not carry refs for absent facts. It is never
decoded to resume a check or to construct an outbound review packet. The candidate item surrounding
it carries `policy_id` and `kind`, so the public bare rule token is unambiguous. The status
`frozen_source_availability=unavailable` token is emitted only from an explicit frozen availability
fact, never from a later disclosure decision. Internal construction bounds the unions above, so the mapper never truncates
to the schema's 64-member ceilings.

`source_availability` is intentionally earlier than disclosure visibility. It says only whether the
frozen projection supplied comparable material, an explicit availability fact, or an
already-redacted marker. The later case
builder/gateway derives `available|not_recorded|not_selected|withheld_by_policy|
redacted_never_send` for each `ChangeObservation`/omission. A pure deterministic basis can never
contain `not_selected` or `withheld_by_policy` because no review-context or egress policy has run.
The basis stays in encrypted local check/semantic-case objects and need not enlarge the public
finding/event schemas.

The `policy` argument is the loaded immutable pack from `kernel/policies/*`, not a dynamic rule
source. The engine rejects an unknown or tampered policy pack rather than trying to approximate it.

Each pack owns an exhaustive rule-to-`FindingFact` crosswalk. The engine MUST emit exactly the
registered observed/missing fact codes for a firing rule, validate the candidate's priority against
the kind's shared `FINDING_KIND_TRAITS` row, and derive actionability only from that row. It may not
infer a fact from `statement`, `summary`, `description`, `reason`, `rationale`,
`described_state`, or any other free-text field. Exact typed refs, closed enums, digest/frontier
equality, exact `RequestedItem.value`/`attempted_items` token equality, and explicit
`disputes_refs` edges are the complete deterministic input vocabulary. Requested-item tokens are
compared byte-exactly and are never linguistically interpreted.

Each policy table also names the complete primary basis tuple used for one raw trigger. Candidate
public roots are derived exactly as follows: `evt|obl|clm` remains itself; `act|res|evd|fnd` resolves
to the current projection record's `source_event_id`. The response-review rules explicitly use the
responded finding's already-public `subject_refs`, rather than replacing them with the finding event.
The sorted unique result is the candidate's complete `subject_refs`; an unresolved primary ID or an
empty result is not repaired. Every fact gets the exact row-specific basis refs from the policy
table, and `supporting_refs` is the sorted unique union of all `observed_facts[*].subject_refs`.
Missing-fact anchors do not enter that union merely because the fact was absent. Over-bound unions
fail case/policy validation rather than truncating or choosing a first ref.

The engine uses the current projection state to derive findings for the active pack:

- the work-integrity pack looks for open obligations, unattempted requested items, missing action →
  result links, stale evidence, unresolved contradictions, stale/incomplete ledgers, and hollow
  responses;
- the research-evidence pack looks for claim-evidence mismatch, diff/account mismatch, omitted
  limitations, and unjustified rejection of evidence-based findings.

Policy cardinality is exact. A logical subject key is the candidate's complete canonical
`subject_refs` tuple; it is not a prose-derived key, first-ref shortcut, or state digest. Within one
rule, the pack first groups all structural trigger inputs by that key and evaluates the grouped
input exactly once. The emitted-key identity is `(policy_id, rule_id, subject_refs)`, and at most one
assessment may have that identity. Producing a duplicate is a policy-wiring defect; the engine
rejects it instead of choosing a subjective "strongest" value. Different rules that apply to the
same subject tuple remain separate findings because they state different registered problems.

The work-integrity rule order is exactly `completion_with_open_obligations`,
`requested_item_never_attempted`, `failed_work_omitted`, `claim_without_admissible_evidence`,
`result_without_action`, `action_without_result`, `stale_evidence_for_changed_state`,
`contradictory_claims_unresolved`, `ledger_stale_or_incomplete`, `weak_or_stale_response`. The
research-evidence order is exactly `evidence_does_not_support_claim`,
`diff_does_not_match_account`, `material_limitation_omitted`,
`questionable_finding_rejection`. Pack output sorts first by this rule ordinal and then by the
unsigned ASCII bytes of every member of the complete `subject_refs` tuple. This order is the
candidate's stable emission ordinal used by status pagination. The coordinator runs and
concatenates the built-in packs in `work-integrity`, then `research-evidence` order; ranking may
later reorder findings but cannot change the pure engine result.

Every produced assessment must:

- carry the exact `kind`, `priority`, `summary`, `detail`, `subject_refs`,
  `policy_id`, `policy_version`, `subject_frontier`, and `coverage` required by the shared finding
  model;
- be conservative about coverage and never claim stronger support than the supporting refs justify;
- render `summary` and `detail` through this module's exact template registry, naming subjects by ID and
  copying no content from behind a `subject_ref` — not an obligation's description, a claim's
  statement, evidence bytes, or a file excerpt (`domain/findings.md`). The rule and its IDs are the
  whole message, which is why deterministic prose discloses nothing about material the requesting
  writer did not author;
- use `provenance = None` because the origin is deterministic, not semantic-model-derived.
- carry a basis whose trigger facts are sufficient for the candidate and whose missing/source-
  availability facts explain what the rule did *not* observe. An edit claim without comparable
  state is `unknown/not_recorded`, never a same-state or no-diff fact.

The deterministic engine is monotonic with respect to the checked state. If the input case is more
complete, the engine may produce more findings or weaker coverage, but it must never silently
invent a stronger conclusion from weaker evidence.

The policy modules own structural trigger and fact selection. This module owns the complete text
registry and renderer, so a pack passes only the `FindingKind` and canonical public subject tuple
across the handoff and cannot carry a second template literal. `DeterministicFindingTemplate` is
exactly `(summary: str, next_action: str)`. `render_deterministic_finding_text` returns the stored
summary and the exact detail spelling `"Subjects: {comma-and-space joined subject IDs}. Main agent:
{next_action}"`. Its complete v0.1 registry is:

| Finding kind | Exact summary | Exact `next_action` |
|---|---|---|
| `completion_with_open_obligations` | `A completion claim covers an obligation that remains open.` | `Resolve the obligation or revise the completion claim.` |
| `requested_item_never_attempted` | `A requested item has no recorded attempt.` | `Attempt the requested item or revise its obligation.` |
| `failed_work_omitted` | `Recorded failed work is omitted from the published account.` | `Disclose the failed work or revise the account.` |
| `claim_without_admissible_evidence` | `A recorded claim has no admissible supporting evidence.` | `Provide admissible support or revise the claim.` |
| `result_without_action` | `A recorded result has no linked action.` | `Publish the linked action or correct the result record.` |
| `action_without_result` | `A recorded action has no linked result.` | `Record the result or state the attempt as unresolved.` |
| `stale_evidence_for_changed_state` | `The cited evidence predates a materially changed subject state.` | `Run a check against the current state.` |
| `contradictory_claims_unresolved` | `Explicitly disputed claims remain structurally unresolved.` | `Record a structural resolution or supersession.` |
| `ledger_stale_or_incomplete` | `The ledger is too incomplete for a current conclusion.` | `Treat the conclusion as coverage-limited.` |
| `weak_or_stale_response` | `A finding response lacks current admissible support.` | `Provide current admissible response evidence.` |
| `evidence_does_not_support_claim` | `The cited evidence does not support the recorded claim.` | `Provide relevant evidence or revise the claim.` |
| `diff_does_not_match_account` | `Recorded subject-state digests contradict the published account.` | `Revise the account or publish matching state evidence.` |
| `material_limitation_omitted` | `A material recorded limitation is omitted from the published account.` | `Disclose the limitation or revise the account.` |
| `questionable_finding_rejection` | `A deterministic finding was rejected without admissible support.` | `Provide current evidence for the rejection.` |

The registry has exactly the fourteen `FindingKind` members and import-time equality is asserted.
The engine rejects an assessment whose candidate text differs from the renderer; no adapter,
status path, or policy pack may repair or re-template it later.

The packs themselves are rule books, not probabilistic scorers:

- `work_integrity_findings(case)` depends only on the frozen projection, frontier, allowed IDs, and
  policy version. It does not inspect provider output or semantic responses.
- `research_evidence_findings(case)` depends only on the frozen projection, frontier, allowed IDs,
  and policy version. It does not inspect live network content or the raw user prompt.

The application keeps the two assessments-only pack results separate together with its own
`CheckPolicyExecution` records, so it can distinguish work-integrity from research-evidence
execution without duplicating accounting inside the pure kernel result.

Rule-level expectations are:

- open obligations and unattempted requested items produce findings only when the case shows they
  were actually required and remain unresolved;
- stale evidence produces findings only when the evidence refers to a materially different state
  than the one being checked;
- a claim/evidence mismatch produces a finding only from the policy table's exact typed
  result/obligation/state contradiction, never from interpreting whether prose supports prose;
- a rejected or waived finding becomes a finding only when the waiver or rejection lacks a credible
  basis in the frozen record.

## Errors and edge cases

- An unknown pack identifier or version is an internal policy wiring error.
- A missing required case component is an incomplete-check condition, not an invented finding.
- The engine never emits provider, transport, or storage errors.
- If the case contains only rootless/global gaps, the engine returns no fabricated finding; the
  caller's material coverage/completeness derivation still produces an insufficient or incomplete
  conclusion as applicable.

## Invariants

1. Deterministic checks are pure and repeatable.
2. The engine never reads ambient time, filesystem state, or provider output.
3. Same case + same pack = byte-equivalent ID-free candidates in the same order.
4. Deterministic findings never carry semantic provenance.
5. The work-integrity and research-evidence packs remain separate so one can evolve without
   silently changing the other.
6. Every deterministic candidate has exactly one stable basis, and basis generation cannot change
   whether a rule fires.
7. Rule prose is templated and ID-only: no content from behind a `subject_ref` reaches `summary` or
   `detail`.

## Tests

- `specs/tests/conformance.md` — deterministic findings from memory and SQLite adapters match.
- `specs/tests/unit/kernel/test_policy_work_integrity.py.md` and
  `test_policy_research_evidence.py.md` — smallest inline trigger and closest-nontrigger values for
  every registered rule; these are test data inside the declared test files, not hidden resources.
- `specs/fixtures/README.md` — the finite, file-by-file public
  trigger/remediation/closest-nontrigger mapping; no separate policy-resource directory exists.
- Basis fixtures freeze trigger/missing fact codes, state relation, source availability, coverage
  gaps, and byte-equivalent output across repeated runs. Marker-string fixtures prove no rule's
  rendered `summary`/`detail` carries content from behind a `subject_ref`.

## Open questions

None.
