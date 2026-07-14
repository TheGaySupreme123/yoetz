# src/yoetz_core/ports/importer.py — bounded Codex JSONL capture, mapping, and durable import resume

**Wave:** B (definition) / D (adapter and support workflow) | **ADRs:** ADR-001, ADR-002,
ADR-003, ADR-004, ADR-005 | **Imports (spec-tree):** `protocol/models.md`,
`protocol/canonical.md`, `protocol/coverage.md`, `protocol/errors.md`, `domain/events.md`,
`domain/values.md`, `ports/ledger.md`, `ports/objects.md` | **Imported by:** `ports/runtime.md`,
`application/import_review.md`, `adapters/importers/codex_jsonl.md`, memory/SQLite import adapters

## Purpose

`ImporterPort` is the task-scoped boundary for importing the documented public
`codex exec --json` stream without treating it as a complete private trace. It durably captures
the exact bounded source, parses it through a pinned mapping profile, preserves malformed/unknown
material as explicit gaps, allocates a crash-stable multi-batch plan, and records batch/report
progress around the normal `publish_work` ledger path.

The port does **not** append events itself, inspect a live repository, call a model, or become an
MCP tool. Application code obtains each durable planned batch, publishes it through the same event
validation/append contract as cooperative work, then reports the accepted result back to this port.

## Public surface

- `class ImporterPort(Protocol)` with async methods:
  - `capture(value: ImportCaptureInput) -> CapturedImportSource`
  - `reserve_or_resume(command: ImportCommand, source: CapturedImportSource) -> ImportAllocation`
  - `prepare_plan(allocation: ImportAllocation) -> PreparedImportPlan`
  - `publish_plan(allocation: ImportAllocation, plan: PreparedImportPlan) -> ImportAllocation`
  - `next_batch(allocation: ImportAllocation) -> ImportBatchSelection`
  - `record_batch(allocation: ImportAllocation, batch: ImportBatch, result: AppendResult) -> ImportAllocation`
  - `prepare_report(allocation: ImportAllocation, report: EncryptedImportReportRef) -> ImportAllocation`
  - `publish_report(allocation: ImportAllocation, report: EncryptedImportReportRef,
    evidence_result: AppendResult) -> ImportAllocation`
  - `status(session_id: str) -> ImportStatusSnapshot`
  - `complete(allocation: ImportAllocation) -> ImportAllocation`
  - `quarantine(allocation: ImportAllocation, reason: ImportSafeReason) -> None`
  - `load_review_source(identity: ImportSourceIdentity, through: Frontier) -> ImportReviewSource | None`
- `class ImportByteSource(Protocol)` — one-shot bounded async byte source with optional declared
  size and an idempotent close; its representation is redacted and contains no path.
- Frozen values `ImportCaptureInput`, `CapturedImportSource`, `ImportSourceIdentity`,
  `ImportCommand`, `ImportAllocation`, `ImportLineOutcome`, `ImportEventCandidate`, `ImportGap`,
  `PreparedImportPlan`, `ImportBatch`, `ImportBatchSelection`, `EncryptedImportReportRef`,
  `ImportStatusSnapshot`, `ImportReviewSource`, and `ImportSafeReason`.
- Enums `ImportAllocationOutcome`, `ImportState`, `ImportPhase`, and `ImportLineStatus`.

All are internal shared registry types. `ImportReport` and `ReviewResult` remain application/
protocol support results; neither is a seventh public workflow model.

## Behavior

### Type contracts

`ImportCaptureInput` is a one-shot sensitive value containing the JSONL `ImportByteSource`,
optional separately captured stderr source, asserted Codex version, raw argv input for sanitizer
consumption, working-directory identity input, exit status, capture time, and source kind
(`file` or `stdin`). Its `repr`/`str` is a constant redacted marker. The application/CLI never
places a filesystem path into a structural result or log.

`CapturedImportSource` contains:

- `source_object: ObjectRef` (`ObjectKind.import_source`), its exact keyed
  `source_commitment: hmac-sha256:…`, byte count, line count, and final-newline flag;
