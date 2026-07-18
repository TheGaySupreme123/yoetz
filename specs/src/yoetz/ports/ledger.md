# src/yoetz/ports/ledger.py — LedgerPort protocol and its command/result types

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003 | **Imports (spec-tree):**
`protocol/ids.md`, `protocol/errors.md`, `protocol/coverage.md`, `protocol/models.md`
(`JsonValue`, `SemanticStatus`, `SemanticReason`, and status item models), `domain/events.md`,
`domain/findings.md`, `domain/values.md` (`Frontier`), `kernel/projections.md`,
`kernel/deterministic_checks.md` (`DeterministicCase`, `build_deterministic_case`),
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

No SQLite, APSW, or transport type appears in any signature. Effectful and point-query methods are
`async`; `load_events` is the sole non-awaiting factory and returns an `AsyncIterator`.

## Public surface

- `class LedgerPort(Protocol)` with the shared methods registered in `specs/INTERFACES.md`:
  - `async def append_batch(self, command: AppendCommand) -> AppendResult`
  - `def load_events(self, session_id: SessionId, *, after: int = 0, through: int | None = None) -> AsyncIterator[LedgerRecord]`
  - `async def load_projection(self, session_id: SessionId, view: ProjectionView) -> StoredProjection | None`
  - `async def query_projection(self, query: ProjectionQuery) -> ProjectionPage`
  - `async def freeze_case(self, session_id: SessionId, writer_id: str, expected_frontier: int | None, request_id: str, request_digest: str) -> FrozenCase | CheckCommitResult`
  - `async def advance_check_phase(self, lease: OperationLease, expected_phase: CheckPhase, next_phase: CheckPhase, durable_object_ref: ObjectRef | None = None) -> OperationLease`
  - `async def enqueue_semantic_job(self, lease: OperationLease, case_digest: str, case_object_ref: ObjectRef) -> SemanticJobRecord`
  - `async def claim_semantic_job(self, lease: OperationLease, job_id: str) -> SemanticAttemptHandle`
  - `async def record_attempt_outcome(self, handle: SemanticAttemptHandle, outcome: AttemptOutcome, result_object_ref: ObjectRef | None = None, terminal_code: SemanticReason | None = None) -> None`
  - `async def select_attempt(self, lease: OperationLease, handle: SemanticAttemptHandle, selected_result_object_ref: ObjectRef) -> SelectedAttempt`
  - `async def renew_leases(self, lease: OperationLease) -> OperationLease`
  - `async def reclaim_operation(self, writer_id: str, operation_id: str, request_digest: str) -> OperationLease | PendingVerdict`
  - `async def commit_check_if_current(self, frozen: FrozenCase, findings: RankedFindings, policy_executions: tuple[CheckPolicyExecution, ...], semantic_status: SemanticStatus, semantic_reason: SemanticReason, semantic_provenance: SemanticProvenance | None, request_id: str) -> CheckCommitResult`
  - `async def lookup_operation(self, writer_id: str, operation_id: str) -> OperationRecord | None`
- `@dataclass(frozen=True, slots=True) class AppendCommand`
- `@dataclass(frozen=True, slots=True) class AppendEntry`
- `@dataclass(frozen=True, slots=True) class AppendResult`
- `@dataclass(frozen=True, slots=True) class AcceptedEventSummary`
- imported `Frontier(sequence: int, head_digest: str)` from `domain/values.py`; this port does not
  define a duplicate frontier type
- `@dataclass(frozen=True, slots=True) class FrozenCase`
- `@dataclass(frozen=True, slots=True) class CheckPolicyExecution`
- `@dataclass(frozen=True, slots=True) class CheckVersionSlice`
- `@dataclass(frozen=True, slots=True) class CheckCommitResult`
- `@dataclass(frozen=True, slots=True) class OperationRecord`
- `@dataclass(frozen=True, slots=True) class OperationLease`
- `@dataclass(frozen=True, slots=True) class SemanticJobRecord`
- `@dataclass(frozen=True, slots=True) class SemanticAttemptHandle`
- `@dataclass(frozen=True, slots=True) class SelectedAttempt`
- `@dataclass(frozen=True, slots=True) class PendingVerdict`
- `@dataclass(frozen=True, slots=True) class StoredProjection`
- `@dataclass(frozen=True, slots=True) class ProjectionQuery`
- `@dataclass(frozen=True, slots=True) class ProjectionPage`
- frozen `ProjectionFilter` and `ProjectionPosition` variant records listed below
- `@dataclass(frozen=True, slots=True) class OperationResultLocator`
- `enum AppendWarning` — exactly `unknown_event_schema_preserved`
- `enum ProjectionView` — `compact`, `assignment`, `obligations`, `findings`, `candidate_findings`, `evidence`, `history`, `versions`
- `enum OperationKind` — `publish_work`, `check`, `respond`, `receipt`
- `enum OperationState` — `pending`, `complete`, `quarantined`
- `enum CheckPhase` — `reserved`, `local_ready`, `semantic_wait`, `ready_to_finalize`, `terminal`
- `enum AttemptOutcome` — `response_durable`, `failed`, `expired`, `late`, `selected`
- `enum OperationQuarantineCode` — `operation_kind_state_contradiction`,
  `operation_result_digest_mismatch`, `operation_event_range_mismatch`,
  `operation_resume_object_invalid`, `operation_lease_shape_invalid`
