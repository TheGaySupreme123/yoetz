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
| `EvidenceObjectSource` | frozen captured-object association `(evidence_id, source_event_id)` |
| `ReplayIndex` | frozen non-plaintext reverse index through one exact accepted frontier |
| `empty_replay_index()` | construct the genesis reverse index |
| `extend_replay_index(index, event)` | validate and add the next accepted envelope |
| `reduce_event(state, event, replay_index)` | return the next `ProjectionState` after folding one record |
| `replay(events)` | fold a ledger-ordered event stream into a fresh projection state |

## Behavior

`reduce_event(state, event, replay_index)` never mutates `state`, `event`, or `replay_index`. It returns a fresh
`ProjectionState` and only uses information already present in the accepted ledger record. If the
input is an `UnknownEvent`, the reducer preserves the event’s metadata, increments
`unknown_event_count`, inserts exactly
`unknown_event:<event_id>:<schema_name>@<schema_version>`, advances `frontier` and `head_digest`, and
otherwise leaves the work projection untouched. Replaying a prefix requires strictly increasing
contiguous ingestion sequences and exact predecessor/head equality; the reducer never sorts or
silently skips a record.

Object-only redaction needs one additional pure input because `ProjectionState` intentionally does
not retain payload-object or locator metadata. `EvidenceObjectSource` is exactly
`(evidence_id: EvidenceId, source_event_id: EventId)`. `ReplayIndex` is the frozen value
`(frontier: int, head_digest: str, payload_event_by_object: Mapping[ObjectId, EventId],
evidence_sources_by_object: Mapping[ObjectId, tuple[EvidenceObjectSource, ...]],
redaction_root_by_object: Mapping[ObjectId, EventId])`. Mappings are defensive
`MappingProxyType` copies; every evidence-source tuple is unique and sorted by unsigned-ASCII
`(evidence_id, source_event_id)` bytes. It contains only typed IDs and the accepted head identity—
never payload handles, payload digests, content, paths, URLs, or human redaction text.

`empty_replay_index()` has frontier `0`, head `genesis`, and empty mappings.
`extend_replay_index(index, event)` requires the same contiguous sequence/predecessor relation as
the ledger and returns a new index through that event. It derives:

- `event.payload_ref.object_id -> event.event_id` for every known or unknown accepted record;
- for an exact-known `evidence_recorded` record, the locator's `logical_key` plus the envelope's
  exact empty-or-singleton `artifact_refs` mirror -> `EvidenceObjectSource`; and
- for an exact-known `redaction_recorded` record, each durable locator object target -> the first
  causative redaction event ID by ledger ingestion sequence. A later redaction of the same object
  preserves that root.

A payload object ID naming two event envelopes, a noncanonical evidence association, or a
redaction locator/object-ref mismatch is corruption. A redaction event cannot target its own
payload object: the target must already be unavailable before that event is accepted. A captured-content object may have several
evidence sources; all are retained in canonical order. The single first-cause object root is
compatible with the bounded public event/obligation/claim subject-ref vocabulary; every later
redaction remains independently visible in the ledger. `reduce_event` requires an index whose
`frontier/head_digest` equals the current event and a state at exactly the preceding frontier/head.
It never accepts a future-complete index. `replay` extends this index before each fold. An
incremental adapter retains it in memory or rebuilds it from accepted envelope/locator rows through
the cached projection frontier, then extends it with the new envelope. Rebuilding the index opens
no payload object and performs no network or object-store lookup.

Every known `AcceptedEvent` carries the runtime-only `ProjectionLocator` captured durably at
acceptance. With a readable payload, the reducer re-encodes it and verifies the locator's schema,
logical key, redaction targets, and canonical payload digest before applying the family rule. With
`payload is None`, it never attempts decryption or invents a body: if the family owns a current
projection record, it upserts the common null-payload tombstone at the locator key; if the family is
`check_recorded`, it leaves/clears `latest_tested_state` as described below; otherwise it advances
only structural state. This is the same code path used by memory and SQLite full replay after
physical object deletion. A missing/mismatched locator is durable corruption and aborts replay;
it is not converted into a weaker successful projection.

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
  preserve unlinked or incomplete state as a normalized gap instead of guessing. A result without
  an action stays visible as evidence of inconsistency and policy input, not as an inferred action
  or a second contradiction family.
- `evidence_recorded`: upsert the evidence keyed by `evidence_id`, preserving declared strength,
  captured object identity, digest, and subject-state reference. The reducer does not promote a
  weak reference into a stronger observation.
- `claim_recorded`: upsert the claim keyed by `claim_id`, retain its supporting refs, and replace
  the contradiction edges asserted by that same logical claim. Before inserting the new body,
  remove every `ContradictionKey` whose `disputing_claim_id` equals this `claim_id`; then insert one
  `ContradictionRecord` for each exact `disputes_refs` member, keyed directionally by
  `(claim_id, disputed_ref)`. The record carries the current claim event ID and ingestion sequence.
  An empty `disputes_refs` tuple clears that claim's prior edges. A decision alone never clears an
  edge, and the reducer never infers support or contradiction from statement prose.