- `metadata_digest` plus bounded safe metadata (Codex capability-profile ID/version, exit status,
  source kind, stderr present/truncated status); sanitized argv/cwd material, if retained, is in an
  encrypted metadata object and only its object ID/keyed commitment is structural. The structural
  `metadata_digest` covers only the listed safe fields, not encrypted audit/argv/cwd content;
- required verified `capture_metadata_object: ObjectRef`
  (`ObjectKind.import_source_manifest`; despite the name it describes this one exact object, not
  chunks); bounded stderr captured-byte count, truncation, and kind-domain-separated keyed
  commitment (no stderr object in v0.1);
- no raw source line, argv value, cwd/path, stderr text, object plaintext, or key identifier.

The ordinary `sha256:` audit digest of exact source/stderr bytes is retained only inside the
encrypted capture-metadata/report object. It is never a structural column, log field, object name,
or safe terminal-envelope value: using it there would create a dictionary oracle for low-entropy
private input. Structural equality uses the kind-domain-separated `K_commit` commitment produced
by `ObjectStorePort`.

`ImportSourceIdentity` is `(task_id, source_commitment, codex_capability_profile_id,
mapping_version)` plus the canonical digest of that tuple. It identifies the logical observation
set. Encryption/object IDs and capture time are excluded, so re-encrypting the same supported
stream does not produce a second history. A different capability/mapping version is not silently
deduplicated because it may interpret categories differently.

`ImportCommand` contains validated session/requesting-writer/request IDs, canonical logical request
digest, `ImportSourceIdentity`, and requested mapping profile. The digest covers target identity,
exact source identity, bounded semantic capture metadata, and mapping version; it excludes source/
plan/report object IDs, encryption randomness, allocated batch/event IDs, lease values, and
ledger-assigned frontiers.

`ImportAllocation` fields:

- `outcome: reserved | resumed | replayed`, source identity, session ID, this alias's
  `requesting_writer_id`/request ID, and the job's immutable `publishing_writer_id`;
- `state: pending | complete | quarantined` and `phase: source_reserved | plan_ready |
  publishing | report_ready | report_published | terminal`;
- owner generation, lease owner/generation/expiry while pending;
- source object/commitment, optional plan digest/object refs, batch/completed counts, optional
  report object/digest and safe terminal result;
- preallocated report request/event/evidence IDs after `plan_ready`; at `report_ready` and later,
  the exact decoded safe `report_evidence_draft` plus its canonical structural bytes/digest;
- `replayed_report` only for terminal identical-source/idempotent replay.

`PreparedImportPlan` is an immutable plan with source/mapper identities, ordered line outcomes,
ordered candidate/gap counts, finalized encrypted per-batch plan objects, batch request IDs,
event/payload logical IDs, and one canonical `plan_digest` over its safe structural manifest
(including keyed plan-object commitments, never candidate plaintext). Structural persistence
stores only IDs/digests/counts/object refs/ranges; candidate payload/user text remains in encrypted
plan objects.

`ImportLineOutcome` records 1-based line ordinal, exact byte start/end, `ImportLineStatus`
(`mapped`, `unknown`, `malformed`, `oversized`, `unsupported`), public Codex category when known,
candidate indexes, and an allowlisted safe gap code. It never stores source text structurally.

`ImportEventCandidate` binds one preallocated event ID and any required payload logical IDs to the
source line/range, target Yoetz schema/version, source category, intended refs, conservative
coverage, and the encrypted batch-plan object that carries its payload. `ImportGap` binds a stable
gap code and coverage weakening to the source object/range. Malformed bytes are referenced in the
already encrypted exact source; they are not copied into SQLite or error text.

`ImportBatch` contains batch index/count, stable batch `request_id`, ordered stable event IDs, the
verified/decrypted `EventDraft` values for this call, plan object/digest, and gaps associated with
those source ranges. It is bounded by both `MAX_EVENTS_PER_BATCH` and
`MAX_CANONICAL_REQUEST_BYTES` before it leaves the port.

`ImportBatchSelection` contains the refreshed `ImportAllocation` after lease renewal and
`batch: ImportBatch | None`. `None` means the refreshed manifest has no remaining batch; callers
must carry the returned allocation into report publication rather than reuse a stale lease value.