- `enum PendingVerdictKind` — `absent`, `live`, `terminal`, `quarantined`
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
- `warnings: tuple[AppendWarning, ...]` — sorted unique closed structural warning tokens, never
  protocol-error reasons or user text. v0.1 has exactly `unknown_event_schema_preserved`.

`FrozenCase` fields:

- `case: DeterministicCase` — the pure kernel case at the frozen frontier (input to
  `run_deterministic_policies` and semantic-case minimization). Its `projection` is the exact
  `ProjectionState`; its immutable `coverage_by_ref`, typed `gaps`, and `allowed_ids` are derived
  by the one pure `build_deterministic_case` helper from the authoritative accepted-record prefix.
- `lease: OperationLease` — the **current** immutable authority returned by `freeze_case`,
  `advance_check_phase`, `renew_leases`, or `reclaim_operation`. Its `frontier` is subject frontier
  `F` and its `dependency_digest` is digest `D` over the material inputs (frontier head digest,
  policy pack IDs/versions, engine version, projection version, config digest).
- `case.allowed_ids` is the closed set of event/obligation/claim/action/result/evidence/finding IDs
  present at `F`; this becomes `SemanticCase.frontier_refs`. Semantic post-validation
  later checks the union of this set plus the same-check durably pinned `local_check_refs`.
  Decisions and responses are cited through their owning event IDs. Every action/result/evidence/finding entry
  also carries a deterministic link to one or more canonical event/obligation/claim roots so a
  semantic citation can be projected into the narrower public `Finding.subject_refs` contract.

Those are the only two `FrozenCase` fields. `case.frontier == lease.frontier` is mandatory. A
terminal same-digest replay is returned as `CheckCommitResult`, not as a fake frozen case with a
nullable lease or a `replayed_result` side channel. A terminal stored failure is raised as its
stable `PublicOperationError`. Therefore every `FrozenCase` value always carries live current
authority and is safe to pass to the next compare-and-swap operation.

`CheckPolicyExecution` is the internal frozen record `(policy_id: str, policy_version: str,
outcome: Literal["run", "skipped", "failed"], reason: Literal["completed",
"material_unavailable", "not_applicable", "policy_failure", "scope_excluded"])`. The legal
outcome/reason pairs are exactly the check-result schema: `run/completed`,
`skipped/material_unavailable|not_applicable|scope_excluded`, or `failed/policy_failure`. The tuple
has one entry per requested built-in pack, in canonical pack-ID order, with no duplicate pack.

`CheckVersionSlice` is exactly `(protocol_version: Literal["0.1"], engine_version: str,
projection_version: str, policy_packs: tuple[str, ...])`; `policy_packs` is the sorted-unique
nonempty subset of `research-evidence/0.1.0|work-integrity/0.1.0` actually bound into `D`.

`CheckCommitResult` is the internal durable success/replay record with exact fields
`outcome: Literal["committed", "replayed"]`, `task_id: str`, `session_id: SessionId`,
`writer_id: str`, `request_id: str`, `subject_frontier: Frontier`, `result_frontier: Frontier`,
`verdict: CheckVerdict`, `findings: tuple[Finding, ...]`, nonnegative `suppressed_count: int`,
`policy_executions: tuple[CheckPolicyExecution, ...]`, `semantic_status: SemanticStatus`,
`semantic_reason: SemanticReason`, `semantic_provenance: SemanticProvenance | None`,
`coverage: Coverage`, and `versions: CheckVersionSlice`. It deliberately is **not** named
`CheckResult`: `protocol/models.py` owns the public `CheckResult = CheckResultModel` wire alias.
The application maps this internal record to the content-complete `CheckSuccessModel` tree,
converts integers/frontiers with the registered wire codecs, and the service-owned result
projection adds `privacy_projection` before ordinary serialization. `outcome` is internal replay
metadata and is not a check-result wire field.

`OperationRecord` fields: `writer_id`, `operation_id`, `operation_kind: OperationKind`,
`request_digest`, `state: OperationState`, `phase: CheckPhase`,
`owner_generation: str | None`, `lease_owner_id: str | None`,
`lease_generation: int | None`, `lease_expires_at: datetime | None`,
`resume_object_ref: ObjectRef | None`,
`result_canonical: bytes | None` (structural terminal envelope; assigned IDs, sequences, digests,
reason codes only), `result_digest: str | None`, `result_locator: OperationResultLocator | None`,
`quarantine_code: OperationQuarantineCode | None`, `terminal_at: datetime | None`. The enum value,
not free text, is serialized in `quarantine_code`. The five values are exhaustive for the
task-ledger `operations` row; database-, object-, and bundle-wide corruption uses the storage
recovery classification instead of inventing another operation code.

