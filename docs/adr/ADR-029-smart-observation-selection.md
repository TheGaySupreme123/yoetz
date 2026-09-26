# ADR-029 — Smart observation selection and bounded observation budgets

**Status:** Proposed for issue #687; the maintainer requested the complete implementation in one
draft PR on 2026-09-10. Product direction is acknowledged; measured performance acceptance and
the larger-profile rollout remain review decisions on that issue. Amended 2026-09-24 for #828
(configurable capacity policy, custom counts, and the typed no-Yoetz-cap outcome) and 2026-09-25
for #843 (a finite over-target drain bound that keeps accounting writable) and #836
(stranded capture handoffs are reconciled as admission-independent maintenance).

**Relates to:** ADR-009, ADR-010, ADR-014, ADR-016, ADR-022, and issues #687, #753, #828, and #843.

## Context

The observation queue is shared by host sessions in a workspace. The previous implementation
coalesced successful routine reads only when materializing the task ledger, after local capture,
serialization and queue admission. Its 512-row target was independent of a 1 MiB bound on the
entire state file and a separate 512-ticket capture limit. Increasing the row constant cannot
increase sustained throughput, preserve content, or expand the evidence selected for a check.

Normal drain means delivering accepted observations and continues in the background. Discard is
loss of observation data and is a last resort. Observation must not prevent the host from working.

## Decisions

### Selection and authority

Focused is the default detail mode. Detailed retains individual routine operations and additional
bounded eligible context. Both modes retain failures, denials, cancellation, interrupted and
unknown outcomes, mutations, declared verification, and evidence explicitly protected for an
obligation, claim or finding. A read-only command is not necessarily routine verification.

Classification is deterministic, versioned, and conservative. Unknown tools, ambiguous shell
composition, nonzero read results, and conflicting outcome facts remain individual observations.
Caller prose and caller-supplied routine labels cannot downgrade protection. An explicit request
to retain evidence can only increase its protection within existing authorization.

Selection precedes optional content extraction and admission. A pre-event has no successful
outcome: its minimum native identity must be durable before it can be paired with a post-event.
Only a proven routine success can contribute to a summary. A later failure retains its original
attempt and outcome identity, including the failure before a successful retry.

Content permission, structural retention, and AI-powered review disclosure are independent. No
detail or capacity setting enables a content category, a provider, a credential, or network
disclosure. The existing redaction, encryption, never-send exclusions, and authority generations
remain mandatory in every mode.

### Summary identity and delivery

A summary is a bounded account of represented source inputs, not evidence of their content.
Its durable identity binds every represented input identity and source position. Counts describe
the actual represented inputs and completed calls; they are not estimates of discarded traffic.
Summaries cannot cross workspace, routed task/session/writer, host session, delegate, source,
source generation, consent generation, restart, or material subject-state boundaries.