`EncryptedImportReportRef` contains finalized report `ObjectRef` (using
`ObjectKind.import_report`), canonical report digest, and safe structural terminal result bytes/
digest. The bytes contain only IDs, digests, counts, frontiers, coverage/gap codes, and versions.

`ImportReviewSource` is an immutable, bounded, read-only snapshot for one verified source identity
at a caller-supplied `Frontier`. It carries job state/phase, publishing writer, source/plan/report
refs and safe commitments/digests, completed batch append results, mapped event IDs, safe line
outcomes/gaps, coverage, versions, and `import_incomplete` when nonterminal. It contains no source/
payload/stderr/
argv/cwd plaintext. It is not a projection or a new public result type.

`ImportStatusSnapshot` is the session-wide bounded structural view used by public status and
operation gates. It contains active/terminal job counts, each active job's identity digest/phase/
completed-vs-total batch count, and terminal report evidence locators up to the status page cap.
It contains no raw capture metadata, source text, filenames, paths, or payloads.

### Exact bounded capture

1. Validate the pinned Codex capability profile and capture metadata shape before reading.
   Unsupported versions fail explicitly; no closest-version parser is selected.
2. Consume source chunks once while counting before allocation. The v0.1 exact-source cap is
   `MAX_OBJECT_PLAINTEXT_BYTES` (4 MiB). A declared larger source fails before reading; an unknown-
   size stdin/source is buffered with cap-plus-one detection and then closed. It is not drained
   unboundedly after rejection.
3. Compute SHA-256 over the exact bytes, including original line endings/final partial line, only
   for encrypted audit metadata; count lines without normalizing; finalize one encrypted
   `import_source` object and use its keyed commitment structurally. ADR-004 explicitly defers
   chunked objects, so v0.1 rejects a larger source with `LIMIT_EXCEEDED` rather than claiming full
   import from digest-only metadata. Importing it later requires a reviewed object-chunking
   amendment and new format.
4. Parse/sanitize argv and cwd metadata into an encrypted bounded metadata object as policy allows.
   Raw argv is consumed only by the sanitizer; removed secret values are never retained. Structural
   metadata keeps only safe category/version/commitment/size fields. Stderr is never mixed into
   JSONL and raw stderr is not retained in v0.1. A cap-plus-one read records presence,
   captured-byte count, truncation status, and a non-publishing `ObjectKind.import_stderr` keyed
   commitment over the retained prefix before discarding it. Its ordinary audit digest stays in
   encrypted capture metadata; truncation forbids presenting either value as the whole stream.
5. Return only after all returned object refs are durably finalized. Failure/cancellation may
   leave temp/orphan encrypted objects but creates no import allocation or event.

Reimport must capture before it can know the exact source commitment; duplicate captures are
harmless orphans once the durable source-identity lookup selects the first accepted job.

### Reservation, source-level idempotency, and leases

The durable model has two conceptual structural tables implemented identically by memory/SQLite:

- request aliases keyed by `(requesting_writer_id, request_id)` with request digest and
  source-identity digest;
- one import job keyed uniquely by `ImportSourceIdentity.identity_digest`, plus ordered batch rows.

`reserve_or_resume` uses one short generation-fenced transaction:

1. Verify the task-scoped importer matches command session/writer and the captured source fields
   exactly reproduce the claimed identity.
2. Existing request alias + different digest → `IDEMPOTENCY_CONFLICT`; same alias follows its job.
3. Existing unique source job terminal → insert/verify the alias and return `replayed` with the
   original report; no event or new plan is created, including when this is a new request ID or
   requesting writer.
4. Existing pending job: a different `requesting_writer_id` from the frozen publishing writer →
   `OPERATION_PENDING` without exposing that writer ID. For the publishing writer, current owner
   generation plus unexpired lease → `OPERATION_PENDING`; expired **or stale owner generation** →
   fenced CAS reclaim, increment lease generation, and return `resumed` at the recorded phase. The
   alias points to that same job.
5. No job: insert request alias and `pending/source_reserved` job carrying original source refs,
   identity, safe metadata digest, `publishing_writer_id=requesting_writer_id`, current owner/lease
   generations and expiry; commit and return `reserved`.