`OperationResultLocator` fields are `first_ingestion_sequence: int | None`,
`last_ingestion_sequence: int | None`, `result_object_ref: ObjectRef | None`, and
`structural_ids: tuple[str, ...]` (sorted unique, at most `MAX_EVENTS_PER_BATCH + 1`). The two
sequences are both absent or both present and ordered. It is sufficient to reconstruct a bounded
terminal replay; replay never scans an unbounded ledger.

`OperationLease` fields are `writer_id`, `operation_id`, `session_id`, `phase: CheckPhase`,
`owner_generation`, `lease_owner_id`, positive `lease_generation`, `lease_expires_at: datetime`,
`frontier: Frontier`, and `dependency_digest`. It is an immutable compare-and-swap capability;
every lease-mutating method names all owner/lease fields plus the expected phase in its update.

`SemanticJobRecord` fields are `job_id`, `writer_id`, `operation_id`, `case_digest`,
`case_object_ref: ObjectRef`, `state: Literal["queued", "leased", "succeeded", "failed",
"quarantined"]`, non-negative `attempt_count`, optional `active_attempt_id`, optional
`selected_attempt_id`, optional lease owner/generation/expiry fields, optional
`selected_result_object_ref`, optional `terminal_code: SemanticReason`, and optional
`terminal_at`. Its legal nullability families are exactly those in bundle migration `0001`.

`SemanticAttemptHandle` fields are `job_id`, `attempt_id`, positive `attempt_ordinal`,
`provider_request_id`, `writer_id`, `operation_id`, `owner_generation`, `lease_owner_id`, positive
`lease_generation`, `lease_expires_at`, `frontier`, and `dependency_digest`. It names the one
attempt authorized by the currently leased job and is never accepted after its job/operation
lease or dependency fence changes.

`SelectedAttempt` fields are `job_id`, `attempt_id`, `result_object_ref: ObjectRef`,
`selected_at: datetime`, `frontier`, and `dependency_digest`. It can be constructed only by the
atomic `select_attempt` transition; `record_attempt_outcome(..., outcome=selected)` is rejected.

`PendingVerdict` fields are `kind: PendingVerdictKind`, `operation: OperationRecord | None`, and
`retry_after_ms: int | None`. `retry_after_ms` is present only for `live`, is clamped to the
remaining lease lifetime, and is safe structural guidance rather than authority.

`StoredProjection` fields: `view: ProjectionView`, `state: ProjectionState` (or the bounded typed
view slice for list views), `frontier: Frontier` (the event frontier the cache represents),
`lag: int` (events accepted after that frontier; 0 when current), `projection_version: str`,
`rebuild_required: bool`.

The bounded row-query types are owned here, rather than by the wire model or either adapter:

- `AssignmentProjectionFilter(actor_id: str | None, include_resolved: bool | None)`;
- `ObligationsProjectionFilter(actor_id: str | None, include_resolved: bool | None,
  status: Literal["open", "resolved"] | None)`;
- `FindingsProjectionFilter(origin: Literal["deterministic", "semantic_model_derived"] | None,
  priority: int | None, disposition: Literal["none", "acknowledged", "rejected", "waived"] | None,
  include_resolved: bool | None)`;
- `EvidenceProjectionFilter(strength: Literal["mutable_reference", "metadata_only",
  "content_digest", "immutable_snapshot", "independently_reproduced"] | None,
  freshness: Literal["current", "partial", "redacted_gap", "stale_after_material_change",
  "unknown"] | None, include_unavailable: bool | None)`; and
- `HistoryProjectionFilter(schema_name: str | None, actor_id: str | None,
  after_sequence: int | None)`.

Each optional member preserves absent versus supplied, integers exclude `bool`, and all supplied
values have already passed the exact status-request bounds. `ProjectionFilter` is the union of
those five records. `compact` and `versions` require `filter=None`. `candidate_findings` has its
separate application-owned priority filter and is never admitted to `ProjectionQuery`.

`IdProjectionPosition(last_id: str)` is used only by `assignment`, `obligations`, and `evidence`.
`FindingProjectionPosition` stores the ten non-negated rank facts in exact order: `priority`,
`actionable`, `artifact_ordinal`, `immutability_ordinal`, `freshness_ordinal`,
`authorship_ordinal`, `real_check_present`, `known_gap_count`, `origin_ordinal`, and `finding_id`;
`priority` is `1..3`, `actionable`/`real_check_present` are exact `bool`, the five strength
ordinals and gap count are nonnegative non-`bool` integers, `origin_ordinal` is `0|1`, and
`finding_id` is canonical;
its comparison key is exactly the registered `rank_key` with the five strength/boolean values
negated and unsigned ASCII finding-ID bytes last. `HistoryProjectionPosition` contains only the
positive `ingestion_sequence`. Their union is `ProjectionPosition`; compact/versions require no
position. These are repository positions, never opaque cursor bytes or SQL fragments.

`ProjectionQuery` fields are exactly `session_id: SessionId`,
`view: Literal["compact", "assignment", "obligations", "findings", "evidence", "history",
"versions"]`, `filter: ProjectionFilter | None`, `requested_frontier: Frontier`, `limit: int`,
`position: ProjectionPosition | None`, and `expected_projection_version: str | None`. `limit` is
an exact non-`bool` integer in `1..100`; the expected version is absent on the first page and is
the version authenticated by the application cursor thereafter. Construction rejects a
view/filter/position mismatch before storage access.

