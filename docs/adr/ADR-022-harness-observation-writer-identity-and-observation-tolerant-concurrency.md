# ADR-022 — Harness observation writer identity and observation-tolerant optimistic concurrency

**Status:** Accepted (2026-08-13), recorded for issues #214–#223 and acknowledged in issue #225.
**Amended:** 2026-09-05 for issue #494 / ADR-027 (observation writer stays per task/session;
only inventory-designated shared-mutable workspace routing or verification-job scheduling authority
may become project-scoped once multiple live tasks share a repository); 2026-09-04 for issue #577 (pending observation rows follow a superseded session
binding after ended-session recovery attach); 2026-09-04 for issue #560 (task-scoped operation identity across workflow
reattach, decision 18); 2026-09-03 for issues #539 (content-bearing committed replay) and #540
(terminal ingest rejection and retry ceiling); 2026-08-30 for issue #302 (captured observation ledger
evidence) and issue #331
(frontier-motion recovery across rewinds and restarts, decision 11); 2026-08-29 for
issue #445 (standing-grant parks are not an observation barrier); 2026-08-27 for issue #418 rollout
replay repairs; 2026-08-18 for the
maintainer-directed issue #346 incident repairs #350, #351, and
#352 (decisions 12–14); 2026-08-18 for maintainer-authored issues #320 and #326 and issue #322
(delivered frontier-motion high-water); 2026-08-16 for maintainer-approved issue #224; 2026-08-14
for moderator-approved issue #244 and the reopened issue #216 recurrence.
**Implemented by:** `src/yoetz/application/observation_materialize.py`,
`src/yoetz/application/observation_coordinator.py`, `src/yoetz/cli/observe_hooks.py`,
`src/yoetz/adapters/memory/ledger.py`,
`src/yoetz/application/publish_work.py`, `src/yoetz/application/receipt.py`,
`src/yoetz/kernel/policies/observation_advice.py`, `src/yoetz/service/ready_composition.py`, and
`src/yoetz/adapters/integrations/observation_local.py`.
**Relates to:** ADR-009, ADR-010, ADR-020, and
issues #214, #216, #217, #223, #224, #225, #226, #227, #244, #302, #320, #322, #326, #331,
#445, #494, #539, #540, #560, and #577.

**Proposed amendment for issue #231:** `provider_not_ready` remains bounded local advice, but the
observation coordinator does not materialize it as an agent-facing finding. Provider readiness is a
machine condition rather than a repair to the recorded work. A requested check still records the
exact semantic status and coverage limitation, and receipts retain that limitation; the amendment
does not make the affected receipt clean. This is the narrow option that preserves proof-based
finding resolution and does not change `ResponseDisposition` or finding-kind traits.

## Context

Live observation and cooperative MCP publication share one task-global ledger frontier. Observation
originally reused the cooperative writer, so each delivered hook moved the frontier held by the
agent and made state-sensitive `publish_work` race continuously. Splitting the writer alone is not
enough because the optimistic guard is task-scoped. `check` has the same problem after freezing a
case: an intervening observation makes its exact-frontier commit fail terminally.

Observation also converted lifecycle prose into claims the agent did not make and derived advice
finding identity from the entire changing envelope snapshot. That made delivery capable of creating
unsupported claims and unbounded duplicate findings.

## Decisions

1. Each task session has a deterministic observation writer:
   `observation_writer_id(task_id, session_id)` calls `stable_observation_id` with
   `kind=IdKind.WRITER`, `source_identity=session_id`, mapping version `obs-writer/1.0.0`, and role
   `observation`. `_admitted_writers_for_session` unions that derived id with the writers admitted by
   completed starts; derivation does not grant durable authority. Existing observation operations
   are looked up under both this writer and the legacy cooperative writer during an upgrade.

   A second `start` is not an admission mechanism: attach semantics mint a fresh session on every
   start path. A synthetic `start_operations` row would assert a start that never happened in the
   table whose history establishes that fact. A separate catalog table would require a migration
   and a resume lifecycle for an identity that is already a pure function of task and session ids.

2. A record is observation-authored only when all four facts hold: actor id is
   `yoetz:observation-coordinator`, actor type is `harness`, authorship assurance is
   `harness_observed`, and publication channel is `hook_observed` or `engine_derived`. Actor id and
   type are caller-supplied, so they are insufficient alone. Assurance and channel are service
   derived; ordinary requests cannot select either admitted observation channel.

