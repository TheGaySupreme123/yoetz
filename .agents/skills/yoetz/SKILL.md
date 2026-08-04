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
product source. The schema is authority for field shapes; the guidance is authority for which call
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
- [ ] publish the completion claim and its evidence
- [ ] check (choose mode deliberately)
- [ ] respond to each finding, then recheck
- [ ] receipt
- [ ] final answer no stronger than the receipt's weakest coverage
```

## Before you claim done

Read `closure_readiness` on any `status` result first: it names the open obligations, unresolved findings, and declared gaps that currently bound a conclusion. Spending a check or receipt while those stand returns a predictably insufficient result. Publish the smallest state-bound diff or symbol and the directly relevant test or failure excerpt; never rely on self-asserted completion prose alone.

Prefer `semantic_if_configured` for material implementation/review claims; use `semantic_required` when qualitative correctness is part of completion; use `deterministic_only` only for structural/no-egress checks and disclose that limitation. Omitting `mode` resolves via policy.

Prefer receipt format `markdown` or `text`. Default policy can return usable `json` receipts; if `json` is blocked under a strict policy, switch format rather than retrying forever.

## A recorded finding stays recorded

`respond` records your disposition and links your evidence; it does not clear the finding. Every actionable finding recorded in a task keeps the receipt conclusion at `unresolved_findings_remain`, whichever disposition you record, even when later checks return no findings. Repair the record anyway — it stops the next check from firing the same rule — but do not call an acknowledged finding resolved, and do not promise a clean receipt afterwards.

Publish exactly the first time: an exact `attempted_items` entry for every requested item, evidence for every claim.

## When to stop retrying

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