`ProjectionItem` is the union of the exact raw (pre-client-projection) status item models owned by
`protocol/models.py`: assignment, compact, evidence, finding, history, obligation, and version
slice. The item branch must match `view`; content-bearing union leaves contain their original
bounded strings here and never an `OmittedContentModel`, because the service projects them only
after the internal result is complete.

`ProjectionPage` fields are exactly `view` (the same seven-value queryable literal),
`items: tuple[ProjectionItem, ...]`, `requested_frontier: Frontier`, `head_frontier: Frontier`,
`effective_frontier: Frontier`, nonnegative `lag: int`, `projection_version: str`,
`rebuild_state: Literal["current", "rebuild_required", "rebuilding"]`, `coverage: Coverage`,
`gaps: tuple[str, ...]`, and `next_position: ProjectionPosition | None`. `items` has at most
`query.limit` members; compact/versions have at most one and no next position. Gaps are sorted
unique. The page never carries a public cursor, request envelope, import status, result frontier,
or `privacy_projection`: `application/status.py` owns those mappings and the service owns local
disclosure projection.

### Exact `ProjectionItem` derivation

All derivation below is evaluated as of `effective_frontier`. Tuples described as canonical are
sorted by unsigned ASCII bytes and duplicate-free. Payload text is read only after the repository
has selected a bounded structural page.

- **assignment** — one item per non-tombstoned `AssignmentProjectionRecord`.
  `assignment_event_id` is the mapping/source event ID; `actor_id` is
  `assignee_actor_id`; and both `obligation_ids` and v0.1 `scope_refs` are the same exact canonical
  `obligation_ids` tuple. Scope prose, causal parents, and generic envelope refs never enter
  `scope_refs`. An assignment is handoff-superseded when a later visible assignment's transitive
  `handoff_of` chain reaches its event ID. `resolved` is true iff it is handoff-superseded or every
  named obligation has effective status `resolved`; a missing/tombstoned obligation makes that
  universal test false. This item contains no payload prose, so a non-redacted row is constructed
  entirely from its verified structural index even when payload keys are presently locked.
- **obligations** — one item per non-tombstoned obligation record. Effective `status` is
  `resolved` iff the accepted payload status is `resolved` or current `plan_change` is
  `superseded|waived`; `carried` preserves the accepted status. `description`,
  `evidence_expectation`, and optional `acceptance_criteria` come byte-for-byte from the selected
  authenticated payload. `source_refs` and `evidence_refs` are its exact canonical `source_refs`
  and `resolution_evidence_refs`. `assigned_actor_ids` is the canonical set of actors from the
  latest non-handoff-superseded assignment branches that name this obligation. `revision_event_id`
  is the later-by-ingestion event among (a) the latest same-ID obligation publication after its
  first publication and (b) the current plan-change source event; it is null when neither exists.
- **findings** — finding-owned fields are the exact decoded `Finding` fields. The current response
  is the non-tombstoned response with the greatest source frontier for that finding. With no
  response, `disposition="none"` and every response field is null; otherwise disposition,
  response event ID, reason, waiver scope, and waiver expiry are copied exactly. A recorded waiver
  remains `waived` before and after its expiry timestamp: wall clock never rewrites status,
  filtering, pagination, or resolution. A response of any disposition never resolves a finding.

  The immutable finding issue key is `(origin, policy_id, policy_version, kind,
  canonical complete subject_refs)`. A later same-key finding supersedes an earlier row. A later
  check is independently *applicable and complete* for a finding only when all of these recorded
  facts hold: its subject frontier includes the finding source event; the matching policy
  execution is exactly `run/completed`; `suppressed_count == 0`; coverage has
  `ledger_freshness=current` and no known gaps; and its normalized scope is whole-case (both scope
  tuples empty) or at least one explicitly scoped claim/obligation ID occurs in the finding's
  subject refs. A semantic-origin finding additionally requires
  `semantic_status=succeeded` with `semantic_reason=semantic_completed`. `resolved` is true only
  when a later same-key finding exists or such a later applicable complete check exists. A
  skipped/failed/capped/gapped/stale/non-overlapping check proves nothing. The newest replacement
  finding emitted by an applicable check is after that check's subject frontier and therefore is
  not resolved by the check that created it.

  This rule requires `CheckRecordedPayload` to persist the request's normalized `scope` and the
  exact `policy_executions` tuple already present in `CheckCommitResult`. The existing event shape
  without those fields cannot prove applicability; an implementation MUST NOT infer it from
  `policies`, verdict, returned IDs, or prose.
- **evidence** — identity, strength, optional captured object ID, optional content digest, and the
  structural `tree_digest`/`diff_digest` members of `subject_state` are exact accepted payload
  facts. Description/reference preserve exact present-versus-null payload meaning. `available` is
  true when no captured object was declared, or when the declared object is present, not redacted,
  and its key slot is readable; it never means a path/URL was probed. Item `freshness` is the
  weaker of the source envelope's `coverage.ledger_freshness` and snapshot projection freshness,
  further capped at `redacted_gap` when a declared captured object is unavailable. Strength is
  never downgraded or invented to explain availability; the separate fields carry those facts.
