# src/yoetz/kernel/policies/work_integrity.py — deterministic work-integrity policy pack

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/deterministic_checks.md`, `kernel/projections.md`, `domain/findings.md`,
`protocol/coverage.md`, `protocol/errors.md` |
**Imported by:** `kernel/deterministic_checks.md`, `kernel/ranking.md`

## Purpose

This pack is the deterministic work-check policy for ordinary task completion. It answers the
question: did the work actually happen, did the requested items get attempted, and is the final
claim honest about what remains open?

Most rules here key off a claim, a response, or a completion, so they answer that question at the
end of the work. `action_without_result` is the deliberate exception and is readable at any
frontier: it reports an attempt the record left unresolved, which is a fact about the work itself
rather than about something the agent asserted. That is what makes the pack useful to an agent
checking its own progress and not only to a completion-time verdict.

The pack is intentionally explicit. It separates “work was incomplete” from “the evidence is too
weak to say.” That distinction keeps receipts and checks honest.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `WORK_INTEGRITY_POLICY_ID` | `str = "work-integrity"` |
| `WORK_INTEGRITY_POLICY_VERSION` | `str = "0.1.0"` |
| `WORK_INTEGRITY_POLICY_PACK` | frozen `PolicyPack` instance for this rule set |
| `WORK_INTEGRITY_FACT_CODES` | frozen exact fact-code registry for every rule basis |
| `work_integrity_findings(case: DeterministicCase)` | run the pack and return `tuple[DeterministicAssessment, ...]` |

## Behavior

The pack imports `PolicyPack`, `DeterministicCase`, `FindingFact`, `FindingBasis`, and
`DeterministicAssessment` from `kernel/deterministic_checks.py`. That owning module has no
module-import-time dependency back to this pack; engine dispatch imports this module locally only
after the shared types exist.

The pack examines the frozen projection state in a deterministic case and emits deterministic
assessments for work that is incomplete, stale, unsupported, or contradicted. Each assessment pairs
the candidate finding with the exact internal `FindingBasis` owned by
`deterministic_checks.md`.

The rule inventory is stable and intentionally narrow:

- `completion_with_open_obligations` — the current claim or check closure leaves open obligations
  that were neither resolved nor waived.
- `requested_item_never_attempted` — a requested item recorded in the work trace was never attempted
  by any action or preserved by a valid waiver.
- `failed_work_omitted` — the state contains a failure result that the current completion claim or
  response omits from its typed supporting refs.
- `claim_without_admissible_evidence` — a claim is present but its support refs are missing,
  unavailable, structurally inadmissible, or stale.
- `result_without_action` — a result appears without a recorded action that could have produced it.
- `action_without_result` — a recorded attempt was left unresolved: no result links to it, and the
  case shows later work on a different subject.
- `stale_evidence_for_changed_state` — evidence no longer matches the tested or described state.
- `contradictory_claims_unresolved` — the projection contains conflicting claims with no recorded
  resolution.
- `ledger_stale_or_incomplete` — the ledger state is missing required history, contains unknown
  events, or is otherwise not current enough for a strong conclusion.
- `weak_or_stale_response` — a rejection or waiver is present but the supporting basis is hollow,
  stale, or too weak to be considered resolved.

`WORK_INTEGRITY_FACT_CODES` is exactly: `completion_claim_present`,
`open_obligation_present`, `valid_waiver_absent`, `requested_item_present`,
`linked_attempt_absent`, `failed_result_present`, `failure_disclosure_absent`, `claim_present`,
`admissible_evidence_absent`, `result_present`, `linked_action_absent`, `action_present`,
`linked_result_absent`, `subsequent_unrelated_work_present`,
`state_comparison_available`, `state_changed`, `evidence_state_mismatch`,
`contradictory_claims_present`, `resolution_absent`, `unknown_event_present`,
`redaction_gap_present`, `freshness_gap_present`, `finding_response_present`,
`response_basis_insufficient`, and `response_state_stale`. A rule may use only these codes in its
observed/missing tuples; adding or renaming one changes the policy-pack version and its golden basis
fixtures.

The rule-to-fact/root crosswalk is exact. In the table, `C/O/A/R/V/F/Q/D` mean a current
claim/obligation/action/result/evidence/finding/response-event/disputed claim-or-event ID; `X` is the
current action/result/claim ID owning the checked `SubjectStateRef`; `L` is a later structurally
unrelated action ID; `G(code)` is the sorted union of nonempty `CaseGap.subject_refs` for that fact
class; and `S(F)` is the responded finding's already-canonical public `subject_refs`. `E(id)` is the
current projection record's source event for `act|res|evd|fnd`; public IDs `evt|obl|clm` remain
themselves. Parenthesized values are exact sorted-unique tuples, not prose metavariables at runtime.
Codes in the missing column enter `required_but_missing_facts`; all others enter `observed_facts`.
For every row, `FindingBasis.supporting_refs` is exactly the union of the observed-fact ref tuples.

| Finding kind | One raw trigger and exact candidate `subject_refs` | Exact observed fact refs | Exact missing fact refs |
|---|---|---|---|
| `completion_with_open_obligations` | primary `(C,O)` -> `(C,O)` | `completion_claim_present:(C)`; `open_obligation_present:(O)` | `valid_waiver_absent:(C,O)` |
| `requested_item_never_attempted` | primary `(O)` -> `(O)`; all unmatched requested-item values on that obligation are grouped | `requested_item_present:(O)` | `linked_attempt_absent:(O)` |
| `failed_work_omitted` | one omitted failure/partial pair `(C,R)` -> `(C,E(R))` | `failed_result_present:(R)` | `failure_disclosure_absent:(C,R)` |
| `claim_without_admissible_evidence` | primary `(C)` -> `(C)` | `claim_present:(C)` | `admissible_evidence_absent:(C)` |
| `result_without_action` | primary `(R)` -> `(E(R))` | `result_present:(R)` | `linked_action_absent:(R)`; the absent action ID is never inserted as an observed ref |
| `action_without_result` | primary `(A)` -> `(E(A))`; every qualifying later action is grouped into `(L...)` | `action_present:(A)`; `subsequent_unrelated_work_present:(A,L...)` | `linked_result_absent:(A)` |
| `stale_evidence_for_changed_state` | one comparison `(V,X)` -> sorted `(E(V), public_root(X))` | each of `state_comparison_available`, `state_changed`, and `evidence_state_mismatch` uses `(V,X)` | none |
| `contradictory_claims_unresolved` | one explicit edge `(C,D)` -> `(C,D)` | `contradictory_claims_present:(C,D)` | `resolution_absent:(C,D)` |
| `ledger_stale_or_incomplete` | `G = union(G(unknown),G(redaction),G(freshness))`; nonempty `G` -> `G` | each present code uses only its own `G(code)` tuple | none; rootless-only gaps emit no candidate |
| `weak_or_stale_response` | primary `(F,Q)` -> `S(F)` | `finding_response_present:(F,Q,<sorted response evidence refs>)`; optional `response_state_stale:(F,Q)` | `response_basis_insufficient:(F,Q)` only when support is absent/inadmissible |

`public_root(X)` follows the shared mapper: a claim stays `C`; an action/result becomes its source
event. The stale-evidence comparison never chooses a state digest as a subject. For the ledger row,
an over-`MAX_REF_LIST` union fails case validation instead of producing a truncated finding. For the
response row, an unreadable/missing responded finding cannot supply `S(F)` and therefore produces a
coverage gap rather than a replacement response finding.

No free-text field participates in deriving any code in this table. Candidate priority and
actionability are the exact `FINDING_KIND_TRAITS` row registered in `specs/INTERFACES.md`; the pack
does not choose them dynamically.

For `action_without_result`, "later work on a different subject" has one exact structural rule.
`action_subject_key(action)` returns the tagged sorted set `("obligations",
action.obligation_refs)` when obligation refs are nonempty; otherwise it returns
`("requested_items", action.attempted_items)` when attempted items are nonempty; otherwise it
returns `None`. `SubjectStateRef` digests identify state, not subject, and never enter this key.
Two keys are comparable only when their tags match, and are different only when their member sets
are disjoint. Subsequent work means an action with a greater ledger ingestion sequence, or a result
linked to such an action. The rule fires only when the unresolved action and one subsequent action
have comparable disjoint keys; an unkeyed/incomparable pair, an overlapping set, or merely a newer
state digest does not fire. This comparison order is fixed even when both actions carry requested
items and obligation refs.

Rule-level behavior is deterministic and conservative:

- `completion_with_open_obligations` triggers only when the case contains an explicit
  `claim_kind=completion` claim and at least one ID in that claim's `obligation_refs` remains
  projected open at the checked
  frontier. It does not trigger when the obligation was already resolved, waived in scope, or never
  material to the requested work.
- `requested_item_never_attempted` triggers only when a requested item is present in the case,
  no `ActionRecordedPayload.attempted_items` contains the exact requested-item value, and the item
  remains in the current projected plan rather than having been explicitly removed from
  scope. It does not trigger when the attempt exists but failed.
- `failed_work_omitted` triggers only when the record says some work failed or was partial while the
  completion claim's typed `supporting_refs` omit that exact failure/partial `result_id`. It does
  not parse the claim statement and does not trigger when the result ID is linked.
- `claim_without_admissible_evidence` triggers only when the claim support refs are absent,
  unresolved, redacted/unavailable, or stale by exact comparable `SubjectStateRef` digests. For a
  completion claim, a referenced `ResultRecordedPayload` with `failure|partial|unknown` is not
  admissible completion evidence. It does not inspect claim prose or interpret evidence relevance,
  and does not trigger when the claim is merely concise.
- `result_without_action` triggers only when a result exists without a matching action or the
  action linkage is internally inconsistent. It does not trigger when the action exists but the
  result is still pending elsewhere.
- `action_without_result` triggers only when a recorded action has no linked result and the case
  also contains later recorded work satisfying the exact `action_subject_key` rule above. That
  later structurally unrelated work is the whole trigger: it is what separates an abandoned
  attempt from one still in flight. The most recent recorded work therefore never fires this rule,
  because the frozen case cannot show whether it has finished. The rule does not trigger when a
  result exists and reports failure, which is `failed_work_omitted`'s subject, and it does not infer
  from a missing result that the attempt failed, succeeded, or never happened — only that the
  record leaves it unresolved. It carries
  priority 3, so a completion-time check ranks material findings above it while a check run during
  the work surfaces it when nothing more material competes.
- `stale_evidence_for_changed_state` triggers only when
  `subject_state_relation(evidence_state, checked_state) == different`. In v0.1 that requires two
  present, unequal `tree_digest` values; unequal `diff_digest` values remain `unknown`. The rule
  does not read `described_state` or other prose and does not trigger for equal digests or
  incomparable state formats.
- `contradictory_claims_unresolved` triggers only from an explicit
  `ClaimRecordedPayload.disputes_refs` edge whose referenced claim/event remains unresolved in the
  projection. It never tries to decide whether two statement strings can be jointly true. It does
  not trigger when a later structural resolution/supersession clears the edge.
- `ledger_stale_or_incomplete` triggers only when unknown events, redaction gaps, or freshness
  limits make the record too weak for a current conclusion. It does not trigger when the history is
  complete and fresh. It uses the sorted union of the triggering `CaseGap.subject_refs`; if that
  union is empty because every material gap is task/global, it emits no candidate and leaves the
  gap to weaken the caller's `RankingContext` and receipt. It never invents a subject ref merely to
  satisfy the public finding schema.
- `weak_or_stale_response` triggers only when a response rejects or waives a finding without a
  structurally admissible typed `evidence_refs` basis, or when its exact finding frontier is stale
  relative to the responded finding. It never grades the free-text reason and does not trigger for
  a response with current admissible support.

Policy cardinality follows the engine contract exactly. For each rule, the logical subject key is
the candidate's complete canonical `subject_refs` tuple. The pack groups every raw structural
trigger input for that exact tuple before rule evaluation, evaluates the grouped input exactly
once, and emits zero or one assessment for the emitted-key identity
`(WORK_INTEGRITY_POLICY_ID, rule_id, subject_refs)`. Repeated raw triggers are therefore inputs to
one evaluation. If pack wiring nevertheless emits the same key twice, the engine rejects the
duplicate as a policy-wiring defect; no duplicate-assessment reconciliation path exists. Different
work-integrity rules may still emit separate assessments for the same subject tuple because their
`rule_id` members differ.

Every rule also emits canonical trigger/missing fact codes and subject refs. Rules involving
edits/tests set the basis relation from `subject_state_relation` and frozen
`source_availability` separately. A claimed edit with no comparable tree digests is
`unknown/not_recorded`; two comparable unequal digests are `different/available` in the basis even
if a later egress policy withholds the excerpt; only equal observed tree digests are `same`.
Therefore neither rule prose nor a later semantic reviewer can report “no code difference” merely
because source content was unavailable.

`work_integrity_findings(case)` uses only the frozen projection state, the case frontier, the
allowed IDs, and the current policy version. It never reads provider output and never consults
SQLite. Findings are emitted with `origin = deterministic`, `provenance = None`, and the exact
policy identity from this pack.

The pack derives each finding's coverage in two exact steps. First it folds `coverage.weakest`
over `case.coverage_by_ref[ref]` for every ref in the assessment's exact sorted
`FindingBasis.supporting_refs`. The tuple is nonempty for every emitted finding, and a missing
index entry is a malformed case, never permission to choose a channel default. Second it constructs
a new `Coverage` that preserves the folded `authorship_assurance`, `artifact_observation`,
`evidence_immutability`, `ledger_freshness`, and `known_gaps` byte-for-byte; adds
`engine_derived` to the folded sorted-unique `publication_channels`; and adds `deterministic` to
the folded `check_types`, removing `none` when present. This derived-finding step changes only the
two set-valued provenance dimensions. It never calls `weakest` with the `engine_derived` channel
default and never strengthens or otherwise rewrites an ordered material dimension. The candidate's
`subject_frontier` is exactly `case.frontier`. If supporting evidence is partial, stale, or
redacted, the finding therefore retains that weakness instead of pretending to be complete.

## Errors and edge cases

- Unknown case shapes or pack wiring errors are internal configuration defects.
- A missing obligation/action/result link is treated as a finding input, not as a missing
  exception.
- The pack never emits semantic provenance or provider-specific language.

## Invariants

1. The pack is deterministic and side-effect free.
2. The pack only speaks about work integrity, not research evidence.
3. Same frozen case → same assessment tuple.
4. The pack never strengthens coverage past the available refs.
5. The pack ID and version are stable release identities.
6. Candidate and basis are generated by the same pure rule evaluation and cannot contradict.
7. An absent result is never evidence of an outcome. `action_without_result` reports only that the
   record leaves an attempt unresolved, never that it succeeded, failed, or never happened.
8. All raw triggers for one rule and complete subject tuple are evaluated once, and duplicate
   emitted keys are rejected rather than reconciled.

## Tests

- `specs/tests/unit/kernel/test_policy_work_integrity.py.md` — inline exact trigger and closest
  non-trigger for every rule; `action_without_result` fires for an unresolved attempt followed by
  later work on another subject and stays silent for the most recent recorded action.
- `specs/fixtures/README.md` maps the finite public work-integrity vectors to exact case files
  `ADV-001-abandoned-obligation`, `ADV-002-omitted-failed-test`, `ADV-003-stale-test-after-edit`,
  `ADV-006-parent-subagent-contradiction`, `ADV-008-stale-redacted-ledger`, and
  `ADV-009-wrong-semantic-finding-rejected`; no separate work-integrity fixture-resource directory
  exists.

## Open questions

None.
