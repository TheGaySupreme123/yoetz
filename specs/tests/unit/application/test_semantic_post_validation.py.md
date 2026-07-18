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
- `test_review_assessment_mapper_uses_bare_kind_and_preserves_basis` — the namespaced internal rule
  maps to the schema's bare `FindingKind` while every fact/ref association remains unchanged.
- `test_review_assessment_policy_identity_is_derived_after_validation` — the final finding policy
  identity comes from the accepted kind's unique owning pack, not reviewer output.
- `test_review_assessment_ref_limit_skips_without_truncation` — every possible over-16 outbound ref
  field skips the entire assessment with one exact structural omission and retains the local basis.
- `test_malformed_review_assessment_is_rejected_not_omitted` — namespace/kind and canonical-ref
  defects cannot masquerade as policy selection.
- `test_deadline_requires_explicit_monotonic_sample` — frozen deadline methods accept the caller's
  process-local sample and never source current time themselves.
- `test_deadline_boundary_and_wall_clock_independence` — before/equal/after samples clamp exactly,
  while changing the diagnostic UTC instant cannot change the monotonic result.

## Behavior

The suite locks the post-validation boundary:

- semantic output must reference the frozen case and frontier;
- invented subject refs or claims are rejected;
- coverage can weaken but not strengthen;
- deterministic findings remain authoritative and are not rewritten by the semantic path;
- deterministic `FindingBasis` values remain unchanged while an accepted `ReviewerChallenge` maps
  only to an ordinary semantic finding summary/detail and a later derived policy identity;
- structural `ReviewAssessment` values omit summary/detail item refs, while an authorized
  goal-aware-or-richer packet requires the exact paired finding-prose refs;
- `project_review_assessment` verifies the internal `policy-id/kind` spelling and emits the bare
  candidate kind for both outbound `finding_kind` and `rule_id`; included projections preserve all
  observed/missing fact objects, gaps, support refs, relation, and availability;
- each of the four source-availability tokens, including `unavailable_at_freeze`, survives that
  projection unchanged; object/key unavailability is never rewritten as absence or redaction;
- candidate refs, every observed-fact ref tuple, every missing-fact ref tuple, and supporting refs
  are tested at 16 and 17 members. Sixteen is included unchanged; seventeen returns the exact first-
  failing-field `ReviewAssessmentSkipped` plus a `bounded_structural_metadata/finding/not_selected`
  omission keyed by the pinned finding ID. No 16-member prefix appears anywhere;
- challenge refs are checked against the union of the two exact case allowlists, including
  deterministic finding IDs allocated after the frozen frontier;
- every accepted challenge is useful to the main agent under the recorded omissions and requests
  exactly `act|provide_evidence|revise_claim|dispute_with_evidence|state_unresolved_limitation`;
- stale or late results are not admitted into the final check.
- `Deadline.remaining_seconds(now_monotonic)` returns the positive difference before the boundary
  and `0.0` at/after it; `expired(now_monotonic)` is false before and true at/after it. Tests pass
  every sample explicitly, reject invalid/nonfinite samples, prove the frozen value cannot mutate,
  and monkeypatch ambient wall/monotonic APIs to fail if called.

## Errors and edge cases

- A semantic result that passes by quoting unrelated text fails the test.
- A result that upgrades coverage without evidence fails the test.
- A challenge without a supported discrepancy/direct agent message, or one that treats
  `not_recorded|not_selected|withheld_by_policy|redacted_never_send` as unchanged state, fails.
- An over-limit assessment that disappears without its structural skip/omission, strengthens packet
  coverage after being skipped, or reaches the adapter with truncated refs fails.
- A zero-argument deadline call, invalid monotonic sample, or hidden ambient-time read fails.

## Invariants

1. Semantic output is advisory, not sovereign.
2. Case-bound refs are mandatory.
3. Coverage only weakens after semantic validation.
4. Reviewer advice cannot widen context, rewrite deterministic truth, or create waiver authority.
5. Provider-budget decisions depend only on a frozen monotonic deadline and an explicit sample.

## Tests

- `tests/unit/application/test_semantic_post_validation.py`

## Open questions

None.
