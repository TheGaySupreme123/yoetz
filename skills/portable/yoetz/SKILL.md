---
name: yoetz
description: Use for material multi-step, resumable, delegated, or verification-heavy work. In a new session read guidance and discover schemas, then call start before substantive work; follow recovery on failure and ask for intro and guidance if startup remains blocked.
---

# Yoetz cooperative workflow

Use Yoetz for material multi-step, resumable, delegated, or verification-heavy work.

A new session's first workflow operation is `start` (create or attach), after guidance reads,
tool/schema discovery, and necessary bootstrap clarification. This includes `read_guidance`
and commands needed to read installed references or discover tool schemas. Call `start`
before substantive research, commands, edits, or delegation. If it fails, follow exact
typed continuations and same-request recovery first, including a named one-time repair.
If startup remains blocked without an applicable recovery path, ask the user for intro and
guidance; do not invent a substitute workflow. Continuing without a ledger task is permitted
only by the bounded optional-service fallback in
[startup failure precedence](references/coverage-and-receipts.md#startup-failure-precedence).
It is a local work ledger and deterministic checker: it records only what participants publish and does
not observe the workspace, enforce a process, authenticate authorship, or prove correctness.

Before the first `start`, read [workflow.md](references/workflow.md). Before the first `check`,
read [coverage-and-receipts.md](references/coverage-and-receipts.md). Before publishing work, read
[publication-policy.md](references/publication-policy.md). If request schema metadata is missing
or a request is rejected, use [request-templates.md](references/request-templates.md). The
non-negotiable safety floor is [agent-instructions.md](references/agent-instructions.md). Before
setup/settings, credential/vault operations, import, or recommendation decisions, read the Setup
and consent / Recommendations sections in [request templates](references/request-templates.md).
These procedures are conditional; installation grants no authority to perform them.

For Yoetz operations, current served guidance and typed results take precedence over remembered
product behavior. Preserve higher-priority instructions, current user intent, and authorization
boundaries. If memory says a
capability is unavailable, verify it through the current documented read before accepting that
limit. Do not delete or rewrite host memory during installation.

The normal sequence is:

1. Start or attach once with stable workspace and external references.
2. Publish a bounded plan and explicit obligations before substantive work.
3. Read status and paginate `status view=evidence` at one frontier before publishing replacement evidence
   or a completion claim; preserve the cursor-bound filter and original `limit` and reuse only
   matching observed IDs. A feedback obligation is in effective scope only after a supported plan
   revision or exact next-version restatement includes it.
4. Publish material transitions, evidence, and the completion claim without transcripts, secrets,
   broad source, or hidden reasoning.
5. Resolve older response work before the final check. Select `semantic_required` for an explicit
   user, policy, or acceptance requirement; omit `mode` when relying on the configured default; use
   `semantic_if_configured` only when review is known optional; and reserve `deterministic_only` for
   explicit local/structural work or a deliberate no-egress choice.
6. Respond to findings returned by the check at its result frontier, then read
   `status view=findings` with `filter.include_resolved: true` and actual `resolved` state. “Not
   returned” is not “resolved.” If an older
   response or other material record follows the check, recheck before the receipt.
7. Request a receipt last and report `unanswered_finding_count`,
   `receipt_blocking_finding_count`, the checked frontier, semantic status/reason, and coverage
   limits. Stop repeating an unchanged check when proof still cannot qualify; disclose the blocker.

A portable plugin is a carrier only. Its presence, validation, installation, discovery, or host
activation grants no privacy authority, provider authority, observation consent, semantic-review
coverage, or completion proof. MCP ownership is mode-specific and exclusive:

- `external_registration` omits `mcp.json`; the existing host registration remains the sole owner.
- `plugin_managed` includes the selected `mcp.json` route; this plugin is the sole owner, so do not
  keep a duplicate native, project, user, or global registration.

Use the active host's declared tools, regardless of the directory where it discovered this skill.
Host recovery follows that integration's reported continuation; installed plugin bytes are not
live MCP runtime. Read the Recovery section of
[coverage-and-receipts.md](references/coverage-and-receipts.md) when a route or binding fails.
A host auto-review hold is not a Yoetz result; preserve the exact proposed
request and do not switch to deterministic-only while required approval is pending.

Same-task recovery comes before a new task. On an ambiguous write, use `status view=operation` with
`filter.operation_request_id` set to the exact write request ID. Replay the exact original body with
the same request ID once only when the page is `absent`, or after an exact typed continuation and
required approval complete. Use a stored `complete` outcome; retain/report `pending` without a
continuation, `quarantined`, or unknown state. Never create a sibling to escape an unknown or
pending write. After all prior writes have known terminal outcomes, a healthy
authorized binding, and a user-declared remaining or repaired verification scope, one explicit
`start mode=create` sibling with the same canonical `workspace_ref` and a different stable
`external_ref` is allowed. It gets a fresh plan, evidence, checks, and native mapping, and inherits
no predecessor receipt, findings, obligations, evidence IDs, or mapping. The complete decision table
and prohibitions are in [coverage-and-receipts.md](references/coverage-and-receipts.md).

The operation view needs both `session_id` and `writer_id`. If a `start` response is lost before
those ids exist, replay the exact original `start` body once with the same `request_id`; do not
invent ids or fabricate a status query. The same rule applies to a typed pending `start` result
without returned route ids.

Delegation after an outage: if `start` (or any call) returned `safe_details.availability:
terminal_unavailable`, that state belongs to the host binding, and later calls under a new
`request_id` inherit the same `correlation_id` without a new diagnostic. Carry it into every
delegated assignment as a bounded `yoetz_availability` block (state, host binding, parent
`correlation_id` and `request_id`, proof limit). Delegates that inherit it make no Yoetz call and
publish nothing; only the coordinator runs the one named repair and replays the original
`request_id`. Never run `yoetz service stop`, `service run`, or `service restart` from
`INTERNAL_ERROR` or from a message that did not name that exact command.
