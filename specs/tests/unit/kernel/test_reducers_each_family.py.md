# tests/unit/kernel/test_reducers_each_family.py — family-by-family reducer transition matrix

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`src/yoetz/kernel/reducers.md`, `src/yoetz/domain/events.md`,
`src/yoetz/kernel/projections.md`
**Imported by:** the kernel unit suite

## Purpose

Lock one deterministic transition suite per event family so every reducer branch has a named oracle.

## Public surface

- `test_session_events_advance_frontier_only` — session open/resume preserve work state and advance
  ledger position.
- `test_plan_and_obligation_supersession` — later plan/obligation records replace visible bodies
  without erasing history.
- `test_assignment_decision_action_result_links` — assignment, decision, action, and result chains
  wire together correctly.
- `test_evidence_claim_response_redaction_paths` — evidence/claim/response/redaction records update
  the right maps and gaps.
- `test_finding_check_receipt_records` — finding/check/receipt records preserve returned findings,
  tested state, and freshness.
- `test_unknown_event_preserves_gap_metadata` — unknown events increment gap accounting without
  inventing facts.
- `test_each_transition_uses_exact_prefix_replay_index` — genesis/extension and reducer frontier
  identities stay synchronized for every family.
- `test_object_only_redaction_resolves_both_envelope_associations` — payload-object ownership and
  evidence captured-object ownership take their distinct exact transition paths.

## Behavior

The suite extends `ReplayIndex` with one accepted record family at a time, feeds the state, record,
and exact through-record index into `reduce_event`, and asserts:

- the correct projection map is updated;
- unrelated maps remain unchanged;
- source metadata and frontier advance exactly once;
- redaction and unknown events weaken coverage rather than erasing history;
- an object-only payload target tombstones its owning current record and removes only its source-
  owned effects, while an object-only captured-content target preserves the evidence body/digest
  and marks only the matching current `(evidence_id, source_event_id)` unavailable;
- a second redaction of the same object preserves the first-by-ingestion public root;
- reducer input values are not mutated.

## Errors and edge cases

- A family update that touches the wrong projection map fails the test.
- A reducer that mutates input state or event fails the test.
- A reducer that accepts a stale/future index, guesses an evidence association from a deleted body,
  or chooses a repeated-redaction root by event-ID order fails the test.

## Invariants

1. Each family has a dedicated transition oracle.
2. Reducers are pure.
3. Missing links become gaps, not invented facts.

## Tests

- `tests/unit/kernel/test_reducers_each_family.py`

## Open questions

None.
