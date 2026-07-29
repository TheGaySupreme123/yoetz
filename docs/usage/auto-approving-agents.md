# Auto-approving an MCP route

Auto-approval should be based on the exact process a host launches, not on an assumption about the
current provider or privacy configuration.

For a Yoetz MCP process that must never request external semantic review, register:

```text
yoetz mcp serve --semantic off
```

This starts the **strict** route profile. For that process lifetime:

- deterministic checks and all six Yoetz workflow operations remain available;
- `check` never requests semantic runtime capability and never dispatches a semantic evaluator;
- a semantic request is recorded as `blocked_by_policy` / `route_semantic_ceiling`;
- `semantic_required` is incomplete and the result and receipt retain the coverage gap;
- `check.openWorldHint` is `false`, and initialize plus `status(view=versions)` disclose `strict`.

The route profile is not an agent request field and cannot be changed over MCP. The descriptor is
an inspectable hint; the application route constraint is the enforcement.

## What strict does not claim

Strict is an external-semantic egress ceiling, not a general-purpose sandbox and not a promise that
the process opens no communication channels. The MCP bridge still uses the approved local Yoetz
service IPC. It does not constrain the host agent, the operating system, separately running local
models, or unrelated processes.

The ordinary command remains:

```text
yoetz mcp serve
```

That starts the **policy** route. Semantic review can occur only when the durable privacy policy,
provider readiness, classification, minimization, and authorization gates all allow it. Its
`check.openWorldHint` is therefore `true`.

## Codex registration

`yoetz setup run` and `yoetz integrate codex mcp preview` show the exact command, route profile, and
digest before registration. A zero-egress setup selects the strict command. If provider/privacy
posture later changes, preview again: a Yoetz-owned registration with the other route profile is a
re-registration, not a silent mutation. A foreign entry named `yoetz` is never overwritten.

Inspect the active route through MCP initialize instructions or `status(view=versions)`. Registration
proves only the configured command; it does not prove that a host has launched it or that a provider
is ready.
