# src/yoetz/kernel/reducers.py — pure event folding into projection state

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`domain/events.md`, `domain/findings.md`, `kernel/projections.md`, `protocol/errors.md`,
`protocol/coverage.md`, `protocol/canonical.md` | **Imported by:**
`adapters/sqlite/repository.md`, `adapters/memory/ledger.md`, `kernel/ranking.md`,
`kernel/receipt_builder.md`

## Purpose

Reducers are the pure, deterministic part of the trust engine. They read accepted ledger records in
sequence and produce a new projection state. They do not know about SQLite, leasing, provider
retry, or user-facing rendering. Their only job is to turn the immutable event stream into the
immutable work snapshot that ranking, receipt building, and durable projection storage can agree
on.

Because reducers are pure, they are the safest place to reason about replay. If two implementations
produce different projection states for the same accepted records, one of them is wrong.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `reduce_event(state, event)` | return the next `ProjectionState` after folding one record |
| `replay(events)` | fold a ledger-ordered event stream into a fresh projection state |

## Behavior

`reduce_event(state, event)` never mutates `state` or `event`. It returns a fresh
`ProjectionState` and only uses information already present in the accepted ledger record. If the
input is an `UnknownEvent`, the reducer preserves the event’s metadata, increments
`unknown_event_count`, appends a coverage gap marker, advances `frontier` and `head_digest`, and
otherwise leaves the work projection untouched.

For known events, the reducer updates the projection collections as follows:

- `session_opened` / `session_resumed`: advance frontier and head digest, seed the session-facing
  coverage/freshness bookkeeping, and preserve the boundary record, but do not fabricate plans,
  obligations, or other work-state objects.
- `plan_published` / `plan_revised`: upsert the current plan record keyed by plan version, retain
  supersession relationships, and keep the latest visible plan body. A later revision replaces the
  visible body but does not erase the historical fact that the earlier plan existed.
- `obligation_published`: declare an immutable obligation keyed by `obligation_id`, or apply the
  sole legal monotonic update `open → resolved` with resolution evidence after verifying every
  meaning-defining field repeats byte-equivalently. A rewrite/reopen is invalid upstream rather
  than latest-body-wins. Material revisions use a new obligation plus `plan_revised`.
- `assignment_recorded`: record the assignment event keyed by event ID and link the assigned
  obligations. If an assignment names an obligation that is not yet visible, the reducer records the
  missing link as a gap instead of guessing the relationship away.
- `decision_recorded`: record the decision event keyed by event ID and mark any superseded decision
  chain if present. The current decision is visible; superseded decisions are retained as
  historical inputs to the chain.
- `action_recorded`: upsert the action keyed by `action_id` and retain any exact attempted-item
  strings. The reducer does not normalize the attempted text beyond canonical storage rules.
- `result_recorded`: upsert the result keyed by `result_id`, link it back to the action, and
  preserve unlinked or incomplete state as a derived contradiction or gap instead of guessing. A
  result without an action stays visible as evidence of inconsistency, not as an inferred action.
- `evidence_recorded`: upsert the evidence keyed by `evidence_id`, preserving declared strength,
  captured object identity, digest, and subject-state reference. The reducer does not promote a
  weak reference into a stronger observation.
- `claim_recorded`: upsert the claim keyed by `claim_id`, retain its supporting refs and any
  explicit dispute refs, and never infer support that was not recorded.
- `redaction_recorded`: mark the targeted record as redacted, preserve the historical existence of
  the underlying fact, and weaken coverage/freshness where the missing plaintext matters. Redaction
  does not erase the record from replay.
- `response_recorded`: upsert the response keyed by `finding_id` and preserve waiver scope and
  expiry if present. If the response explains a rejection or waiver badly, the weak or stale
  response remains visible for later checks.
- `finding_recorded`: upsert the finding keyed by `finding_id`; the record is authoritative and
  may come from the engine or from an imported observation, but it never becomes stronger than its
  declared coverage.
- `check_recorded`: preserve the returned finding set, update `latest_tested_state`, refresh the
  freshness dimension, and attach the check’s coverage/gap summary. The reducer does not rerun the
  policy pack; it only stores the already-evaluated check outcome.
- `receipt_recorded`: advance frontier and head digest; receipt history itself is handled by the
  receipt object store, not by the work projection. A receipt record changes projection freshness
  but does not re-rank findings.

The reducer is conservative about mismatches. If an accepted record is structurally valid but
refers to currently missing companions, the reducer records the gap rather than inventing a
relationship. This is deliberate: missing links are later check findings, not reasons to lose the
underlying history.

`replay(events)` starts from `empty_projection_state()` and folds the supplied records in the order
they are yielded. It assumes ledger order, does not sort the iterable, and does not consult the
database. The caller is responsible for only supplying accepted records that have already been
validated by the ledger.

## Errors and edge cases

- A malformed event record at this layer is an internal programming error and should not appear in
  normal flows.
- Duplicate logical IDs in the same fold are resolved by the later record only when the event
  family itself defines republishing as latest-body-wins; otherwise they are treated as a broken
  upstream invariant.
- Redacted payloads are folded using the available metadata only; reducers never guess at missing
  plaintext.
- The reducer never emits public errors, writes logs, or touches I/O.

## Invariants

1. Same ordered event stream → same projection state.
2. The reducer is pure and side-effect free.
3. Unknown and redacted inputs weaken coverage instead of strengthening it.
4. Reducers never create facts that the ledger did not already record.
5. The reducer and the durable typed projection tables must stay byte-for-byte equivalent on the
   same fixture stream.

## Tests

- `specs/tests/conformance.md` — replay parity between memory and SQLite adapters.
- `specs/tests/unit.md` — family-by-family fold behavior and unknown-event preservation.
- `specs/tests/property.md` — replay idempotence and prefix stability under the same ordered
  stream.

## Open questions

None.