- **history** — fields come only from the accepted envelope and its locator:
  event/schema/author/publication/ingestion/occurred-at are copied exactly;
  `projection_status=projected` only for an exact supported `(schema_name, schema_version)`, else
  `unknown_unprojected`; and `summary_code` is the supported schema name or `opaque_unknown`.
  History never opens a payload object.
- **compact** — task/session IDs are route identity. `task_title` is the exact supported
  `session_opened` title. `current_plan_event_id` is the source event of the greatest visible plan
  version having no successor, or null. `open_obligation_count` counts every effectively open
  obligation plus every obligation tombstone whose prior status can no longer be proved;
  `unresolved_finding_count` counts every structurally unresolved finding plus every finding
  tombstone. The bounded summaries select at most ten ordinary rows in obligation-ID and finding
  rank order respectively. Unreadable selected summaries are omitted without backfill while the
  counts remain unchanged. Item freshness/coverage/gaps equal the page snapshot values.
- **versions** — the single item copies protocol/engine/projection/object/storage identities from
  the verified runtime/projection manifest; Python/APSW/SQLite identities from the running
  validated storage runtime; policy packs as the canonical installed enabled built-in set; and
  provider profiles as the canonical configured profile-ID set. It reads no event payload.

`CheckRecordedPayload.scope` is the exact request `CheckScopeModel` after canonical sorting; empty
claim and obligation tuples mean the whole case. `policy_executions` has one exact legal
`CheckPolicyExecutionModel` per requested pack in canonical pack order, mapped one-for-one from
the internal `CheckPolicyExecution` tuple. These are additive required v0.1 durability facts, not
status-only guesses.

### Indexed filtering, ordering, and bounded hydration

Filters are conjunctive. `include_resolved` absent and false are identical and add
`resolved=false`; true adds no resolution predicate. `include_unavailable` absent and false are
identical and add `available=true`; true adds no availability predicate. Therefore an explicit
`status=resolved` combined with absent/false `include_resolved` is a valid empty query, not an
implicit override. Actor, status, origin, priority, disposition, strength, freshness, schema, and
after-sequence filters are exact equality/range predicates over typed structural columns.

Ordering and exclusive positions are exact:

- assignment, obligation, and evidence: canonical logical ID ascending;
- findings: `priority ASC, actionable DESC, artifact_ordinal DESC, immutability_ordinal DESC,
  freshness_ordinal DESC, authorship_ordinal DESC, real_check_present DESC,
  known_gap_count ASC, origin_ordinal ASC, finding_id ASC`;
- history: positive ingestion sequence ascending; and
- compact/versions: singleton, no position.

The adapter applies snapshot visibility, filters, and the exclusive position in its indexed
structural query, reads at most `limit + 1` candidate keys, and opens payload objects only for the
first `limit` selected candidates. It never decrypts a row to decide whether that row matches.
If selected content is unreadable, the row is omitted without backfill and the exclusive position
still advances over that scanned structural candidate; the lookahead alone decides whether a next
position exists. Thus one page opens at most `limit` primary payloads plus at most one current
response payload per selected finding. Compact opens at most one title, ten obligation, and ten
finding payloads.

### Required nonplaintext structural query index

The memory and SQLite adapters maintain the same disposable, replay-derived query facts. SQLite's
canonical DDL may choose normalized table names, but the finite logical inventory is mandatory:

- snapshot versions: validity frontier interval, task-title source event, current-plan source
  event, exact open/unresolved counters, projection freshness, canonical status coverage, and
  canonical public gap codes;
- assignment versions: event ID, actor ID, optional handoff event, derived resolution, tombstone
  flag, validity interval, plus assignment-to-obligation edges;
- obligation versions: obligation ID, declared/effective status, first/latest/revision source
  events, tombstone flag, validity interval, plus source-ref and resolution-evidence-ref edges;
- finding versions: finding ID, issue-key canonical bytes, kind/origin/policy/priority, derived
  resolution, subject-frontier sequence+digest, actionable, every non-negated rank fact, exact structural
  coverage bytes, tombstone flag, validity interval, plus subject-ref edges;
- response versions: finding/response event IDs, disposition, waiver scope/expiry, tombstone flag,
  source frontier, validity interval, plus response evidence-ref edges;
- check versions: check event/subject frontier, suppression count, semantic status/reason,
  structural coverage/freshness/gap count, validity interval, plus normalized scope edges,
  policy-execution rows, and returned-finding edges;
- evidence versions: evidence ID, strength, captured object ID, content digest, structural
  subject-state digests, source-envelope freshness, derived item freshness, captured-object
  availability, tombstone flag, and validity interval; and
- accepted-history columns: session, ingestion sequence, event/schema/actor/publication/occurred-at
  identities and supported/unknown projection summary.