The source identity, not object ID, request ID, or requesting writer, provides same-stream
deduplication. A job's `publishing_writer_id` is frozen at first reservation because changing it
would change writer-chain/event identity. A later alias from another active writer may replay a
terminal job but cannot resume a pending one or borrow the original writer's authority. Recovery
targets the frozen publishing writer explicitly and never replans under a new writer. A new request
with the same source identity but different nonmapping capture metadata replays the original
logical history and reports a bounded `source_already_imported_metadata_differs` warning; it never
creates different events. Which alternate safe metadata is retained is an open policy below.

Every mutating method verifies job state/phase, task/session membership, the immutable publishing
writer, current bundle owner generation, lease owner/generation, and expiry by CAS. Live lease
means pending; expiry or stale generation permits resume. Generation loss immediately fences plan
publication, batch recording, report publication, and completion.

### Parse and prepare a durable plan

`prepare_plan` is permitted only at `source_reserved` and performs no SQLite write transaction:

1. Open/authenticate the original source object and recheck exact keyed commitment/size.
2. Parse one bounded line at a time using strict UTF-8/JSON and the exact Codex capability profile.
   Retain ordinal/range for known, unknown, malformed, truncated-final, and oversized lines. One
   bad line does not discard independent valid lines; raw parser text never becomes a safe reason.
3. Apply the versioned mapping table. Create a known `EventDraft` candidate only when every
   required field is supported. Never fabricate missing outcomes, IDs, evidence strength, actor
   identity, causal parents, final file state, or source support. Otherwise create a versioned
   imported observation/unknown-unprojected candidate or an `ImportGap`, as the event registry
   permits.
4. Clamp every candidate to `publication_channel=codex_jsonl_import`, artifact observation no
   stronger than `import_observed`, and authorship no stronger than `harness_observed` where the
   stream truly observed it. Durable author is `importer`; observed agent/model labels remain
   payload context.
5. Allocate all candidate-batch request/event/payload logical IDs plus the final import-report
   request/event/evidence IDs once for this provisional plan. Preserve source order and partition
   deterministically at both public batch caps. Finalize one encrypted `import_plan` object per
   batch; each contains only that batch's candidates/line outcomes and is itself below the object
   cap. Build the bounded structural manifest and `plan_digest`.

`publish_plan` then uses one short transaction to reverify source identity/lease/phase and insert
the plan digest plus every batch row (`planned`, stable request/event IDs, plan object ref/digest)
atomically, advancing `source_reserved → plan_ready`. A crash before commit abandons provisional
IDs/objects and may plan afresh. A crash after commit must reopen the stored plan and reuse all IDs;
a different plan under `plan_ready` is contradiction, never last-write-wins.

### Publish batches through the normal ledger path

`next_batch` selects the lowest-index noncomplete batch for `plan_ready`/`publishing`, after one
short CAS authenticates its plan object/digest, checks the job lease, and renews expiry for the
fixed local-publication budget. It returns the refreshed allocation with that batch. It never
skips a batch, returns more than the caps, or reads a later regenerated plan; no remaining batch
returns a selection whose `batch is None`. If the safe lease window cannot be renewed to cover the
budget, it raises `OPERATION_PENDING` before exposing payloads and does not begin a publish under
an expiring lease.

The application converts the returned value into the importer-channel `PublishWorkRequest` and
calls the ordinary publication use case. The importer never directly inserts event/object/
projection rows. Global ledger events from other writers may interleave; source order is preserved
by this import writer's stable batch/event order, not by claiming exclusive global ingestion.

`record_batch` executes one short transaction:

1. Verify the batch belongs to the allocation and the `AppendResult`'s ordered event IDs exactly
   equal the plan; verify result digests/frontiers are structurally valid.
2. If the row is already complete with the same result, return idempotently. Different accepted
   identity/result is corruption/contradiction.
3. Store canonical structural append result/digest and first/last frontier; mark the batch complete;
   advance `plan_ready → publishing` on the first completion; increment exact completed count.

A crash after ledger commit but before `record_batch` leaves the row planned. Retry returns the
same batch request/event IDs; `publish_work` replays the terminal append, then `record_batch`
heals the manifest. Thus multi-batch import is not globally atomic, but every visible batch is
individually atomic and duplicate-free. Partial imports remain explicitly pending/gapped.

