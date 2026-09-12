# Yoetz cooperative workflow

Read before the first `start`, or when resuming without the prior workflow in context. Use the
current tool schemas; missing schema metadata routes to [request templates](request-templates.md).
Yoetz records participant-published facts and checks that bounded record; it does not prove the
underlying work correct. For operational behavior, this served guidance and the typed result take
precedence over remembered product behavior. Preserve higher-priority instructions, current user
intent, and authorization boundaries; if memory says a capability is impossible, verify it with
the current documented read before accepting that limit. Do not delete or rewrite host memories as
part of installation or recovery.

## Start and resume

A new session's first workflow operation is `start` (create or attach), after guidance reads,
tool/schema discovery, and necessary bootstrap clarification. This includes `read_guidance`
and commands needed to read installed references or discover tool schemas. Call `start`
before substantive research, commands, edits, or delegation. If it fails, follow exact
typed continuations and same-request recovery first, including a named one-time repair.
If startup remains blocked without an applicable recovery path, ask the user for intro and
guidance; do not invent a substitute workflow. Continuing without a ledger task is permitted
only by the bounded optional-service fallback in startup failure precedence.

Hook mapping, plugin registration, and SessionStart context are cues, not a substitute for that
call and not proof that the current plan is in force. Trivial questions or edits still skip Yoetz.
See [startup failure precedence](coverage-and-receipts.md#startup-failure-precedence) for the exact
order of recovery, user handoff, and the bounded optional-service fallback.

`start` resumes by one of two selectors (never by bare `task_id`):

1. `session_id` — continue the exact session you already hold.
2. `workspace_ref` + `external_ref` as a pair — resolve the durable task for that project work item without a `session_id`. Under `mode=create_or_attach`, the same pair creates on first use and attaches on every later conversation. Attach mints a fresh session and writer; use the returned ids. The previously held session is retired for routing, but `status view=operation` from the successor session recovers that task's request ids, and `start mode=attach` with the retired `session_id` re-binds the same task. A different complete pair can be independent work, even when the workspace already has a dormant task. Automatic host admission may first recover a unique valid same-host mapping whose `SessionEnd` was received; it holds the required workspace and lifecycle locks, revalidates ownership and state, and only then attaches. With no usable persisted selector, it creates the new pair. An ambiguous candidate, ownership failure, busy recovery lock, or changed recovery snapshot remains a typed refusal or retry boundary; it never guesses from workspace membership or age. `workspace_task_exists` is reserved for explicit `mode=create` colliding with an identical pair.

Convention:

- `workspace_ref` = the canonical absolute repository root of the working tree you are in (a linked Git worktree is its own root). Never a remote URL: selector commitments use the exact value, and host hooks use this same root for consent and automatic recovery. A different workspace value is a different selector and may create independent work; workspace membership and `workspace_task_exists` do not authorize automatic attachment.
- `external_ref` = stable task identity within that project (branch name, issue reference, or plan slug). A hook-mapped task carries `<host>-session:<host session id>`; do not reproduce that pair. Attach to a host-mapped task with `mode=attach` and the `session_id` the session-start context names.

Same conversation resuming, or a fresh conversation continuing the same work → `mode=create_or_attach` with the same pair and no `session_id`. Sibling work in the same project → use `mode=create` with the same `workspace_ref` and a different `external_ref` only when the recovery table permits one; do not use `create_or_attach` for a new sibling. Both refs are one-shot redacted values: only installation-keyed HMAC commitments are persisted, so a repository path or remote URL never lands in durable state — do not self-censor into unstable refs.

## Recovery decision table (0.2)

Use this shipped 0.2 table after a reconnect, timeout, session rotation, host handoff, or a known
terminal same-task boundary. The 0.3 lineage/project selectors and modes extend the workflow below
without changing these recovery floors. Recovery adds no separate tool and never authorizes guessing
a task identity.

The operation view requires both `session_id` and `writer_id`. If a `start` response is lost before
those ids are returned, do not invent them or issue a fabricated status query: replay the exact
original `start` body once with its same `request_id`; the start idempotency path returns the stored
result or a typed boundary.

| Situation | Required action | Do not do |
| --- | --- | --- |
| A read-only timeout or reconnect permits a retry (`status`, diagnostics, or an operation-recovery read) | Repeat the same read intent with a new read `request_id`; preserve its view, filter, cursor, and limit. A missing read is not proof that the record is absent. | Reuse a timed-out read ID as if it were a write, or infer absence from an unreadable response. |
| Any write has an unknown outcome (`start`, `publish_work`, `check`, `respond`, `receipt`, or equivalent) | For `start` without returned session/writer ids, use the exact-start branch above. Otherwise read `status view=operation` with `filter.operation_request_id` set to the exact original write `request_id`. If `state=absent`, replay the exact original body once with that same request ID. If `state=complete`, use the stored outcome and do not replay. If `state=pending` includes an exact typed continuation, follow that continuation and its required user-approval path, then replay the original request once; without a continuation, retain and report pending. If `state=quarantined` or unknown, retain and report that boundary. | Mint a fresh request ID, fresh task, or sibling to escape an ambiguous write; replay a complete, quarantined, or pending operation without its exact continuation; fabricate start identity; guess the result. |
| A typed `OPERATION_PENDING` result is returned | If it is a `start` result without returned session/writer ids, use the exact-start branch above. Otherwise read operation status once with the exact `filter.operation_request_id`. Replay the original request only when the typed result or status page supplies an exact continuation and its required approval has completed; otherwise retain and report `pending`, `quarantined`, or unknown. | Blindly replay a pending request, fabricate start identity, repeat probes, create a new task, or claim a clean completion. |
| An exact held `session_id` is available after rotation or handoff | Use that exact `session_id` as the `mode=attach` selector. The host binding or CLI repository context supplies the canonical workspace fence; if the request carries identity refs, send the canonical `workspace_ref` + `external_ref` pair together. Use the returned successor session/writer and inspect `status` before continuing. | Add an unpaired `workspace_ref`, use a bare `task_id` or workspace membership as resume authority, or guess a sibling. |
| The same work resumes in a fresh host conversation with no held session | Call `start mode=create_or_attach` with the exact canonical `workspace_ref` + `external_ref` pair and no `session_id`. A fresh conversation is not automatically a new task. | Use a remote URL as `workspace_ref`, invent a task ID, or create an implicit second task. |
| The same-task pair/session cannot be recovered, every prior write has a known terminal outcome, and the user declares a bounded remaining or repaired verification scope | Start one intentional sibling with `mode=create`, the same canonical workspace, and a different stable `external_ref`. Give it a fresh plan, evidence, checks, and native binding; begin with a bounded handoff note that the predecessor receipt remains separate and unresolved. | Silently replace the task, inherit findings/obligations/evidence, reuse cross-task IDs without an existing contract, or invent lineage. |
| Recovery is exhausted but no new scope is declared, or a sibling would only make the old receipt look clean | Keep the old receipt and limitations, report the bounded failure, and wait for a supported continuation decision. | Loop through new siblings, move unresolved findings out of view, or present the latest sibling as whole-work closure. |
| The ledger has immutable proof limits, writes are terminal, and a fresh review of the repaired/current state is wanted | Use one explicitly scoped verification sibling on a healthy authorized binding. Publish its current-state plan and obligations, collect new admissible evidence/checks, verify native mapping, and disclose the old receipt's limits. | Repeat work only to obtain a smaller finding count, drop outstanding acceptance criteria, or present the sibling as proof that the old task was resolved. |
| The first `start` fails | Follow exact continuations and same-request recovery first, including a named one-time repair. Outside the fallback below, if startup remains blocked without an applicable recovery path, ask the user for intro and guidance and pause material work. | Invent a substitute workflow, skip recovery, keep working without a ledger task, or treat hook mapping as activation. |
| After successful startup Yoetz becomes unavailable, or a named one-time repair/retry ends in terminal unavailability | Continue ordinary work only when Yoetz is optional, the user/host permits it, and no write or approval remains pending; disclose which subsequent work lacks Yoetz proof. A first non-retryable `start` failure alone does not qualify. Once healthy, use the sibling row only when a tracked continuation is still wanted and no write is ambiguous. | Claim a live task, finding, verdict, or receipt, bypass startup handoff, or reset old findings by switching tasks. |

An explicit sibling is a new ledger boundary. Its receipt covers only its newly declared scope and
newly observed work. The predecessor's receipt, actionable findings, feedback obligations, and
unresolved status remain intact and must be disclosed when the sibling is used for a repaired or
remaining verification. A sibling does not inherit the predecessor's mapping, session, evidence
IDs, or receipt authority unless a current contract explicitly permits that exact reuse. If the old
task identity is unknown, say so rather than guessing or exposing a task ID. If an operation may
have committed, its operation-recovery row always wins over the sibling row.

“Not give up” means using this one bounded, explicit verification handoff after the known terminal
boundary. It does not mean creating tasks until a receipt looks clean.

## Upgrade and schema continuity

For an explicit user-requested update, run `yoetz upgrade` and preserve the existing host roots,
ownership, route, observation profile, settings, permissions, and integrations. Stop old hosts,
hooks, and the service through their supported lifecycle before accepting package replacement. The
package command is only the package step. On the next controlled service startup, a compatible
0.2-to-0.3 bundle migration runs backup-first before READY and preserves existing task data; the
user does not perform a per-task migration ceremony.

Treat a startup migration refusal, unsupported layout, holder conflict, or rollback-required result
as a typed boundary. Keep the same operation and backup identity, follow the returned supported
recovery procedure, and never retry with a new migration or edit storage by hand. An automatic
schema upgrade changes no observation consent, content selection, provider, disclosure, or egress
authority. Host refresh, activation, reload, and a fresh-session check remain separate evidence
facets. A package exit or a READY service without the corresponding migration result is not full
upgrade proof.

Tell the user that Yoetz is being used, and claim activation only after `start` returns. Apply
[startup failure precedence](coverage-and-receipts.md#startup-failure-precedence) before any
continue-and-disclose fallback. A first non-retryable failure does not itself permit material work
without a task. Never invent ledger, check, or receipt coverage.

## Material work

Before substantive work, publish the bounded plan, requested outcomes, and acceptance evidence.
Read [publication policy](publication-policy.md) before the first `publish_work`; declare explicit
obligations or the admitted empty-scope reason. Group work into independently reviewable outcomes,
not one obligation per file. Publish material transitions and evidence as they occur. Delegate only
when the task warrants and permits it; give each delegate a distinct logical writer and bounded
assignment, never a transcript. A delegate's summary is a claim, not proof.

## Cadence

<a id="cadence"></a>

| Operation | How often |
| --- | --- |
| `start` | Once per task, before substantive work. On resume (same or fresh conversation), `mode=create_or_attach` with the same `workspace_ref` + `external_ref` pair and no `session_id`; attach selectors are `session_id` or the ref pair, never bare `task_id`. When the host's session-start context names a task already mapped to this session, continue it with `mode=attach` and the `session_id` that context names instead of a new pair. |
| `publish_work` | One batch per material transition, usually one to eight events; a batch admits up to 100, so keep one transition in one batch rather than splitting it. A normal session is a handful of batches, never one per file, tool call, or message. Every set-valued reference list must already be unique and in ascending ASCII order; a one-element dry-run subset cannot demonstrate that kernel rule. |
| `status` | After resume, compaction, or delegate handoff, and before any completion claim. Not between routine tool calls. |
| `check` | After publishing the completion claim and its evidence, and again after any material edit or new evidence. A readable response identifying a finding that check returned is not material change; a redacted or unreadable response requires a recheck. Also consider a check when you move between subtasks or phases — after publishing that transition's batch — not only at the completion claim. Use `semantic_if_configured` only when review is known to be optional; select `semantic_required` when the user, effective policy, or named acceptance criterion requires independent semantic judgment; omit `mode` when relying on the configured default. Reserve `deterministic_only` for explicitly local/structural work or a deliberate no-egress choice and disclose `semantic_review_not_requested`; classify required review as unmet and preserve that requirement in later final checks. A check with no new events since the last one adds nothing. |
| `respond` | Once per finding, at the result frontier of the check that returned it — not the finding's `subject_frontier`, which precedes the finding's own record. |
| `receipt` | Once at the end, and again only if material state changed after the previous receipt. |

Under-publishing hides the work; over-publishing buries it. The test is whether an independent reader reviewing only the ledger would reach a different conclusion without the fact.

Live hook observation may append advisories or evidence after you read a frontier. Those
observation-authored records do not invalidate the held `expected_frontier`; ordinary cooperative
or imported work still does. On a real frontier conflict, re-read `status` rather than guessing.

Hook observation advice may include a next-action token. Those tokens are English next-move names,
not MCP tools and not `yoetz observe` verbs. The ten values are `resolve_failed_command`,
`rerun_approved_check`, `provide_verification`, `disclose_limitation`,
`address_subagent_finding`, `revise_plan_scope`, `refresh_observation`, `connect_provider`,
`attempt_semantic_dispatch`, and `reground_status`.

`refresh_observation` means observation coverage is incomplete or stale. Run
`yoetz observe status` from the host shell and wait for drain to recover. If the gap remains at
check time, disclose it as a limitation. There is no `refresh_observation` MCP tool or CLI command.

## Completion

Read `status` and `closure_readiness` before closing. Resolve remediable open obligations and
publish the completion claim with its evidence; that claim is an assertion, not a conclusion.
Read [coverage and receipts](coverage-and-receipts.md) before the first `check` for mode selection,
finding disposition, pending decisions, and coverage-bounded wording. `respond` does not clear a
finding: only a later qualifying check of the repaired record may resolve it. Recheck after material
changes or new evidence, not unchanged state. Request `receipt` last, then report what it supports.

Before claiming feedback complete, put its obligation in a supported plan revision or exact
next-version restatement; a stored or resolved obligation alone does not update effective scope.
Include each material delivery outcome the user requested in the effective obligations, or state the
narrower scope of the claim and receipt. Place the final receipt after the last material outcome it
covers, and distinguish check input, check result, response-only records, and later observation
appends using the returned facts.

Continue authorized implementation and focused verification through completion. Distinguish
completed work from an unmet required review; never silently substitute deterministic coverage
for required semantic review. If required review is unavailable or fails, report completed
implementation/structural checks separately from the unmet requirement and do not claim overall
completion. Describe local ledger writes separately from product-file changes.

## Errors and continuations

Read the typed result before acting. For a retryable read timeout or reconnect, issue a new read
`request_id` with the same intent. For any write with an unknown outcome, query `status
view=operation` using `filter.operation_request_id` for the exact original write `request_id` and
follow the state branches in the recovery table: replay once only for `absent`, use the stored
outcome for `complete`, and replay after an exact typed continuation and required approval only for
`pending`; retain and report `quarantined` or unknown state. A timeout does not authorize a fresh
task. A `retryable: false` error is terminal except for its exact typed continuation: do not probe
with new requests or other operations. Read
[Recovery](coverage-and-receipts.md#recovery) only when an error, outage, or inherited
unavailability requires it. Delegates inheriting `terminal_unavailable` make no Yoetz calls; only
the coordinator performs a named repair.

For `vault_initialization_required`, setup/settings changes, credential or vault operations,
import, or a recommendation, read [Setup and consent](request-templates.md#setup-and-consent)
before acting. Preserve exact request and pending identities. Never run service lifecycle commands
for `INTERNAL_ERROR` or a message that did not name that command.

1. Decide whether the task is material enough for Yoetz.
2. Start or attach with stable request identity and the intended create or attach semantics.
3. Publish a bounded plan, requested outcomes, acceptance evidence, and assignments. Declare completion scope with obligation refs, or — only when the effective ref set is empty — one typed `no_obligations_reason`: `no_material_change`, `single_atomic_change`, or `exploratory_scope_unknown`. Group large inventories into independently reviewable work packages; files are leaf evidence, not automatic obligations.
4. Delegate with `start mode=delegate` using the parent's current session. Give the intended child the complete returned `attach_handle` and bounded assignment context. The child attaches with that handle and uses its own returned session and writer. Do not send or publish full transcripts.
5. Publish material work-package transitions: assignment, decision, blocked attempt, independently useful result, completion, or revision. Omit routine reads, searches, formatting, and per-file mechanics.
6. Stay next to the record. After resume, compaction, handoff, or uncertainty about what is already done or committed, call `status`. `view=candidate_findings` is an advisory read: it creates no verdict, IDs, receipt, or event. For claim correction, read `candidate_findings`, `history`, and `results`, then dry-run one `claim_recorded/1.1.0` replacement: admissible support belongs in `supporting_refs`, partial/failed results in `limitation_refs`, and prior effective claim ids in `supersedes_claim_refs`.
7. Before completion, publish the intended material completion claim and current evidence, then call `check`. Read `declared_obligation_count`, `no_obligations_reason`, and `closure_readiness` on `status` first. A readable plan with zero declared obligations and no reason is blocked by `no_obligations_declared`; add effective obligations or revise the plan with a typed reason. The reason clears readiness but a completion claim over zero obligations still yields an insufficient-coverage gap. Resolve remediable blockers before spending a check or receipt. `receipt_findings_unresolved` is different: it says an actionable finding is still current. Only a later qualifying check of the repaired record resolves it, never a response; if you can repair the record, do so and recheck. Then read the finding's `resolved` state. If the issue re-fires, or it does not re-fire but remains `resolved=false` because the check did not qualify, proceed to the receipt rather than rechecking unchanged state. A deterministic check with otherwise readable proof may still qualify when its only case-wide host-observation limits are `captured_object_unavailable`, `content_unselected`, `host_outcome_unavailable`, or `unpaired_event`; those codes remain receipt limitations, require the original finding coverage to have been readable, and never relax semantic-finding proof. Select `semantic_required` when the user, effective policy, or named acceptance criterion requires independent semantic review; omit `mode` when relying on the configured default; use `semantic_if_configured` only when review is known to be optional; use `deterministic_only` only for explicitly local/structural checks, a semantic-disabled policy, or a deliberate no-egress choice, and disclose that limitation. Publish the smallest state-bound diff/symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.
8. Respond to each challenge by accepting and acting, supplying evidence, revising the claim, disputing with evidence, or stating an unresolved limitation. Agents can record `acknowledged`, `provenance_disputed`, or `rejected`; `waived` is reserved for an authorized local-CLI human. A readable response identifies the finding as answered and removes it from `unanswered_finding_count`, but it does not erase the historical finding, reduce `receipt_blocking_finding_count`, or close an underlying coverage gap. Repair the record and recheck: a later qualifying check that finds the same issue absent resolves the finding, which then stays visible as history; the receipt wording names resolved history apart from current findings and from coverage limitations.
9. Recheck after any material edit, evidence change, or plan change. A readable response to a finding returned by the current check needs no recheck; a redacted or unreadable response does because it cannot prove which finding it answered.
10. Request a receipt and keep the final answer no stronger than its weakest material coverage, freshness, unresolved findings, and limitations. All receipt formats (`json`, `markdown`, `text`) project under default policy; if a stricter owner policy blocks `json`, re-request `markdown` or `text`.
## Consumer and maintainer scope

To operate Yoetz, use schemas, guidance, and `status`; do not inspect its live SQLite databases,
catalog, or product source to reconstruct a request or recover a pending decision. An assigned
Yoetz development/debugging task may inspect source and isolated tests. That exception grants no
access to live mutable storage and no setup, credential, or egress authority.

## Evidence-first closure

Before a material evidence publication or completion claim, paginate `status view=evidence` at
one frontier, preserving the cursor-bound filter and limit. Reuse only matching observed IDs;
do not author duplicate digest-only placeholders. Read per-item availability and subject state:
a digest-only or clipped item does not make other native excerpts absent. The full procedure and
mixed example are in `yoetz://guidance/publication-policy.md`.

`status view=obligations` separates asserted `unattempted_items` accounting from `command_attempts`:
matching observation supports an attempt only; mismatch requires correcting the assertion or a
supported obligation revision with rationale; unknown does not mean the command never ran.
Do not copy a command onto an edit action just to close the accounting gap. Do not normalize shell
wrappers or changed test targets into an exact-command claim.

The optional CLI `yoetz closure-prepare --session-id <returned-session> --writer-id <returned-writer>`
reads the complete closure inventory without publishing. `yoetz closure-schema` describes explicit
selection inputs; `--input <selection.json>` prepares one operation with fresh lowercase UUID-v4
IDs, a dry-run publication where applicable, and a same-request recovery query. Review and submit
explicitly. It never invents attempts, evidence, finding dispositions, or obligation satisfaction.

### Delegation and project coordination

Each participating child has its own task ledger, session, and writer. The parent calls `start`
with `mode=delegate` and its current `session_id`; the returned child is `parent_minted` and
`accepted`. Pass the complete expiring, single-use `attach_handle` only to the intended child in
its assignment. The child calls `start mode=attach` with that handle and publishes with its own
returned session and writer. The parent keeps its original binding. A timeout requires the exact
same request and `request_id`; never create another child to recover a pending delegation.

A child without a handle may create a separate task with `parent_session_id`. This is
`self_registered` and `pending`: knowing a parent session is not authority to add blocking work.
The parent may publish `child_accepted` or `child_rejected`. Acceptance never changes origin;
an accepted relationship cannot later be rejected. Conflicting selectors, invalid parent refs,
expired handles, cycles, and configured depth or fan-out limits produce typed refusals. Recover
through the returned operation/status information, not by guessing identities.

Use `status view=lineage` after a handoff to inspect recorded relationships. Host observations
can be provisional annotations without a child ledger; never claim those children published or
checked work. A delegate summary is a claim, not proof. The parent's own obligations must cover
incorporating each child's work and verifying the combined result; a clean child receipt does
not establish integration. Preserve contradictory claims until a recorded decision resolves them.

Work state, session health, and receipt history are separate. A receipt never closes work:
publish `work_closed` when the work is complete. Lost contact leaves work open during the
documented recovery window; the service records abandonment only after it expires. Late evidence
remains visible with a gap. `delegation_cancelled` revokes the Yoetz capability, not the host
process. `child_written_off` and cancellation preserve an accepted dependency and its incomplete
outcome. Publish these lifecycle transitions through `publish_work`, using the request templates.

Parent checks use the latest dependency manifest recorded in the parent's ledger. Receipt creation
does not refresh it. If a newer manifest was recorded after the check, recheck for an updated
conclusion; an honest incomplete receipt remains available while children are active. Read
[coverage and receipts](coverage-and-receipts.md) for severity and freshness limits.

### Project coordination

Projects group work without granting attach authority. Automatic repository grouping begins with
a second live task when enabled. Use `status view=project`, `yoetz project status`, or `/project`
to inspect the admitted scope; membership and a dormant sibling never select a task to resume.
Each source workspace must grant workspace-level observation consent. The `prj_` project membership
object is a separate grouping fact. General and cross-repository projects additionally need approval
for the exact current membership generation. Revocation, unlink, dissolve, or opt-out invalidates
old deliveries; project membership does not authorize cross-repository semantic input.

Presence and duplicate-finding notes are advisory, separate from findings, and cannot change the
verdict. Overlap advice concerns declared structured resources, not inferred intent or ownership.
A `coordination_overlap` finding requires an explicitly declared coordination obligation and a
recorded context. Publish the obligation first, then `coordination_obligation_declared` with its
ID and the admitted detection, project, recipient task, and membership generation. Ordinary file
or source requested items identify potential overlap; they do not declare coordination work.
Publish a `coordination_disposition_recorded` event linking that same obligation and
evidence for `shared_work`, `sequencing`, or `scope_revision`. That addresses the obligation;
a later qualifying `coordination/0.1.0` check resolves a finding. A bare `respond` acknowledgement
does neither. An agreed shared-work disposition may leave the resource overlap in place.
### Protect an evidence-sensitive read

If a later claim, obligation, or finding may depend on a read, protect the next read before the
host call when possible. Use the exact current host session and an existing or planned structural
reference with the supported CLI:

```text
yoetz observe protect-read --workspace /exact/project \
  --session-id <host-session-id> --reference obl_<id> \
  --count 1 --json
```

The reference must use the closed `obl_`, `clm_`, or `fnd_` form. Protection is a bounded narrowing
of structural retention: at most 32 logical reads can be outstanding, the default lifetime is ten
minutes, and `--expires-at` cannot extend that bound. It requires the active observation grant and
the selected session, but it does not authorize content capture, privacy disclosure, a provider,
credentials, or a network route. Increasing protection within these bounds is a narrower use of
the existing grant and does not require a new permission decision.

The hook binds the protection to the exact native read identity. A pre-event reserves that identity
and its logical post consumes one slot; failures, denials, cancellation, partial or unknown
outcomes, and missing posts remain individually visible. Never replace a protected read with a
caller-supplied routine label or infer success from its content.

### Start selectors and findings

`start` resumes by a held session or exact identity pair; an intended new child may instead use
its complete attach handle. Never attach by bare `task_id`:

1. `session_id` — continue the exact session you already hold.
2. `workspace_ref` + `external_ref` as a pair — resolve the durable task for that work item without a `session_id`. Under ordinary `mode=create_or_attach`, the same pair creates on first use and attaches on later conversations; a different complete pair creates an independent sibling. A host hook may first recover a unique valid same-host mapping whose `SessionEnd` was received, under the workspace and lifecycle locks; no usable selector creates the new pair, while an ambiguous candidate, ownership failure, busy recovery lock, or changed snapshot refuses or retries without guessing. Attach mints a fresh session and writer; use the returned ids. The previously held session is retired for routing, but `status view=operation` from the successor session recovers that task's request ids, and `start mode=attach` with the retired `session_id` re-binds the same task. Incomplete or conflicting selectors refuse without guessing among siblings.

Convention:

- `workspace_ref` = the canonical absolute repository root of the working tree you are in (a linked Git worktree is its own root). Never a remote URL: selector commitments use the exact value. Repository grouping uses trusted repository identity separately and never substitutes for a workspace selector.
- `external_ref` = stable task identity within that project (branch name, issue reference, or plan slug). A hook-mapped task carries `<host>-session:<host session id>`; do not reproduce that pair. Attach to a host-mapped task with `mode=attach` and the `session_id` the session-start context names.

Same conversation resuming, or a fresh conversation continuing the same work → `mode=create_or_attach` with the same pair and no `session_id`. Sibling work → a different complete pair, or explicit `mode=create`. Selector refs are committed with installation-keyed HMACs rather than stored as structural plaintext. Keep them stable; do not self-censor into a different identity.

## Findings and recheck

Candidate findings are what deterministic packs currently say about the record. They carry no verdict and cannot be cited as a check. An empty candidate list means only that no rule fired in that advisory read. Only a recorded check can support receipt-bounded completion wording.

The cheapest finding is the one that never fires. Before the first `check`, read `status` with `view=obligations`: every row exposes its exact `requested_items` plus the `unattempted_items` subset under the existing obligation-text privacy category. Record each attempted value exactly on `action_recorded.attempted_items` (`attempted_items` belongs to that family alone — never a claim), and do not resolve the obligation while `unattempted_items` remains non-empty. Also confirm that completion scope is declared, every claim has linked evidence, and every declared obligation is resolved or deliberately left open with a stated reason. A typed empty-scope declaration still produces `completion_scope_declared_none` when a completion claim exists; it records the scope decision rather than proving it. This pre-flight costs one status read; an actionable finding costs the receipt for the rest of the task.

## Degraded and unavailable behavior

Never invent success. State the unavailable or degraded boundary, continue ordinary work when allowed, and do not claim a live task, finding, verdict, or receipt. If the host requires Yoetz, stop at that host-owned requirement.

Read `retryable` on every error before acting. A `retryable: false` error is terminal for that call: do not repeat it with a new `request_id`, do not probe with other Yoetz operations to "confirm", and do not rewrite state to work around it. Record the `correlation_id`; if a shell is available, run `yoetz service diagnostics --correlation-id <id>` once and report its bounded record, then continue without Yoetz. A `SERVICE_UNAVAILABLE` error whose message names a repair command (for example `yoetz service restart` when the running service belongs to a different Yoetz installation) is the one case where a single repair is appropriate: run exactly that command if the host allows shell use, then retry the original call once with the same `request_id`. If it fails again, treat Yoetz as unavailable for the rest of the task and say so. Lifecycle commands (`yoetz service stop`, `service run`, `service restart`) are never a response to `INTERNAL_ERROR` or to any message that did not name that exact command.

One typed exception: an error carrying `safe_details.continuation: vault_initialization_required` is a bounded first-run handoff, not an ordinary terminal error. The vault was never initialized, nothing was written, and no unlock or recovery path applies. Suspend the original request and follow the continuation exactly once: run the carried `prepare_command`, present the returned pending's danger text and digests to the user, and wait for their exact decision; if a pending consent action already exists, read it with `yoetz consent status` instead of preparing another. Yoetz generates and stores the initialization secret locally — never request, receive, or transmit a secret or recovery material. Relaying an approval through the carried `authorize_command` is valid only for an allowlisted first-party agent-chat client acting on an explicit current-chat instruction; every other host directs the user to run the carried `review_command` on a local terminal and waits. When the ceremony reports ready, replay the exact original `request_id` and body once (`replay_request_id` names it) and continue normally; on denial or expiry, do not prepare again in the same task — state the boundary and continue without Yoetz. Never create a replacement `start`, and never treat chat assent as authority.

### Inherited unavailability and delegation

An availability failure belongs to the host binding — this MCP process, its route, and the service endpoint — not to the request that first saw it. When an error carries `safe_details.availability: terminal_unavailable`, the bridge has latched that state: every later call under a new `request_id` returns the same `correlation_id` with `availability_inherited: true` and records no new diagnostic, until the named repair changes the running service, the original `request_id` replays successfully, or — for a `retryable: true` class only — the bridge's own quiet handshake finds the service listening again, in which case the call simply proceeds. That handshake belongs to the bridge, never to you: nobody probes to find out. An inherited answer is not a fresh failure; do not diagnose it again.

When you delegate after that result, carry it into every assignment as a bounded `yoetz_availability` block: `state: terminal_unavailable`, the host binding (`host_profile`, `route_profile`), the parent `correlation_id` and original `request_id`, and the proof limit ("no live Yoetz ledger, publication, check, or receipt exists for this task") — never transcript content. A delegate that inherits `terminal_unavailable` makes no Yoetz call for that binding and work item: no `start`, `status`, `check`, diagnostics, or `yoetz service` command. It publishes nothing, states that the parent has no live ledger, and returns its work to the coordinator. Only the coordinator runs the one repair the typed result named and replays the original `request_id` once. In the final report, separate the initial integration cause (the parent's correlation) from delegate amplification, and never claim delegate publications, assignments, or attribution without a task and session.

## Safety and privacy

Publish no hidden reasoning, transcript, secret, broad repository content, or unrelated source. Prefer typed facts, digests, bounded counts, and only the smallest material state-bound excerpt. See [publication policy](publication-policy.md) and [coverage and receipts](coverage-and-receipts.md).
### Promote a buffered read

If the read becomes relevant after classification but before the bounded buffer is delivered,
promote its exact source identity:

```text
yoetz observe promote --workspace /exact/project \
  --source-identity <source-identity> --json
```

Promotion retains the original structural identity, route, and observed time as an individual
record. It only works while that identity is buffered. After delivery the result is
`promotion_window_closed` with `content_availability: not_retained`; it cannot recover omitted or
expired bytes. Rerun or reacquire the current state when needed and record it as new evidence with
its new time and subject state. Do not use that rerun to prove the historical state.
