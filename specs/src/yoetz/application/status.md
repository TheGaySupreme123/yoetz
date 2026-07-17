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
uncertainty rather than of its work. Looking must be free of consequence, or the agent stops
looking.

## Public surface

- `async execute_status(app: Application, request: StatusRequest) -> StatusResult` —
  implementation behind `Application.status`.
- Application-internal cursor validation/encoding helpers. Cursor bytes are opaque transport data,
  not ledger truth and not a public identifier kind.

The shared `ProjectionQuery`, typed `ProjectionFilter` variants, `ProjectionCursor`, and
`ProjectionPage` are registered in `specs/INTERFACES.md`. This module uses the registered
`LedgerPort.query_projection` boundary rather than trying to implement bounded historical/filter/
pagination semantics through `load_projection`.

## Behavior

### Request and capability validation

1. Resolve the validated `session_id` and optional/required-by-wire `writer_id` to exactly one task
   runtime. Status never enumerates bundles, guesses from paths, or treats possession of an ID as
   authorization.
2. Require `view` to be exactly `compact`, `assignment`, `obligations`, `findings`,
   `candidate_findings`, `evidence`, `history`, or `versions`. Strictly reject unknown filter keys,
   duplicate set members, filter
   values not meaningful for the chosen view, negative/noncanonical frontiers, oversized cursors,
   and limits outside the registered bounds before any query.
3. `at_frontier = null` means the head observed when the first page starts. A supplied canonical
   sequence resolves to the exact canonical `Frontier` (sequence plus head digest) in this task;
   sequence 0 uses the genesis frontier. It may not exceed the head. Subsequent pages use the
   frontier embedded in the cursor even if newer events arrive.
4. Status requires `structural_read`. Views/fields that decode user payload require
   `payload_read`; if the runtime intentionally admits structural-only reading, it returns only
   the registry-approved structural slice and explicit `payload_unavailable` gaps. It never
   fabricates empty descriptions or silently drops rows.

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

Set-valued filters are sorted/unique and enum-valued filters use exact registered tokens. Adding a
filter changes the schema version; adapters may not accept arbitrary predicates, column names, SQL,
or free-form search.

### The `candidate_findings` view

The view replays to the frozen frontier and calls `run_deterministic_policies` against the resulting
case. Both halves are already pure, so the result is a deterministic function of recorded evidence
at an exact frontier — the same character as the projection itself, which is already a derived cache
of meaning rather than stored bytes. The view freezes no case object, reserves no operation, takes
no lease, allocates no ID, and persists nothing.

Three properties keep it from becoming a second `check`:

- **It returns no verdict.** No `CheckVerdict` token appears in the result. `no_issue_detected` is a
  recorded conclusion carrying coverage and a durable receipt; nothing computed here can produce it.
- **An empty result is not a clean result.** An empty candidate tuple means only that no rule fired
  against this record at this frontier. The result carries the same gaps, freshness, and coverage
  metadata as any other view, and `guidance/coverage-and-receipts.md` owns the rule that only a
  recorded check bounds an agent's final wording.
- **Candidates have no IDs.** `finding_id` is allocated through `IdPort` in `check` and nowhere
  else, so a candidate is structurally uncitable: `respond` cannot address it, a receipt cannot
  reference it, and no event can name it. The absent ID is the enforcement, not a rule about it.

Candidates carry the exact `policy_id`/`policy_version` that produced them, so the same frontier and
the same pack reproduce the same tuple. A `check` recorded later at an unchanged frontier draws its
findings from exactly this candidate set, then ranks and caps them to `max_findings` — so the view
is a superset of what that check returns, never a different answer, and never itself a substitute
for the ranked, recorded, coverage-bound result. Semantic evaluation never runs here: the view is
deterministic-only by construction, which invariant 6 already requires of every status call.

Unlike every other view, `candidate_findings` cannot be served from a `ProjectionQuery`: a rule
evaluates one whole frozen case, not a page of rows. The view therefore loads `ProjectionState`
through `load_projection` exactly as the check path does, runs the packs, and paginates the
resulting candidate tuple. This is not the pattern the port prerequisite below forbids — that
prohibition is on loading whole projections to slice *rows* the repository could have filtered and
paged itself. No repository can page a rule evaluation. The response stays bounded, paginated, and
capped, and the internal cost is exactly `check` step 2 with none of its durability.

Deterministic candidate prose is rule-templated and names its subjects by ID (`domain/findings.md`),
so a candidate discloses nothing about material the requesting writer did not author and the
`agent_context` ceiling has nothing to withhold from it. The view is an agent projection like any
other and reserves and completes its `AgentProjectionAuditSubject` receipt. That receipt lives in
the privacy audit store, not the task ledger, so looking stays free of ledger consequence while
remaining audited.

### Query, page, and frontier semantics

1. Decode and authenticate/verify the opaque cursor before repository access. Its canonical
   content binds the cursor version, session ID, view, canonical filter digest, frozen requested
   frontier, effective projection version, stable last sort key, and page limit policy. A cursor
   from another query/session/version is `INVALID_REQUEST`; it never changes the requested query.
