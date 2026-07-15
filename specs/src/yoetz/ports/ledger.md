# src/yoetz/ports/ledger.py — LedgerPort protocol and its command/result types

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`protocol/ids.md`, `protocol/errors.md`, `protocol/coverage.md`, `domain/events.md`,
`domain/findings.md`, `domain/values.md` (`Frontier`), `kernel/projections.md`,
`ports/objects.md` (ObjectRef type only) |
**Imported by:** `application/service.md`, `application/publish_work.md`, `application/check.md`,
`application/respond.md`, `application/status.md`, `application/receipt.md`,
`application/import_review.md`, `adapters/sqlite/repository.md`, `adapters/memory/ledger.md`

## Purpose

`LedgerPort` is the single abstract boundary between the application layer and the authoritative
task-bundle event ledger. Every durable post-`start` effect — atomic batch append, idempotent
retry resolution, check freezing/finalization, projection reads, and operation lookup — crosses
this protocol. Without it, CLI/MCP adapters would talk to SQLite directly, the in-memory reference
adapter and SQLite adapter could diverge, and the conformance suite ("SQLite and an in-memory
reference adapter implement the same protocol") would have no shared contract to run against.

No SQLite, APSW, or transport type appears in any signature. All methods are `async`.

## Public surface

- `class LedgerPort(Protocol)` with the shared methods registered in `specs/INTERFACES.md`:
  - `async def append_batch(self, command: AppendCommand) -> AppendResult`
  - `def load_events(self, session_id: SessionId, *, after: int = 0, through: int | None = None) -> AsyncIterator[AcceptedEvent]`
  - `async def load_projection(self, session_id: SessionId, view: ProjectionView) -> StoredProjection | None`
  - `async def query_projection(self, query: ProjectionQuery) -> ProjectionPage`
  - `async def freeze_case(self, session_id: SessionId, writer_id: str, expected_frontier: int | None, request_id: str, request_digest: str) -> FrozenCase`
  - the seven durable check-orchestration methods registered in `INTERFACES.md`;
  - `async def commit_check_if_current(self, frozen: FrozenCase, findings: RankedFindings, semantic_status: SemanticStatus, semantic_reason: SemanticReason, semantic_provenance: SemanticProvenance | None, request_id: str) -> CheckResult`
  - `async def lookup_operation(self, writer_id: str, operation_id: str) -> OperationRecord | None`
- `@dataclass(frozen=True, slots=True) class AppendCommand`
- `@dataclass(frozen=True, slots=True) class AppendEntry`
- `@dataclass(frozen=True, slots=True) class AppendResult`
- `@dataclass(frozen=True, slots=True) class AcceptedEventSummary`
- imported `Frontier(sequence: int, head_digest: str)` from `domain/values.py`; this port does not
  define a duplicate frontier type
- `@dataclass(frozen=True, slots=True) class FrozenCase`
- `@dataclass(frozen=True, slots=True) class OperationRecord`
- `@dataclass(frozen=True, slots=True) class StoredProjection`
- `enum ProjectionView` — `compact`, `assignment`, `obligations`, `findings`, `evidence`, `history`, `versions`
- `enum OperationKind` — `publish_work`, `check`, `respond`, `receipt`
- `enum OperationState` — `pending`, `complete`, `quarantined`
- Type alias `SessionId = str` (validated `ses_` ID; opaque past validation).

## Behavior

### Types

`AppendCommand` fields:

- `task_id: str`, `session_id: SessionId`, `writer_id: str` — validated opaque IDs.
- `operation_id: str` — the public mutating `request_id` (`req_` + UUIDv4).
- `operation_kind: OperationKind`.
- `request_digest: str` — `sha256:<hex>` of the publication request identity bytes computed by the
  application using `specs/src/yoetz/protocol/canonical.md` (caller logical headers + keyed payload commitments; never
  plaintext payloads, object IDs, nonces, or ledger-assigned fields).
- `expected_frontier: int | None` — optimistic guard; `None` means intentionally append-only.
- `entries: tuple[AppendEntry, ...]` — 1..`MAX_EVENTS_PER_BATCH`, already validated upstream.

`AppendEntry` fields (one accepted-envelope precursor per event):

- `draft: EventDraft` — client-shaped logical event (stable `event_id`, schema name/version,
  `occurred_at`, sorted-unique `causal_parents`, artifact/evidence refs).
- `author: Actor` — server-constrained assurance already applied; the port never upgrades it.
- `payload_object: ObjectRef` — finalized encrypted payload object (durable before this call).
- `payload_commitment: str` — `hmac-sha256:<hex>` keyed commitment over the canonical payload.
- `media_type: str`, `plaintext_size: int`.
- `publication_channel: PublicationChannel`, `coverage: Coverage`.
- `projection_status: Literal["projected", "unknown_unprojected"]` — unknown schemas are appended
  opaque, never coerced.

`AppendResult` fields:

- `outcome: Literal["accepted", "replayed"]` — `replayed` means the identical request was already
  terminal and the stored original result is being returned bit-for-bit.
- `accepted: tuple[AcceptedEventSummary, ...]` — per event: `event_id`, `ingestion_sequence: int`,
  `writer_sequence: int`, `entry_digest: str`, `projection_status`.
- `subject_frontier: Frontier`, `result_frontier: Frontier`.
- `warnings: tuple[str, ...]` — bounded reason codes only (e.g. `unknown_event_schema_preserved`);
  never user text.

`FrozenCase` fields:

- `case: ProjectionState` — the kernel projection at the frozen frontier (input to
  `run_deterministic_policies` and semantic-case minimization).
- `frontier: Frontier` — subject frontier `F`.
- `dependency_digest: str` — digest `D` over the material inputs (frontier head digest, policy
  pack IDs/versions, engine version, projection version, config digest) that a semantic result
  must still match at finalization.
- `allowed_ids: frozenset[str]` — the closed set of event/obligation/claim/action/result/evidence/
  finding IDs present at `F`; this becomes `SemanticCase.frontier_refs`. Semantic post-validation
  later checks the union of this set plus the same-check durably pinned `local_check_refs`.
  Decisions and responses are cited through their owning event IDs. Every action/result/evidence/finding entry
  also carries a deterministic link to one or more canonical event/obligation/claim roots so a
  semantic citation can be projected into the narrower public `Finding.subject_refs` contract.
- `operation: OperationRecord` — the durable `pending/reserved` check-operation row created by
  this call (lease owner, generations, expiry).
- `replayed_result: CheckResult | None` — non-`None` when the operation was already terminal for
  the identical request; the caller returns it without re-running anything.

`OperationRecord` fields: `writer_id`, `operation_id`, `operation_kind`, `request_digest`,
`state: OperationState`, `phase: Literal["reserved", "local_ready", "semantic_wait",
"ready_to_finalize", "terminal"]`, `owner_generation: str | None`, `lease_owner_id: str | None`,
`lease_generation: int | None`, `lease_expires_at: datetime | None`,
`result_canonical: bytes | None` (structural terminal envelope; assigned IDs, sequences, digests,
reason codes only), `result_digest: str | None`, `quarantine_code: str | None`,
`terminal_at: datetime | None`.

`StoredProjection` fields: `view: ProjectionView`, `state: ProjectionState` (or the bounded typed
view slice for list views), `frontier: Frontier` (the event frontier the cache represents),
`lag: int` (events accepted after that frontier; 0 when current), `projection_version: str`,
`rebuild_required: bool`.

### `append_batch`

1. Precondition (documented, not re-verified here): every `payload_object` was finalized through
   `ObjectStorePort.finalize` before this call. A crash between finalize and append leaves only an
   orphan object, never an acknowledged event with a missing object.
2. The adapter executes this port's durable append shape: an optional bounded preflight idempotency
   read (never trusted for correctness), then one `BEGIN IMMEDIATE`-equivalent atomic section that
   re-checks idempotency, and—only for a new `operation_kind=receipt`—requires no pending import
   for the session in that same transaction; then verifies the bundle owner generation is current, verifies writer
   sequence/predecessor continuity, verifies `expected_frontier` when present, allocates N
   consecutive ledger sequences and N consecutive writer sequences, builds accepted envelopes and
   `entry_digest`s over canonical bytes, inserts object inventory/events/parents/refs, advances
   writer and global heads, applies incremental pure reducers, persists the canonical structural
   operation result and digest, and inserts the `complete/terminal` idempotency row — all
   atomically. Success is returned only after durable commit.
3. Idempotency resolution follows the decision table below exactly, keyed on
   `(writer_id, operation_id)` (physically equivalent to `(task_id, writer_id, operation_id)` in
   the one-task-per-database v0.1 layout):

| Existing operation | New `request_digest` | Behavior |
|---|---|---|
| none | any valid digest | Validate and accept all events atomically; `outcome = "accepted"`. |
| `complete` | same | Return the stored original result (`outcome = "replayed"`), including originally assigned sequences and digests. Append nothing. |
| `complete` | different | Raise `PublicOperationError(IDEMPOTENCY_CONFLICT)`. Append nothing. |
| `pending` | same | Only `check` may be `pending` in v0.1. For any other kind this is contradictory durable state → quarantine. For `check`: valid lease (current owner generation AND unexpired) → raise `OPERATION_PENDING`; expired or stale generation → fenced CAS reclaim (this path is exercised via `freeze_case`, not `append_batch`). |
| `pending` | different | Raise `PublicOperationError(IDEMPOTENCY_CONFLICT)`. |
| `quarantined` | any | Return/raise the stored stable quarantine envelope (`INTERNAL_ERROR` with the allowlisted `quarantine_code` in `safe_details`); operator repair is explicit. |

4. Batch atomicity: one invalid entry (duplicate `event_id` anywhere in history, causal parent not
   already accepted in the same task, reference to a non-durable object) rejects the whole batch
   with no partial acceptance.
5. A lease is valid only when owner generation is current AND expiry is in the future. Wall-clock
   expiry never revives a stale generation (ADR-001).

### `load_events`

Returns an async iterator over `AcceptedEvent` values for the session, strictly ordered by
`ingestion_sequence`, with `after < ingestion_sequence <= through` (`through=None` = current
head). Adapters MUST paginate internally in exact `LEDGER_READ_PAGE_SIZE = 500` pages and release any
read transaction between pages. Long-lived read transactions on hot paths are prohibited by this
port. On read, the adapter re-verifies each entry: `entry_digest` matches stored canonical
bytes, and indexed columns agree with those bytes; a mismatch raises
`PublicOperationError(STORAGE_CORRUPT)` and the bundle enters quarantine. Payload decoding
(object decryption) is lazy via the `AcceptedEvent` payload handle; a `key_locked`/`key_missing`
payload yields a typed unavailable-payload handle, never a fabricated empty payload.

### `load_projection` and `query_projection`

Returns the cached projection for `view`, or `None` when no cache exists (caller decides whether
to replay). Never rebuilds implicitly on the hot path. The returned `frontier` and `lag` are what
`status` discloses to callers ("served from a projection cache representing frontier X, lag N").

`query_projection` is the bounded, typed status boundary. It resolves the requested exact
frontier, validates a cursor bound to the same query/frontier/projection version, applies the
view-specific filter and stable ordering in the adapter, and returns no more than the requested
page size plus a next cursor. It never requires the application to load/filter an unbounded full
projection.

### `freeze_case`

Implements the freeze step used by `specs/src/yoetz/application/check.md`:

1. In one atomic section: idempotency lookup for `(writer_id, operation_id)` — terminal + same
   digest returns `FrozenCase.replayed_result`; different digest raises `IDEMPOTENCY_CONFLICT`;
   valid live lease raises `OPERATION_PENDING`; expired/stale-generation lease is fenced CAS
   reclaimed and the recorded phase is resumed (returned via `operation.phase`).
2. For a new or resumed nonterminal check, require that no importer job for `session_id` has
   `state=pending`, inside this same atomic section. Otherwise raise retryable
   `OPERATION_PENDING` without freezing/advancing anything. A prior `ImporterPort.status` read is
   an optional UX preflight only.
3. Catch projections up through the head, verify `expected_frontier` when provided (mismatch
   raises `FRONTIER_CONFLICT` with safe `{"expected", "actual"}` details), capture frontier `F`,
   compute `dependency_digest` `D` and `allowed_ids`.
4. Durably publish the encrypted resume-case object first (adapter-internal collaboration with the
   object store), then insert the `operations` row `pending/reserved` with the current bundle
   owner generation, lease owner, lease generation `1`, and expiry; commit before any expensive
   work. The pending row stores only `resume_object_id` — no fabricated terminal response.

### `commit_check_if_current`

Implements the final check commit used by `specs/src/yoetz/application/check.md`. In one
atomic section: verify the operation lease
(owner generation current, lease owner/generation match, unexpired) and that the dependency
revisions in `frozen.dependency_digest` still hold; require again that the session has no pending
import job in this same transaction; append the `check_recorded` event and one
`finding_recorded` event per returned finding with exact coverage vectors; store the stable
canonical result; set `complete/terminal` and clear lease fields; commit; only then return
`CheckResult`. A stale frontier/dependency at this point completes the operation with the
findings labeled stale and verdict computed by the application (a stale semantic result cannot
steer — see `application/check.md`); a lost lease raises `OPERATION_PENDING` (another owner) or
resumes via reclaim rules. `CheckResult` fields: `verdict: CheckVerdict`,
`findings: tuple[Finding, ...]` (≤ `max_findings`), `suppressed_count: int`,
`policies_run/skipped/failed: tuple[str, ...]`, `semantic_status: SemanticStatus`,
`semantic_reason: SemanticReason`, `semantic_provenance: SemanticProvenance | None`,
`subject_frontier: Frontier`, `result_frontier: Frontier`, `coverage: Coverage`,
`versions: VersionManifest slice`.

The adapter validates the closed status/reason pair and final-provenance presence rules before
append. It never derives a reason from `Coverage.known_gaps` and never accepts provisional provider
provenance. A complete `semantic_required` fallback therefore remains a successful operation
result with `verdict=incomplete_check`, the deterministic findings, no semantic findings, and the
machine-readable reason for the missing semantic result.

### `lookup_operation`

Bounded point read of the operation row; returns `None` when absent. Used by CLI/MCP retry
guidance, `CANCELLED` ambiguity resolution (`application/unit_of_work.md`), and conformance. It
never mutates state and never extends or reclaims a lease.

## Errors and edge cases

- Expected failures leave as `PublicOperationError` with exactly these codes per method:
  `append_batch`: `IDEMPOTENCY_CONFLICT`, `OPERATION_PENDING`, `FRONTIER_CONFLICT`,
  `EVENT_INVALID`, `LIMIT_EXCEEDED`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`, `STORAGE_CORRUPT`,
  `MIGRATION_REQUIRED`; `freeze_case` adds `SESSION_NOT_FOUND`; `commit_check_if_current`:
  `OPERATION_PENDING`, `STORAGE_*`; reads: `SESSION_NOT_FOUND`, `STORAGE_*`.
- A terminal same-digest check/receipt replay is returned before the pending-import predicate:
  replay performs no new freeze/append and remains stable even if a later import is pending.
- `SQLITE_BUSY`-class contention that outlasts the bounded busy timeout is `BUNDLE_BUSY`
  (retryable with backoff); it never emits partial success.
- Timeout/cancellation never proves failure: after any ambiguous termination the caller retries
  the identical `operation_id` and the durable row decides.
- Nothing user-controlled (payload text, titles, paths, prompts) may appear in any raised error,
  warning code, or `result_canonical` bytes.
- Wall-clock reversal or skew never affects ordering; `accepted_at` is metadata.

## Invariants

1. Acknowledgement only after durable commit; no acknowledged event may reference a missing
   object.
2. Accepted events are immutable; retry returns byte-identical original results.
3. `ingestion_sequence` is strictly increasing per bundle; `writer_sequence` increases by exactly
   1 per writer stream; conflicting predecessors fail closed, never last-write-wins.
4. Only `check` may persist a `pending` task-bundle operation row in v0.1.
5. The in-memory reference adapter and the SQLite adapter MUST produce identical canonical bytes,
   outcomes, projections, findings, coverage, and receipts on every conformance fixture.
6. A stale owner generation invalidates every lease immediately, regardless of wall clock.
7. No new/resumed check freeze, check final commit, or new receipt append can commit while the
   same session has a pending import; each decision is atomic with its ledger transaction.

## Tests

- `specs/tests/conformance.md`: dual-adapter suite (memory vs SQLite) over golden fixtures —
  append/replay identity, idempotency table rows, frontier conflicts, unknown-event preservation,
  batch atomicity, projection incremental/full equivalence, and import-start/finish races at check
  freeze/finalization and receipt append.
- `specs/tests/integration.md` and `specs/tests/subprocess.md`: kill matrix points 5–11; busy/full-disk/readonly;
  digest-verification-on-read corruption fixtures.
- `specs/tests/property.md`: Hypothesis state machine (append/retry/reuse-key/kill/reopen/replay)
  asserting the reference model, not the SQLite implementation.

## Open questions

None.