### Report publication, terminal replay, and quarantine

After `next_batch` returns a selection with `batch is None`, the application carries its refreshed
allocation forward, builds the deterministic `ImportReport` from the stored source/plan/batch
outcomes, and finalizes its encrypted object. Before any ledger append, `prepare_report` verifies
all planned candidate batches are complete and the report's source/plan/batch commitments,
digests, counts, and frontiers match. One short CAS stores that exact report ref/digest plus the
canonical safe `evidence_recorded` draft identity under the preallocated request/event/evidence
IDs, renews the lease, and advances `publishing` (or zero-batch `plan_ready`) to `report_ready`.
A crash before this CAS may orphan the report object; a retry may rebuild it. After this CAS, retry
must reopen the stored report/draft from the returned/resumed allocation and may not substitute a
newly encrypted object. That draft is safe structural data only: fixed schema/IDs, report object ID
and content digest, importer actor/channel, observation time, conservative coverage/gap codes, and
no report/source text.

From `report_ready`, the application publishes that ordinary importer-authored
`evidence_recorded` event with `evidence_kind=import_report`, `strength=immutable_snapshot`, and
the stored report object/digest. This uses `publish_work`, so completed import evidence and its gaps
belong to a replayable ledger frontier. `publish_report` revalidates the stored report and verifies
that `evidence_result` accepted or replayed exactly the reserved final event; one short CAS stores
the append result/evidence locator and advances `report_ready → report_published`. A crash after
the ledger append but before this CAS reuses the stored object and exact publication identity, so
`publish_work` replays instead of conflicting on a new object ID.

`complete` reopens/verifies the report object and, in one short transaction, rechecks lease/source/
plan/batch/report identity, sets `complete/terminal`, clears lease fields, and commits. Only then
may import acknowledge. Same-source/new-request or same-request replay returns that original report
object/digest/frontiers; it never reparses or rebuilds against later ledger state.

`status(session_id)` performs a bounded structural read and returns `ImportStatusSnapshot`. Public
status composes it with the projection page. Check/receipt may use it as a bounded early UX gate,
but this read is not a correctness boundary: the ledger adapter repeats the session's
no-pending-import predicate atomically inside check freeze/finalization and receipt append. A
failed predicate returns retryable `OPERATION_PENDING`, so no acknowledged check/receipt can race
through a halfway import.

`quarantine` is reserved for verified source/object/plan/batch identity contradiction, impossible
phase transition, or irreconcilable commit ambiguity. It stores only an allowlisted
`ImportSafeReason.code`, safe terminal envelope/digest, clears leases, and sets
`quarantined/terminal`. Malformed/unknown source lines, unsupported categories, missing artifact
evidence, ordinary key/storage failure, or partial progress are normal gap/pending outcomes and do
not quarantine the job.

### Read-only review snapshot

`load_review_source` resolves only the exact task-scoped `ImportSourceIdentity`; it never searches
filenames, cwd values, source text, or similar reports. It reads job/batch rows, authenticates every
referenced source/plan/report object needed for the snapshot, and verifies their digests and IDs
against the structural manifest. Missing identity returns `None`; contradiction is
`STORAGE_CORRUPT`. A quarantined job is not review evidence and also returns the stable
`STORAGE_CORRUPT` classification rather than exposing possibly contradictory rows.

The supplied frontier must be at or after the last event of every completed batch selected into
the snapshot. A complete job requires all batches and its final report-evidence event to be at or
before it. A pending job may
return only its already completed batches and safe planned gaps, explicitly marked
`import_incomplete`; it never exposes an event planned but not durably accepted as observed. A
frontier that cuts a selected batch/report history is `INVALID_REQUEST`, not silently rounded.
The method acquires no job lease, mutates no state, and returns bounded safe mapping facts; review
loads accepted event payloads through the ordinary ledger/object paths at that same frontier.

### Review boundary and artifact inspection deferral

The v0.1 review authority requires comparison with “repository/artifact evidence,” but does not
require this port to inspect a live repository. Review consumes `evidence_recorded`, captured
object/digest refs, and imported file-change observations already present at the frozen frontier.
An absent final content capture is an explicit coverage gap, never a reason to run Git/shell/files
implicitly.

