---
name: yoetz
description: Records material work in a local Yoetz ledger and checks completion claims against that record. Use for multi-step, delegated, resumable, or verification-heavy work — implementation, review, migration, refactor, research, multi-agent handoff — and before any completion claim, receipt, or handoff summary. Call start before substantive work, publish material transitions as they happen, then check and request a receipt before saying done. Also use on resume, after compaction, or when uncertain what is already done or committed. Skip trivial questions, single-line edits, and explanations where the ledger ceremony exceeds the integrity benefit.
metadata:
  short-description: Local work ledger and completion checks for material multi-step or delegated work — call start before substantive work, check and receipt before claiming done
---

# Yoetz for Codex

Yoetz is a local work ledger and deterministic checker. It records only what participants publish and checks that record at a named frontier. It is not an enforcement system, observer, authorship proof, transcript recorder, or orchestrator, and a clean check does not mean the underlying work is correct.

## Step 0: read the guidance before the first call

Read these with the MCP `resources/read` request for the exact URI. They are served by the `yoetz`
server itself, so they resolve without any repository checkout. After install they are also on disk
beside this file as `references/workflow.md`, `references/coverage-and-receipts.md`,
`references/publication-policy.md`, `references/request-templates.md`, and
`references/agent-instructions.md`.

- Before the first `start`: `yoetz://guidance/workflow.md` (the ten steps, cadence, resume behavior) and `yoetz://guidance/coverage-and-receipts.md` (coverage, findings, receipt wording).
- Before the first `publish_work`: `yoetz://guidance/publication-policy.md` (what is material and safe to publish).
- When schema metadata is missing or a request is rejected:
  `yoetz://guidance/request-templates.md` (complete bodies for all six operations and all nine
  ordinary publish families; replace every illustrative value before use).
- `yoetz://guidance/agent-instructions.md` is the non-negotiable safety floor. It is already delivered as the server's initialize instructions; re-read it if that text is not in context.

Author each request from its tool input schema plus this guidance, never from memory or from product
source. If the host drops schema metadata, use the request templates resource rather than reading
product source. This holds when something goes wrong too: Yoetz's own SQLite databases, catalog
files, and source tree are never the way to work out what a result meant. Every recoverable fact is
reachable through `status`. The schema is authority for field shapes; the guidance is authority for which call
to make and when. `start` takes `mode` as exactly one of `create`, `attach`, or `create_or_attach`.

## When to activate

Activate for material multi-step, delegated, resumable, or verification-heavy work, and call `start` before substantive work. Activate on resume, after compaction, and before any completion claim, receipt, or handoff summary. Do not activate for trivial questions or edits where the ledger ceremony exceeds the integrity benefit.

Tell the user briefly that you are using Yoetz as a local work ledger and verifier. Use the MCP server named `yoetz`; do not imply it started until `start` returns. If the optional server is unavailable, continue unless the user or host requires it, and say that no live Yoetz ledger or receipt will exist.

## How often to call each operation

| Operation | How often |
| --- | --- |
| `start` | Once per task, before substantive work. On resume (same or fresh conversation), `mode=create_or_attach` with the same `workspace_ref` (project remote URL or absolute root) + `external_ref` (branch/issue/plan slug) pair and no `session_id`. Sibling work: same workspace, different external_ref. Attach selectors are `session_id` or the ref pair — never bare `task_id`. |
| `publish_work` | One batch per material transition, usually one to eight events; a batch admits up to 100, so keep one transition in one batch rather than splitting it. A normal session is a handful of batches, never one per file, tool call, or message. Set `dry_run: true` first for an unfamiliar batch shape. |
| `status` | After resume, compaction, or delegate handoff, and before any completion claim. Not between routine tool calls. |
| `check` | After publishing the completion claim and its evidence, and again after any material edit, new evidence, or finding response. A check with no new events since the last one adds nothing. |
| `respond` | Once per finding, at that finding's recorded frontier. |
| `receipt` | Once at the end, and again only if material state changed after the previous receipt. |

