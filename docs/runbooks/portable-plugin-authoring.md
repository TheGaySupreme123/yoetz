# Portable plugin authoring and lifecycle runbook

This runbook covers Yoetz's Agent Plugins 1.0.0 artifact, including its optional exclusive
plugin-managed MCP projection. It is a format carrier, not
an authority or a support claim.

## Exact source and output

The only portable wrapper source is `skills/portable/yoetz/SKILL.md`. Shared reference bytes remain
owned under `guidance/`. The vendored upstream schemas live under
`support/agent-plugins/1.0.0/`; their accepted SHA-256 values are recorded in ADR-023.
The registered Codex project-root evidence is
`fixtures/agent-plugins/codex-project-root.case.json` (`agent-plugins-codex-project-root-1`).

The external-registration renderer emits exactly:

```text
plugin.json
skills/yoetz/SKILL.md
skills/yoetz/references/agent-instructions.md
skills/yoetz/references/coverage-and-receipts.md
skills/yoetz/references/publication-policy.md
skills/yoetz/references/request-templates.md
skills/yoetz/references/workflow.md
```

In `external_registration` mode there is no `mcp.json`; existing `HarnessMcpPort` registration is
the one MCP owner. In `plugin_managed` mode the same inventory also contains root `mcp.json`, with
one stdio server named `yoetz`, executable token `yoetz`, and args `mcp serve` (policy) or
`mcp serve --semantic off` (strict). It contains no environment, headers, credentials, secret
references, or shell command. Neither mode includes a hook, permission, or Codex-specific skill
manifest. Run `uv run python scripts/sync_resource_ripple.py --write` after an intentional source or
inventory change, then `--check`. Do not edit packaged mirrors.

## Validation

Validation is offline. Fatal known-field failures reject `plugin.json` and name the bounded field;
unknown top-level fields are reported and ignored for component loading, as Agent Plugins v1
requires. An invalid immediate-child skill is skipped at the skill boundary and does not turn a
valid root manifest into an activation or authority claim. Symlinks, escaping paths, collisions,
missing members, and oversized members fail closed.

Invalid or mismatched top-level `mcp.json` disables only MCP. One invalid entry, unsupported
transport, missing executable, or start/connect/auth/handshake failure skips and reports only that
server; an independently valid skill remains available. A GUI host must already be able to resolve
the bare `yoetz` executable. Fix that host capability outside the artifact rather than injecting
shell configuration, environment, or credentials.

Schema success proves only format validity. It does not prove host discovery, activation, skill
delivery, MCP runtime, observation, semantic dispatch, or workflow closure.

## Preview, apply, status, and rollback

The preview digest binds the trusted target identity, current-state digest, requested action,
format/schema/renderer versions, complete sorted member inventory, selected MCP ownership,
optional strict/policy profile, complete route bytes, and current `McpOwnershipState`. Apply rejects
stale previews or changed ownership before mutation.

Before a plugin-managed preview, inspect both native/global registration and the project plugin
root. Proceed only from exclusive `absent` or already exact `plugin` ownership. `external`, `dual`,
`foreign`, and `ambiguous` states are preserved and refused; there is no force overwrite.
Plugin-managed apply never invokes native/global registration.

Codex uses the existing `.agents/plugins/yoetz` root. Migration is a whole-directory swap between
marker-identified native and portable trees; files are never merged. A preserved exact native tree
is restored when the portable tree is removed. Modified, partial, unmanaged, foreign, unsafe, or
ambiguous trees are preserved and refused. Stage or rollback remnants produce
`recovery_required`; status reports them and never deletes or chooses between them.
Removing or rolling back a plugin never removes Yoetz data, vault keys, credentials, privacy
grants, provider bindings, or foreign host configuration.

Standalone install, replace, and remove use the `plugin_artifact_apply` `review_only` operation.
The exact preview digest is single-shot and agent-chat authorization is forbidden. Until a
production action-bound user-presence adapter exists, that lane fails closed with
`human_authority_unavailable`. The separately authorized setup composition remains distinct.

After an interrupted call, replay the same request identity when the process still has its stored
result; otherwise call status and reconcile the installed bytes. Never infer failure from a
timeout, and never retry with a fresh request before reconciliation.