Therefore no `ArtifactInspectionPort` is added for v0.1. Live Git/worktree/filesystem inspection is
deferred until an ADR fixes consent, allowed roots, symlink/submodule behavior, command execution,
size/redaction policy, subject-state digest semantics, and packaging tests. If later approved, that
port captures evidence first; the deterministic review still consumes recorded evidence rather
than ambient mutable state.

## Errors and edge cases

- Unsupported Codex profile/mapping, invalid metadata/source handle, bad source identity, or unsafe
  mapping request → `INVALID_REQUEST`; exact-source, bounded stderr-capture, aggregate line-count,
  manifest, or batch-count cap → `LIMIT_EXCEEDED` before plan publication.
- One line/candidate above its semantic or event-size cap becomes an `oversized` line outcome and
  explicit gap when the exact source range is retained; it never becomes a partial known event or
  makes independently bounded lines disappear.
- Same request ID/different digest → `IDEMPOTENCY_CONFLICT`; live job lease →
  `OPERATION_PENDING`; writer/runtime contention → `BUNDLE_BUSY`.
- Object/key/generation/schema/canonical failures map through `STORAGE_UNSAFE`,
  `STORAGE_CORRUPT`, and `MIGRATION_REQUIRED`. No raw parse/provider/filesystem exception is public.
- Cancellation before reservation leaves only possible orphan capture objects. After reservation,
  phase/lease decides resume. Cancellation after a batch append is healed by same-batch replay;
  after report preparation reuses the stored object/draft; after evidence append is healed by
  publish replay; after terminal commit it replays the report.
- Empty valid stream is permitted only if policy confirms: it creates a zero-candidate plan plus
  `empty_public_stream` gap and an honest report, not evidence no work occurred.
- Unknown/malformed/oversized lines retain byte ranges in the encrypted source and safe gap codes.
  They are never dropped, coerced to the nearest known event, or echoed in errors/logs.

## Invariants

1. Every imported candidate/gap maps to an exact byte range in one verified encrypted source.
2. One source identity produces one stable plan/event set; new request IDs and crashes never create
   a different logical history.
3. Every imported ledger batch uses the ordinary publication path and stable stored IDs.
4. Imported authorship/observation/immutability never exceeds what the public stream proves.
5. Structural import state contains only bounded IDs, keyed commitments, safe digests, counts,
   ranges, enums, versions, frontiers, and safe reason codes—no source/argv/cwd/stderr/payload
   plaintext or raw source/stderr audit digest.
6. Acknowledgement follows terminal report commit; timeout/cancellation never proves failure.
7. The importer performs no network/model/live-repository work and is not an MCP workflow tool.

## Tests

- `specs/tests/unit.md`: exact byte/line accounting, strict parser profiles, every mapping category,
  malformed/unknown/truncated/empty lines, metadata sanitization, coverage clamping, batch partition.
- `specs/tests/conformance.md`: memory/SQLite import state-machine parity; same source/new request,
  same request/different digest, exact planned IDs, batch replay, report-ready object/draft replay,
  terminal report identity, and byte-equivalent review snapshots.
- `specs/tests/integration.md`: 1/100/101 event boundaries, 1 MiB request boundary, exact 4 MiB
  source/cap-plus-one rejection, live writer interleaving, partial-import review gaps.
- `specs/tests/subprocess.md`: kill after capture/reservation/plan objects/plan commit/every batch
  append and record/report prepare/evidence append/report publish/terminal commit; resume produces
  one event set/report without an idempotency conflict.
- `specs/tests/integration.md`: source/argv/cwd/payload canaries and raw SHA audit digests absent
  from DB/WAL/SHM/logs/errors/reprs; approved source/metadata/payload values appear only in
  authenticated encrypted objects, raw stderr is retained nowhere, and keyed commitments remain
  stable across re-encryption.
- `specs/tests/property.md`: arbitrary line outcomes and kill/retry sequences preserve source-range
  coverage, monotonic phases, exact completed count, and no duplicate event ID.

## Open questions

None.

The report checkpoint, tested-version mapping, first-capture authority, no raw-stderr
retention, frozen publishing writer, and release limits are binding v0.1 decisions above and in
the concrete memory/SQLite importer specs.
