# Yoetz cooperative workflow

Read before the first `start`, or when resuming without the prior workflow in context. Use the
current tool schemas; missing schema metadata routes to [request templates](request-templates.md).
Yoetz records participant-published facts and checks that bounded record; it does not prove the
underlying work correct.

## Start and resume

`start` resumes by one of two selectors (never by bare `task_id`):

1. `session_id` — continue the exact session you already hold.
2. `workspace_ref` + `external_ref` as a pair — resolve the durable task for that project work item without a `session_id`. Under `mode=create_or_attach`, the same pair creates on first use and attaches on every later conversation. Attach mints a fresh session and writer; use the returned ids. The previously held session is retired for routing, but `status view=operation` from the successor session recovers that task's request ids, and `start mode=attach` with the retired `session_id` re-binds the same task. A different `external_ref` in a workspace that already has a task is `SESSION_CONFLICT` (`workspace_task_exists`) without task selectors — attach with the previously held session id, or retry with `mode=create` for an explicit sibling.

Convention:

- `workspace_ref` = the canonical absolute repository root of the working tree you are in (a linked Git worktree is its own root). Never a remote URL: the workspace commitment is keyed on the exact value, so hook observation on Claude Code, Codex, and Cursor auto-attaches with this root and `workspace_task_exists` protects you from a sibling only under the same value. A remote URL or any other spelling is a different workspace and silently creates a sibling task.
- `external_ref` = stable task identity within that project (branch name, issue reference, or plan slug). A hook-mapped task carries `<host>-session:<host session id>`; do not reproduce that pair. Attach to a host-mapped task with `mode=attach` and the `session_id` the session-start context names.

Same conversation resuming, or a fresh conversation continuing the same work → `mode=create_or_attach` with the same pair and no `session_id`. Sibling work in the same project → `mode=create` with the same `workspace_ref` and a different `external_ref` (do not use `create_or_attach` for a new sibling). Both refs are one-shot redacted values: only installation-keyed HMAC commitments are persisted, so a repository path or remote URL never lands in durable state — do not self-censor into unstable refs.

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
| `check` | After publishing the completion claim and its evidence, and again after any material edit or new evidence. A readable response identifying a finding that check returned is not material change; a redacted or unreadable response requires a recheck. Also consider a check when you move between subtasks or phases — after publishing that transition's batch — not only at the completion claim. Choose the mode deliberately: `deterministic_only` is local and fast and catches record-hygiene gaps (stale ledger, digest-only evidence, open obligations) early; reserve semantic review for the claim unless the transition itself warrants it. A check with no new events since the last one adds nothing. |
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

Continue authorized implementation and focused verification through completion. Distinguish
completed work from an unmet required review; never silently substitute deterministic coverage
for required semantic review. Describe local ledger writes separately from product-file changes.

## Errors and continuations

Read the typed result before acting. Reuse the original `request_id` after timeout or reconnect;
a timeout has unknown outcome. Use `status view=operation` to recover an operation rather than
reading live storage. A `retryable: false` error is terminal except for its exact typed continuation:
do not probe with new requests or other operations. Read [Recovery](coverage-and-receipts.md#recovery)
only when an error, outage, or inherited unavailability requires it. Delegates inheriting
`terminal_unavailable` make no Yoetz calls; only the coordinator performs a named repair.

For `vault_initialization_required`, setup/settings changes, credential or vault operations,
import, or a recommendation, read [Setup and consent](request-templates.md#setup-and-consent)
before acting. Preserve exact request and pending identities. Never run service lifecycle commands
for `INTERNAL_ERROR` or a message that did not name that command.

## Consumer and maintainer scope

To operate Yoetz, use schemas, guidance, and `status`; do not inspect its live SQLite databases,
catalog, or product source to reconstruct a request or recover a pending decision. An assigned
Yoetz development/debugging task may inspect source and isolated tests. That exception grants no
access to live mutable storage and no setup, credential, or egress authority.
