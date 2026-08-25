---
name: yoetz
description: Record material work in a local Yoetz ledger and check completion claims against that bounded record.
---

# Yoetz cooperative workflow

Use Yoetz for material multi-step, resumable, delegated, or verification-heavy work. It is a
local work ledger and deterministic checker: it records only what participants publish and does
not observe the workspace, enforce a process, authenticate authorship, or prove correctness.

Before the first workflow call, read [workflow.md](references/workflow.md) and
[coverage-and-receipts.md](references/coverage-and-receipts.md). Before publishing work, read
[publication-policy.md](references/publication-policy.md). If request schema metadata is missing
or a request is rejected, use [request-templates.md](references/request-templates.md). The
non-negotiable safety floor is [agent-instructions.md](references/agent-instructions.md).

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