2. Execute one `ProjectionQuery` for the frozen frontier and one bounded
   `TaskRuntime.importer.status(session_id)` structural query. The repository either reads an active
   projection generation that can answer that exact frontier or performs bounded canonical replay
   into a read-only query snapshot; it never holds a read transaction across page delivery and
   never loads the whole ledger merely to slice in application memory.
3. Sort deterministically by view-specific stable keys: assignment/obligation/evidence rows by
   their canonical ID bytes, findings by registered stable finding order then ID, and history by
   ingestion sequence. Candidate findings use the same registered stable finding order as the
   `findings` view, so an agent never sees one ordering here and a different one in a check result,
   but they carry no ID to break ties and use the engine's canonical emission ordinal instead; that
   ordinal is the cursor position and is already byte-stable for a frozen case and pack
   (`kernel/deterministic_checks.md`), which is exactly the frontier the cursor binds. The cursor is
   exclusive of the last returned key. A page has at most the
   validated limit and reports a next cursor only when another matching row exists at the same
   snapshot frontier.
4. Return the requested/head frontier, the exact projection frontier represented by the rows,
   projection lag relative to the requested/head frontier, projection version, rebuild-required
   state, and sorted unique unknown/redacted/unavailable gaps. If an explicit `at_frontier` cannot
   be represented exactly, fail or rebuild/replay; do not return a different frontier as though it
   answered the request. For latest reads, an explicitly disclosed lagged cached page is permitted
   only if its `subject_frontier` is the effective projection frontier and `head_frontier`/`lag`
   make the difference unambiguous.
   The result also carries bounded pending/terminal import counts and safe phases/report evidence
   locators; it never includes source metadata, filenames, paths, or text.
5. Because status is read-only, `subject_frontier == result_frontier` at the effective projection
   frontier. It writes no event, object, operation, lease, projection mutation, or receipt and
   makes no provider/network call. Normal read-only SQLite cache effects are not product writes;
   a required projection rebuild is a fenced maintenance action outside the status acknowledgement
   and must be disclosed until complete.

`compact` and `versions` are bounded single-object views: `items` contains at most one typed view
and `next_cursor` is always null. Every other view is paginated even when the current fixture is
small.

### Port prerequisite

`LedgerPort.load_projection(session_id, view)` cannot express `at_frontier`, filters, stable page
limits/cursors, or an exact historical snapshot. Loading `ProjectionState` and filtering it in the
application would violate the bounded-read promise. Register and implement in both adapters a
shared surface equivalent to:

- `ProjectionQuery` — session, closed view/filter value, exact requested frontier, limit, and
  typed stable cursor position;
- `ProjectionPage` — typed rows/single view, requested/head/effective projection frontiers, lag,
  projection version, rebuild state, gaps, and next stable position;
- `LedgerPort.query_projection(query) -> ProjectionPage`.

Opaque cursor serialization/authentication stays in the application/protocol boundary; repository
positions stay typed and contain no SQL. The existing `load_projection` remains useful to the
check/kernel path and is not silently redefined by this spec.

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
- Cancellation closes/relinquishes the bounded read and returns no partial success envelope. A
  retry needs no idempotency lookup because status made no durable effect.
- Empty match is a successful empty page with the exact frontier/coverage metadata, not evidence
  that the corresponding real-world work never occurred.
- An unknown or tampered policy pack is an internal policy wiring error and fails the
  `candidate_findings` request. It never degrades to an empty candidate list, which a reader could
  mistake for a record that no rule objected to.

## Invariants

1. Status is the only one of the six operations that creates no task-ledger operation/event.
2. Every list response is bounded, deterministically ordered, and snapshot-stable across pages.
3. A result never claims a newer frontier than the projection rows it actually represents.
4. Unknown, redacted, unavailable, stale, and rebuild gaps remain explicit and weaken coverage.
5. Cursor input cannot inject repository predicates or cross a task/view/filter/frontier boundary.
6. Status performs no semantic-provider/network work and never strengthens actor/coverage claims.
7. `candidate_findings` returns no verdict and allocates no finding ID, so nothing it returns can be
   responded to, waived, cited in a receipt, or named by an event. An empty result is never a
   conclusion.

## Tests

- `specs/tests/unit.md`: view/filter cross-product, unknown-key rejection, bounds, cursor binding
  and tampering, empty-page semantics, subject/result frontier equality; a `candidate_findings`
  result carries no verdict field and no finding ID, and a wired-wrong pack fails rather than
  returning an empty tuple.
- `specs/tests/conformance.md`: all views over golden fixtures; latest/historical snapshot and
  multi-page parity between memory and SQLite; append-between-pages isolation; deterministic order.
  every finding a recorded `check` returns appears among the `candidate_findings` candidates at the
  same frontier with identical rule identity, policy version, priority, and prose, and the
  `candidate_findings` call itself appends no event and creates no operation.
- `specs/tests/integration.md`: projection lag/rebuild, key locked/missing structural slice,
  redacted/unknown events, canonical corruption detection, session/writer mismatch.
- `specs/tests/property.md`: arbitrary page sizes/cursors concatenate to the same exact view as one
  reference query with no duplicates/omissions.
- `specs/tests/subprocess.md`: cancellation/slow-reader bounded resources and zero stdout noise.

## Open questions

None.

Structural status without keys is deferred to v0.2.
