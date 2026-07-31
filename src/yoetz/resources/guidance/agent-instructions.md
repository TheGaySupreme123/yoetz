# When to use Yoetz

Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work. Call `start` before substantive work. Skip Yoetz for trivial questions or edits where the ceremony exceeds the integrity benefit.

Cadence: `start` once, `publish_work` once per material transition, `check` after the completion claim, `receipt` last. Read `status` after a resume, compaction, or handoff before working again from memory. Never claim Yoetz is active until `start` returns.

# What Yoetz is

Yoetz is a local work ledger and deterministic checker. It records only what participants publish and checks that record at a named frontier.

# What Yoetz is not

Yoetz is not an enforcement system, observer, authorship proof, transcript recorder, or orchestrator. A clean check does not mean the underlying work is correct.

# How often to call each operation

- `start` — once per task, before substantive work. On resume, attach to the existing task instead of starting a second one.
- `publish_work` — one batch per material transition, usually one to eight events; a batch admits up to 100, so keep one transition in one batch rather than splitting it. A normal session is a handful of batches, never one per file, tool call, or message.
- `status` — after resume, compaction, or delegate handoff, and before any completion claim. Not between routine tool calls.
- `check` — after publishing the completion claim and its evidence, and again after any material edit, new evidence, or finding response. A check with no new events since the last one adds nothing.
- `respond` — once per finding, at that finding's recorded frontier.
- `receipt` — once at the end, and again only if material state changed after the previous receipt.

# Never publish

Never publish chain-of-thought or hidden reasoning; full prompts, transcripts, or conversation history; credentials or secrets; whole files, whole repositories, or broad unrelated source. A small problem-local excerpt is permitted only when it is material, in scope, and bound to the relevant state.

# Before you claim done

Publish the material completion claim and its current evidence, call `check`, disposition any findings, then call `receipt`. Recheck after a material change, new evidence, or a finding response. Treat this as the normal publish → check → receipt loop.

For `check` mode: use `semantic_if_configured` for most material implementation/review claims; use `semantic_required` when the completion claim depends on qualitative correctness, design conformance, security/privacy reasoning, interoperability, or whether the code satisfies the ask; use `deterministic_only` only for explicitly local/structural checks, semantic-disabled policy, or a deliberate no-egress choice — and disclose that limitation. Omitting `mode` resolves via the configured verification policy (default optional → `semantic_if_configured`).

# A recorded finding stays recorded

`respond` records your disposition and links your evidence; it does not clear the finding. Every actionable finding recorded in a task keeps the receipt conclusion at `unresolved_findings_remain`, whichever disposition you record. Publish exactly the first time — an exact `attempted_items` entry for every requested item, evidence for every claim — because a finding cannot be un-fired. Repairing the record is still worth doing: it stops the next check from firing again and it shows the reader what you did.

# When to stop retrying

Semantic review that does not succeed is a coverage gap, not a retry problem. `not_configured`, `blocked_by_policy`, and `human_denied` will not change without owner action; take the first answer. `unavailable` and `timeout` already spent that job's own attempt budget. `refused`, `invalid`, and `failed` are not retried inside the job at all. When a second job in one session again returns no judgment, stop requesting semantic review, run `deterministic_only`, and disclose the gap naming the recorded `semantic_status` and `semantic_reason`.

On `OPERATION_PENDING`, read `status` with `view=operation` once and replay the same `request_id` once; if it is still pending, continue with a new deterministic-only request and say the earlier operation never reached a terminal result.

# Canonical request values

Fields backed by canonical integers stay JSON strings on the wire. In particular, send frontier `sequence` and pagination `limit` as strings such as `"10"`, never JSON numbers.

# Word conclusions honestly

Match the weakest material coverage and every limitation in the current receipt.

Permitted: "Yoetz found no deterministic issue in the cooperatively published record at this frontier."

Forbidden: "Yoetz verified the work."

# Never invent Yoetz state

Never fabricate a session ID, publication, finding, verdict, or receipt. If a call fails or Yoetz is unavailable, say that no live Yoetz record or receipt is available. Every tool request's `client` is exactly `{kind, version, integration}` — never send `client.id` or any other client field.

# Non-default actions need consent

Only operations explicitly listed in `catalog.default_safe` are default-safe. For anything else, run `yoetz consent catalog` / `status`. Only operations with `implemented=true` may be prepared. The agent-safe pending view contains structural review facts and the fixed `yoetz consent review` command, but nothing that grants authority. Review requires independently verified action-bound OS user presence; a foreground console or pseudo-terminal alone is never approval. The current runtime has no production presence adapter, so the command fails closed with `human_authority_unavailable` and leaves pending state untouched. Do not attempt to approve through arguments, environment, stdin, MCP, JSON, caller booleans, or terminal automation. The explicitly selected manual `yoetz service initialize-passphrase` ceremony remains separate. Locked vaults still need the ordinary local-human unlock ceremony. No `--yolo`.

# Read more

- `yoetz://guidance/workflow.md` - read before your first `start`: the cooperative workflow, cadence, resume behavior, and final response.
- `yoetz://guidance/coverage-and-receipts.md` - read before your first `check`: coverage, findings, freshness, and receipt wording.
- `yoetz://guidance/publication-policy.md` - read before your first `publish_work`: what is material and safe to publish.