Not publishable: reading, searching, formatting, regenerating derived files, repeating a status read, or republishing unchanged state.

## Task checklist

Copy this and check items off as you go:

```
- [ ] Read workflow.md and coverage-and-receipts.md
- [ ] start (stable request identity; create_or_attach with workspace_ref + external_ref, or attach via session_id)
- [ ] publish the plan, requested outcomes, acceptance evidence, assignments
- [ ] publish each material transition as it happens
- [ ] status before closing: read closure_readiness for open obligations and gaps
- [ ] publish the completion claim and its evidence (an assertion, not a conclusion)
- [ ] check (choose mode deliberately)
- [ ] if awaiting_human: show the supplied command, wait, then replay the exact same `check` request with the same `request_id`
- [ ] respond to each finding, then recheck
- [ ] receipt
- [ ] final answer no stronger than the receipt's weakest coverage
```

## Semantic review authority: who already decided what

Two different permissions are in play, and confusing them is what strands a check.

**Your host's authorization** is Codex deciding whether you may call the `check` tool at all. **A
Yoetz disclosure decision** is the machine owner deciding whether one exact prepared case may leave
the machine. Getting the first never grants the second, and needing the second does not mean the
first was wrong.

Ordinary `check` is default-safe. If a provider route is active, the user selected it explicitly
during setup and committed a bounded standing policy: an exact provider, model, endpoint profile,
workspace, purpose, category set, retention ceiling, and credential authority. `check` **cannot
widen any of that**. It cannot change the provider, reach a different workspace, add a category,
raise a limit, or reuse a credential for anything else. Whether a case is actually dispatched stays
enforced at runtime by the installed provider binding and privacy policy, not by the wording of your
request or by anything you can set on the call.

So calling `check` is not a request for new permission. It is a request to run the review the user
already authorized. Do not ask the user to re-approve a route they configured, and do not describe
an ordinary check as if it were an egress decision.

## When a check is waiting on a local decision

If a check returns `semantic_status: awaiting_human` with `semantic_reason:
human_approval_required`, its typed `continuation` identifies either a standing repository setup
handoff or a one-use disclosure decision. Both carry the exact trusted command and original request
id; only the one-use confirmation carries a `pending_id` and `expires_at`.

Do exactly this:

- **Show the user the supplied command verbatim** — it is `yoetz privacy decide-disclosure <pending_id>`
  with the real id filled in. Do not retype it from memory or reconstruct it.
- **Do not create a new check request.** A fresh request builds a fresh case with a fresh provider
  request id, which abandons the proposal the user is being asked to approve. After they decide,
  replay the *exact same* `check` request with the *same* `request_id`.
- **Do not inspect the Yoetz database, catalog files, or product source** to find the pending id or
  work out what happened. Everything you need is in the result. If you lost it, read `status` with
  `view=operation` for that `operation_request_id`: it returns the same continuation when the
  durable record of the wait is still available. If it comes back without one, the decision window
  is gone — run the check again rather than guessing an id.
- **Do not request a receipt yet, and do not tell the user the task is done.** The check has not
  reached a terminal result, so there is no verdict, no coverage, and nothing to conclude from.

`awaiting_human` is not a coverage gap and not a failure. It is the one nonterminal check outcome.
For one-use confirmation the operation, semantic job, and physical attempt remain open. Missing
standing repository authority stops earlier with only the operation suspended and no provider job
or attempt created. Denial or expiry resolves a one-use decision once; a provider retry creates a
fresh proposal and needs its own decision.

## When the current repository grant is missing

Act only when Yoetz explicitly reports that the current repository grant is missing; do not infer
it from a generic policy refusal. Tell the user to run the exact trusted CLI/TUI entrypoint:

```text
yoetz --privacy
```

Ask them to complete the repository review there and then tell you when it is done. A “yes” or
“done” in agent chat is notification only and never grants authority. Do not try to approve through
MCP, arguments, environment, stdin, or terminal automation.

