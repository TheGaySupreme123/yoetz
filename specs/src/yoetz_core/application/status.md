# src/yoetz_core/application/status.py — bounded read-only projection queries at a stable frontier

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-005 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/values.md`, `kernel/projections.md`, `ports/ledger.md` |
**Imported by:** `application/service.md`

## Purpose

`status` answers bounded questions about recorded task state without publishing an event or
creating an idempotency operation. It serves one deterministic view at the latest or an explicitly
requested stable frontier, applies only closed filters, paginates every list, and discloses
projection lag, rebuild state, unknown events, redactions, and unavailable payloads. It is a view
of recorded evidence, never a claim that unobserved work did or did not happen.

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
2. Require `view` to be exactly `compact`, `assignment`, `obligations`, `findings`, `evidence`,
   `history`, or `versions`. Strictly reject unknown filter keys, duplicate set members, filter
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
| `evidence` | evidence identity, strength, subject-state freshness, availability and references; no large content | `strength`, `freshness`, `include_unavailable` |
| `history` | structural accepted-event summaries, not payload bodies or the entire ledger | `schema_name`, `actor_id`, `after_sequence` |
| `versions` | protocol/engine/policy/projection/object/storage/Python/SQLite/provider-profile identities relevant to this task/runtime | none |

Set-valued filters are sorted/unique and enum-valued filters use exact registered tokens. Adding a
filter changes the schema version; adapters may not accept arbitrary predicates, column names, SQL,
or free-form search.

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
   ingestion sequence. The cursor is exclusive of the last returned key. A page has at most the
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
  invalid limit, or future frontier → `INVALID_REQUEST` (future/stale optimistic semantics may use
  `FRONTIER_CONFLICT` only if the protocol registry chooses that uniformly).
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

## Invariants

1. Status is the only one of the six operations that creates no task-ledger operation/event.
2. Every list response is bounded, deterministically ordered, and snapshot-stable across pages.
3. A result never claims a newer frontier than the projection rows it actually represents.
4. Unknown, redacted, unavailable, stale, and rebuild gaps remain explicit and weaken coverage.
5. Cursor input cannot inject repository predicates or cross a task/view/filter/frontier boundary.
6. Status performs no semantic-provider/network work and never strengthens actor/coverage claims.

## Tests

- `specs/tests/unit.md`: view/filter cross-product, unknown-key rejection, bounds, cursor binding
  and tampering, empty-page semantics, subject/result frontier equality.
- `specs/tests/conformance.md`: all views over golden fixtures; latest/historical snapshot and
  multi-page parity between memory and SQLite; append-between-pages isolation; deterministic order.
- `specs/tests/integration.md`: projection lag/rebuild, key locked/missing structural slice,
  redacted/unknown events, canonical corruption detection, session/writer mismatch.
- `specs/tests/property.md`: arbitrary page sizes/cursors concatenate to the same exact view as one
  reference query with no duplicates/omissions.
- `specs/tests/subprocess.md`: cancellation/slow-reader bounded resources and zero stdout noise.

## Open questions

None.

Structural status without keys is deferred to v0.2.
