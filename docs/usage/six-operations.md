# The six operations

`start`, `publish_work`, `check`, `respond`, `status`, `receipt`. Identical request and result
contracts on the CLI and over MCP — same names, same fields, same errors
([ADR-002](../adr/ADR-002-canonical-protocol.md),
[ADR-010](../adr/ADR-010-harness-integration-port.md)).

Everything else the CLI offers — `import`, `review`, `backup`, `restore`, `migrate`, `integrate`,
`version`, `service`, `mcp`, `state` — is a bounded support surface, not a seventh operation.

## Calling them

Each takes a strict JSON request and returns a canonical JSON result:

```text
yoetz start --request '{"request_id":"req_...", ...}' --json
yoetz check --input request.json --json
yoetz status --input - --json          # read the request from stdin
```

`--deadline-ms` bounds the call. Every operation is idempotent on its request identity: **a timeout
has an unknown outcome, never a known failure.** Reuse the same `request_id` to retry, or call
`status view=operation` with that `request_id` as a state lookup without reconstructing the
original body. A `complete` publish returns stored frontiers and accepted event ids; other states
report only what is honest for that state (`pending`/`quarantined`/`absent`/non-publish).

Field-level shapes live in [`docs/INTERFACES.md`](../INTERFACES.md); the JSON Schemas under
[`schemas/`](../../schemas/) and the golden vectors under [`fixtures/`](../../fixtures/) are the
exact wire authority.

If an MCP host drops schema examples or renders required fields as unknown, read the ordinary
guidance resource `yoetz://guidance/request-templates.md`. It contains complete bodies for all six
operations and all nine ordinary publication families. Replace every illustrative identifier,
timestamp, frontier, digest, and content string before use; the resource is an authoring fallback,
not a second wire authority.

## What each one is for

### `start`
Opens a task, or attaches to an existing one, and issues a session and a distinct logical writer
identity. Create-vs-attach is explicit in the request — Yoetz does not guess.

### `publish_work`
Records bounded, participant-published facts: plan, requested outcomes, obligations, claims,
actions, results, evidence. Publish material transitions — an assignment, a decision, a blocked
attempt, an independently useful result, a completion, a revision. Skip routine reads, searches,
formatting, and per-file mechanics.

A plan must make completion scope inspectable. Normally, list its obligation refs. If no
obligations apply, record `no_obligations_reason` as exactly `no_material_change`,
`single_atomic_change`, or `exploratory_scope_unknown`; the reason is invalid beside effective
obligation refs. A revision restates the declaration, so omission clears an earlier reason.

Read `status view=obligations` first and account for every exact `unattempted_items` value. Then set
`dry_run: true` to validate a batch and preview what would be accepted without appending. The
preview is not evidential and is not citable as a check, publication, or coverage source. Reuse the
same `request_id` for the real publish after the preview succeeds.

Yoetz does not watch your workspace. What is published is what exists.

Authoring help for MCP: tool descriptions name `yoetz://guidance/publication-policy.md`; invalid
requests include nested field hints plus `yoetz://guidance/request-templates.md`.

### `check`
Runs the deterministic policy packs over the recorded state and returns findings with an exact
coverage vector. `mode` selects how much:

| Mode | Use it when |
|---|---|
| `semantic_if_configured` | Most material implementation or review claims. Runs semantic review if it is available; degrades honestly if not. |
| `semantic_required` | Completion depends on qualitative correctness, design conformance, security or privacy reasoning, interoperability, or whether the code satisfies the ask. |
| `deterministic_only` | Explicitly local or structural checks, semantic-disabled policy, or a deliberate no-egress choice — and the limitation gets disclosed. |

`semantic_required` never erases deterministic truth. If the provider is absent, denied by policy,
refuses, times out, or returns stale or invalid output, you get the deterministic findings back with
verdict `incomplete_check`, an explicit reason, and no semantic findings.

### `respond`
Answers a finding: accept and act, supply evidence, revise the claim, dispute with evidence, or
state an unresolved limitation. **A response does not erase a finding**, and no disposition
resolves one: an actionable finding keeps the receipt conclusion at `unresolved_findings_remain`
for the rest of the task, whichever disposition is recorded. Recheck after any material edit,
evidence change, or plan change. A readable response to a finding returned by the current check
does not require a recheck; a redacted or unreadable response does.