3. A stale `expected_frontier` is tolerated only when it is behind the real head and every
   intervening accepted record satisfies that four-fact predicate. The new batch appends at the real
   task head and reports that head as its subject frontier. Any cooperative, importer, or otherwise
   authored intervening record still conflicts. Dry-run applies the identical rule. Append
   computation and commit share one critical section so no concurrent append can invalidate ledger
   digests or assigned sequences between them.

4. Check acquisition may accept a caller frontier behind the live head only across an
   observation-authored suffix. It freezes the case at the live head, under a transient in-memory
   reservation that defers observation appends until the durable frozen-case barrier is installed.
   The reservation is never persisted and expires after 60 seconds if acquisition stalls.
   Observation-authored append attempts while either barrier is active receive retryable
   `OPERATION_PENDING`; the durable outbox retries after the check commits. A check parked on a
   standing-repository-grant suspension (`suspension_kind=repository_grant`) is not an active
   barrier: no provider job exists, the lease is expired, and the ceremony may never complete.
   Observation appends proceed; same-request replay re-installs the barrier when it reclaims the
   lease. Commit keeps the frozen subject frontier — the verdict is never retargeted to a frontier
   it did not inspect — and tolerates an observation-authored suffix after that frontier the same
   way acquisition and `append_batch` do, appending check events at the live head. Cooperative or
   importer motion still conflicts.

5. Receipt acquisition may keep its caller-supplied subject frontier across an
   observation-authored suffix only when the supplied projection is the exact genesis-prefix replay
   at that frontier and the suffix contains no `finding_recorded` event. The receipt document stays
   pinned to that subject frontier. Its locator append repeats the finding-free suffix check so a
   finding drained between availability inspection and append still conflicts. Material motion,
   spoofed observation authorship, a mismatched prefix projection, or a finding-bearing suffix
   returns the shared retryable `FRONTIER_CONFLICT` shape with repair facts.

6. Hook envelopes are observation evidence, not agent claims. Completion-like lifecycle signals
   materialize as metadata-only `evidence_recorded` events. A narrow claim path exists only when the
   structural payload explicitly carries an admitted `claim_kind`. Mapping version
   `obs-ledger/1.3.0` separates these identities from historical observation-derived claims and
   gives paired hook/stream results source-independent action/result IDs. Before staging a new
   `1.3.0` append, upgrade replay checks both the session observation writer and the legacy
   cooperative writer for committed `1.3.0` and `1.2.0` operations. A `1.2.0` hit carries that
   mapping version through logical-identity claim repair, preserving the original operation and
   claim identities for a committed-but-unacknowledged outbox row instead of duplicating them. A
   replayed `1.2.0` operation cannot be assumed to carry a host-stated result: it may be a
   pre-upgrade hook operation whose committed result is `UNKNOWN`. Its `1.2.0` record identities
   cannot be re-derived under `1.3.0`, so the correction path consults the committed result through
   the replayed operation's accepted event ids, skips the correction only when the committed result
   already states the same explicit fact, and otherwise binds the correction (with `dedup_conflict`
   coverage on a contradictory explicit fact) to the exact committed legacy action and its committed
   action event as causal parent.

7. Deterministic advice finding IDs are condition-scoped over policy, kind, rule code, and detail
   token. Evidence refs prove the condition but never identify it: several rules intentionally cite
   a rolling or accumulating envelope window. Candidate subject refs map only their source envelopes
   to ledger event ids; a standing condition with no envelope anchors to the session lifecycle
   event. Once a readable finding for that condition exists, later evidence-window or frontier
   changes do not append another `finding_recorded` event. The current observation snapshot and
   coverage/gap state retain the changing evidence context without growing the durable finding set.

8. `provenance_disputed` is the fourth `ResponseDisposition`. It records that the responder
   contests the finding's authorship or provenance premise, requires a non-empty reason, and may
   carry evidence, but is not scored as an evidence-free rejection by either deterministic policy
   pack. It never resolves or erases the finding. Public compact/readiness projections expose two
   distinct counters: `unanswered_finding_count` decreases after any readable response, including a
   provenance dispute, while `receipt_blocking_finding_count` continues to count current actionable
   findings whose receipt state remains `resolved=false` for every disposition.