Intervals are `valid_from_seq <= effective_seq < valid_to_seq`, with null `valid_to_seq` meaning
current. Every update closes the prior structural version and inserts the next in the same reducer
transaction. A redaction is a global privacy override: it scrubs payload-derived columns/edges for
that source from the disposable query index, including older intervals, and leaves only the
minimal tombstone identity. A rebuild after physical deletion converges to the same shape.

Required indexes begin with snapshot visibility and then the filter/order columns above: ID views
on `(validity, resolved/available/filter fields, logical_id)` as applicable; findings on
`(validity, resolved, origin, priority, disposition,` the complete mixed-direction rank tuple`)`;
history on `(session_id, ingestion_sequence)` plus `(session_id, schema_name,
ingestion_sequence)` and `(session_id, actor_id, ingestion_sequence)`; edge tables on both owner
and target lookup orders; and check applicability on policy outcome, scope target, and subject
frontier. No index or column contains task title, scope description, obligation text, finding
summary/detail/reason, evidence description/reference, provider text, payload JSON, or arbitrary
metadata. Those values remain only in encrypted objects.

A tombstoned assignment/obligation/finding/evidence row cannot satisfy an item schema without
inventing required facts, so it is omitted and contributes `redacted_event`. A non-tombstoned
selected obligation/finding/evidence row whose authenticated event payload cannot be opened
contributes `event_payload_unavailable` and is omitted; an unreadable current response omits its
finding for the same reason. A non-tombstoned assignment remains renderable from its exact
structural index and history/versions never need payload hydration, although snapshot coverage
still discloses any broader key/unavailability gap. `include_unavailable` concerns captured
evidence objects only and never forces an event tombstone/unreadable payload into an item.
Tombstoned obligations/findings remain in compact conservative counts as described above. If the
task-title payload is unavailable the compact item itself is omitted. Page coverage is the
snapshot-wide weakest structural coverage (not merely the returned page): fold
`coverage.weakest` over every accepted envelope through `effective_frontier`, then apply the
registered caps for every normalized projection marker, captured-object unavailability, and
selected-row omission. Page gaps are the sorted unique union of those envelope gaps and the exact
public codes `unknown_event`, `redacted_event`, `redacted_object`, `missing_ref`,
`captured_object_unavailable`, and `event_payload_unavailable`; they equal the final coverage's
`known_gaps`. Overflow is an integrity/bounds failure, never truncation.

`load_projection(session_id, candidate_findings)` returns the full `ProjectionState`, never a view
slice: a deterministic rule reads the whole case, so there is no slice that could answer it. It is
the same state the check path loads. The status application also reads the authoritative accepted
record prefix through that exact frontier and calls `build_deterministic_case`; a projection cache
alone is not enough to invent per-ref coverage. `candidate_findings` is the only list view served
through `load_projection` rather than `query_projection` for that reason
(`application/status.md`, `INTERFACES.md`).

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

Returns an async iterator over `LedgerRecord` values (`AcceptedEvent | UnknownEvent`) for the
session, strictly ordered by
`ingestion_sequence`, with `after < ingestion_sequence <= through` (`through=None` = current
head). Adapters MUST paginate internally in exact `LEDGER_READ_PAGE_SIZE = 500` pages and release any
read transaction between pages. Long-lived read transactions on hot paths are prohibited by this
port. On read, the adapter re-verifies each entry: `entry_digest` matches stored canonical
bytes, and indexed columns agree with those bytes; a mismatch raises
`PublicOperationError(STORAGE_CORRUPT)` and the bundle enters quarantine. Payload decoding
(object decryption) happens lazily as iteration reaches each verified row and only after its read
transaction has been released. An exact known family/version yields a complete `AcceptedEvent`
whose `payload` is the decoded `EventPayload` when the present object is available. Every
syntactically valid unsupported schema pair yields the complete `UnknownEvent` structural record
whose available `payload` is deeply frozen strict `JsonValue`, whose
`canonical_payload_digest` is retained, and whose projection status is
`unknown_unprojected`. For either variant, redaction, `key_locked`, `key_missing`, or an otherwise
unavailable authenticated object yields that same complete record with `payload=None`; there is no
third unavailable-payload handle and no fabricated empty payload. All accepted-envelope fields,
including ancestry, refs, redaction, payload ref, and entry digest, remain populated. A malformed
known payload or an available unknown payload whose canonical digest does not verify is canonical
storage corruption, not `payload=None`.

### `load_projection` and `query_projection`

Returns the cached projection for `view`, or `None` when no cache exists (caller decides whether
to replay). Never rebuilds implicitly on the hot path. The returned `frontier` and `lag` are what
`status` discloses to callers ("served from a projection cache representing frontier X, lag N").

`query_projection` is the bounded, typed status boundary. It resolves the requested exact
frontier, validates a cursor bound to the same query/frontier/projection version, applies the
view-specific filter and stable ordering in the adapter, and returns no more than the requested
page size plus a next cursor. It never requires the application to load/filter an unbounded full
projection. The port receives only the typed `position` decoded from a cursor and returns only a
typed `next_position`; opaque cursor authentication/encoding is application work. A
`candidate_findings` query is always `INVALID_REQUEST`: that view takes the one whole-case path
through `load_projection` plus the exact accepted-record prefix and is never smuggled through a
row-query filter.

