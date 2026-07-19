# src/yoetz/application/import_review.py — crash-safe Codex JSONL import and comparative review support

**Wave:** D | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-005 | **Imports
(spec-tree):** `protocol/models.md`, `protocol/canonical.md`, `protocol/coverage.md`,
`protocol/errors.md`, `domain/events.md`, `domain/values.md`, `kernel/projections.md`,
`ports/runtime.md`, `ports/importer.md`, `ports/ledger.md`, `ports/objects.md`,
`ports/clock.md`, `ports/ids.md`,
`application/publish_work.md`, `application/check.md`, `application/unit_of_work.md` |
**Imported by:** `application/service.md`, `cli/app.md`

## Purpose

Import/review is a support workflow for comparing the public `codex exec --json` observation
stream with cooperative Yoetz publication and recorded artifact evidence. Import preserves exact
source material, maps only what the public stream actually supports, and makes unknown/malformed
material an explicit gap. Review freezes the combined record and routes disagreements through the
same deterministic checking machinery. Neither surface overwrites live history or becomes a
seventh/eighth public workflow operation or MCP tool.

## Public surface

- `async execute_import_codex_jsonl(app: Application, request: ImportCodexJsonlRequest) -> ImportReportInternal`.
- `async execute_review(app: Application, request: ReviewRequest) -> ReviewInternal`.

The module owns frozen `ImportCodexJsonlRequest`, `ImportReportInternal`, `ReviewRequest`,
`ReviewCounts`, and `ReviewInternal` values. The two internal results are sink-independent and
serialize the exact support-success body fields except `privacy_projection`; only the central
application facade performs the one client-specific projection. `ReviewInternal` carries the
structural nested check result, also without an embedded projection; the facade reuses its one
projection record at both schema-required locations without making a second disclosure decision.

These are application support methods invoked by the CLI commands `yoetz import codex-jsonl`
and `yoetz review`. They are not registered in the six-tool MCP registry. The application
routes the exact task through `BundleRuntimePort` and uses its task-scoped `ImporterPort`;
application code never imports a concrete Codex parser, manifest repository, or filesystem path
adapter.

## Behavior

### Capture and identify one source

1. The CLI opens the named file/stdin through an `ImportByteSource` and passes a validated
   `ImportCodexJsonlRequest`, session/writer identity, request ID, capture metadata, and limits.
   The request requires exact stderr-absent constants (`false`, `0`, `false`); the application
   rejects any legacy/crafted true branch before constructing `ImportCaptureInput`, which has no
   stderr source. Resolve
   the task with `app.runtime.route(..., access=write)` and use `TaskRuntime.importer`; never infer
   a task from source cwd/transcript path.
2. Call `importer.capture`, which preserves the exact byte sequence in one encrypted
   `import_source` object before importing observations. Its kind-domain-separated keyed
   commitment is the structural identity; the ordinary exact-byte SHA-256 audit digest stays only
   inside encrypted capture/report content. ADR-004 forbids chunked objects in v0.1, so an exact
   source above `MAX_OBJECT_PLAINTEXT_BYTES` (4 MiB) is `LIMIT_EXCEEDED`; digest-only metadata is
   insufficient for an import that promises retained source bytes.
3. Capture only bounded metadata: pinned Codex version/schema capability, capture time, sanitized
   argv, stable working-directory identity, exit status, and the stderr-absent constants. Sanitized
   cwd/argv audit material stays encrypted; values removed as secrets are not retained. Structural
   reports use keyed commitments/safe categories. Raw stderr, a caller-invented stderr commitment,
   and a confidential stderr channel are absent from the v0.1 import contract.
4. Import source identity is `(task_id, exact_source_commitment, codex_capability_profile_id,
   importer_mapping_version)`. Reimporting that identity under a new CLI request resolves the same
   durable plan/observation set. Same request ID with different source/metadata semantics is
   `IDEMPOTENCY_CONFLICT`.

### Parse and map conservatively

At a `source_reserved` allocation, call `importer.prepare_plan`. It parses line by line with a hard
line cap and the exact captured Codex capability profile, retaining source ordinal/byte range for
every `ImportLineOutcome`. One malformed/truncated/unknown line does not invalidate independently
parseable lines, but nothing is silently skipped.

| Public Codex JSONL category | Candidate Yoetz observation | Required limitation |
|---|---|---|
| thread/turn lifecycle and top-level errors | `codex_jsonl_observation@1.0.0` opaque candidate plus gap | never becomes a Yoetz session lifecycle/error fact; public IDs do not prove Yoetz task/session/writer identity |
| command execution | `action_recorded` and, only with a terminal outcome, `result_recorded` candidate | exit/output/truncation and subject state remain separate facts |
| file change | edit `action_recorded` and terminal `result_recorded` candidate | reported path/kind is not final content, subject state, or evidence; always records `file_content_not_captured` |
| MCP tool call | other `action_recorded` and terminal `result_recorded` candidate | server/tool labels are descriptive; arguments/results are not admissible evidence or server authentication |
| collaboration tool call | other `action_recorded` and terminal `result_recorded` candidate | thread/agent labels are unverified and no assignment/obligation authority is invented |
| model message or reasoning | `codex_jsonl_observation@1.0.0` opaque candidate plus gap | never becomes a claim; text lacks claim identity, assertion role, assurance, and subject-state support |
| todo list | `codex_jsonl_observation@1.0.0` opaque candidate plus gap | never becomes a plan; it lacks Yoetz plan version/scope/approval/authority |
| web search | research `action_recorded` and completed execution `result_recorded` candidate | occurrence/query is observed, but sources/support are not captured; always records `web_results_not_captured` |
| unknown/malformed/oversized line | exact encrypted source range, `codex_jsonl_observation@1.0.0` opaque candidate where representable, plus explicit gap | no projection meaning or nearby family is invented |

