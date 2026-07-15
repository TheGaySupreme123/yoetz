# tests/unit/application/test_semantic_post_validation.py — semantic result validation fences

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/domain/findings.md`,
`src/yoetz/protocol/coverage.md`, `src/yoetz/ports/semantic.md`
**Imported by:** the application unit suite

## Purpose

Prove the application rejects semantic results that do not belong to the frozen case or that try to
strengthen evidence beyond what was actually seen.

## Public surface

- `test_invented_ids_are_rejected` — semantic results cannot mint unrelated IDs.
- `test_out_of_case_quotes_are_rejected` — text not tied to the frozen case does not pass validation.
- `test_coverage_upgrade_is_rejected` — semantic output cannot strengthen evidence beyond the case.
- `test_deterministic_claims_remain_deterministic` — semantic paths do not rewrite deterministic
  findings.
- `test_stale_frontier_is_rejected` — late semantic results against the wrong frontier fail.
- `test_split_frontier_and_local_check_refs_are_exact` — a challenge may cite the deterministic
  finding IDs pinned by this check, but no ID outside `frontier_refs ∪ local_check_refs`.
- `test_reviewer_challenge_requires_actionable_closed_shape` — discrepancy, alternative,
  direct main-agent message, uncertainty, and one closed requested-next-step value are required.
- `test_hidden_source_is_not_unchanged_source` — unavailable content cannot support a `same` or
  “no change” conclusion.
- `test_review_assessment_prose_refs_follow_profile_and_policy` — structural has no finding prose;
  richer authorized packets require a paired summary/detail mapping.

## Behavior

The suite locks the post-validation boundary:

- semantic output must reference the frozen case and frontier;
- invented subject refs or claims are rejected;
- coverage can weaken but not strengthen;
- deterministic findings remain authoritative and are not rewritten by the semantic path;
- deterministic `FindingBasis` values remain unchanged while an accepted `ReviewerChallenge` maps
  only to an ordinary semantic finding summary/detail;
- structural `ReviewAssessment` values omit summary/detail item refs, while an authorized
  goal-aware-or-richer packet requires the exact paired finding-prose refs;
- challenge refs are checked against the union of the two exact case allowlists, including
  deterministic finding IDs allocated after the frozen frontier;
- every accepted challenge is useful to the main agent under the recorded omissions and requests
  exactly `act|provide_evidence|revise_claim|dispute_with_evidence|state_unresolved_limitation`;
- stale or late results are not admitted into the final check.

## Errors and edge cases

- A semantic result that passes by quoting unrelated text fails the test.
- A result that upgrades coverage without evidence fails the test.
- A challenge without a supported discrepancy/direct agent message, or one that treats
  `not_recorded|not_selected|withheld_by_policy|redacted_never_send` as unchanged state, fails.

## Invariants

1. Semantic output is advisory, not sovereign.
2. Case-bound refs are mandatory.
3. Coverage only weakens after semantic validation.
4. Reviewer advice cannot widen context, rewrite deterministic truth, or create waiver authority.

## Tests

- `tests/unit/application/test_semantic_post_validation.py`

## Open questions

None.
