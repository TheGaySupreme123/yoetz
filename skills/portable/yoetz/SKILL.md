---
name: yoetz
description: Record material work in a local Yoetz ledger and check completion claims against that bounded record.
---

# Yoetz cooperative workflow

Use Yoetz for material multi-step, resumable, delegated, or verification-heavy work. It is a
local work ledger and deterministic checker: it records only what participants publish and does
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
product behavior. Preserve current user intent and authorization boundaries. If memory says a
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

Host recovery is host-specific; installed plugin bytes are not live MCP runtime. The Cursor case is:
After a Cursor plugin replacement, query
`yoetz integrate cursor plugin status` and read `mcp.route_profile` plus `mcp.runtime`. If a
`semantic_required` check returns `blocked_by_policy` / `route_semantic_ceiling` while installed
status is `policy`, or `mcp.runtime.activation` is `full_restart_required`, that is an activation
mismatch: fully quit Cursor (Reload Window is not enough), then continue only after live runtime
matches the installed policy route. Do not mint a fresh semantic check against the stale process,
and do not change privacy settings. A live installed strict route remains the ordinary terminal
ceiling. For Claude Code, follow its own reported host continuation; do not run Cursor commands
or quit another host. Codex likewise follows its own reported continuation and does not inherit a
Claude or Cursor command. A host auto-review hold is not a Yoetz result; preserve the exact proposed
request and do not switch to deterministic-only while required approval is pending.

Same-task recovery comes before a new task. On an ambiguous write, use `status view=operation` and
replay the exact original body with the exact original request ID once; never create a sibling to
escape an unknown or pending write. After all prior writes have known terminal outcomes, a healthy
authorized binding, and a user-declared remaining or repaired verification scope, one explicit
`start mode=create` sibling with the same canonical `workspace_ref` and a different stable
`external_ref` is allowed. It gets a fresh plan, evidence, checks, and native mapping, and inherits
no predecessor receipt, findings, obligations, evidence IDs, or mapping. The complete decision table
and prohibitions are in [coverage-and-receipts.md](references/coverage-and-receipts.md).

Delegation after an outage: if `start` (or any call) returned `safe_details.availability:
terminal_unavailable`, that state belongs to the host binding, and later calls under a new
`request_id` inherit the same `correlation_id` without a new diagnostic. Carry it into every
delegated assignment as a bounded `yoetz_availability` block (state, host binding, parent
`correlation_id` and `request_id`, proof limit). Delegates that inherit it make no Yoetz call and
publish nothing; only the coordinator runs the one named repair and replays the original
`request_id`. Never run `yoetz service stop`, `service run`, or `service restart` from
`INTERNAL_ERROR` or from a message that did not name that exact command.
