# tests/unit/kernel/test_deterministic_checks.py — deterministic policy engine behavior

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/deterministic_checks.md`, `src/yoetz/kernel/policies/work_integrity.md`,
`src/yoetz/kernel/policies/research_evidence.md`
**Imported by:** the kernel unit suite

## Purpose

Lock the deterministic checker as a pure rule engine that emits the expected assessments and no
more, with one exact machine-readable basis for every candidate finding.

## Public surface

- `test_each_pack_has_minimum_trigger_and_closest_nontrigger` — every rule has a positive and a
  near-miss fixture.
- `test_unknown_pack_is_rejected` — tampered pack wiring does not approximate.
- `test_findings_are_origin_deterministic` — deterministic findings never carry semantic provenance.
- `test_pack_results_are_order_stable` — rule order and deduping remain stable.
- `test_policy_result_is_assessments_only` — the kernel exposes no run/skipped/failed accounting.
- `test_projection_frontier_uses_integer_and_head_pair` — case frontier equality checks both the
  projection's integer sequence and separate head digest.
- `test_rule_subject_cardinality_and_order_are_exact` — one assessment exists per exact
  `(policy, rule, complete subject-ref tuple)` and pack/rule/ref ordering is byte-stable.
- `test_deterministic_templates_are_complete_and_exact` — all fifteen kinds use the single
  registry and exact ID-only renderer.
- `test_candidate_and_finding_basis_are_one_to_one` — every candidate has one stable rule/fact/ref
  explanation and no orphan basis exists.
- `test_basis_separates_state_relation_from_source_availability` — unrecorded source never means
  equal state or no change, and later privacy facts never enter the pure basis.
- `test_basis_uses_nominal_subject_state_and_typed_refs` — the domain enum and exact seven-kind
  `FindingBasisRef` union are reused without raw-string/parallel-enum substitutes.
- `test_rule_root_and_fact_ref_tables_are_exact` — all fifteen rules use their frozen primary,
  public-root, observed/missing, and supporting-ref mappings.
- `test_status_basis_projection_is_controlled_and_exact` — namespaced rule IDs, fact/ref flattening,
  availability spelling, and evidence/result ref selection map exactly to the frozen status shape.
- `test_deterministic_case_codec_round_trips_canonical_bytes` — encrypted continuation material
  reconstructs the exact frozen case and canonical bytes.
- `test_deterministic_case_decoder_rejects_extra_and_contradictory_state` — open objects,
  coverage/ref mismatch, and projection/frontier mismatch fail closed.
- `test_finding_basis_codec_is_lossless_closed_and_distinct_from_status` — the durable internal
  basis round trips exactly and rejects both extra members and the lossy public status shape.
- `test_redaction_and_unavailability_coverage_caps_are_componentwise` — all six kernel gap
  conditions preserve/cap/add exactly the registered fields and never strengthen a weaker base.
- `test_redacted_object_root_is_first_cause_and_stable` — repeated object redactions yield one gap
  rooted at the earliest causative event by ledger sequence.
- `test_case_availability_facts_are_explicit_and_exact` — envelope redaction-state mapping and the
  unavailable-event tuple are exact; captured-object rows must be canonical, current, associated,
  and non-redacted, while their probe completeness is tested at the ledger port.

## Behavior

The suite exercises:

- work-integrity and research-evidence packs separately;
- minimal triggering cases and closest non-trigger cases;
- redacted, weak, and stale coverage variants;
- exact subject refs, priority, policy identity, and coverage behavior;
- exact `FindingBasis` rule ID, observed facts, required-but-missing facts, supporting refs,
  subject-state relation, frozen-source availability, and coverage gaps;
- `FrozenSourceAvailability` covers exact available/not-recorded/unavailable-at-freeze/redacted
  precedence and projects to the four frozen status tokens;
- non-public `act|res|evd|fnd` primary refs map to their current source event, response rules retain
  the responded finding's public roots, and missing IDs never become invented roots;
- a projection state `(frontier: int, head_digest: str)` maps to exactly one equal `Frontier`, and a
  same-sequence/different-head prefix is rejected;
- raw trigger records for one rule and complete subject tuple are aggregated before evaluation;
  an emitted duplicate key is rejected, while a different rule on the same tuple remains distinct;
- work-integrity precedes research-evidence, rule ordinals are frozen, and subject tuples break
  rule-local ties by unsigned ASCII bytes;
- all fifteen exact summary/next-action literals plus the exact `Subjects: ... Main agent: ...`
  detail spelling;
- status projection emits the bare kind as `rule_id`, unique observed code/ref unions, missing
  codes, the exact availability translation, gaps, and only `evd_`/`res_` supporting refs; it never
  mutates the internal fact/ref tuples or decodes the flattened public shape as an internal basis.
- table-driven coverage cases for redacted/unavailable event payloads, redacted/unavailable
  captured content, missing refs, and opaque events verify exact ordered-field caps, unchanged
  channel/authorship/check tuples, and sorted union of respectively `redacted_event`,
  `event_payload_unavailable`, `redacted_object`, `captured_object_unavailable`, `missing_ref`, and
  `unknown_event`; combinations apply every cap and an over-64 gap union fails instead of truncating;
- envelope `logically_redacted|erased_claimed` selects recorded-redaction coverage,
  `key_unavailable` selects frozen unavailability, and `present` is available exactly when its
  payload is readable; illegal state/payload/fact combinations fail;
- object-only payload targeting adds both the object and effective-event tokens; captured-content
  targeting adds only the object token to that evidence ref; two or more causative redaction events
  keep the first-by-ingestion event as the exact one-member `CaseGap.subject_refs`, even when event-
  ID byte order differs.
- candidate-status and durable-check builders supplied the same accepted prefix and
  `CaseAvailabilityFacts` yield byte-equivalent `DeterministicCase` values; a changed availability
  tuple changes the case/dependency digest without changing `ProjectionState` snapshots.
- strict case and basis codecs preserve canonical bytes, re-run their frozen constructors, and do
  not accept the flattened status basis as restart state.

## Errors and edge cases

- A pack that reads provider output fails the test by design.
- A finding that appears with semantic provenance in the deterministic path fails the test.
- Free-form model prose, provider output, or an unsupported state/visibility inference in a basis
  fails the test.
- Taking only the integer half of a frontier, selecting a subjective strongest duplicate, or
  copying a policy-owned template literal instead of the shared renderer fails the test.
- Dropping a causative redaction root, strengthening `mutable_reference` to `metadata_only`, or
  replacing/averaging channel and check-type sets fails the test.

## Invariants

1. Deterministic checks are pure.
2. Pack behavior is fixed and separate.
3. Findings are auditable and bounded.
4. Basis data is deterministic input to semantic review, not semantic authority over the finding.

## Tests

- `tests/unit/kernel/test_deterministic_checks.py`

## Open questions

None.