- `redaction_recorded`: mark the targeted record as redacted, preserve the historical existence of
  the underlying fact, and weaken coverage/freshness where the missing plaintext matters. The
  reducer uses the locator's target tuples even if the redaction payload itself is later deleted;
  the human `remaining_gap` text is never required for replay. It first computes the effective
  event-target set as the sorted union of explicit locator event targets and every prior event ID
  found in `replay_index.payload_event_by_object` for a locator object target. Effective target
  events are processed before captured-content targets, matching REP-002/REP-003. A current record
  whose `source_event_id` is effectively targeted is replaced by the same-key tombstone.
  Source-owned secondary effects are removed: a targeted
  `plan_revised` clears the supersession/obligation-change effects derivable from its still-visible
  body, a targeted superseding decision clears the matching prior decision link, a targeted claim
  removes contradiction records with that source event, and a targeted latest check clears
  `latest_tested_state`. Effects from later unredacted events remain. Target objects mark each
  current evidence record unavailable exactly when its `(logical evidence ID, source event ID)`
  occurs in `replay_index.evidence_sources_by_object[target_object_id]`; this works even if an
  earlier payload body is already gone. The reducer inserts `redacted_event:<event_id>` for every
  effective event target and `redacted_object:<object_id>` for every exact locator object target.
  Thus an object-only event-payload deletion produces both markers, whereas an object-only captured-
  content deletion produces the object marker and preserves the evidence payload/digest metadata.
  One object matching both indexes applies both effects in that fixed order. Accepted envelopes,
  locators, and replay-index associations are never deleted.
- `response_recorded`: upsert the response keyed by `finding_id` and preserve waiver scope and
  expiry if present. If the response explains a rejection or waiver badly, the weak or stale
  response remains visible for later checks.
- `finding_recorded`: upsert the finding keyed by `finding_id`; the record is authoritative and
  may come from the engine or from an imported observation, but it never becomes stronger than its
  declared coverage.
- `check_recorded`: preserve the returned finding set, update `latest_tested_state`, refresh the
  freshness dimension, and copy `CheckRecordedPayload.coverage` plus its gaps into the latest-test
  record. `latest_tested_state` retains the check event ID, subject frontier, verdict, exact
  returned finding IDs, nonnegative suppressed count, and exact coverage; weakening the projection
  uses those recorded facts rather than reconstructing them from visible finding IDs. A later
  response does not clear the suppressed count. Only a newer recorded check at the applicable
  frontier replaces it. The reducer does not rerun the policy pack; it only stores the
  already-evaluated check outcome. If its payload is already unavailable at full-replay time, the
  locator proves the check existed but cannot reproduce its verdict/coverage, so it does not create
  a latest-test record and the later durable redaction marker carries the uncertainty; incremental
  redaction clears the same record, preserving convergence.
- `receipt_recorded`: advance frontier and head digest; receipt history itself is handled by the
  receipt object store, not by the work projection. A receipt record changes projection freshness
  but does not re-rank findings.

The reducer is conservative about mismatches. If an accepted record is structurally valid but
refers to currently missing companions, the reducer records the gap rather than inventing a
relationship. This is deliberate: missing links are later check findings, not reasons to lose the
underlying history.

After each known fold, the reducer discards every prior `missing_ref:` marker and recomputes them
from the current nonredacted payload records against the logical keys presently visible (a
tombstone counts as visible). Each unresolved typed projection ID becomes exactly
`missing_ref:<source_event_id>:<target_id>`. Unknown/redaction markers are retained unchanged; the
final tuple is sorted unique by unsigned ASCII bytes. This single recomputation rule defines
resolution, source replacement, and source-redaction removal—there is no family-specific stale-gap
cleanup.

Freshness is derived, never wall-clock driven: an empty state is `unknown`; any redaction marker
makes it `redacted_gap`; otherwise any unknown/missing marker makes it `partial`; otherwise a known
prefix is `current`. A successful `check_recorded` may carry `stale_after_material_change` from its
recorded coverage, and a later material family (plan, obligation, assignment, decision, action,
result, evidence, claim, finding, response) makes an applicable latest check stale. Redaction and
partial marker precedence remain weaker than stale. Session and receipt lifecycle events alone do
not make a tested state stale.

`replay(events)` starts from `empty_projection_state()` and `empty_replay_index()`, extends the
index with each record, and folds the supplied records in the order they are yielded. It assumes
ledger order, does not sort the iterable, and does not consult the database. The caller is
responsible for only supplying accepted records that have already been validated by the ledger.
The full-replay input may contain unreadable known payload handles, but it must still contain every
accepted envelope and durable locator; this is precisely why projection tables remain disposable
after redaction.

## Errors and edge cases

- A malformed event record at this layer is an internal programming error and should not appear in
  normal flows.
- Duplicate logical IDs in the same fold are resolved by the later record only when the event
  family itself defines republishing as latest-body-wins; otherwise they are treated as a broken
  upstream invariant.
- Redacted payloads are folded using the available metadata only; reducers never guess at missing
  plaintext.
- A missing locator, locator/payload digest mismatch, wrong logical key, redaction-target mismatch,
  evidence artifact mirror mismatch, duplicate payload-object owner, or replay-index frontier
  mismatch is projection corruption and stops replay before a new state is returned.
- The reducer never emits public errors, writes logs, or touches I/O.

## Invariants

1. Same ordered event stream → same projection state.
2. The reducer is pure and side-effect free.
3. Unknown and redacted inputs weaken coverage instead of strengthening it.
4. Reducers never create facts that the ledger did not already record.
5. The reducer and the durable typed projection tables must stay byte-for-byte equivalent on the
   same fixture stream.
6. Deleting every redacted payload object and every disposable `p1_` table, then replaying from
   accepted envelopes plus locators, yields the same current projection snapshot and digest.
7. Object-only redaction has identical incremental/full-replay effects because both paths use the
   same envelope-derived `ReplayIndex`; neither path consults deleted plaintext.

## Tests

- `specs/tests/conformance.md` — replay parity between memory and SQLite adapters.
- `specs/tests/unit.md` — family-by-family fold behavior and unknown-event preservation.
- `specs/tests/property.md` — replay idempotence and prefix stability under the same ordered
  stream.

## Open questions

None.
