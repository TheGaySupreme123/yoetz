---
name: yoetz
description: Use for material multi-step, resumable, delegated, or verification-heavy Claude Code work. In a new session read guidance and discover schemas, then call start before substantive work; follow recovery on failure and ask for intro and guidance if startup remains blocked.
---

# Yoetz for Claude Code

Use this skill when the Claude Code task is material, multi-step, resumable, delegated, or
verification-heavy.

A new session's first workflow operation is `start` (create or attach), after guidance reads,
tool/schema discovery, and necessary bootstrap clarification. This includes `read_guidance`
and commands needed to read installed references or discover tool schemas. Call `start`
before substantive research, commands, edits, or delegation. If it fails, follow exact
typed continuations and same-request recovery first, including a named one-time repair.
If startup remains blocked without an applicable recovery path, ask the user for intro and
guidance; do not invent a substitute workflow. Continuing without a ledger task is permitted
only by the bounded optional-service fallback in
[startup failure precedence](references/coverage-and-receipts.md#startup-failure-precedence).
Yoetz records participant-published facts in a local ledger and checks claims
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
receipt remain together. For a parent-minted delegate, call `start mode=delegate` with the parent's
current session, pass the complete single-use `attach_handle` and a bounded assignment to the
intended child, and have that child call `start mode=attach` with the handle. The child uses its own
returned session and writer while the parent keeps its binding. A child without a handle may use a
stable child-specific pair with `parent_session_id`; that relationship is `self_registered` and
`pending` until the parent publishes `child_accepted` or `child_rejected`. Read `status view=lineage`
after handoff. A receipt does not close work or refresh a child dependency manifest, and a clean
child receipt does not prove that the parent incorporated its result. Project membership groups
work; it is not an attach selector or permission to read sibling content. Each source workspace
grants workspace-level observation consent separately from the `prj_` grouping object, and the exact
membership generation binds coordination delivery. Carry the bounded assignment,
task identity, and current availability explicitly; inherited `terminal_unavailable` means delegates
make no Yoetz calls. A new conversation or subagent is not a request to create a sibling ledger;
follow the canonical recovery rules.

### Claude native boundary

Claude's structural native profile is the exact capability cell
`claude-code-cli-local-project-2.1.241`. The 0.3 host cell may additionally emit
`SubagentStart` and `SubagentStop`; those hooks retain only a bounded child identity and structural
correlation. A validated child route must be established by the service or cooperative delegation;
an optional `agent_id`, transcript path, agent type, prompt, or callback without a validated child
identity never assigns work to the parent and remains an attribution gap. Parent and child lanes keep
separate task, session, writer, frontier, advice, and receipt state. Native execution, plugin
activation, and hook drainage do not prove accepted observation content, semantic selection, or a
receipt; report those as separate evidence cells. An observed neighboring Claude version, including
an installed `2.1.263`, does not upgrade the pinned `2.1.241` capability claim.

Ordinary Claude tool capture uses the exact `claude-code-ordinary-observation-v1` profile and its
separate workspace content-consent selection. Structural hook consent alone does not authorize
content retention or semantic disclosure. Captured content remains bounded local evidence and
semantic selection still requires repository privacy authority plus the independently authorized
provider route. If native child identity, content, or semantic evidence is unavailable, continue
through cooperative MCP when allowed and disclose the missing native coverage.

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
