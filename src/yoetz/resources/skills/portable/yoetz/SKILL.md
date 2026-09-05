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

The normal sequence is:

1. Start or attach once with stable workspace and external references.
2. Publish a bounded plan and explicit obligations before substantive work.
3. Publish material transitions, evidence, and the completion claim without transcripts, secrets,
   broad source, or hidden reasoning.
4. Read status before closing, check the claim, respond to every finding, and recheck after any
   material change.
5. Request a receipt and keep the final wording no stronger than its weakest coverage and
   limitations.

A portable plugin is a carrier only. Its presence, validation, installation, discovery, or host
activation grants no privacy authority, provider authority, observation consent, semantic-review
coverage, or completion proof. MCP ownership is mode-specific and exclusive:

- `external_registration` omits `mcp.json`; the existing host registration remains the sole owner.
- `plugin_managed` includes the selected `mcp.json` route; this plugin is the sole owner, so do not
  keep a duplicate native, project, user, or global registration.

The following recovery applies only to **Cursor**. Installed plugin bytes are not live MCP runtime.
After a Cursor plugin replacement, query
`yoetz integrate cursor plugin status` and read `mcp.route_profile` plus `mcp.runtime`. If a
`semantic_required` check returns `blocked_by_policy` / `route_semantic_ceiling` while installed
status is `policy`, or `mcp.runtime.activation` is `full_restart_required`, that is an activation
mismatch: fully quit Cursor (Reload Window is not enough), then continue only after live runtime
matches the installed policy route. Do not mint a fresh semantic check against the stale process,
and do not change privacy settings. A live installed strict route remains the ordinary terminal
ceiling. For Claude Code, follow its own reported host continuation; do not run Cursor commands
or quit another host.

Delegation after an outage: if `start` (or any call) returned `safe_details.availability:
terminal_unavailable`, that state belongs to the host binding, and later calls under a new
`request_id` inherit the same `correlation_id` without a new diagnostic. Carry it into every
delegated assignment as a bounded `yoetz_availability` block (state, host binding, parent
`correlation_id` and `request_id`, proof limit). Delegates that inherit it make no Yoetz call and
publish nothing; only the coordinator runs the one named repair and replays the original
`request_id`. Never run `yoetz service stop`, `service run`, or `service restart` from
`INTERNAL_ERROR` or from a message that did not name that exact command.
