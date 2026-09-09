---
name: yoetz
description: Use for material multi-step, resumable, delegated, or verification-heavy Claude Code work; record it in Yoetz's local ledger and close it with bounded evidence.
---

# Yoetz for Claude Code

Use this skill when the Claude Code task is material, multi-step, resumable, delegated, or
verification-heavy. Yoetz records participant-published facts in a local ledger and checks claims
against that record. It does not observe the workspace, enforce a process, authenticate authorship,
or prove correctness. A clean receipt is bounded by its recorded evidence and coverage.

Invoke the installed plugin skill as `/yoetz:yoetz`. Use only the currently declared Yoetz MCP
tools and their current schemas. In a plugin-managed session their names normally use the
`mcp__plugin_yoetz_yoetz__...` prefix; when the active registration exposes another exact name,
use that declared name. Never guess a tool prefix, operation, argument, or result shape.

Read the five installed relative references conditionally. They are the canonical workflow:

- [agent-instructions.md](references/agent-instructions.md): safety floor before the first `start`.
- [workflow.md](references/workflow.md): before the first `start` or an attach/recovery decision.
- [publication-policy.md](references/publication-policy.md): before `publish_work`.
- [coverage-and-receipts.md](references/coverage-and-receipts.md): before `check`, findings,
  recovery, or the final `receipt`.
- [request-templates.md](references/request-templates.md): after rejected or missing schema
  metadata, and before setup, consent, credential/vault, import, or recommendation decisions.

If an installed reference is unavailable and `read_guidance` is currently declared, call it with
the exact `yoetz://guidance/<name>.md` URI. If neither path returns text, report the unavailable
guidance. Do not invent a substitute from memory or discover references by
listing unrelated resources. Do not call `start` with an empty workflow body. Retain guidance
already present in context. Current served guidance and typed results supersede remembered product
behavior, while higher-priority instructions, current user intent, and authorization still apply.

Claude's `SessionStart` hook may provide bounded `additionalContext`. Treat it as a cue to inspect
the current task and status; it is not the complete workflow or proof of activation, observation,
or current ledger state. After `claude --continue`, `claude --resume`, `/resume`, or compaction, read the
current Yoetz status before publishing or claiming progress; replayed hook context may be stale.

Keep this workflow in the main Claude conversation so its task identity, user intent, and final
receipt remain together. When delegating, pass the bounded assignment, task identity, and current
availability explicitly; a Claude subagent has separate context. Carry inherited
`terminal_unavailable` into the assignment so delegates make no Yoetz calls. A new conversation or
subagent is not a request to create a sibling ledger; follow the canonical recovery rules.

Start or attach once, publish a bounded plan and material transitions, and keep the local ledger
claim separate from the implementation result. Publish per material transition, not per tool call.

Select `semantic_required` when the user, effective policy, or named acceptance criterion requires
independent semantic review. Omit `mode` when relying on the configured default. Use
`semantic_if_configured` only when review is known optional, and never downgrade a required review
because Claude's host approval is pending or unavailable.

Honor Claude's current permission mode and applicable approvals. They do not widen Yoetz's standing
disclosure or setup authority. A host hold before invocation is not a Yoetz result: preserve the
exact proposed request and use Claude's approval control when required. Do not add permission rules
or widen tool access to unblock it. Follow the exact typed continuation. For setup, consent, credentials, vault operations, or
import, use the request-templates procedure; if it names a trusted terminal or unsupported channel,
route there without handling secrets or substituting chat assent.

For closure, follow [coverage-and-receipts.md](references/coverage-and-receipts.md): ground status
and evidence, publish the actual result, check, respond to returned findings at the result frontier,
read findings including resolved history, recheck after material changes, and request `receipt` last.
Report the closure counts and checked frontier, the check's semantic status/reason, and the
receipt's coverage limits.