### `freeze_case`

Implements the freeze step used by `specs/src/yoetz/application/check.md`. The public method is one
call, but an adapter MUST implement these ordered internal stages; a resume-case object may never
be created before the case it contains:

1. **Existing-row decision.** In one short authoritative atomic section, look up
   `(writer_id, operation_id)`. Terminal + same digest reconstructs and returns
   `CheckCommitResult(outcome="replayed", ...)` (or raises the stored terminal public failure);
   different digest raises `IDEMPOTENCY_CONFLICT`; a valid live lease raises
   `OPERATION_PENDING`. For an expired/stale-generation pending check, require no pending import
   for this session and fenced-CAS reclaim the recorded phase. After commit, open and authenticate
   the row's existing `resume_object_ref`, verify its request, session, writer, frontier,
   dependency, and case-digest bindings, and return that stored `DeterministicCase` with the
   replacement lease.
   Reclaim never rebuilds or republishes a case. A missing, unreadable, or binding-mismatched
   stored object is fenced into `operation_resume_object_invalid`; it is never replaced by a new
   object under the old row.
2. **Prepare an absent operation.** In a bounded snapshot section, repeat the absent idempotency
   decision; require the indexed no-pending-import predicate; verify `expected_frontier`; and
   capture head frontier `F`, the active projection generation/version/state digest at `F`, and
   every version/config revision. After releasing the snapshot, canonicalize those captured
   revisions to compute dependency digest `D`. The active projection
   must already be current at `F`; projection catch-up/rebuild pure work is completed before this
   stage and is never hidden inside the reservation transaction. This stage inserts no operation,
   allocates no lease, and performs no object-store work.
3. **Build, then publish.** With no write transaction or shared state lock held, page the verified
   authoritative `LedgerRecord` prefix through immutable `F`, replay/build the exact projection
   at `F`, and call `build_deterministic_case(projection, records)`. Any missing source-envelope
   coverage, malformed typed gap, or case/projection frontier mismatch is an internal
   storage-integrity failure; the adapter never supplies a publication-channel default. Only
   after that exact case exists, canonicalize a bounded resume envelope binding request identity,
   `F`, `D`, the prepared projection identity, and the case digest; then encrypt and durably
   publish it through `ObjectStorePort`. Encryption, hashing, canonicalization, paging, replay,
   filesystem I/O, and fsync all occur outside a write transaction. A crash here leaves only an
   unreferenced encrypted object eligible for orphan GC.
4. **Final reservation.** In one short atomic write, repeat idempotency and the no-pending-import
   predicate, require that head frontier is still exactly `F`, require that active projection
   generation/version/state digest still equals the prepared identity at `F`, compare every
   current dependency revision byte-for-byte with the prepared inputs whose digest is `D`, and
   recheck `expected_frontier` and current bundle owner generation. The write transaction performs
   no case construction or object I/O; it only
   validates the already-finalized `ObjectRef` descriptor (`check_resume` kind, task/media/size,
   commitment to the exact canonical resume bytes, and envelope digest) and atomically inventories
   it while inserting the `operations` row as `pending/reserved`, referencing that object with the
   current owner, lease-generation `1`, and expiry. Any failed revalidation inserts no inventory or
   operation and never references the candidate object; a concurrent same-digest winner is handled
   by the repeated idempotency decision. Commit before policy/provider work, then return
   `FrozenCase(case, lease)` with `case.frontier == lease.frontier` and
   `lease.dependency_digest == D`.

Thus the only durable resume pointer is written after its complete object is durable, while the
object itself is produced only after its exact case is built. An ambiguous final commit is resolved
solely by repeating the idempotency lookup; the caller never guesses whether the pointer was
installed.

### Durable check orchestration and `reclaim_operation`

`advance_check_phase` permits only `reserved -> local_ready`, `local_ready -> semantic_wait`,
`semantic_wait -> ready_to_finalize`, and the direct `local_ready -> ready_to_finalize` branch
when no semantic job is needed. Only `commit_check_if_current` performs
`ready_to_finalize -> terminal`. `durable_object_ref` is required for the transition that
claims the local result/case is resumable and absent on transitions that do not bind a new object.
The method compares every field in `lease`, the expected phase, and the current owner generation;
zero rows changed is a lost lease, not success.

Every successful phase advance, renewal, or reclaim returns a replacement `OperationLease`; the
old value is spent. Before the next step the application replaces `FrozenCase.lease` with that
returned value (constructing another two-field frozen value with the identical case). The new
lease must keep the same writer/operation/session/frontier/dependency identity and may change only
the authorized phase/owner/lease generation/expiry fields for that transition. The port rejects a
`FrozenCase` whose embedded lease is not the current `ready_to_finalize` lease at final commit.

