# tests/integration/application/test_check.py — check end-to-end and semantic gates

**Wave:** D/E | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz/application/check.md`, `src/yoetz/adapters/providers/fake.md`,
`src/yoetz/kernel/deterministic_checks.md`
**Imported by:** integration application tests

## Purpose

Prove the public `check` operation freezes the case, runs the deterministic packs, and conditionally
admits semantic results only after post-validation.

## Public surface

- `test_deterministic_only_mode` — deterministic findings and verdict are returned without semantic
  work.
- `test_semantic_optional_and_required_modes` — mode gating is exact.
- `test_semantic_fake_outcomes_and_post_validation` — fake provider outcomes are validated.
- `test_stale_frontier_and_late_response_handling` — stale results do not steer the current check.
- `test_assisted_packet_carries_bases_timeline_excerpts_and_omissions` — useful context is built
  from the frozen case without ambient source access.
- `test_reviewer_challenge_reaches_agent_as_existing_finding` — no new operation/event/result field
  is required for the reviewer-to-agent loop, and the final finding identity still comes from the
  accepted kind's owning built-in pack.
- `test_overbound_assessment_is_omitted_without_ref_truncation` — semantic selection may skip a
  valid 64-ref local basis but can never serialize a lossy 16-ref prefix.
- `test_semantic_provenance_requires_durable_matching_receipt` — provisional provenance and a
  missing/nonterminal/mismatched receipt fail at the coordinator with their exact registered reasons.
- `test_selected_semantic_ids_are_pinned_before_ranking` — selected semantic candidates receive
  durable resume-stable `fnd_` IDs before the ranker is called.
- `test_final_semantic_provenance_matches_selected_attempt` — top-level status/reason and optional
  final provenance describe the same selected attempt; earlier late/nonselected attempts remain
  audit-only.
- `test_semantic_evaluator_crash_degrades_to_not_run_without_false_clean` — evaluator crash or
  non-success terminal preserves deterministic findings, records
  `semantic_relevance_review_not_run`, and never yields a false clean `no_issue_detected` verdict.
- `test_freeze_reservation_race_and_resume` — a final-reservation race installs no stale case and
  a reclaimed check resumes from the exact stored case object without rebuilding it.
- `test_direct_scope_and_durable_policy_accounting` — selected claim/obligation roots constrain
  candidates directly while required dependency material does not expand the recorded scope.

## Behavior

The test freezes a case and then asserts:

- deterministic findings are stable;
- `execute_check` returns the closed unprojected `CheckCommitResult` directly; persisted/replayed
  state has no client sink, rendering mode, omission marker, `privacy_projection`, or local
  disclosure receipt, and the application facade owns the only later client projection;
- every deterministic candidate has one persisted machine-readable `FindingBasis`, and the
  semantic case binds both frozen-frontier refs and locally pinned deterministic finding refs;
- semantic capability is optional or required only by mode;
- the effective `ReviewContextProfile` selects the exact structured goal/obligation/claim/decision,
  material timeline, assessment, change, coverage, targeted-excerpt, and omission sections;
- late, invented, or coverage-upgrading semantic results are rejected;
- an accepted challenge becomes an ordinary semantic finding whose projected summary/detail can be
  consumed by the main agent; hidden/unrecorded source never becomes an unchanged-state fact, and
  the resulting finding policy identity is derived after validation from the chosen kind owner;
- the final check result respects the frozen frontier and returned findings.
- omitted scope normalizes to empty/empty whole-case; a nonempty scope admits only candidates whose
  complete subject refs directly intersect an explicitly selected claim/obligation ID. Dependency
  traversal may supply evaluation facts but never promotes another root. A pack with no direct
  applicable root records `skipped/scope_excluded`, while an applicable clean evaluation records
  `run/completed`; the committed event and result preserve the same nonempty canonical execution
  tuple.
- every terminal path selects the exact `SemanticReason`; provider/policy failures in
  `semantic_required` preserve deterministic findings and produce no semantic findings;
- provider adapters return provisional attempt provenance, while only the coordinator may publish
  receipt-finalized provenance after the terminal privacy receipt is durable;
- after post-validation and attempt selection, every accepted semantic candidate receives a
  durably pinned ID before ranking; crash/retry reuses the same map, and the ranker is never called
  with an identity-less semantic candidate;
- the final event/result semantic status and reason describe only the selected/final attempt;
  whenever provenance is present its nested pair is identical, while an earlier late or nonselected
  attempt remains in attempt audit storage and never replaces the selected provenance;
- passing `ProviderAttemptProvenance` to finalization raises
  `provider_attempt_provenance_is_not_final`, while finalizing before a durably readable,
  identity-matched terminal egress/local-disclosure receipt raises `privacy_receipt_not_durable`;
  neither path converts the defect into a provider outcome or discards deterministic findings;
- a 17-ref assessment creates one exact `not_selected` finding omission, remains in the full
  coverage fold and encrypted local result, and supplies no content item or truncated assessment to
  the fake adapter.
- pausing after resume-object finalization and then changing the head, projection identity,
  dependency revision, import state, or idempotency row makes final reservation fail without an
  operation pointer; after a successful reservation, restart/reclaim opens the exact stored case
  while the case builder and object publisher are fail-on-call.

## Errors and edge cases

- A semantic result that rewrites deterministic findings fails.
- A stale frontier that still steers the result fails.
- Missing/mismatched reason or provenance published before receipt durability fails.
- A top-level semantic pair that differs from present nested provenance fails, as does exposing an
  earlier late/nonselected attempt as the final provenance after a successful retry.
- Ranking before selected semantic candidate IDs are durably pinned fails; resume must not allocate
  replacements.
- Retrying receipt verification may resume `privacy_receipt_not_durable`; provisional provenance is
  a programmer defect and may never be retried as though it were provider unavailability.
- Browsing Git/filesystem during case construction, or returning a challenge with refs outside the
  split allowlists, fails.
- A transitive-only scope match, missing execution row, policy/execution mismatch, or reordered
  execution tuple fails before commit.

## Invariants

1. Frozen case controls the check.
2. Semantic work is optional or required by mode.
3. Post-validation is mandatory.
4. Rich semantic context remains frozen, bounded, privacy-selected, and problem-local.
5. Resume durability never inverts case-build, object-publication, and reservation order.
6. Ranking sees only durably identified deterministic and selected-semantic findings.
7. Final provenance, when present, has the selected attempt's exact top-level status/reason pair.

## Tests

- `tests/integration/application/test_check.py`

## Open questions

None.
