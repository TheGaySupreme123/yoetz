# src/yoetz/kernel/projections.py — immutable projection state and projection storage shape

**Wave:** B | **ADRs:** ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`domain/events.md`, `domain/findings.md`, `domain/receipts.md`, `protocol/coverage.md`,
`protocol/canonical.md` | **Imported by:** `ports/ledger.md`,
`kernel/reducers.md`, `kernel/ranking.md`, `kernel/receipt_builder.md`,
`adapters/sqlite/migrations.md`, `adapters/sqlite/repository.md`

## Purpose

This file defines the pure derived work state that Yoetz rebuilds from the ledger. The
projection is the system’s current understanding of plans, obligations, actions, results,
evidence, claims, contradictions, findings, responses, freshness, and unknown-event gaps. It is
the structure that reducers write, rankers read, receipt builders summarize, and SQLite persists in
typed projection tables.

The projection is not the ledger. It is a deterministic cache of meaning built from accepted
events. Its job is to be replayable, small enough to inspect, and strict enough that a corrupted or
stale projection can be discarded and rebuilt without changing the underlying event history.
It is a pure upstream value module: it never imports `ports/ledger.py` or `version.py`.
`ports/ledger.py` may name `ProjectionState` in boundary records, not the reverse.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PROJECTION_VERSION` | `str = "yoetz/0.1.0"` |
| `PROJECTION_GENERATION` | `int = 1` |
| `ProjectionRecord[T]` and specialized record subclasses | exact frozen generation-1 record values |
| `LatestTestedState` | exact frozen last-check value |
| `ContradictionKey` | frozen `(disputing_claim_id, disputed_ref)` logical edge key |
| `ContradictionRecord` | frozen unresolved explicit-dispute edge record |
| `ProjectionState` | frozen dataclass containing the derived work snapshot |
| `empty_projection_state()` | construct the empty derived state for a new bundle or replay |
| `projection_snapshot(state)` | canonical JSON-compatible view used for digesting and persistence |
| `projection_digest(state)` | `sha256:` digest of the canonical projection snapshot |

## Behavior

`ProjectionState` is a frozen dataclass with exactly these domain fields and types (snapshot
decimal strings are described separately below):

```text
frontier: int                                      # 0..signed-int64 max
head_digest: str                                  # genesis iff frontier == 0, else sha256
plans: Mapping[int, PlanProjectionRecord]
obligations: Mapping[ObligationId, ObligationProjectionRecord]
decisions: Mapping[EventId, DecisionProjectionRecord]
assignments: Mapping[EventId, ProjectionRecord[AssignmentRecordedPayload]]
actions: Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]]
results: Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]]
evidence: Mapping[EvidenceId, EvidenceProjectionRecord]
claims: Mapping[ClaimId, ProjectionRecord[ClaimRecordedPayload]]
contradictions: Mapping[ContradictionKey, ContradictionRecord]
findings: Mapping[FindingId, ProjectionRecord[FindingRecordedPayload]]
responses: Mapping[FindingId, ProjectionRecord[ResponseRecordedPayload]]
latest_tested_state: LatestTestedState | None
freshness: LedgerFreshness
unknown_event_count: int                          # nonnegative, bool forbidden
coverage_gaps: tuple[str, ...]                    # sorted unique marker grammar below
```

Every mapping is defensively copied into a `MappingProxyType` in `ProjectionState.__post_init__`;
callers can neither retain a mutable alias nor mutate through the exposed `Mapping`. Every record is
`@dataclass(frozen=True, slots=True)`.

The common generic record is exactly
`ProjectionRecord(payload: T | None, payload_digest: str, redacted: bool,
source_event_id: EventId, source_frontier: int)`. `payload_digest` is the locator's canonical
payload digest and remains after deletion. `payload is None` iff `redacted is True`; a tombstone
therefore retains only its logical mapping key, digest, source event, and source frontier.
`source_frontier` is a positive signed-int64 domain integer.

Specialized records add only the fixture-owned optional structural fields:

```text
PlanProjectionRecord(ProjectionRecord[PlanPublishedPayload | PlanRevisedPayload],
                     superseded_by_plan_version: int | None = None)
ObligationProjectionRecord(ProjectionRecord[ObligationPublishedPayload],
                           plan_change: ObligationChangeKind | None = None,
                           plan_change_reason: str | None = None,
                           superseded_by_obligation_ids: tuple[ObligationId, ...] = ())
DecisionProjectionRecord(ProjectionRecord[DecisionRecordedPayload],
                         superseded_by_event_id: EventId | None = None)
EvidenceProjectionRecord(ProjectionRecord[EvidenceRecordedPayload],
                         object_available: bool = True,
                         redacted_object_id: ObjectId | None = None)
LatestTestedState(source_check_event_id: EventId, subject_frontier: Frontier,
                  verdict: CheckVerdict,
                  returned_finding_ids: tuple[FindingId, ...],
                  suppressed_count: int, coverage: Coverage)