Mapping produces bounded `EventDraft` candidates only when every required known-family field can be
supported. Otherwise it produces a versioned imported observation/unknown-unprojected record and
gap rather than fabricating missing action IDs, outcomes, evidence strength, causal parents, or
subject state. Original source ordinal/range and source object reference remain attached.

All accepted observations:

- use publication channel `codex_jsonl_import` and artifact observation no stronger than
  `import_observed`;
- are authored by the `importer`; an observed model/agent label is payload context, not durable
  authorship;
- receive `harness_observed` assurance at most, and only where the public stream actually observed
  the transition;
- never masquerade as `cooperative_mcp`/`local_cli`, never upgrade content to independently
  reproduced evidence, and never rewrite an existing event.

### Crash-safe deterministic batching

1. Build `ImportCommand` and call `importer.reserve_or_resume` immediately after capture. Terminal
   source identity returns the original report; a live lease returns `OPERATION_PENDING`; expired
   or stale-generation work resumes its recorded phase. The allocation freezes the first job's
   `publishing_writer_id`; a same-source alias from another writer may replay a terminal report but
   cannot resume the pending job or regenerate its plan under that new writer.
2. At `source_reserved`, call `prepare_plan`, then replace the current allocation with the value
   returned by `publish_plan`. The latter atomically stores the exact source/plan identity,
   encrypted batch-plan refs, counts, all candidate-batch IDs, and the final import-report
   request/event/evidence IDs before the first event append. A recorded phase is a lower bound;
   every resume reopens/verifies the stored objects.
3. An import may exceed `MAX_EVENTS_PER_BATCH`/`MAX_CANONICAL_REQUEST_BYTES` even though the exact
   source is at most 4 MiB. The plan partitions deterministically within both public caps; each
   encrypted plan object remains under the object cap.

For each `ImportBatchSelection` returned by `importer.next_batch` in source order, replace the
current allocation with `selection.allocation`; when `selection.batch is None`, leave the loop and
carry that refreshed allocation into report publication. Otherwise:

1. Use the returned verified stored plan and stable IDs; never reparse into new identities.
2. Build a valid importer-channel `PublishWorkRequest` within both caps, preserving source order,
   stable IDs, and the allocation's immutable `publishing_writer_id`. Only that writer may resume
   a pending job; a different same-task writer may replay only after terminal completion.
3. Execute through the same `publish_work` preparation and `LedgerPort.append_batch` path as any
   event ingress. No importer writes repository rows directly or bypasses event/reference/schema
   validation.
4. Pass the terminal/replayed `AppendResult` to `importer.record_batch` and replace the current
   allocation with its return value. A crash after ledger commit but before this CAS repeats the
   same publication and heals from ledger replay.

Global ingestion may interleave with live cooperative writers; import never rewinds or overwrites
the head. Its own writer order and source ordinals preserve source sequence. Until every batch/gap
link is terminal, `importer.status` exposes `import_incomplete`; public status composes it. Check
and receipt may use that snapshot as an early UX rejection, but correctness comes from the
ledger's same-transaction pending-import predicate at check freeze/final commit and receipt append.
Already committed events remain immutable and visible. After all candidate batches, build/finalize the canonical
`ImportReportInternal` and call `importer.prepare_report` before publishing anything; replace the
allocation with its `report_ready` return. On resume at `report_ready`, reopen the stored report
and use `allocation.report_evidence_draft` rather than rebuilding/re-encrypting either value.
Publish that importer-authored `evidence_recorded` event with `evidence_kind=import_report`,
immutable report object/digest, and conservative import coverage through ordinary `publish_work`,
then pass its terminal/replayed `AppendResult` and the same report ref to
`importer.publish_report`, retaining the returned allocation. Call `importer.complete`;
acknowledge only after terminal commit. The
encrypted report includes the source audit digest/object ref while its structural identity and
safe terminal envelope use the source commitment; it also includes imported/quarantined/unknown/
malformed counts, batch outcomes/frontiers, coverage, warnings/gaps, mapping/Codex versions, and
sanitized metadata—never source text.

### Comparative review

1. Validate the review request/session/writer and freeze one exact frontier `F` after the selected
   import manifest(s). Select sources by their durable identity, never by fuzzy filename/cwd match;
   call `TaskRuntime.importer.load_review_source(identity_digest, F)` for each and reject a snapshot whose
   completed batch/report history is not wholly at or before `F`. Because v0.1 `check` has no
   arbitrary-import-subset scope, the selection must equal the bounded complete terminal import
   identity set reported for the session; silently widening a subset to the whole case is forbidden.
