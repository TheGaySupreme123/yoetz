# Cursor local integration runbook

This runbook covers the local Cursor IDE, Agent CLI, and TypeScript/Python SDK cells implemented by
issue #153. Cursor Cloud and Cloud Agents are out of scope. Keep regular and testing profiles
separate; every command below names the exact Cursor configuration root and project.

## Proof facets are independent

Record these separately: Yoetz source and wheel identity; rendered artifact; installed bytes;
Cursor product/SDK/bridge identity; plugin source; discovery; activation; skill delivery; MCP owner;
MCP binding; raw MCP runtime; model-visible tools; correlated model-controlled use; hook capability;
observation consent; accepted observation evidence; service/provider readiness; privacy receipt;
workflow receipt. A later facet never backfills an earlier one.

## Exact local cells

The initial pins are Cursor IDE `3.17.8` build `3.17.8`, Cursor Agent CLI
`2026.07.09-a3815c0`, `@cursor/sdk==1.0.23`, `cursor-sdk==1.0.24`, and bridge protocol
`sdk.v1`. Record the executable/package/bridge digest, OS, architecture, scope, and activation
source. Cursor's Python package deliberately has no `1.0.23` release; it aligned with the shared
SDK release line at `1.0.24`. Nearby versions are untested, not implicitly compatible.

## Preview and install

Use an explicit isolated root; never point a test at regular `~/.cursor`.

```text
yoetz integrate cursor plugin preview \
  --cursor-config-root /exact/testing/home/.cursor \
  --project-root /exact/testing/project \
  --format native \
  --mcp-ownership plugin-managed \
  --route-profile strict \
  --json
```

Review `request_id`, `preview_digest`, target scope, format, before state, MCP owner state, route,
artifact digest, and warnings. Apply with the same request and digest. A stale preview, modified or
unmanaged copy, symlink, rollback residue, or conflicting MCP source refuses without overwrite.
Portable uses root `plugin.json`; native uses `.cursor-plugin/plugin.json`. One installed tree may
contain only one of those manifests.

For local IDE development the explicit user root resolves to `plugins/local/yoetz` below the named
Cursor configuration root. Restart Cursor or run `Developer: Reload Window`; that is activation
work, not installation proof. For CLI use the exact installed tree with `--plugin-dir`. Do not copy
the skill to `.cursor/skills`, add a rule, or rely on `.agents/skills` as fallback evidence.

## MCP ownership and source precedence

Ownership mode is exactly `external_registration` or `plugin_managed`. Observed state is exactly
`absent|external|plugin|dual|foreign|ambiguous`. Configuration source is plugin, project, user,
inline-create, or inline-send. Preserve duplicate and foreign same-name entries.

The local SDK precedence is:

1. per-send inline servers (replace creation-time servers);
2. creation-time inline servers;
3. plugin servers when `plugins` is selected;
4. project `.cursor/mcp.json` when `project` is selected;
5. user `~/.cursor/mcp.json` when `user` is selected.

An inline/project/user `yoetz` server must not create a plugin-managed pass. Prove negative controls
for each source, duplicate exact entries, a foreign same-name route, and the alternate strict/policy
route. The exact plugin-managed routes are `yoetz mcp serve` (policy) and
`yoetz mcp serve --semantic off` (strict), with no environment or secret fields. Raw initialize and
tools/list prove only runtime registration. Require a correlated model-controlled `start` or
`status` call for use.

## SDK TypeScript and Python

Pin package and bridge versions. TypeScript uses `local.settingSources`; Python uses
`local.setting_sources`. Plugin-managed runs must include `plugins`; external runs must include the
intended `project` or `user` source. Do not rely on ambient sources. Do not replace Yoetz MCP tools
with SDK custom-tool callbacks. Record sandbox and approval modes as host capability facts; they do
not widen Yoetz authority.

Test both bindings independently. Each must show its package/bridge identity, explicit sources,
source-winning negative controls, model-visible Yoetz operations, one correlated model-controlled
call, and its own final result row.

## Hooks and observation

The native plugin advertises only `sessionStart`, `sessionEnd`, `afterMCPExecution`,
`afterFileEdit`, and `stop` for the pinned local profile. It intentionally excludes
`afterAgentThought`. Hooks call `yoetz hooks cursor-observe`, are fail-open, and never enforce Cursor
work.

Before local storage the adapter discards prompts, reasoning, response text, file paths/content/
edits, MCP arguments/results, transcripts, command output, email, and workspace roots. Fixtures
must place canaries in every denied field and prove absence from structural state, objects, logs,
errors, and hook output. Installation, hook configuration, or `sessionStart` earns no observation
coverage. Grant observation separately, prove accepted `cursor_hook` envelopes, pause, resume,
revoke, restart/replay, dedupe, and explicit gaps.

## Upgrade, rollback, and removal

Upgrade is a whole-directory previewed replacement. The preview binds the current tree digest,
future inventory, format, MCP owner/route, target identity, artifact digest, and request identity.
Do not mutate Cursor caches to force selection. Reload and re-prove source after replacement.

Removal moves only an exact marker-verified managed tree and deletes it after the directory swap is
durable. Modified plugin bytes or recovery residue are preserved for review. Foreign, dual, or
ambiguous MCP sources do not block exact plugin removal because the operation leaves every external
source untouched. Removal does not delete ledgers, vault/keyring state, provider credentials,
privacy grants, project/user MCP entries, or unrelated Cursor settings. After removal, independently check installed bytes,
discovery, activation, MCP sources, stale process/cache behavior, and regular-profile isolation.

## Troubleshooting

| Symptom | Interpretation |
|---|---|
| Skill appears but plugin identity is absent | fallback discovery; not a plugin pass |
| `tools/list` succeeds but owner is dual/ambiguous | source collision; do not choose silently |
| SDK sees no plugin MCP | `plugins` missing from explicit setting sources |
| SDK sees the wrong route | a higher-precedence inline/project/user source won |
| Installed MCP executable changed but Cursor still shows the old tool inventory | first run `Developer: Reload Window`; if the pinned IDE still reuses its shared MCP process, fully quit that exact Cursor testing app, verify its process exited, relaunch it with the same isolated profile, and re-prove discovery plus `tools/list` before claiming activation |
| Hook fires but status stays published-only | configuration/trigger is not accepted observation evidence |
| Strict route has no semantic review | expected route ceiling; authorize a separate policy route when intended |
| Modified plugin cannot remove | preserved local change; inspect and resolve manually |

Always report what is proven and the remaining cells/gaps. A clean local test never proves Cursor
Cloud, a neighboring version, regular-profile isolation without a before/after check, provider
dispatch without provenance, or workflow completion without a current receipt.
