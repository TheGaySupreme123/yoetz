---
name: yoetz
description: Use for material multi-step, resumable, delegated, or verification-heavy work in Cursor Agent. In a new session read guidance and discover schemas, then call start before substantive work; follow recovery on failure and ask for intro and guidance if startup remains blocked.
---

# Yoetz for Cursor Agent

## Trigger

Use this skill when the request has multiple material steps, delegates work, resumes an earlier
conversation, repairs evidence, or asks whether a result is complete.

A new session's first workflow operation is `start` (create or attach), after guidance reads,
tool/schema discovery, and necessary bootstrap clarification. This includes `read_guidance`
and commands needed to read installed references or discover tool schemas. Call `start`
before substantive research, commands, edits, or delegation. If it fails, follow exact
typed continuations and same-request recovery first, including a named one-time repair.
If startup remains blocked without an applicable recovery path, ask the user for intro and
guidance; do not invent a substitute workflow. Continuing without a ledger task is permitted
only by the bounded optional-service fallback in
[startup failure precedence](references/coverage-and-receipts.md#startup-failure-precedence).
Yoetz records facts that
participants publish; it does not observe the workspace, authenticate authorship, or prove that
the underlying work is correct.

## Workflow

Honor the current Plan, Ask, or Agent mode and its tool restrictions. A Cursor plan or saved plan
file is context for the work; it becomes Yoetz scope only when its bounded plan and obligations
are published. This skill does not authorize changing modes or requesting additional approvals.
After a resume, compaction, or handoff, read current Yoetz status before publishing. A new chat
does not by itself create a new ledger. Pass task identity and bounded availability to delegates;
inherited `terminal_unavailable` means they make no Yoetz calls.

For cooperative delegation, the parent calls `start mode=delegate` with its current session and
passes the complete single-use `attach_handle` with a bounded assignment to the intended child. The
child calls `start mode=attach` and uses its own returned session and writer. A child without a handle
may create a stable child-specific task with `parent_session_id`; it remains `self_registered` and
`pending` until the parent records `child_accepted` or `child_rejected`. Read `status view=lineage`
after handoff. A receipt never closes work or refreshes the parent's dependency manifest. The `prj_`
project membership object groups work; each source workspace grants workspace-level observation
consent separately, and the exact membership generation binds coordination delivery. Membership does
not select a task or authorize sibling content. A clean child receipt does not prove integration.

### Cursor native boundary

The reviewed Cursor IDE profile exposes `sessionStart`, `sessionEnd`, `afterMCPExecution`,
`afterFileEdit`, and `stop`; it is post-only for structural observation and excludes
`afterAgentThought`. Cursor's Agent CLI, Cloud/Cloud Agents, and SDK fixture metadata do not supply
an admitted native child-hook cell here. Shipped type names or a newer installed Cursor build are
artifact evidence only and do not prove runtime child identity. A child event without an explicit
cooperative task/session binding remains an attribution gap and is never assigned to the parent.
`generation_id` identifies a host turn or conversation and never becomes a tool-call identity.

Ordinary Cursor tool capture uses the exact `cursor-ordinary-observation-v1` profile and its separate
workspace content-consent selection. Structural observation consent does not grant content retention
or semantic disclosure. Hooks are fail-open and never enforce Cursor work. Native registration,
session binding, hook drainage, or MCP approval does not prove accepted observation content, semantic
review, or receipt coverage; report those boundaries separately. When a native child or content
signal is unsupported, use cooperative MCP delegation or explicit task registration when allowed and
disclose the native gap.

## Load the shared Yoetz guidance

These five references are the complete installed guidance set. Follow the one that matches the
operation; do not copy their procedures into a second checklist.

- Safety floor when absent from context: [agent-instructions.md](references/agent-instructions.md)
- Before `start` or an attach decision: [workflow.md](references/workflow.md)
- Before `check`, findings, or recovery: [coverage-and-receipts.md](references/coverage-and-receipts.md)
- Before `publish_work`: [publication-policy.md](references/publication-policy.md)
- Missing/rejected schemas; before setup, consent, credential/vault, import, or recommendation
  decisions: [request-templates.md](references/request-templates.md)

Retain already-read guidance in context. If a reference is unavailable, use the declared Yoetz
`read_guidance` with its exact `yoetz://guidance/<name>.md` URI. Do not call `start` with an empty
workflow body. Current served guidance and typed results supersede remembered product behavior;
higher-priority instructions, current user intent, and authorization still apply.

## Tools

In the IDE, inspect the active server and its tools in Customize or Available Tools. In the CLI, use
`agent mcp list` and `agent mcp list-tools <identifier>` to find the connected server and its
actual tool names. Call only the exposed Yoetz operations (`start`, `publish_work`, `status`,
`check`, `respond`, `receipt`, and read-only `read_guidance`); do not assume a server prefix.

Start or attach once, publish the bounded plan before substantive work, and
keep the returned session/task identity. For a required independent semantic judgment, select
`semantic_required`; omit `mode` for the configured default, and use `semantic_if_configured`
only when review is known to be optional. Keep a strict route's semantic ceiling.

## Guardrails

If Cursor asks for MCP approval, preserve the exact proposed request and request ID while it is
held. Do not retry with a new request, switch to deterministic-only, or use `--approve-mcps` as a
workaround. Do not change Cursor privacy, auto-run, or MCP settings without the user's decision.

## Cursor activation recovery

Use this branch only after a plugin replacement when `yoetz integrate cursor plugin status` reports
`mcp.runtime.activation: full_restart_required`, or a required check reports
`blocked_by_policy` / `route_semantic_ceiling` while the installed route is `policy`. Inspect the
reported route and runtime, fully quit the exact Cursor app, relaunch the same profile, and verify
the live runtime matches before continuing. Reload Window alone may leave an old MCP helper.
Do not mint a fresh check against that stale process or change privacy settings. Otherwise follow
the current typed continuation and the shared recovery guidance.

## Output

Follow the linked coverage and publication references for the repair, evidence, finding-response,
and receipt sequence. Request a receipt last. Report the closure counts and checked frontier, the
check's semantic status/reason, and the receipt's coverage limits. Keep completed product work
separate from unresolved verification. Activation, registration, and hook delivery do not establish
observation, semantic review, or completion.
