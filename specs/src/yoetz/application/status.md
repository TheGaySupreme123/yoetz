# src/yoetz/application/status.py — bounded read-only projection queries at a stable frontier

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-005, ADR-006 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/values.md`, `domain/findings.md`, `kernel/projections.md`,
`kernel/deterministic_checks.md`, `ports/ledger.md` |
**Imported by:** `application/service.md`

## Purpose

`status` answers bounded questions about recorded task state without publishing an event or
creating an idempotency operation. It serves one deterministic view at the latest or an explicitly
requested stable frontier, applies only closed filters, paginates every list, and discloses
projection lag, rebuild state, unknown events, redactions, and unavailable payloads. It is a view
of recorded evidence, never a claim that unobserved work did or did not happen.

Status is also the surface an agent uses on itself while working: to recover what it has already
done and committed to after resume, handoff, or context loss, and — through `candidate_findings` —
to run the deterministic packs against the current record without recording anything. That view
exists because `check` is the completion-time instrument: it reserves an operation, appends
`check_recorded` and `finding_recorded`, and creates findings that require `respond` to clear.
Paying that to ask a question during the work would make the ledger a record of the agent's
uncertainty rather than of its work. Looking must be free of task-ledger consequence, or the agent
stops looking; the separate local-disclosure audit still records what Yoetz released to a client.

## Public surface

- `async execute_status(app: Application, request: StatusRequest) -> StatusInternalResult` —
  content-complete internal implementation behind `Application.status`. `StatusInternalResult`
  has the exact status-success fields except the service-owned `privacy_projection`; it never
  crosses an ordinary client boundary directly.
- `@dataclass(frozen=True, slots=True) class StatusInternalResult` — exact internal record below.
- Application-internal cursor validation/encoding helpers. Cursor bytes are opaque transport data,
  not ledger truth and not a public identifier kind.

The exact `ProjectionQuery`, typed filter/position variants, and `ProjectionPage` are owned by
`ports/ledger.py`; opaque `ProjectionCursor` encoding remains application-local. Every view except
`candidate_findings` uses `LedgerPort.query_projection`. That one view uses the single registered
whole-case exception described below and never constructs a `ProjectionQuery`.

`StatusInternalResult` is the frozen content-complete record with the exact status-success fields
`protocol_version`, `schema_version`, `request_id`, `ok=true`, `task_id`, `session_id`, `writer_id`,
`view`, `requested_frontier`, `head_frontier`, `subject_frontier`, `result_frontier`,
`projection_lag`, `projection_version`, `rebuild_state`, the view-matched raw `page`, `coverage`,
sorted `gaps`, and `import_status`. It deliberately omits only `privacy_projection`; after common
local-disclosure projection, the resulting tree validates as `StatusSuccessModel`. This internal
record is not the public `StatusResult = StatusResultModel` alias and has no serializer.

## Behavior

### Request and capability validation

1. Resolve the validated `session_id` and optional/required-by-wire `writer_id` to exactly one task
   runtime. Status never enumerates bundles, guesses from paths, or treats possession of an ID as
   authorization.
2. Require `view` to be exactly `compact`, `assignment`, `obligations`, `findings`,
   `candidate_findings`, `evidence`, `history`, `versions`, or `advice`. Strictly reject unknown filter keys,
   duplicate set members, filter
   values not meaningful for the chosen view, negative/noncanonical frontiers, oversized cursors,
   and limits outside the registered bounds before any query.
3. `at_frontier = null` means the head observed when the first page starts. A supplied canonical
   sequence resolves to the exact canonical `Frontier` (sequence plus head digest) in this task;
   sequence 0 uses the genesis frontier. It may not exceed the head. Subsequent pages use the
   frontier embedded in the cursor even if newer events arrive.
4. Status requires `structural_read`. Views/fields that decode user payload require
   `payload_read`. The inherently structural assignment/history/versions items remain available
   without payload keys. Content-bearing rows that are tombstoned or cannot be opened are omitted
   under the exact bounded scan rule below and add `redacted_event` or
   `event_payload_unavailable`; no field is replaced with an invented empty/null value. v0.1 has no
   alternate partial-item schema.

### Closed views and filters

The frozen v0.1 filter models below are closed and canonicalized; absence means the view's full
bounded set at the frozen frontier:

| View | Returned slice | Allowed filters |
|---|---|---|
| `compact` | task/session reference, current plan ref, open-obligation and unresolved-finding summaries/counts, freshness, coverage, versions, gaps | none |
| `assignment` | current assignments and their bounded obligation/scope references | `actor_id`, `include_resolved` |
| `obligations` | latest obligation state plus revision/evidence reference summaries | `actor_id`, `include_resolved`, `status` (`open` or `resolved`) |
| `findings` | findings with current response/waiver state and coverage | `origin`, `priority`, `disposition`, `include_resolved` |
| `candidate_findings` | ID-free deterministic candidates computed from the frozen record at this frontier, each with its rule/policy identity, basis facts, and coverage; no verdict, no semantic candidates | `priority` |
| `evidence` | evidence identity, strength, subject-state freshness, availability and references; no large content | `strength`, `freshness`, `include_unavailable` |
| `history` | structural accepted-event summaries, not payload bodies or the entire ledger | `schema_name`, `actor_id`, `after_sequence` |
| `versions` | protocol/engine/policy/projection/object/storage/Python/SQLite/provider-profile identities relevant to this task/runtime | none |
| `advice` | latest versioned bounded observation-advice projection: finding/rule identities, evidence commitments, coverage/freshness, verification and semantic state, and next action | none |

`advice` reads the latest durable snapshot through the task observation repository at the routed
task frontier. Its page format is `yoetz.advice-snapshot/1`; it exposes no raw encrypted content,
plaintext path, transcript, tool output, or semantic packet. Finding IDs match ordinary materialized
`finding_recorded` events, so `findings` and `compact` naturally include unresolved advice instead
of creating a parallel namespace. Missing observation/advice state returns an empty bounded page
with explicit freshness/coverage limitations.

Set-valued filters are sorted/unique and enum-valued filters use exact registered tokens. Adding a
filter changes the schema version; adapters may not accept arbitrary predicates, column names, SQL,
or free-form search.

### Exact stored-row projection

`ports/ledger.md` owns the field-by-field raw item mapping and structural index inventory. The
application relies on these exact consequences and does not remap them:

- assignment `scope_refs` is exactly its canonical obligation IDs. It is resolved only by a later
  handoff chain or when every referenced obligation is effectively resolved;
- an obligation is effectively resolved only by accepted `status=resolved` or plan change
  `superseded|waived`; `carried` preserves accepted status. Actor/source/evidence/revision fields
  use the exact structural joins registered by the port;
- finding disposition is the latest recorded response token, or `none`; waiver expiry is displayed
  but never evaluated against wall clock for filtering, ordering, or resolution. A response never
  resolves a finding;
- evidence strength is the accepted token, availability describes only its captured object (never
  a live path/URL probe), and freshness is the weakest source-envelope/projection freshness capped
  at `redacted_gap` for an unavailable captured object;
- history is accepted-envelope metadata with the exact supported schema name as `summary_code`, or
  `opaque_unknown`; and
- versions is one verified runtime/projection manifest slice and performs no event read.

For `include_resolved` and `include_unavailable`, absent is identical to false and adds the false/
available predicate; true removes only that predicate. Every other supplied filter is ANDed. Thus
`status=resolved` without `include_resolved=true` is a valid empty query. Filters never override
one another implicitly.

Finding resolution is evidence-based. Its issue key is exactly `(origin, policy_id,
policy_version, kind, complete canonical subject_refs)`. A newer same-key finding supersedes the
older row and begins unresolved. Otherwise an old row becomes resolved only after a later recorded
check proves applicability: the check frontier includes the finding event, matching policy
execution is `run/completed`, suppression is zero, coverage freshness is current with no gaps, and
normalized scope is whole-case or directly intersects the finding's claim/obligation subject refs.
A semantic finding additionally requires `succeeded/semantic_completed`. While the supporting
events remain visible, weak, skipped, failed, capped, stale, or non-overlapping later checks do
nothing and never reopen a prior resolution. A later redaction may remove the structural proof and
conservatively make the old row unresolved again; the explicit redaction gap explains that
weakening.

That proof requires two required additive fields on `CheckRecordedPayload`: normalized
`scope(claim_ids, obligation_ids)` with both empty meaning whole-case, and the existing exact
`policy_executions` tuple. `policies`, verdict, and returned IDs alone cannot establish that an
arbitrary scoped/skipped check evaluated the old issue.

Redacted projection tombstones retain too little payload meaning to satisfy assignment,
obligation, finding, or evidence item schemas. Those rows are omitted and add `redacted_event`;
the adapter may not retain or reconstruct actor/status/strength/rank/content facts after the
redaction scrub. A non-redacted structurally indexed obligation/finding/evidence row whose selected
payload (or current finding response) cannot be opened is likewise omitted with
`event_payload_unavailable`; non-redacted assignments remain renderable from their exact
structural index. Captured-content
unavailability is different: an otherwise readable evidence item remains present with
`available=false` and weakened freshness when `include_unavailable=true`.

Compact counts remain conservative despite omissions: every obligation tombstone counts open and
every finding tombstone counts unresolved; ordinary structural rows use their exact derived
status. Top-ten summaries are bounded selections, not count sources. Unreadable selected summaries
are omitted without backfill, and an unreadable task title omits the compact singleton itself. The
page's explicit gaps prevent an empty/short summary tuple from being read as clean state.

Every repository page carries one snapshot-wide coverage value independent of filter and page
size: `coverage.weakest` over all accepted-envelope coverage through the effective frontier,
weakened by normalized projection gaps, captured-object unavailability, and selected-row omission.
The page gap tuple is exactly that final coverage's sorted `known_gaps`; an empty filtered page can
therefore never erase unrelated uncertainty.

### The `candidate_findings` view

The view resolves the exact frontier, obtains/replays the full `ProjectionState`, reads the
authoritative accepted-record prefix through that same frontier, calls
`LedgerPort.load_case_availability(session_id, frontier, projection)`, and calls
`build_deterministic_case(projection, records, availability)`. It then calls
`run_deterministic_policies` against
that pure `DeterministicCase`. A projection cache alone cannot supply or guess per-reference
coverage. The helper is exactly the one used by `LedgerPort.freeze_case`, so status and check join
the same current projection source links to the same accepted-envelope coverage and typed gaps.
Both halves are pure after the bounded ledger read, so the result is a deterministic function of
recorded evidence at an exact frontier. The view persists no resume-case object, reserves no
operation, takes no lease, allocates no ID, and writes nothing.

With implicit whole-case scope, the view uses the same structural `not_applicable` and
`material_unavailable` pre-invocation predicates as `check`. It emits no `CheckPolicyExecution`
because status is not a recorded check; skipped-pack uncertainty remains visible only through the
same case coverage/gaps. Every pack it does invoke returns assessments only.

Three properties keep it from becoming a second `check`:

- **It returns no verdict.** No `CheckVerdict` token appears in the result. `no_issue_detected` is a
  recorded conclusion carrying coverage and a durable receipt; nothing computed here can produce it.
- **An empty result is not a clean result.** An empty candidate tuple means only that no rule fired
  against this record at this frontier. The result carries the same gaps, freshness, and coverage
  metadata as any other view, and `guidance/coverage-and-receipts.md` owns the rule that only a
  recorded check bounds an agent's final wording. In particular, a rootless/global `CaseGap`
  weakens this metadata without fabricating a finding subject.
- **Candidates have no IDs.** `finding_id` is allocated through `IdPort` in `check` and nowhere
  else, so a candidate is structurally uncitable: `respond` cannot address it, a receipt cannot
  reference it, and no event can name it. The absent ID is the enforcement, not a rule about it.

Candidates carry the exact `policy_id`/`policy_version` that produced them, so the same frontier and
the same pack reproduce the same tuple. A `check` recorded later at an unchanged frontier draws its
**deterministic** findings from exactly this candidate set, then allocates IDs, optionally adds
independently validated semantic findings, ranks, and caps to `max_findings`. The candidate view is
therefore a superset only of the recorded check's deterministic findings; semantic-model-derived
findings are intentionally absent and excluded from parity assertions. It is never itself a
substitute for the ranked, recorded, coverage-bound result. Semantic evaluation never runs here:
the view is deterministic-only by construction, which invariant 6 already requires of every
status call.

Unlike every other view, `candidate_findings` cannot be served from a `ProjectionQuery`: a rule
evaluates one whole frozen case, not a page of rows. The view therefore loads `ProjectionState`
through `load_projection` exactly as the check path does, runs the packs, and paginates the
resulting candidate tuple. This is not the pattern the port prerequisite below forbids — that
prohibition is on loading whole projections to slice *rows* the repository could have filtered and
paged itself. No repository can page a rule evaluation. The response stays bounded, paginated, and
capped, and the internal cost is exactly `check` step 2 with none of its durability.

Deterministic candidate prose is rule-templated and names its subjects by ID (`domain/findings.md`),
so the frozen result-field registry classifies its v0.1 leaves as structural. It still follows the
ordinary result-projection path: after `execute_status` finishes its task-state read, the
service-owned `project_result_for_client` reserves/atomically completes the
`AgentProjectionAuditSubject` local-disclosure receipt and adds the required
`privacy_projection`. This module imports no privacy domain/port type and never writes the task
ledger. The audit-catalog write is an ordinary service-boundary disclosure effect, not a task
frontier change; failure yields `privacy_projection_unavailable` before serialization.

### Query, page, and frontier semantics

1. Decode and authenticate/verify the opaque cursor before repository access. Its canonical
   content binds the cursor version, session ID, view, canonical filter digest, frozen requested
   frontier, effective projection version, stable last sort key, and page limit policy. A cursor
   from another query/session/version is `INVALID_REQUEST`; it never changes the requested query.
2. For every view except `candidate_findings`, execute exactly one `ProjectionQuery` for the frozen
   frontier. For `candidate_findings`, execute no `ProjectionQuery`: load/replay the full
   `ProjectionState`, stream the authoritative accepted prefix through the same frontier, snapshot
   the exact case availability, build the deterministic case, run both applicable packs, apply the
   request's priority filter, and page the resulting
   bounded tuple in application memory. In both branches execute one bounded
   `TaskRuntime.importer.status(session_id)` structural query. The repository reads the exact
   frontier from its replay-derived, interval-indexed structural query facts; it never decrypts an
   unbounded prefix to evaluate a filter, holds a read transaction across page delivery, or loads
   the whole ledger merely to slice in application memory.
3. Sort deterministically by view-specific stable keys: assignment/obligation/evidence rows by
   their canonical ID bytes, findings by the registered stable finding order, and history by
   ingestion sequence. Recorded findings use the complete ten-part `rank_key`, whose final
   tie-break is unsigned ASCII `finding_id`. Candidate findings use the identical prefix through
   `origin_ordinal` but, having no ID, use the engine's canonical emission ordinal as the final
   tie-break. Consequently tied candidates and recorded findings are not positionally compared
   after ID allocation; parity is by deterministic candidate identity and each view is tested
   against its own declared order. The candidate cursor position is the full rank prefix plus
   emission ordinal and is byte-stable for a frozen case and pack
   (`kernel/deterministic_checks.md`), which is exactly the frontier the cursor binds. The cursor is
   exclusive of the last returned key. A page has at most the
   validated limit and reports a next cursor only when another matching row exists at the same
   snapshot frontier.
   Repository selection reads at most `limit + 1` structural candidates. It hydrates only the
   first `limit`; an unreadable selected candidate is omitted without replacement, but the cursor
   advances over its structural key so retry cannot loop on it. Lookahead is never decrypted.
4. Return the requested/head frontier, the exact projection frontier represented by the rows,
   projection lag relative to the requested/head frontier, projection version, rebuild-required
   state, and sorted unique unknown/redacted/unavailable gaps. If an explicit `at_frontier` cannot
   be represented exactly, fail or rebuild/replay; do not return a different frontier as though it
   answered the request. For latest reads, an explicitly disclosed lagged cached page is permitted
   only if its `subject_frontier` is the effective projection frontier and `head_frontier`/`lag`
   make the difference unambiguous.
   The result also carries bounded pending/terminal import counts and safe phases/report evidence
   locators; it never includes source metadata, filenames, paths, or text.
5. Because status is task-state read-only, `subject_frontier == result_frontier` at the effective
   projection frontier. `execute_status` writes no task event, task object, operation, lease, or
   projection mutation and makes no provider/network call. The later common client-disclosure step
   writes/replays exactly one privacy-audit catalog receipt and adds the required result
   `privacy_projection`; it does not change either task frontier. Normal read-only SQLite cache
   effects are not product writes;
   a required projection rebuild is a fenced maintenance action outside the status acknowledgement
   and must be disclosed until complete.

`compact` and `versions` are bounded single-object views: `items` contains at most one typed view
and `next_cursor` is always null. Every other view is paginated even when the current fixture is
small.

### Port prerequisite

`LedgerPort.load_projection(session_id, view)` cannot express `at_frontier`, filters, stable page
limits/cursors, or an exact historical snapshot. Loading `ProjectionState` and filtering it in the
application would violate the bounded-read promise. Both adapters implement the exact port-owned
surface:

- `ProjectionQuery(session_id, queryable view, typed filter, requested_frontier, limit,
  typed position, expected_projection_version)`;
- `ProjectionPage(view, items, requested/head/effective frontiers, lag, projection version,
  rebuild state, coverage, gaps, next typed position)`;
- `LedgerPort.query_projection(query) -> ProjectionPage`.

Opaque cursor serialization/authentication stays in the application/protocol boundary; repository
positions stay typed and contain no SQL. The existing `load_projection` remains useful to the
check/kernel path and is not silently redefined by this spec. `candidate_findings` is absent from
the queryable-view literal and a repository receiving it fails `INVALID_REQUEST`.

The query prerequisite also includes the finite nonplaintext structural facts in
`ports/ledger.md`: temporal visibility, closed filter fields, complete rank facts, normalized ID
edges, check applicability facts, compact counters/source locators, and accepted-history metadata.
Payload-derived prose/body/JSON is forbidden from those columns. Redaction scrubs payload-derived
structural facts across interval history, so an older-frontier query cannot recover facts the
tombstone intentionally removed.

## Errors and edge cases

- Unknown view/filter, invalid filter combination, cursor mismatch/tampering/expiry policy,
  invalid limit, or future frontier → `INVALID_REQUEST`. This mapping is frozen for v0.1: status is
  a read-only query, so a requested sequence beyond the observed head is invalid query input and
  never `FRONTIER_CONFLICT`. `FRONTIER_CONFLICT` remains for stale optimistic mutation guards.
- Unknown session/route → `SESSION_NOT_FOUND`; writer/session mismatch → `SESSION_CONFLICT`.
  Canonical ledger/index/digest disagreement → `STORAGE_CORRUPT`; unsupported schema/build →
  `MIGRATION_REQUIRED`/`STORAGE_UNSAFE`.
- Events appended between pages do not appear because the first page's snapshot frontier is bound
  into the cursor. A redaction/key loss that makes previously readable payload unavailable is
  reported as a gap; pagination identity remains structural and stable.
- A row omitted for tombstone/unreadability is not backfilled on that page. It still consumes its
  scanned cursor position; otherwise an empty page could loop forever or decrypt an unbounded tail.
- Cancellation closes/relinquishes the bounded read and returns no partial success envelope. A
  retry needs no task-ledger idempotency lookup; the service's exact local-disclosure binding
  idempotently replays its receipt when the internal result/policy are unchanged.
- Empty match is a successful empty page with the exact frontier/coverage metadata, not evidence
  that the corresponding real-world work never occurred.
- An unknown or tampered policy pack is an internal policy wiring error and fails the
  `candidate_findings` request. It never degrades to an empty candidate list, which a reader could
  mistake for a record that no rule objected to.
- Privacy-audit reservation/completion failure maps to the common retryable
  `privacy_projection_unavailable` service error before a `StatusResult` is serialized; it never
  returns an unreceipted result or changes the task frontier.

## Invariants

1. Status is the only one of the six operations that creates no task-ledger operation/event; its
   ordinary result still carries the mandatory service-owned local-disclosure receipt.
2. Every list response is bounded, deterministically ordered, and snapshot-stable across pages.
3. A result never claims a newer frontier than the projection rows it actually represents.
4. Unknown, redacted, unavailable, stale, and rebuild gaps remain explicit and weaken coverage.
5. Cursor input cannot inject repository predicates or cross a task/view/filter/frontier boundary.
6. Status performs no semantic-provider/network work and never strengthens actor/coverage claims.
7. `candidate_findings` returns no verdict and allocates no finding ID, so nothing it returns can be
   responded to, waived, cited in a receipt, or named by an event. An empty result is never a
   conclusion.
8. `candidate_findings` never invokes `query_projection`; every other view invokes it exactly once.
9. Disposition and waiver expiry never stand in for finding resolution; only the frozen
   same-issue/applicable-check proof can resolve a row. Ordinary later checks cannot reopen it,
   while redaction may remove proof and weaken it explicitly.
10. Indexed structural filtering always precedes bounded payload hydration; payload text is never a
    SQL/index field.

## Tests

- `specs/tests/unit.md`: view/filter cross-product, unknown-key rejection, bounds, cursor binding
  and tampering, empty-page semantics, subject/result frontier equality; a `candidate_findings`
  result carries no verdict field and no finding ID, and a wired-wrong pack fails rather than
  returning an empty tuple.
- `specs/tests/conformance.md`: all views over golden fixtures; latest/historical snapshot and
  multi-page parity between memory and SQLite; append-between-pages isolation; deterministic order.
  Every deterministic finding a recorded `check` returns appears among the
  `candidate_findings` candidates at the same frontier with identical rule identity, policy
  version, priority, and prose; semantic findings are excluded. Recorded ties use finding ID,
  candidate ties use emission ordinal. The `candidate_findings` call appends no task event and
  creates no task operation, while ordinary serialization carries one replayable privacy
  projection receipt.
- `specs/tests/integration.md`: projection lag/rebuild, key locked/missing structural slice,
  redacted/unknown events, canonical corruption detection, session/writer mismatch.
- `specs/tests/property.md`: arbitrary page sizes/cursors concatenate to the same exact view as one
  reference query with no duplicates/omissions among renderable rows; injected unreadable rows
  advance once, add a gap, and are never retried or backfilled.
- `specs/tests/subprocess.md`: cancellation/slow-reader bounded resources and zero stdout noise.

## Open questions

None.

An alternate partial-item schema for structural status is deferred to v0.2; v0.1 uses the exact
inherently structural views and whole-row omission rules above.