`enqueue_semantic_job` is idempotent on `(writer_id, operation_id, case_digest)` and returns the
existing byte-equal record on replay. `claim_semantic_job` can claim `queued` or fenced-expired
work only while the parent operation is `pending/semantic_wait`. `record_attempt_outcome` accepts
these exact shape pairs: `response_durable` requires `result_object_ref` and no terminal code;
`failed`, `expired`, and `late` require a `terminal_code` and accept a result object only for
`late`; `selected` is forbidden because `select_attempt` owns the atomic attempt/job selection.
`select_attempt` requires a `response_durable` current attempt and atomically writes both the
attempt's `selected` state and the job's identical selected result reference. `renew_leases`
renews the parent first and live child jobs second, never beyond the parent's new expiry.

`reclaim_operation` performs one bounded point lookup and returns exactly:

| Durable row | Same `request_digest` behavior |
|---|---|
| absent | `PendingVerdict(kind=absent, operation=None, retry_after_ms=None)` |
| complete | `PendingVerdict(kind=terminal, operation=row, retry_after_ms=None)` |
| quarantined | `PendingVerdict(kind=quarantined, operation=row, retry_after_ms=None)` |
| pending + current-generation unexpired lease | `PendingVerdict(kind=live, operation=row, retry_after_ms=bounded_remaining_ms)` |
| pending + expired or stale-generation lease | fenced compare-and-swap reclaim and return the new `OperationLease` |

A present row with a different request digest raises `IDEMPOTENCY_CONFLICT`. Any pending
non-check row or illegal phase/lease shape is terminally quarantined with its exact
`OperationQuarantineCode`; it is never repaired by guessing. The method never creates a new
operation: `freeze_case` remains the only absent-to-pending check reservation path.

### `commit_check_if_current`

Implements the final check commit used by `specs/src/yoetz/application/check.md`. In one
atomic section: verify the operation lease
(owner generation current, lease owner/generation match, unexpired) and that the dependency
revisions in `frozen.lease.dependency_digest` still hold; require again that the session has no pending
import job in this same transaction; append the `check_recorded` event and one
`finding_recorded` event per returned finding with exact coverage vectors; store the stable
canonical result; set `complete/terminal` and clear lease fields; commit; only then return
`CheckCommitResult(outcome="committed", ...)`.

The final-currentness outcome is singular. If either the current head frontier differs from
`frozen.lease.frontier` or any material revision makes current `D` differ from
`frozen.lease.dependency_digest`, this same transaction appends no event, stores the stable
terminal `FRONTIER_CONFLICT` failure envelope (safe `reason_code=frontier_changed` or
`dependency_changed`), clears the operation lease, and commits; the method then raises that stable
public error. Same-digest retry replays the same terminal failure, and a caller that wants a check
of the new state uses a new request ID. The adapter never conditionally rewrites caller findings,
re-ranks inside storage, or chooses between stale success and conflict. A semantic result already
classified `stale` for a wrong/late attempt can still be part of an otherwise current deterministic
commit, but no candidate derived under a now-stale frozen dependency can be published.

A lost lease instead raises `OPERATION_PENDING` (another owner) or resumes via reclaim rules; it
does not terminalize the operation because another current owner may still be working.

The adapter validates the `CheckCommitResult` field bounds, every policy outcome/reason pair, the
closed semantic status/reason pair, and final-provenance presence rules before
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
  `OPERATION_PENDING`, `FRONTIER_CONFLICT`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`, `STORAGE_CORRUPT`,
  `MIGRATION_REQUIRED`; `reclaim_operation`: `IDEMPOTENCY_CONFLICT`, `BUNDLE_BUSY`,
  `STORAGE_UNSAFE`, `STORAGE_CORRUPT`, `MIGRATION_REQUIRED`; `query_projection`:
  `INVALID_REQUEST`, `SESSION_NOT_FOUND`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`, `STORAGE_CORRUPT`,
  `MIGRATION_REQUIRED`; other reads: `SESSION_NOT_FOUND`, `BUNDLE_BUSY`, `STORAGE_UNSAFE`,
  `STORAGE_CORRUPT`, `MIGRATION_REQUIRED`.
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
8. Every `FrozenCase` embeds exactly one current `OperationLease`; a spent lease is never reused or
   represented as nullable authority.
9. `candidate_findings` is the sole whole-case read exception and can never enter
   `query_projection`.

## Tests

- `specs/tests/conformance.md`: dual-adapter suite (memory vs SQLite) over golden fixtures —
  append/replay identity, exact `AppendWarning` identity/order, idempotency table rows, frontier
  conflicts, complete known/unknown ledger records including unavailable `payload=None`, batch
  atomicity, active frozen-case lease handoff, typed page positions with `candidate_findings`
  rejected from `ProjectionQuery`, the one terminal dependency-stale conflict, projection
  incremental/full equivalence, and import-start/finish races at check freeze/finalization and
  receipt append.
- `specs/tests/integration.md` and `specs/tests/subprocess.md`: kill matrix points 5–11; busy/full-disk/readonly;
  digest-verification-on-read corruption fixtures.
- `specs/tests/property.md`: Hypothesis state machine (append/retry/reuse-key/kill/reopen/replay)
  asserting the reference model, not the SQLite implementation.

## Open questions

None.