This is a standing grant for that exact repository until revoked or changed. It is different from
the one-use `confirm_every_request` decision carried by the other continuation kind. Keep the
missing-grant request open: recover it with `status` using `view=operation`, or replay the exact
original check with the same `request_id`. Never create a fresh request. Denial, expiry,
cancellation, stale authority, or an incomplete ceremony remains a no dispatch outcome.

## Before you claim done

Publishing a completion claim records an **assertion awaiting verification**. It is the input to a
check, not the output of one, and it is not permission to tell the user the work is finished. The
sequence is: publish the claim and its evidence → `check` → respond and recheck if there are
findings → `receipt` → then answer. Announcing completion after the publish step — or after a check
that never reached a terminal result — states as settled something the record does not yet support.

Read `closure_readiness` on any `status` result first: it names the open obligations, unresolved findings, and declared gaps that currently bound a conclusion. Spending a check or receipt while those stand returns a predictably insufficient result. Publish the smallest state-bound diff or symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.

Prefer `semantic_if_configured` for material implementation/review claims; use `semantic_required` when qualitative correctness is part of completion; use `deterministic_only` only for structural/no-egress checks and disclose that limitation. Omitting `mode` resolves via policy.

Prefer receipt format `markdown` or `text`. Default policy can return usable `json` receipts; if `json` is blocked under a strict policy, switch format rather than retrying forever.

## A recorded finding stays recorded

`respond` records your disposition and links your evidence; it does not clear the finding. Every actionable finding recorded in a task keeps the receipt conclusion at `unresolved_findings_remain`, whichever disposition you record, even when later checks return no findings. Repair the record anyway — it stops the next check from firing the same rule — but do not call an acknowledged finding resolved, and do not promise a clean receipt afterwards.

Publish exactly the first time: an exact `attempted_items` entry for every requested item, evidence for every claim.

## When to stop retrying

- `awaiting_human` is the exception to everything in this section: it is nonterminal, so it is neither a gap to disclose nor a retry to spend. Follow the continuation instead — see "When a check is waiting on a local decision" above.
- Semantic review that does not succeed is a coverage gap, not a retry problem. `not_configured`, `blocked_by_policy`, and `human_denied` will not change without owner action — take the first answer. `unavailable` and `timeout` already spent that job's own attempt budget. `refused`, `invalid`, and `failed` are not retried inside the job at all, so a fresh request is a fresh gamble.
- When a second job in one session again returns no judgment, stop: run `deterministic_only`, disclose the gap naming the recorded `semantic_status` and `semantic_reason`, and do not spend a third job.
- On `OPERATION_PENDING`, read `status` with `view=operation` once and replay the same `request_id` once. If it is still pending, continue with a new deterministic-only request and say the earlier operation never reached a terminal result.
- A rejected request is a schema problem, not a retry problem. Correct the named field and resend once.

## Never publish

Chain-of-thought or hidden reasoning; full prompts, transcripts, or conversation history; credentials or secrets; whole files, whole repositories, or broad unrelated source. A small problem-local excerpt is permitted only when it is material, in scope, and bound to the relevant state.

## Word conclusions honestly

Match the weakest material coverage and every limitation in the current receipt. Never fabricate a session ID, publication, finding, verdict, or receipt; if a call fails or Yoetz is unavailable, say that no live Yoetz record or receipt is available.

Permitted: "Yoetz found no deterministic issue in the cooperatively published record at this frontier."

Forbidden: "Yoetz verified the work."

Using Yoetz is itself a state change. A run that started a task, advanced the ledger, or obtained a check or receipt has changed durable local state even when it edited no product file; separate the two in the final answer instead of saying nothing changed.

## Compatibility

Use only the six registered Yoetz MCP tools and their current schemas. Every tool request's `client` is exactly `{kind, version, integration}`; do not send `client.id` or any other client field. Fields backed by canonical integers stay JSON strings on the wire: send frontier `sequence` and pagination `limit` as strings such as `"10"`, never JSON numbers. Compatibility is exact and evidence-bound in the adjacent `manifest.json`; an empty profile set means this Yoetz skill advertises no tested harness version or hook.