2. Build a deterministic comparison case from three explicitly labeled classes already available
   at `F`: cooperative ledger events, imported observations/gaps, and captured/digested
   repository/artifact evidence. Preserve source channel, assurance, freshness, and immutability;
   equal-looking text does not merge identities.
3. Compare only claims the versioned review policy can support. Examples include cooperative
   action absent from the public stream, observed command/result absent from the cooperative
   account, conflicting outcome/exit/subject state, claimed file state without captured content,
   or evidence stale after a material edit. Silence in an incomplete public stream is an
   observation gap, not proof an action did not occur.
4. Route the frozen comparison through `execute_check`/the same deterministic policy, ranking,
   finding, lease, and append contracts. Disagreements become explicit deterministic findings
   where rule-supported or sorted coverage gaps otherwise. Review never appends a hand-built
   finding and never lets semantic output bypass post-validation.
5. Return `ReviewInternal` containing `F`, selected source/report identities, check result, comparison
   coverage, and unmatched/unknown/redacted/unavailable counts. Any later event or artifact change
   makes the review stale under ordinary frontier/subject-state rules.

v0.1 deliberately compares already recorded/captured artifact evidence only. It does not add an
artifact-inspection port or call Git, shell commands, or arbitrary filesystem reads. Live
inspection is deferred until its own consent/root/symlink/submodule/execution/size/redaction/
subject-state ADR; if later approved, it must publish captured evidence before deterministic review.
ADR-011's content-withholding structural digest does not change this boundary: it may be published
as a subject-state reference, but it supplies no source content and gives this service no live
inspection capability.

### Port and schema prerequisites

The complete shared state machine is `ports/importer.md`; memory/SQLite implementations must expose
that exact `ImporterPort`. Register its request aliases/job/batch persistence, `ObjectKind.import_plan`,
`ObjectKind.import_report`, and import-gap/projection link. Do not add import to the four task-ledger `OperationKind` values,
reuse a check pending row, pretend many batches are globally atomic, derive non-UUIDv4 IDs from
hashes, or rely on process memory for crash recovery.

## Errors and edge cases

- Unsupported Codex version/schema, invalid source handle/metadata, exact-source/aggregate/manifest
  cap, or invalid review selection → `INVALID_REQUEST`/`LIMIT_EXCEEDED` before publishing affected
  events. An individually oversized but range-identifiable line becomes an explicit gap.
- Malformed, truncated, unknown, or oversized individual lines are preserved as encrypted
  range-linked gaps when the overall bounded import can continue; they are not exception text and
  do not become known events.
- Same request/different source or same durable source identity with contradictory manifest bytes
  → `IDEMPOTENCY_CONFLICT` or quarantine for verified durable contradiction. Ordinary partial
  import/crash is resumable and not corruption.
- Cancellation may leave a partial manifest with terminal batches. Retry resumes allocated IDs;
  it never republishes completed batches. At `report_ready` it reuses the exact stored report and
  evidence draft; a response lost after terminal completion replays the original report.
- Source/key/storage corruption disables normal import/review through `STORAGE_CORRUPT`. Missing
  artifact evidence weakens review; it is not an empty successful comparison.

## Invariants

1. Exact source bytes/encrypted audit digest and source-to-observation ordinal mapping are
   recoverable; structural equality uses only the keyed source commitment.
2. Reimporting the same source creates no different logical history, even after multi-batch crash.
3. Imported material remains visibly imported and never exceeds harness-observed/import-observed
   coverage.
4. Unknown/malformed material becomes an explicit durable gap and is never discarded/coerced.
5. Import batches use the normal publication path; review findings use the normal check path.
6. Import/review remain CLI support surfaces and do not change the exact six MCP operations.

## Tests

- `specs/tests/unit.md`: all mapping categories, partial/malformed/unknown/truncated lines,
  argv/cwd sanitization, exact stderr-absent constants, coverage clamping, deterministic
  batching/IDs.
- `specs/tests/conformance.md`: same source/new request dedupe, report/event parity across memory and
  SQLite, source-order mapping, imported-vs-cooperative comparison fixtures.
- `specs/tests/integration.md`: exact 4 MiB source/cap-plus-one rejection, >100/>1 MiB multi-batch
  import, live writer interleaving, partial import visibility, artifact mismatch/staleness.
- `specs/tests/subprocess.md`: kill at source capture/reservation/plan/every batch/report prepare/
  evidence append/report publication/completion boundary; resume produces no duplicates,
  object-ID idempotency conflict, or changed report.
- `specs/tests/integration.md`: raw source/cwd/argv/user text absent from DB/WAL/logs/errors and
  present only in approved encrypted objects; raw stderr is absent everywhere in v0.1.

## Open questions

None.

`prepare_report`/`report_ready`, exact tested-version mapping, no live artifact-inspection
port, and no raw-stderr retention are frozen v0.1 decisions in the owning port/adapter specs.