9. Standing provider-readiness advice is not converted into a `material_limitation_omitted`
   finding. That finding kind remains actionable for an omission in the work account. Provider
   configuration and availability remain visible through observation advice before a check and
   through the check coverage vector and receipt limitations after one.

10. Successful routine reads are rate-limited at the task-ledger boundary. The hook adapter labels
   only a closed set of read tools and conservatively parsed single read-only shell commands as
   `routine_read`; path-qualified executables, side-effecting Git options, shell composition,
   redirection, mutation flags, unknown commands, explicit failures or denials, and other
   ambiguity fail closed to ordinary materialization. The complete hook envelope remains in the
   bounded local observation store, but its pre-event and successful post-event do not mint
   individual task-ledger records. A failed post-event still materializes action and result records,
   and edits, checks, tests, lifecycle events, and other non-routine observations are unchanged.

11. Every newly accepted observation-authored append records one bounded pending frontier-motion
    notice for the originating Codex session. A retry of a completed append whose local notice
    write did not land reconstructs that notice from the committed append's frontier metadata.
    After the notice's bytes reach the hook consumer, the store retains that session's delivered
    high-water frontier (`to_sequence` plus `head_digest`), scoped to the announced task ledger,
    so a later replay of the same append is not re-announced. The coordinator also supplies the
    routed ledger's actual current frontier: a historical completed-operation replay may carry an
    older result frontier while the current lineage is still at or beyond the delivered mark, so
    the older result digest alone never proves a rewind. When the actual current sequence is below
    the mark, or is at the same sequence with a different digest, the stored mark and stale pending
    notice are discarded and announcement restarts from the rewound lineage. A known limitation: a
    rewind that re-appends past the mark's sequence before the store next observes the lineage is
    indistinguishable from continued append under this predicate, so the replaced range at or
    behind the mark is not re-announced. Otherwise candidates
    at or behind the mark are dropped and overlapping candidates are clamped to the undelivered
    remainder so `from` and record count describe only motion not yet announced. A mark recorded
    for a different task likewise neither drops nor clamps: when a session's mapping moves to
    another task, the stale mark and any pending notice for the old task are discarded so the new
    ledger's motion is announced from its start. Contiguous undelivered appends coalesce their
    from/to sequences and record count. The per-workspace notice and delivered-mark maps persist
    per-entry recency ordinals, evict ended sessions first, then evict the least-recently-used entry
    at the cap even after restart. A legacy delivered mark without frontier-digest and recency
    identity is ignored, failing open to a duplicate rather than suppressing unknown lineage.
    A later advice-safe `PostToolUse` hook surfaces that the motion was hook-observed, explains
    that held cooperative publish frontiers remain admissible only across observation-authored
    motion, and directs callers to use repair facts when an operation still conflicts. The pending notice is
    removed only after its bytes reach the hook consumer. When a contiguous merge races that
    peek/commit window, identity mismatches; commit still advances the delivered high-water to
    the peeked frontier (sequence and digest) and clamps the merged remainder rather than
    re-announcing the emitted range. When the queued same-task notice instead sits below the
    emitted frontier — or at its sequence with a different digest — it proves the emitted lineage
    was rewound away after the peek, so commit records no mark and leaves the rewind notice
    queued so the new lineage's prefix is still announced. It grants no authority, changes no
    optimistic-concurrency predicate, and adds no MCP operation.