One definition decides whether a routine read succeeded. The classifier resolves it from the host
payload and records that decision on the envelope; a later caller re-derives the same state from
the fields the envelope retains rather than applying a second, stricter rule to a lossy copy of
them. Where the two disagreed, the buffer admitted an input the summary then refused (issue #753).

Refusing to summarize a buffered lane is an accounting loss for that lane alone. Its members are
still accepted observations: they are admitted individually with the durable
`routine_summary_invalid` coverage gap, the remaining lanes commit, and the buffer drains. The
workspace retains one bounded, deduplicated account of the refused lane so the cause is named
once. An invariant failure while summarizing must never stop ingestion for the session.

The pending representation and source cursor commit together. A replayable input cannot advance
past an input for which neither an individual record nor its summary account is durable.
Source-order delivery and existing fair drain selection remain unchanged: selection priority does
not authorize reordering. Before a later protected record is admitted, any earlier pending summary
in its lane is flushed. Background maintenance also flushes bounded pending work, so another hook
is not a prerequisite for progress.

Successful summaries flush after two seconds or 16 completed calls. An unresolved routine pre
becomes an individual pending action after five seconds; this does not invent its outcome.
Pairing identities remain available for ten minutes so longer tool calls can still be linked.
Expiry or a fenced session end records `pending_attempt_expired` before retiring that pairing
state. A later post retains its actual outcome as an unpaired observation with explicit coverage
limits. These wall-clock deadlines survive restart; a retry cannot extend the original deadline.

Accepted outbox rows are immutable pending delivery. Automatic degradation affects future
optional work; it does not rewrite accepted individual rows as summaries or move protected rows
into quarantine merely to fit the state file. Disposable read caches and bounded diagnostic
detail are reclaimed first. When a safe write cannot preserve accepted records, it fails without
replacing the previous durable state.

### Capacity, pressure, and setting lifetime

Detail and capacity are separate controls. Queue-count profiles are 512, 2,048, and 8,192; 512 is
the default. Every active profile must also have finite queue-byte, aggregate-state, pending-pair,
capture, diagnostic, and protected-reserve bounds. The shared workspace ceiling and per-session
admission accounting are visible. A capacity override never changes a sibling's detail mode or
capture authority.

The initial numerical budget proposal is:

| Queue profile | Queue bytes | State bytes | Protected reserve (rows / bytes) | Session share (rows / bytes) |
| --- | --- | --- | --- | --- |
| 512 | 512 KiB | 1 MiB | 128 / 128 KiB | 128 / 128 KiB |
| 2,048 | 2 MiB | 4 MiB | 512 / 512 KiB | 512 / 512 KiB |
| 8,192 | 8 MiB | 16 MiB | 2,048 / 2 MiB | 2,048 / 2 MiB |

All profiles share a 256 pending-attempt limit and an independent 512-ticket / 128 MiB capture
budget. These are finite engineering limits, not measured throughput guarantees. The byte and
session bounds can stop admission before the selected row count. Optional routine detail is
bounded to 16 KiB in Focused fallback and 64 KiB in Detailed, within the pre-existing content
permissions. Protected failure and evidence content retains its existing capture bounds.

Capture admission counts both retained tickets and outstanding reservations across task bundles
in the workspace. A new service generation requires a complete inventory before admitting new
content. Missing or unreadable relevant routes leave accounting unknown; a partial task update
cannot clear that state. Reservation overlap requires exact ticket and retained-byte bindings.
When older state lacks those bindings, pressure uses a conservative upper bound rather than
subtracting bytes that might belong to another ticket. Content refusal preserves the structural
observation and records both capture-budget and content-unavailable gaps.

Unknown capture inventory is also durable service-maintenance demand, even when the outbox
and selected buffer are empty. The READY observation sweep can reconcile that demand before any
new native input is admitted (#695). It uses an existing unambiguous lifecycle mapping and the
same authoritative catalog/bundle inventory as capture reservation; it does not invent a source
event, a task binding, or a zero-backlog proof. Missing mappings and unreadable or inactive relevant
routes remain unknown and are retried without requiring another hook. A healthy proven workspace
does not request another recovery scan.

A known inventory can still hold one stranded handoff: a ticket and reservation whose structural
row was already acknowledged or quarantined, so nothing will consume it. Its age alone held the
oldest-age dimension at the hard limit with an empty queue, closing admission and content for the
whole workspace until an unrelated authority change (#836). A handoff at least 30 seconds old is
therefore also maintenance demand. After inventory is known, the same sweep turn opens at most
eight owning task routes through the catalog, oldest first, and retires a handoff only when current
authority no longer backs it or no outbox row or selected input can still deliver its row. The
ordering (structural rows read before tickets, under the capture lock) cannot retire a handoff that
is about to be consumed. Genuinely pending handoffs keep their pressure: this does not relax the
pending-age limit, raise a capture ceiling, or clear pressure directly. Once the stranded age is
gone, hard admission reopens at once and optional detail follows the unchanged recovery dwell. The
coordinator rotates a bounded per-workspace cursor through that deterministic candidate order after
each attempted batch, so an unavailable prefix cannot starve a later route. The cursor is only a
scheduling hint and does not change route, task, or capture authority.

Retirement accounting is a durable boundary before destructive cleanup. The local
`content_capture_unavailable` marker and payload-free retirement record commit under the stable
ticket identity before the ticket is tombstoned or its central reservation is released. A failure
or cancellation leaves the handoff active and retryable. A retry reuses the ticket identity and
does not duplicate the loss count or diagnostic when accounting already committed before the
failure. Replay identities for tickets with active reservations are pinned within the bounded
outstanding-ticket set, so repeated retirement failures cannot evict an accounted handoff's key.

A sweep rotates through at most four workspace candidates with a shared five-second cooperative
recovery budget inside its ordinary sweep budget. A workspace turn rotates through at most eight
existing session candidates, then performs at most one complete inventory bootstrap. Session
cursor hints are bounded to 256 workspaces; eviction loses a hint, not accounting or authority.
Recovery does not hold the general workflow/control gate or an outbox drain lease. Publication
uses the same capture lock as reservation. Both complete catalog scans use worker-owned read-only SQLite connections; opening,
querying, decoding and closing happen in the worker, never through the shared catalog writer.
Synchronous task metadata reads and local publication also run off the service event loop; cancellation joins started worker operations before releasing
the lock or runtime. These joins can exceed the cooperative deadline. The deadline is not a hard
promise about a contended storage operation's elapsed time. Every opened runtime must match
its catalog task/session identity. The service/vault generation is checked before inventory reads,
after the final catalog reread, and under the local publication lock, so an obsolete READY
instance cannot mint a replacement proof after waiting for that lock. Partial ticket enumeration
cannot authorize releasing a reservation merely because its identity was not returned.

A recovered inventory proves accounting, not spare capacity: real count, byte, pending-pair and
capture ceilings still govern admission. Recovery neither deletes loss/quarantine history nor
creates complete task coverage. Retained, fully routed loss lanes are now independent durable
maintenance demand (#695). Before capture recovery, each workspace turn attempts at most eight
pending loss lanes, rotating past unavailable routes. A lane binds source, native session,
source generation, original task/session/writer and capture-authority generation. One permanent
`observation_input_loss` evidence marker per lane is appended through the existing observation
writer and encrypted-payload commit path, followed by an internal task observation gap and advice.
Only then is the local lane acknowledged. Reporting never replays the rejected input, advances a
source cursor, grants content capture, changes loss counts/identities, or claims recovered bytes.
The marker says at least one input was lost; exact cumulative counts remain in local accounting.
Retries and restarts resolve the same task-wide operation before staging another payload.

New checks reconcile this task's pending routed losses before freezing their case. Existing
check operations preserve their original frozen inputs and idempotent results. Transient or
unrecoverable publication failures remain fail-closed instead of omitting known task loss. A
terminal route drift or quarantined marker operation may use the already authenticated runtime
for the same task and a distinct deterministic recovery operation identity; the marker retains
the original route and never retargets the loss. Route-valid lane-digest mismatches receive an
explicit unreconciled loss marker; ranges whose route or source identity cannot be authenticated stay in local
accounting and are never assigned to a task from a parser failure. Historical loss attribution survives
same-task session supersession; it is never rebound to a new task or represented as a new
observation under the current capture grant. Original authority identity remains part of the
report even after that grant changes: reporting already-recorded loss is structural maintenance,
not new observation or content permission.

The existing 64-range retention bound still applies. Unrouted input and overflow-only aggregate
history have no provable task attribution and remain visible locally; they are never broadcast
to every task or assigned to a later mapping. A recovered/empty queue is not evidence that earlier
losses were absent. Lane acknowledgement adds only private, bounded retry state; an older writer
forgetting it can cause a replay but cannot duplicate the stable ledger marker.

Detailed and larger capacity default to a current-session override. Workspace persistence is an
explicit owner choice. A preview precedes non-default authority and describes scope, expiry,
finite budgets, and increased storage and processing costs. Expiry, revoke, reset, and lowering a
setting affect future admission. Accepted records drain under a finite over-target transition;
they are not deleted to make occupancy match a lower selection. The transition's byte bound is
defined in the #843 amendment below.

Selection is local operational state under ADR-014, not an artifact replacement or a privacy
grant. The preview binds the requested detail, capacity, exact scope and expiry to an acceptance
digest. Local CLI/TUI apply requires that digest; ordinary MCP status is read-only. This records
an owner-attested operational choice and does not authenticate OS user presence. An agent may
relay it only after the owner explicitly accepts the displayed scope and costs. Repository
configuration cannot silently grant Detailed or a larger session override. Content and disclosure
changes continue through their independent ADR-012/ADR-016 authority paths.

Pressure is driven by the worst relevant count, byte, pending-age, pending-pair or capture-backlog
constraint. Rising pressure reduces optional content first. High pressure temporarily makes a
selected Detailed session effectively Focused. A hard limit pauses replayable ingestion at the
last durably accounted input and records bounded loss for non-replayable input. Sustained low
pressure restores only a still-valid owner selection, with hysteresis and a recovery dwell.

The initial thresholds are 65% rising pressure, 85% high pressure, and a hard stop at 100% of
the worst relevant budget. Under `observation-budget-v2-provisional`, leaving every current
hard threshold transitions `hard_limit` to `high` immediately. Structural admission reopens only
when the whole proposed buffer/outbox transition also fits the selected count and byte limits.
Optional detail remains reduced until all dimensions stay at or below 45% for ten seconds.
Retained diagnostic or quarantine bytes can therefore keep optional detail reduced without
indefinitely reporting a hard admission stop after pending work drains. Background maintenance
advances the dwell even when no new hook arrives; a status read never starts it.

New native input is checked at the shared selected-admission commit under the store lock.
Already accepted buffered transfers and raw outbox replay remain drainable. A generation-fenced
session end removes its pressure snapshot; ended lanes do not keep idle maintenance scheduled.
An unfinished session keeps its snapshot across restart. Ending a session does not erase its
pending rows, quarantine, or loss history.

Status distinguishes selected and effective settings, origin, scope and expiry, current pressure,
and historical loss. A downgrade and a recovery each produce one notice; actual loss and stalled
recovery use bounded aggregation. Status reads and successful redundant Yoetz bookkeeping must
not amplify their own observation traffic.

### Evidence and receipts

The stages observed, retained or summarized, delivered, and selected for a particular check are
distinct. Summary coverage does not claim per-call content coverage or a successful verification.
An empty queue and recovered pressure do not erase historical loss.

Promotion can use only retained native identities and their original subject-state provenance.
Where historical bytes are no longer retained, reacquisition is a new observation of a new time
and state. It cannot retroactively prove the earlier state. Unrecoverable history remains a
coverage limitation. No universal raw-content cache is introduced.

JSON, markdown and text receipts describe the same bounded coverage. Existing AI-powered review case
capacity refusals, exclusions, provenance and unresolved findings remain independent of queue
depth and detail mode.

## Validation and rollout

Performance acceptance uses synthetic or public-safe replay with measured upstream capture,
serialization and writes, rather than ledger row counts alone. The owning issue records the
baseline, proposed numerical targets, measured profile budgets, native-host acceptance, and any
remaining limits before this draft is marked ready. Larger profiles are not validated merely by
passing a count-boundary unit test. No live installation or private corpus is changed by the
implementation or its synthetic benchmarks.

## Amendment — configurable capacity policy, custom counts and the no-Yoetz-cap outcome (2026-09-24, #828)

Issue #828 asks for explicit larger, custom and no-Yoetz-cap choices for multi-agent capacity,
each with a resource and cost disclosure on the terminal interface, the CLI and agent guidance.
This amendment delivers the local structural observation queue dimension end to end and records
the policy for the other dimensions. It does not change any default.

### Capacity dimensions

| Dimension | Owner-selectable finite values | No Yoetz cap | Status |
| --- | --- | --- | --- |
| Structural observation queue | `standard` 512, `larger` 2,048, `largest` 8,192, or `custom` 64..8,192 rows | Unsupported: typed `capacity_no_cap_unsupported`, reason `state_document_ceiling` | Implemented |
| Pending pre/post pairs | Fixed at 256 | Unsupported | Unchanged |
| Native capture lane | Fixed at 512 tickets / 128 MiB per workspace | Unsupported | Unchanged |
| AI-powered review input, output and spend | Hard product constants and privacy-policy fields only | Unsupported: provider and egress ceilings still apply | Out of scope; a separate privacy/egress design change |

A custom count uses the same byte ladder as the three profiles. For a queue count `N`:

- queue bytes are `N × 1 KiB`;
- the aggregate state document is `max(1 MiB, 2 × queue bytes)`, never above the 16 MiB
  `STATE_DOCUMENT_CEILING_BYTES` safety ceiling that the local store also enforces;
- the protected reserve is `max(64, N / 4)` rows and `max(128 KiB, queue bytes / 4)`, each capped
  at half the queue so a small custom count still admits unprotected rows;
- the per-session fair share is `max(1, N / 4)` rows and `max(1, queue bytes / 4)` bytes.

The 512, 2,048 and 8,192 profiles produce exactly the limits in the profile table in Capacity,
pressure, and setting lifetime. The pending-pair,
capture, diagnostic, optional-detail and pressure-threshold values are the same for every count.

### No Yoetz cap

The local observation state is one document that is re-encoded on every save. Its 16 MiB safety
ceiling is a product quota that would defeat any "unlimited" queue selection downstream, so this
storage revision does not offer an uncapped structural queue. A no-cap request is still accepted
as input on preview and apply and answered with the non-retryable typed outcome
`capacity_no_cap_unsupported` (`invalid_request` class). The outcome names the state-document
ceiling and the largest supported finite capacity (8,192 rows, `--capacity largest` or
`--capacity custom --queue-count 8192`) and changes nothing. Status projects the same availability
as the closed `no_cap` object. No surface may label a setting unlimited while this ceiling or a
provider limit applies. Incremental persistence of the state document is the prerequisite for
revisiting this and is not part of this amendment.

### Explicit increases and one disclosure contract

No capacity increase happens automatically, and ordinary task permission never authorizes one. Every
change goes through the existing preview and digest-bound apply. The preview carries the closed
`yoetz.capacity-change-disclosure/1` record: dimension, scope, change (`increase`, `decrease`,
`unchanged` or `unsupported`), current and requested counts with their queue and state byte limits,
closed consequence tokens, the limits that still apply, the unchanged
content/privacy/provider/credential/network authority, the lower, revoke, pause and resume commands,
and `validation_status: not_validated`. Every command in the disclosure and every `next_command`
uses the literal `<workspace>` and `<session-id>` placeholders rather than the typed path or session
id. The CLI human output and the terminal interface render that record through one shared renderer,
and agent guidance tells agents to relay those lines, so the three surfaces cannot drift. An
increase names possible disk, memory and CPU use and possible slowdown of Yoetz or other apps, and
carries the consequence token `workspace_aggregate_raised` with the line that the shared workspace
queue follows the largest active selection, so it can raise the queue and state-document bounds for
every session in the workspace. A decrease affects future admission only; accepted records drain and
are not deleted. The preview digest binds the requested queue count and the change token, but not
live pressure. The selection-preview payload schema tag is `yoetz.observation-selection-preview/2`.
The CLI no-cap failure is a standard ADR-030 failure: `error.recovery` carries the continuation and
directive, and `error.capacity` carries the facts as `{no_cap, alternative_command}`.

Status and the local control projection add a `yoetz.observation-effective-budget/1` record: the
selected and effective counts and labels, why they differ (`selected` or `workspace_aggregate`),
every finite limit, the limiting dimension and its utilization, and the no-cap availability. MCP
status stays read-only and does not expose or change capacity; an agent relays a change only after
the owner accepts the displayed preview through the local CLI or terminal interface.

The shared workspace queue is the largest of the active workspace setting's count (or the 512
default when none is active) and every active session selection's count. A session selection can
raise that aggregate but never lower it, so a session-scoped custom count below 512 lowers only
that session's own admission and never a sibling's.

### Defaults, allocation and upgrade

Focused with `standard` (512) remains the default. The benchmark phase in
[the performance runbook](../runbooks/observation-selection-performance.md) still owns any default
change, and the custom ladder has not been measured.

The intended multi-agent allocation model is a per-task reservation plus a shared burst pool.
That model is recorded as direction only; per-session fair share remains the implemented
allocation.

Saved selections are preserved across upgrade and the settings document keeps the queue count as
an integer. Local control schema `2.9.0` accepts custom counts, capacity labels and the
effective-budget record; both peers must run the `2.9.0` manifest to exchange them. An older
revision reading a saved custom count treats it as malformed, drops that selection to the default,
and does not report the drop. That is a disclosed limitation of downgrade, not a supported path.

## Amendment — a finite over-target drain keeps accounting writable (2026-09-25, #843)

### Problem

Lowering, revoking or letting a larger selection expire, or ending the session that held it, can
leave the state document above the 1 MiB fallback. The store used the current file size as the
byte bound for that drain. Accepted rows were kept, but no write could grow the file. Accounting
for refused input (`outbox_overflow`, `observation_input_loss`), delivery-attempt metadata, and
a local session end all failed as `storage_unsafe`. A hook's capture batch rolled back with them,
so the hook reported a refusal as accounted when it was not. The native `SessionEnd` hook
discarded the failure, so the session and its temporary override stayed active.

### Decision

While accepted rows exceed the lowered selection, a write that does not fit the ordinary bound
may use a finite over-target bound. Accepted rows exceed the selection when their count is above
its queue count or their admission bytes are above its queue bytes. The bound is:

- the selected state bytes;
- plus the persisted bytes of accepted pending rows and buffered inputs above the selected queue
  bytes;
- plus a fixed 128 KiB accounting reserve;
- never above the 16 MiB `STATE_DOCUMENT_CEILING_BYTES` and never below the current file size.

Accepted rows carry their own persisted bytes, including delivery-attempt metadata. Everything
else has the lowered selection's non-queue budget plus the reserve, including loss and lifecycle
accounting. That accounting is count-bounded (at most 256 session gap maps and 64 loss ranges),
so recording refusals cannot raise the bound. Only admission and drain change accepted rows, and
admission is closed while the queue is over target. The bound shrinks as rows drain. It ends when
they fit the selected target, and the ordinary bound applies again.

The bound is never used outside an over-target transition. An ordinary full queue keeps the
standard retention ladder, and the standard pressure seam is unchanged. Retention still trims
disposable classes to the bound first. When protected state cannot fit even then, the write
still fails without replacing the previous durable state. Landing within the over-target bound
is not evidence of health: an active truncation gap clears only with headroom under the ordinary
bound.

Admission is unchanged. New host input, including protected and lifecycle observations, is
refused and accounted while accepted rows exceed the selected target. The 600-rows-over-512
backlog still reports `outbox_overflow` and keeps its rows.

A `SessionEnd` hook whose local session end still cannot persist stays fail-open. It prints
`hook_observe_degraded: session_end_unrecorded` and records the bounded `session_end_unrecorded`
hook diagnostic instead of discarding the failure.

### Unchanged

No wire field or schema changes. Status keeps reporting the lowered selection's limits. The
transition shows as over-100% utilization with `pressure_state: hard_limit`, beside the queue
counts and bytes, gaps, and loss accounting. The per-write cost of a large state document is also
unchanged: each acknowledgement during a 2 MiB drain rewrites the whole document. Incremental
persistence remains the prerequisite for revisiting that.
