# Yoetz cooperative workflow

## What Yoetz does and does not do

Yoetz is a local ledger for bounded, participant-published work facts and a deterministic checker of that record. It does not observe the workspace, enforce a process, authenticate authorship, record hidden reasoning, or prove that work is correct.

## When to activate

Use Yoetz for material multi-step work, multiple requested outcomes, delegation, meaningful verification, long-running or resumable work, or a material completion claim. Skip it for translation, ordinary questions, explanations, and trivial edits where the ceremony would exceed the integrity benefit.

## How often to call each operation

<a id="cadence"></a>

| Operation | How often |
| --- | --- |
| `start` | Once per task, before substantive work. On resume (same or fresh conversation), `mode=create_or_attach` with the same `workspace_ref` + `external_ref` pair and no `session_id`; attach selectors are `session_id` or the ref pair, never bare `task_id`. |
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

## When to stop retrying

<a id="stop-rules"></a>

- Semantic review that does not succeed is a coverage gap, not a retry problem. `not_configured`, `blocked_by_policy`, and `human_denied` will not change without owner action: take the first answer, except when installed plugin status names `policy` while this process is a live strict route (`full_restart_required` / activation mismatch). That case needs a full host quit, not a privacy change and not a fresh semantic check against the stale process. `unavailable` and `timeout` already spent that job's own attempt budget. `refused`, `failed`, and every `invalid` reason except `response_content_invalid` are not retried inside the job at all. `response_content_invalid` (an incomplete or overlong provider answer) may spend exactly one in-job repair retry when the profile has retry budget and deadline left, so by the time you see it that repair is already spent. A fresh request is a fresh gamble rather than a continuation.
- When a second job in one session again returns no judgment, stop: run `deterministic_only`, disclose the gap naming the recorded `semantic_status` and `semantic_reason`, and do not spend a third job on the same binding.
- On `OPERATION_PENDING`, read `status` with `view=operation` once and replay the same `request_id` once. If it is still pending, continue with a new deterministic-only request and state that the earlier operation never reached a terminal result.
- A rejected request is a schema problem, not a retry problem. Correct the named field and resend once; do not resend the same body.

## Startup and availability disclosure

Tell the user briefly that Yoetz is being used as a local work ledger and verifier. Do not imply initialization succeeded before `start` returns. If the optional service is unavailable, continue unless the user or host requires it, disclose that no live ledger or receipt will exist, and invent no state.

If the host drops schema examples or renders required fields as unknown, read
`yoetz://guidance/request-templates.md` and replace every illustrative value in the complete request
body. Never inspect product source to reconstruct a request.

## The ten steps

1. Decide whether the task is material enough for Yoetz.
2. Start or attach with stable request identity and the intended create or attach semantics.
3. Publish a bounded plan, requested outcomes, acceptance evidence, and assignments. Declare completion scope with obligation refs, or — only when the effective ref set is empty — one typed `no_obligations_reason`: `no_material_change`, `single_atomic_change`, or `exploratory_scope_unknown`. Group large inventories into independently reviewable work packages; files are leaf evidence, not automatic obligations.
4. Delegate with the session, task, distinct logical writer, and bounded assignment context. Do not send or publish full transcripts.
5. Publish material work-package transitions: assignment, decision, blocked attempt, independently useful result, completion, or revision. Omit routine reads, searches, formatting, and per-file mechanics.
6. Stay next to the record. After resume, compaction, handoff, or uncertainty about what is already done or committed, call `status`. `view=candidate_findings` is an advisory read: it creates no verdict, IDs, receipt, or event. For claim correction, read `candidate_findings`, `history`, and `results`, then dry-run one `claim_recorded/1.1.0` replacement: admissible support belongs in `supporting_refs`, partial/failed results in `limitation_refs`, and prior effective claim ids in `supersedes_claim_refs`.
7. Before completion, publish the intended material completion claim and current evidence, then call `check`. Read `declared_obligation_count`, `no_obligations_reason`, and `closure_readiness` on `status` first. A readable plan with zero declared obligations and no reason is blocked by `no_obligations_declared`; add effective obligations or revise the plan with a typed reason. The reason clears readiness but a completion claim over zero obligations still yields an insufficient-coverage gap. Resolve remediable blockers before spending a check or receipt. `receipt_findings_unresolved` is different: it says an actionable finding is still current. Only a later qualifying check of the repaired record resolves it, never a response; if you can repair the record, do so and recheck. Then read the finding's `resolved` state. If the issue re-fires, or it does not re-fire but remains `resolved=false` because the check did not qualify, proceed to the receipt rather than rechecking unchanged state. A deterministic check with otherwise readable proof may still qualify when its only case-wide host-observation limits are `captured_object_unavailable`, `content_unselected`, `host_outcome_unavailable`, or `unpaired_event`; those codes remain receipt limitations, require the original finding coverage to have been readable, and never relax semantic-finding proof. Choose mode deliberately: `semantic_if_configured` for most material implementation/review claims; `semantic_required` when completion depends on qualitative correctness, design conformance, security/privacy reasoning, interoperability, or whether the code satisfies the ask; `deterministic_only` only for explicitly local/structural checks, semantic-disabled policy, or a deliberate no-egress choice — and disclose that limitation. Publish the smallest state-bound diff/symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.
8. Respond to each challenge by accepting and acting, supplying evidence, revising the claim, disputing with evidence, or stating an unresolved limitation. Agents can record `acknowledged`, `provenance_disputed`, or `rejected`; `waived` is reserved for an authorized local-CLI human. A readable response identifies the finding as answered and removes it from `unanswered_finding_count`, but it does not erase the historical finding, reduce `receipt_blocking_finding_count`, or close an underlying coverage gap. Repair the record and recheck: a later qualifying check that finds the same issue absent resolves the finding, which then stays visible as history; the receipt wording names resolved history apart from current findings and from coverage limitations.
9. Recheck after any material edit, evidence change, or plan change. A readable response to a finding returned by the current check needs no recheck; a redacted or unreadable response does because it cannot prove which finding it answered.
10. Request a receipt and keep the final answer no stronger than its weakest material coverage, freshness, unresolved findings, and limitations. All receipt formats (`json`, `markdown`, `text`) project under default policy; if a stricter owner policy blocks `json`, re-request `markdown` or `text`.