12. Paired `PostToolUse` materialization consumes every host-stated outcome fact: `exit_status`
    remains authoritative; without it, explicit `denied`, boolean `success`, and a closed
    `result_status` spelling table map to `SUCCESS`/`FAILURE`/`PARTIAL`. Only a payload with no
    outcome fact at all records `UNKNOWN` — a missing outcome is never upgraded to success. Such a
    record keeps its durable per-call action/result identity but carries the
    `host_outcome_unavailable` known gap on its entry coverage. Because check coverage and receipts
    fold per-record known gaps into one deduplicated code set, any number of outcome-less observed
    calls surface as one bounded task/session coverage condition. The research-evidence policy
    excludes exactly these records — result outcome `UNKNOWN`, no exit status, and source coverage
    carrying the service-derived `hook_observed` channel that decision 2 keeps unselectable by
    cooperative requests — from per-result `material_limitation_omitted` candidates. Explicit
    observed `FAILURE`/`PARTIAL` results and cooperative `UNKNOWN` results remain individually
    limiting, an outcome-less result still cannot support a completion claim, and the receipt
    retains the limitation through the coverage gap even when no actionable finding is emitted.
    Historical rows are not rewritten; mapping identity is unchanged so in-flight outbox replays of
    committed appends still deduplicate (issues #346/#350).

13. The retryable `OPERATION_PENDING` that decision 4 applies to observation appends during check
    acquisition or a frozen-case barrier — and the adjacent transient `BUNDLE_BUSY` and
    `FRONTIER_CONFLICT` coordination shapes — is designed back-pressure, not failure. The
    coordinator rejects such an ingest with the retryable `operation_pending` reason without an
    unexpected-exception diagnostic; drain paths keep the row pending without recording a coverage
    gap or a failure-shaped hook diagnostic, and the reason never projects into observation
    status, advice, coverage, or receipt inputs. Losing the nonblocking drain lease records
    nothing: the holder is a live hook or the service sweeper, and flock releases with a dead
    process. Hook drain-budget expiry records `drain_budget_exhausted` only when the pass moved no
    backlog; a bounded slice that delivered or quarantined rows and then yielded to the sweeper is
    working as designed. Genuine `SERVICE_UNAVAILABLE`, vault, storage, and preflight failures
    keep their existing visibility (issues #346/#351).

14. A mapped session's advice snapshot is constructed from that session's own retained envelopes
    and session-scoped lifecycle/gap health, resolved from the ingest envelope's session
    commitment or the durable workspace session route. The workspace-wide aggregate remains the
    operator surface and the source of deliberately standing machine conditions (composition
    facts), but is never a silent input to a mapped task snapshot — completing at the construction
    layer the delivery-selection boundary #250 established (issues #346/#352).

15. A hook `PostToolUse` with no outcome fact may commit `UNKNOWN` before the session stream later
    supplies an authoritative exit outcome for the same call. The original row is never rewritten.
    A replayed canonical operation consults the current projection and appends an idempotent
    `result_correction` linked to the same canonical action when it enriches `UNKNOWN` or exposes a
    contradictory explicit fact. Explicit outcomes are
    never downgraded or overwritten. Correction operation/claim identities bind the exact outcome
    and exit status: exact retries replay, while contradictory explicit facts append separately with
    `dedup_conflict` coverage so neither fact is silently discarded. Correction is part of ingest
    acceptance: if the candidate-findings projection is missing, lagging, rebuilding, or lacks a
    readable core result, the coordinator returns retryable `SERVICE_UNAVAILABLE` and the durable
    outbox row remains pending. It does not acknowledge the already-committed core append alone,
    because doing so could permanently lose the authoritative correction; a repaired or rebuilt
    projection lets the same row replay and finish idempotently.

16. `obs-ledger/1.4.0` carries trusted observation-content manifests into ledger materialization.
    A manifest binds the encrypted object envelope plus the SHA-256 digest and byte count of its
    secret-scanned inner content. Only tool output, selected changed-file bytes, and workspace diff
    are eligible; the resulting `evidence_recorded/1.2.0` event uses `observation_captured`,
    `immutable_snapshot`, `bounded_excerpt`, and mirrors the object in `artifact_refs`. A linked
    result cites the new evidence; a routine read with eligible retained content is no longer
    coalesced away. Durable inspection facts and bounded excerpts use a separate idempotent
    snapshot operation and separate evidence records, so their provenance is not borrowed from a
    tool envelope. Missing, deleted, or legacy-unbound content weakens to
    `content_capture_unavailable`; policy-excluded retained kinds use `content_unselected`;
    redaction remains visible as `content_redacted`; and bounded inspection prefixes retain
    `truncated_payload`. Replays check current 1.4 identities and the 1.3/1.2 legacy operation
    identities before staging, preserving append-only upgrade behavior.

17. Captured-content chunks remain ephemeral at the hook boundary, but their encrypted manifests
    are durable and indexed by a phase identity derived from the canonical host call plus normalized
    phase. Equivalent hook/stream copies share that identity; Pre/Post/unpaired siblings do not.
    Before materialization, ingest queries both that current identity and the legacy canonical
    identity. The matching legacy source group may supply usable content; every legacy source group
    remains a separate lookup-only candidate so a post-upgrade stream copy can find a hook commit
    without combining sibling phases. It authenticates the object kind, media type, canonical
    envelope, inner bytes, and multipart completeness. Only a complete, readable, digest-bound set
    may strengthen a new materialization. Incomplete, unbound, orphaned, contradictory, or
    unreadable stored metadata is replay-only: it may reconstruct old role tuples for completed-
    operation lookup, but it never grants captured coverage or enters a new append. Once a phase
    has durable manifests, an equivalent-source retry cannot expand the stable role set. The
    content-independent core role tuple is also a replay candidate: if core-only materialization
    committed before content arrived, the later content-bearing copy reuses that operation and
    records `content_capture_unavailable` rather than reminting core event IDs or retroactively
    upgrading coverage. Thus the role-scoped `obs-ledger/1.4.0` lookup safely handles either arrival
    order. Missing, incomplete, contradictory, or unreadable objects remain
    the explicit `content_capture_unavailable` gap; they are never inferred. A non-retryable public
    error from ingest is terminal `ledger_rejected`, quarantines only that envelope, and never projects service
    availability failure. Retryable reasons retain their existing scope, but 128 consecutive
    rejections with the same reason terminally quarantine the row; designed `operation_pending`
    back-pressure and workspace-global pause/vault/disabled gates remain pending under their
    existing recovery contracts. Quarantine stays visible and lets the next FIFO row proceed. This ceiling
    is a defensive bound for future catch-all classification defects, not evidence that a retryable
    failure became successful (issues #539 and #540).

18. Yoetz observing its own MCP workflow is delivered in proportion to distinct evidence (issue
    #564). Every host hook (Codex, Claude Code, Cursor) and the Codex session stream record Yoetz's
    own `start`, `publish_work`, `check`, `respond`, `status`, `receipt`, and `read_guidance` calls
    exactly like any other tool, and the hook process that records them is the same process that
    drains the outbox, so the prescribed workflow's own status/respond/check traffic produced two
    outbox rows plus a content capture per call and starved its own delivery. The service already
    holds the authoritative record of every Yoetz-owned call it served, so the hook adapter and the
    stream reconciler ingest such envelopes into the bounded local observation store
    unconditionally but enqueue them for delivery only when they carry evidence the service cannot
    know from serving the call: an explicit host failure or denial (any phase, any Yoetz tool), or
    the post-event of a mutation (`start`, `publish_work`, `check`, `respond`). The pre-event of
    every Yoetz-owned call and the post-event of a Yoetz-owned read (`status`, `receipt`,
    `read_guidance`) without a stated failure stay local; pairing bookkeeping is unaffected, so a
    delivered post-event still materializes its action. Yoetz-owned tool arguments and results are
    never captured as tool input/output content: they are already durable in the ledger the call
    read or wrote. Tool identity derives from the one registry tuple rendered into every host
    spelling; a name that merely resembles a Yoetz tool is ordinary. Like decision 10 this is a
    delivery-volume policy, not a coverage limitation, so it records no gap. On the consumer side,
    the service sweeper yields with its partial summary on a budget under the daemon's sweep
    deadline, so progress made under a backlog is never discarded as a timeout, and the manual
    `observe drain` repeats bounded passes while a pass resolves rows and reports a terminal
    condition (`drained`, `retry_pending`, `service_unavailable`, `pass_limit`) instead of stopping
    at the first retryable lane head. `observe status` reports the receipt time of the oldest
    pending row beside the last successful drain so convergence is readable from one call.
18. `obs-ledger/1.5.0` keys the observation operation digest on the task, the canonical logical
    identity, the exact draft-role tuple, and the mapping version only. The stable event ids an
    operation commits are derived from the task and the source identity, never from the routed
    Yoetz session or writer, so the operation that owns them must be findable from every later
    session of the same task. A workflow reattach in one host session (a second `start`) rotates
    the mapped Yoetz session and therefore the derived observation writer; under the
    session-bound `1.4.0`, `1.3.0`, and `1.2.0` digests the repeated envelope missed its committed
    operation, re-minted the same event ids, and was quarantined as `ledger_rejected` (#560).
    Before staging, the coordinator resolves the current identity task-wide through
    `lookup_task_operation`, so an idempotent repeat after a lost acknowledgement, a service
    restart, or any number of reattachments reuses the committed operation without a new append
    or a quarantine row; the same task-wide resolution applies to the task-scoped advice-finding
    operation. A task-wide hit whose stored request digest differs from the candidate digest is a
    conflicting reuse of the operation identity and fails closed as non-retryable
    `IDEMPOTENCY_CONFLICT` (`ledger_rejected`); a repeat that reuses committed event ids under a
    different logical identity still fails closed as `EVENT_INVALID`. Legacy versions keep their
    session-bound digests as replay-only upgrade candidates probed under both admitted writers,
    exactly as before: a pre-upgrade committed row is still replayable from the session that
    committed it, and only from that session. Hook ordinals, session generations, and host session
    commitments are keyed on the host session, not the mapping, so they stay monotonic across a
    reattach.
19. An ended-host-session recovery attach that rotates the task route does not retire still-pending
    observation rows of the predecessor (issue #577). `SESSION_NOT_FOUND` with
    `reason_code: session_superseded` already carries the current task binding; ingest follows that
    binding, routes with the observation writer derived for the successor session, and persists the
    updated lifecycle mapping on each hop under its lifecycle lock only if the stored mapping still
    equals the predecessor. Busy locks, changed or cleared mappings, and persistence failures skip
    the cache update without blocking successor delivery. The shared hook recovery path also rewrites every ended
    same-host predecessor mapping for that task in the same pass it stores the successor mapping. A
    row refused only because its route was retired is never `ledger_rejected`; a superseded payload
    that cannot be followed (missing or mismatched task/session/writer ids, a hop cycle, or a
    rotation after the route already opened) quarantines that row as `session_superseded`. That
    reason is not `mapping_missing`, so ended-unmapped drain terminalization and the status rule
    that hides `mapping_missing` while a mapping file exists cannot mislabel a mapped retirement.
    `ledger_rejected` remains the terminal class for content and identity refusals.

## Amendment — multi-task workspace observation home (2026-09-05, issue #494 / ADR-027)

Decision 1 is unchanged: the observation writer remains a pure function of task and session.
Decision 14 is unchanged: a mapped session's advice snapshot is constructed from that session's
own retained envelopes and is never silently fed a workspace-wide aggregate.

ADR-027's bounded reversal of the #250/#352 no-cross-task-state posture does not create a shared
writable ledger and does not let workspace-wide observation become a silent input to another
task's advice snapshot. The #498 ownership inventory must classify each table before #496 moves
anything. Only inventory-designated shared-mutable workspace routing may move to a catalog or
project home; task-owned provenance remains in its task bundle. In the expected `0004` inventory,
`observation_workspace_session_routes` is one expected shared-mutable candidate. The
`observation_verification_jobs` per-workspace running-job uniqueness in `0003` is a separate
expected scheduling-authority candidate. Job results, `observation_inspection_snapshots`, and
`observation_session_advice` remain task-owned unless the inventory proves otherwise. Until that
migration, `workspace_task_exists` still keeps the store single-task-safe.

## Security and privacy consequences

The observation writer id is derivable from public task/session identifiers, but derivation grants
no publication authority. `publish_work` derives its channel from the closed integration kind; a
caller cannot request `hook_observed` or `engine_derived`, and a spoofed harness actor therefore does
not pass the observation predicate.

ADR-009 includes `other_writer` disclosure provenance. The production privacy enforcer currently
ships without a provenance resolver. When that resolver is implemented, this harness writer must
classify as `engine_derived_from_self_authored`: a hook observation is an observation of the agent's
own action, not unrelated third-party authorship.

## Consequences

Cooperative work can retain a frontier across observation-only delivery, while real cooperative
concurrency still conflicts. Check cases make progress by temporarily applying back-pressure to the
outbox rather than weakening verdict binding. Observation can contribute evidence and bounded
advice without impersonating the agent or minting the same standing finding indefinitely. Routine
reads retain their local observation evidence without producing an unbounded task-ledger trail,
Yoetz's own tool calls are observed without recursively feeding the outbox they are drained
from, and
cooperative writers learn about meaningful observation-authored frontier motion before their next
state-sensitive operation. Captured objects contribute immutable byte identity without being
upgraded to validation, reproduction, or disclosure authority. Observation-advice policy `0.1.2`
applies the condition-scoped identity
to new materialization (`0.1.1` first introduced it; `0.1.2` rebased the standing
`provider_not_ready` condition onto per-build structural machine facts for issue #265). Historical duplicate findings remain append-only evidence; this change does
not erase or rewrite an existing task ledger.
