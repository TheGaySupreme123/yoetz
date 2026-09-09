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

`start` resumes by one of two selectors (never by bare `task_id`):

1. `session_id` — continue the exact session you already hold.
2. `workspace_ref` + `external_ref` as a pair — resolve the durable task for that project work item without a `session_id`. Under `mode=create_or_attach`, the same pair creates on first use and attaches on every later conversation. Attach mints a fresh session and writer; use the returned ids. The previously held session is retired for routing, but `status view=operation` from the successor session recovers that task's request ids, and `start mode=attach` with the retired `session_id` re-binds the same task. A different `external_ref` in a workspace that already has a task is `SESSION_CONFLICT` (`workspace_task_exists`) without task selectors — attach with the previously held session id, or retry with `mode=create` for an explicit sibling.

Convention:

- `workspace_ref` = the canonical absolute repository root of the working tree you are in (a linked Git worktree is its own root). Never a remote URL: the workspace commitment is keyed on the exact value, so hook observation on Claude Code, Codex, and Cursor auto-attaches with this root and `workspace_task_exists` protects you from a sibling only under the same value. A remote URL or any other spelling is a different workspace and silently creates a sibling task.
- `external_ref` = stable task identity within that project (branch name, issue reference, or plan slug). A hook-mapped task carries `<host>-session:<host session id>`; do not reproduce that pair. Attach to a host-mapped task with `mode=attach` and the `session_id` the session-start context names.

Same conversation resuming, or a fresh conversation continuing the same work → `mode=create_or_attach` with the same pair and no `session_id`. Sibling work in the same project → use `mode=create` with the same `workspace_ref` and a different `external_ref` only when the recovery table permits one; do not use `create_or_attach` for a new sibling. Both refs are one-shot redacted values: only installation-keyed HMAC commitments are persisted, so a repository path or remote URL never lands in durable state — do not self-censor into unstable refs.

## Recovery decision table (0.2)

Use this table after a reconnect, timeout, session rotation, host handoff, or a known terminal
same-task boundary. It uses the existing `start`, `status`, and workflow operations; it does not
add a task-lineage field or change the wire contract.

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
| Yoetz remains unavailable after its named one-time repair/retry, or returns a non-retryable error | Continue ordinary work only when the user/host permits it and disclose which subsequent work lacks Yoetz proof. Once healthy, use the sibling row only when a tracked continuation is still wanted and no write is ambiguous. | Claim a live task, finding, verdict, or receipt, or reset old findings by switching tasks. |

An explicit sibling is a new ledger boundary. Its receipt covers only its newly declared scope and
newly observed work. The predecessor's receipt, actionable findings, feedback obligations, and
unresolved status remain intact and must be disclosed when the sibling is used for a repaired or
remaining verification. A sibling does not inherit the predecessor's mapping, session, evidence
IDs, or receipt authority unless a current contract explicitly permits that exact reuse. If the old
task identity is unknown, say so rather than guessing or exposing a task ID. If an operation may
have committed, its operation-recovery row always wins over the sibling row.

“Not give up” means using this one bounded, explicit verification handoff after the known terminal
boundary. It does not mean creating tasks until a receipt looks clean.

Tell the user that Yoetz is being used, and claim activation only after `start` returns. If the
optional service is unavailable, continue the task unless the user or host requires it; disclose
which ledger, check, or receipt is missing. Never invent state.

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