```

`plan_change_reason` is present exactly when the applied `PlanRevisedPayload.ObligationChange`
carried a reason; `superseded_by_obligation_ids` is emitted only when nonempty. Evidence has
`redacted_object_id` exactly when the reducer's envelope-derived `ReplayIndex` proves that the
current record's source event mirrored that one now-unavailable captured object. This remains
derivable even if that event payload is subsequently deleted; no payload prose is retained to make
the association. The common record fields always appear in snapshots, including `payload: null`
for a tombstone; optional specialized members are omitted when `None`/empty. No other record member
is permitted.

`latest_tested_state`, when present, contains exactly the fields above. Projection freshness and
normalized gaps may weaken from its recorded coverage but may never reconstruct a stronger value
or infer suppressed identities from the returned finding IDs.

Those collections are immutable mappings or tuples of current visible records, not live database
rows. The mapping keys are stable logical IDs:

- `plans` keyed by plan version;
- `obligations` keyed by `obligation_id`;
- `decisions` keyed by the decision event ID;
- `assignments` keyed by the assignment event ID;
- `actions` keyed by `action_id`;
- `results` keyed by `result_id`;
- `evidence` keyed by `evidence_id`;
- `claims` keyed by `claim_id`;
- `contradictions` keyed by `ContradictionKey`;
- `findings` keyed by `finding_id`;
- `responses` keyed by `finding_id`.

`ContradictionKey` is exactly `(disputing_claim_id: ClaimId, disputed_ref: ClaimId | EventId)`.
It represents one directional explicit `ClaimRecordedPayload.disputes_refs` edge; the direction is
meaningful and is not sorted away. Its canonical snapshot-object key is exactly
`"<disputing_claim_id>|<disputed_ref>"`. The separator is collision-free because `|` is forbidden
by both registered ID grammars. Snapshot ordering compares those complete ASCII key bytes.

`ContradictionRecord` is exactly `(disputing_claim_id: ClaimId, disputed_ref: ClaimId | EventId,
source_event_id: EventId, source_frontier: int)`. The mapping contains unresolved current edges
only. The snapshot renders these four fields in that order and renders `source_frontier` as the
canonical decimal string used by the other projection records. The record does not duplicate
coverage: `DeterministicCase.coverage_by_ref` joins both logical refs and the source event to their
authoritative accepted-envelope coverage. It also does not store prose or an inferred resolution.

Re-publication of the same `claim_id` is the only claim-owned structural replacement operation:
reducers remove every old key whose `disputing_claim_id` equals that claim, then insert exactly its
new `disputes_refs`. An empty new tuple therefore clears that claim's old edges. A decision event by
itself does not clear an edge; the ADV-006 resolved variant contains both the decision and the
superseding claim. If the disputing claim's own payload is redacted, its source-owned edges are
removed because the minimal durable locator does not retain `disputes_refs`; the claim tombstone
and redaction marker preserve the bounded historical fact without retaining those payload
relations. An unredacted claim disputing a redacted target remains an edge, as REP-003 freezes.
Orphan actions/results are represented by their visible record plus a normalized gap and policy
input, not by inventing another contradiction kind.

The stored record values are frozen, implementation-local records that retain the current visible
body, the source event metadata, and the minimum derivation data needed to explain how the state
was produced. The projection never stores raw event payload bytes; it stores the canonical record
shapes that reducers derived from them.

Each stored record is the current visible projection for its logical subject. The record keeps the
stable logical key, the source event ID, the source frontier at which it became visible, the body
needed by rankers and receipt builders, and the coverage/freshness note that explains why it is
present. The projection does not store ambient object-store bytes, provider transcripts, or any
open-ended JSON blob that would require a second interpretation step.

This module owns the pure typed projection shape and derivation semantics, not executable SQL bytes.
The canonical root migration `migrations/bundle/0001.sql` alone owns the exact generation-1 table,
column, constraint, index, statement-order, and newline bytes; its installed resource is an opaque
byte-identical copy. This module exports no DDL string, and migration/runtime code must not append,
template, or synthesize projection SQL from Python. Schema-identity tests instead prove that the
root migration's `p1_` tables can persist and reconstruct these exact frozen record families.

The required generation-1 typed storage families are fixed for v0.1:

- `p1_projection_state` for frontier, head digest, latest-check event/frontier/verdict,
  returned-finding IDs, suppressed count, coverage, freshness, unknown-event count, and rebuild
  metadata;
- `p1_plans`, `p1_obligations`, `p1_decisions`, `p1_assignments`, `p1_actions`, `p1_results`,
  `p1_evidence`, `p1_claims`, `p1_contradictions`, `p1_findings`, and `p1_responses` for the
  derived records;
- `p1_coverage_gaps` for normalized gap markers;
- only the helper edge tables explicitly frozen in the root migration for source references,
  supersession, or subject links.

Every durable row stores the stable logical key plus the minimum fields needed to reconstruct the
frozen projection record. The adapter may store canonical JSON bytes for a body column, but it may
not require a second ad hoc serializer to interpret those bytes.

`empty_projection_state()` returns a state with:

- `frontier` at sequence `0` and head digest `"genesis"`;
- empty mappings for every derived collection;
- `latest_tested_state = None`;
- `freshness = unknown`;
- `unknown_event_count = 0`;
- `coverage_gaps = ()`.

`projection_snapshot(state)` converts the frozen state into a canonical JSON-compatible object with
stable key ordering and stable record ordering. The snapshot is the structure used for digesting
and for the SQLite `state_digest` column. It is not a human render. The top-level object follows
registry order, and each mapping is sorted by the logical key that names the record. Contradiction
keys use the exact pipe-separated encoding above.

The snapshot has exactly the 17 `ProjectionState` keys and no version wrapper. `frontier`, every
record `source_frontier`, plan-map keys, `superseded_by_plan_version`, and contradiction
`source_frontier` render as canonical unsigned-decimal strings. `unknown_event_count`,
`LatestTestedState.suppressed_count`, and payload-schema integer fields remain JSON integers.
Record payloads use `domain.events.encode_payload`; tombstones render `payload: null` without any
locator or target metadata. `LatestTestedState.subject_frontier` uses the shared frontier codec.

`coverage_gaps` is the unsigned-ASCII sorted unique tuple of only these generation-1 markers:

```text
unknown_event:<event_id>:<schema_name>@<schema_version>
redacted_event:<target_event_id>
redacted_object:<target_object_id>
missing_ref:<source_event_id>:<target_logical_id>
```

Their typed `CaseGap` codes/roots are respectively: `unknown_event` rooted at the unknown event;
`redacted_event` rooted at the target event; `redacted_object` rooted at the one causative
`redaction_recorded` event with the lowest ledger ingestion sequence whose durable locator contains
that target object; and `missing_ref` rooted at the visible source event. Later redactions of the
same object remain ledger facts but do not replace that first-cause root. This exact single-root
rule fits every public subject-ref bound and never depends on event-ID or set-iteration order. Zero
causative roots for a retained `redacted_object` marker is projection corruption. A target logical ID is one of the typed
projection IDs (`obl|act|res|evd|clm|fnd`); an event ID is never guessed missing from
`ProjectionState` because the state intentionally does not keep every lifecycle event.

Unknown/redaction markers are append-only facts and survive later publications. Missing-ref markers
are recomputed after every fold from current nonredacted payload records: a marker disappears when
the target tombstone/body becomes visible or when the source record is replaced/redacted, and a
republished source contributes only its current exact refs. No other reducer-owned marker grammar
exists in generation 1. Per-reference coverage and typed `CaseGap` values belong to the pure
`DeterministicCase` sidecar built from authoritative records; they are deliberately not new
`ProjectionState` or snapshot fields. This preserves the published generation-1 snapshot shape and
digest vectors while making policy inputs explicit.

An object-only redaction may additionally resolve a payload-object target to its owning event. In
that case reducers append both the raw `redacted_object:<object_id>` marker and the effective
`redacted_event:<event_id>` marker, then apply the ordinary event-tombstone/secondary-effect rule.
If the object is an evidence captured-content object, the raw object marker remains the only
projection marker and the current matching `EvidenceProjectionRecord` becomes unavailable. One
object may satisfy both associations, in which case both effects apply in that fixed order.

`projection_digest(state)` is the `sha256:` digest of the canonical snapshot bytes. It must be
stable across hash seeds, locales, and installation order.

## Errors and edge cases

- A non-frozen or malformed state record is invalid at construction time.
- A record with duplicate logical keys is a projection bug and must not be silently merged.
- Unknown freshness values or unsupported record shapes are internal errors, not user-facing
  protocol errors.
- The projection never invents missing evidence, claims, or findings to fill gaps.
- A corrupted snapshot digest means the typed projection tables must be rebuilt from the ledger.

## Invariants

1. The projection is derived only from accepted ledger records.
2. Projection snapshots are deterministic and replayable.
3. Unknown events and redactions weaken coverage; they never strengthen the projection.
4. The projection does not read or write SQLite, clocks, providers, or network resources.
5. Generation 1 is the only supported durable projection shape in v0.1.

## Tests

- `specs/tests/conformance.md` — memory and SQLite projection parity over the same event stream.
- `specs/tests/integration.md` — projection corruption, rebuild, and stale-generation handling.
- `tests/unit/kernel/test_replay_and_projections.py` — snapshot ordering, full/incremental parity,
  tombstone rebuild, marker lifecycle, and digest stability against `fixtures/replay/*.case.json`.

## Open questions

None.