### `status`
Reads current state — use it after a resume, a compaction, a handoff, or any uncertainty about what
is already done. `view=candidate_findings` is an advisory read: it creates no verdict, no IDs, no
receipt, and no event. An empty candidate list means only that no rule fired in that read; it is not
a check and cannot be cited as one. After any ambiguous write, prefer `view=operation` with
`filter.operation_request_id` set to the write's `request_id`: it looks up that operation's state
within the authenticated task without requiring a byte-identical replay body. This survives a
same-task attach that rotates the active session and writer, but never crosses into another task
bundle. Stored outcome, frontiers, and accepted event ids are present only when the operation is a
complete `publish_work`; other states omit them rather than invent detail.
`view=obligations` includes each requested item and its exact unattempted subset, subject to the
existing obligation-text projection policy. `view=results` resolves `res_` ids to bounded structural
facts: source event, payload availability, outcome, linked action, and linked evidence ids.

Before completion, read `declared_obligation_count`, `no_obligations_reason`, and
`closure_readiness`. An empty readable plan without a typed reason is blocked by
`no_obligations_declared`; a typed reason clears that readiness blocker but remains visible. If the
plan scope is unreadable, readiness is unknown rather than zero. Read the finding counters
separately: `unanswered_finding_count` is response work still to do, while
`receipt_blocking_finding_count` is the current actionable finding set that prevents a clean
receipt even after every response. Accordingly, `findings_unanswered` is remediable;
`receipt_findings_unresolved` is a persistent conclusion bound and should not trigger another
response loop.

### `receipt`
Projects the honest summary of what was checked, at what coverage, and what remains open. Formats:
`json`, `markdown`, `text` — all three project under the default policy. If a stricter owner policy
blocks `json`, re-request `markdown` or `text`.

See [Receipts and coverage](receipts-and-coverage.md) for how to read one.

## A whole task, in order

1. `start` — open or attach.
2. `publish_work` — plan, outcomes, acceptance evidence, assignments.
3. `publish_work` — material transitions as the work happens.
4. `status` — after any resume or uncertainty.
5. `publish_work` — the intended completion claim plus current evidence.
6. `check` — with a deliberately chosen mode.
7. `respond` — to each finding.
8. `check` — again, after any material change.
9. `receipt` — and keep the final answer no stronger than its weakest coverage, freshness,
   unresolved findings, and limitations.

## How often to call each one

| Operation | How often |
| --- | --- |
| `start` | Once per task, before substantive work. On resume, attach to the existing task instead of starting a second one. |
| `publish_work` | One batch per material transition, roughly one to eight events. A normal session is a handful of batches, never one per file, tool call, or message. |
| `status` | After resume, compaction, or delegate handoff, and before any completion claim. Not between routine tool calls. |
| `check` | After publishing the completion claim and its evidence, and again after any material edit or new evidence. A readable response to a finding returned by that check needs no recheck; a redacted or unreadable response does. A check with no new events since the last one adds nothing. |
| `respond` | Once per finding, at that finding's recorded frontier. |
| `receipt` | Once at the end, and again only if material state changed after the previous receipt. |

Under-publishing hides the work; over-publishing buries it. Reading, searching, formatting,
regenerating derived files, repeating a status read, and republishing unchanged state are not
publishable transitions.

### When to stop retrying

Semantic review that does not succeed is a coverage gap, not a retry problem. `not_configured`,
`blocked_by_policy`, and `human_denied` will not change without owner action. `unavailable` and
`timeout` are the only statuses retried inside a job, so by the time you see one that job already
spent its attempt budget; `refused`, `invalid`, and `failed` are not retried in-job at all. When a
second job in one session again returns no judgment, switch to `deterministic_only`, disclose the
gap with the recorded status and reason, and do not spend a third job on the same binding.

On `OPERATION_PENDING`, read `status` with
`view=operation` once and replay the same `request_id` once; if it is still pending, continue with
a new deterministic-only request and say the earlier operation never reached a terminal result.

The agent-facing version of this loop, including when *not* to use Yoetz at all, is
[`guidance/workflow.md`](../../guidance/workflow.md).
