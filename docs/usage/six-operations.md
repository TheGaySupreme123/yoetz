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
`status` to find out what actually happened.

Field-level shapes live in [`docs/INTERFACES.md`](../INTERFACES.md); the JSON Schemas under
[`schemas/`](../../schemas/) and the golden vectors under [`fixtures/`](../../fixtures/) are the
exact wire authority.

## What each one is for

### `start`
Opens a task, or attaches to an existing one, and issues a session and a distinct logical writer
identity. Create-vs-attach is explicit in the request — Yoetz does not guess.

### `publish_work`
Records bounded, participant-published facts: plan, requested outcomes, obligations, claims,
actions, results, evidence. Publish material transitions — an assignment, a decision, a blocked
attempt, an independently useful result, a completion, a revision. Skip routine reads, searches,
formatting, and per-file mechanics.

Yoetz does not watch your workspace. What is published is what exists.

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
state an unresolved limitation. **A response does not erase a finding.** Recheck after any material
edit, evidence change, plan change, or response.

### `status`
Reads current state — use it after a resume, a compaction, a handoff, or any uncertainty about what
is already done. `view=candidate_findings` is an advisory read: it creates no verdict, no IDs, no
receipt, and no event. An empty candidate list means only that no rule fired in that read; it is not
a check and cannot be cited as one.

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

The agent-facing version of this loop, including when *not* to use Yoetz at all, is
[`guidance/workflow.md`](../../guidance/workflow.md).