## State the record you changed

Using Yoetz is itself a state change. A run that starts a task, advances the ledger, or obtains a check or receipt has changed durable local state even when it edited no product file. Separate the two in the final answer instead of collapsing them.

Permitted: “No product source, provider configuration, credential binding, or privacy authorization was changed. This run created a Yoetz task, published N events, and recorded one check and one receipt.”

Forbidden: “Nothing changed” or “no runtime state changed” after a real session, publication, check, or receipt.

Reuse the original request and operation IDs after timeout or reconnect. A timeout has unknown outcome; retry idempotently or inspect status. An operation that reports failure after its write may have committed: read `status` for the authoritative frontier before assuming it failed. Prefer `status view=operation` with the write's `request_id` as a state lookup without reconstructing the body — a complete `publish_work` surfaces stored frontiers and accepted event ids; pending, quarantined, absent, and non-publish states report only what is honest for that state. When replaying a write, reuse the same `request_id` rather than composing a new one — a matching body returns the stored result, and a different body returns `REQUEST_IDENTITY_CONFLICT` with the committed frontier rather than re-appending.

## Multi-agent attribution and handoff

The parent publishes assignments and gives each delegate a distinct logical writer identity. A delegate publishes its own bounded claims; the parent neither impersonates it nor upgrades self-asserted authorship. Before integration, read current assignments, decisions, contradictions, and obligations. A delegate summary is a claim, not proof, and contradictions remain visible until a recorded decision resolves them.

## Resume and compaction

On resume, attach to the existing task and read status before reconstructing work from memory. Preserve request and writer sequences and do not duplicate a prior publication. A trigger, when an exact capability profile proves one, may prompt the same bounded re-grounding; it observes nothing and changes no coverage.

### Workspace grouping and attach selectors

`start` resumes by one of two selectors (never by bare `task_id`):

1. `session_id` — continue the exact session you already hold.
2. `workspace_ref` + `external_ref` as a pair — resolve the durable task for that project work item without a `session_id`. Under `mode=create_or_attach`, the same pair creates on first use and attaches on every later conversation. Attach mints a fresh session and writer; use the returned ids. The previously held session is retired for routing, but `status view=operation` from the successor session recovers that task's request ids, and `start mode=attach` with the retired `session_id` re-binds the same task. A different `external_ref` in a workspace that already has a task is `SESSION_CONFLICT` (`workspace_task_exists`) without task selectors — attach with the previously held session id, or retry with `mode=create` for an explicit sibling.

Convention:

- `workspace_ref` = stable project identity (repository remote URL, or absolute repository root when there is no remote).
- `external_ref` = stable task identity within that project (branch name, issue reference, or plan slug).

Same conversation resuming, or a fresh conversation continuing the same work → `mode=create_or_attach` with the same pair and no `session_id`. Sibling work in the same project → `mode=create` with the same `workspace_ref` and a different `external_ref` (do not use `create_or_attach` for a new sibling). Both refs are one-shot redacted values: only installation-keyed HMAC commitments are persisted, so a repository path or remote URL never lands in durable state — do not self-censor into unstable refs.

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
