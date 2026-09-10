# ADR-029 — Smart observation selection and bounded observation budgets

**Status:** Proposed for issue #687; the maintainer requested the complete implementation in one
draft PR on 2026-09-10. Product direction is acknowledged; measured performance acceptance and
the larger-profile rollout remain review decisions on that issue.

**Relates to:** ADR-009, ADR-010, ADR-014, ADR-016, ADR-022, and issue #687.

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

Content permission, structural retention, and semantic disclosure are independent. No detail or
capacity setting enables a content category, a provider, a credential, or network disclosure.
The existing redaction, encryption, never-send exclusions, and authority generations remain
mandatory in every mode.

### Summary identity and delivery

A summary is a bounded account of represented source inputs, not evidence of their content.
Its durable identity binds every represented input identity and source position. Counts describe
the actual represented inputs and completed calls; they are not estimates of discarded traffic.
Summaries cannot cross workspace, routed task/session/writer, host session, delegate, source,
source generation, consent generation, restart, or material subject-state boundaries.

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

Detailed and larger capacity default to a current-session override. Workspace persistence is an
explicit owner choice. A preview precedes non-default authority and describes scope, expiry,
finite budgets, and increased storage and processing costs. Expiry, revoke, reset, and lowering a
setting affect future admission. Accepted records drain under a finite over-target transition;
they are not deleted to make occupancy match a lower selection.

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
the worst relevant budget. Recovery requires pressure below 45% for ten seconds. Background
maintenance advances this dwell even when no new hook arrives; a status read never starts it.

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

JSON, markdown and text receipts describe the same bounded coverage. Existing semantic-case
capacity refusals, exclusions, provenance and unresolved findings remain independent of queue
depth and detail mode.

## Validation and rollout

Performance acceptance uses synthetic or public-safe replay with measured upstream capture,
serialization and writes, rather than ledger row counts alone. The owning issue records the
baseline, proposed numerical targets, measured profile budgets, native-host acceptance, and any
remaining limits before this draft is marked ready. Larger profiles are not validated merely by
passing a count-boundary unit test. No live installation or private corpus is changed by the
implementation or its synthetic benchmarks.
