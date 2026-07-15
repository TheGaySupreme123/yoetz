# src/yoetz/kernel/policies/work_integrity.py — deterministic work-integrity policy pack

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/projections.md`, `domain/findings.md`, `protocol/coverage.md`, `protocol/errors.md` |
**Imported by:** `kernel/deterministic_checks.md`, `kernel/ranking.md`

## Purpose

This pack is the deterministic work-check policy for ordinary task completion. It answers the
question: did the work actually happen, did the requested items get attempted, and is the final
claim honest about what remains open?

The pack is intentionally explicit. It separates “work was incomplete” from “the evidence is too
weak to say.” That distinction keeps receipts and checks honest.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `WORK_INTEGRITY_POLICY_ID` | `str = "work-integrity"` |
| `WORK_INTEGRITY_POLICY_VERSION` | `str = "0.1.0"` |
| `WORK_INTEGRITY_POLICY_PACK` | frozen `PolicyPack` instance for this rule set |
| `WORK_INTEGRITY_FACT_CODES` | frozen exact fact-code registry for every rule basis |
| `work_integrity_findings(case)` | run the pack and return deterministic assessments |

## Behavior

The pack examines the frozen projection state in a case and emits deterministic assessments for
work that is incomplete, stale, unsupported, or contradicted. Each assessment pairs the candidate
finding with the exact internal `FindingBasis` owned by `deterministic_checks.md`.

The rule inventory is stable and intentionally narrow:

- `completion_with_open_obligations` — the current claim or check closure leaves open obligations
  that were neither resolved nor waived.
- `requested_item_never_attempted` — a requested item recorded in the work trace was never attempted
  by any action or preserved by a valid waiver.
- `failed_work_omitted` — the state contains a failure result that the current completion claim or
  response fails to account for honestly.
- `claim_without_admissible_evidence` — a claim is present but its support is too weak, missing, or
  stale to justify it.
- `result_without_action` — a result appears without a recorded action that could have produced it.
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
`admissible_evidence_absent`, `result_present`, `linked_action_absent`,
`state_comparison_available`, `state_changed`, `evidence_state_mismatch`,
`contradictory_claims_present`, `resolution_absent`, `unknown_event_present`,
`redaction_gap_present`, `freshness_gap_present`, `finding_response_present`,
`response_basis_insufficient`, and `response_state_stale`. A rule may use only these codes in its
observed/missing tuples; adding or renaming one changes the policy-pack version and its golden basis
fixtures.

Rule-level behavior is deterministic and conservative:

- `completion_with_open_obligations` triggers only when the case contains an explicit completion
  claim or terminal result and at least one matching obligation remains unresolved at the checked
  frontier. It does not trigger when the obligation was already resolved, waived in scope, or never
  material to the requested work.
- `requested_item_never_attempted` triggers only when a requested item is present in the case,
  there is no linked action/result for that item, and the item was not explicitly removed from
  scope. It does not trigger when the attempt exists but failed.
- `failed_work_omitted` triggers only when the record says some work failed or was partial while the
  claim or response omits that failure in a way that changes the meaning of the result. It does not
  trigger when the failure is fully disclosed.
- `claim_without_admissible_evidence` triggers only when the claim support refs are absent,
  inadmissible, or too weak to carry the claim. It does not trigger when the claim is merely
  concise.
- `result_without_action` triggers only when a result exists without a matching action or the
  action linkage is internally inconsistent. It does not trigger when the action exists but the
  result is still pending elsewhere.
- `stale_evidence_for_changed_state` triggers only when the evidence references a different state
  than the one now being checked and that difference is material to the claim. It does not trigger
  for unchanged state or trivial metadata drift.
- `contradictory_claims_unresolved` triggers only when two or more claims in the same case cannot
  be jointly true and there is no recorded resolution. It does not trigger when a later claim
  supersedes an earlier one cleanly.
- `ledger_stale_or_incomplete` triggers only when unknown events, redaction gaps, or freshness
  limits make the record too weak for a current conclusion. It does not trigger when the history is
  complete and fresh.
- `weak_or_stale_response` triggers only when a response rejects or waives a finding without a
  sufficient recorded basis, or when the explanation itself is stale relative to the frontier. It
  does not trigger for a well-supported acknowledgement or waiver.

Each rule produces at most one finding per logical subject. The pack deduplicates repeated
symptoms on the same subject so the caller sees the strongest single finding, not a pile of
duplicates.

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

The pack’s coverage for each finding is the weakest coverage of the refs that support the rule
trigger. If the evidence is only partially available, the finding weakens rather than pretending to
be complete.

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

## Tests

- `specs/tests/unit.md` — per-rule fixtures for open obligations, stale evidence, and result/action
  mismatches.
- `fixtures/policies/work_integrity/` — golden cases and expected findings.

## Open questions

None.
