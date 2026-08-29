# Cursor local integration runbook

This runbook covers the current local Cursor IDE and Agent CLI implementation rows from issue #153. Operational
TypeScript/Python SDK support is deferred; the SDK package/bridge fixtures remain metadata-only
experimental scaffolding and do not define current capability cells. Cursor Cloud and Cloud Agents are out
of scope. Keep regular and testing profiles separate; every command below names the exact Cursor
configuration root and project.

## Proof facets are independent

Record these separately: Yoetz source and wheel identity; rendered artifact; installed bytes;
Cursor product/SDK/bridge identity; plugin source; discovery; activation; skill delivery; MCP owner;
MCP binding; raw MCP runtime; model-visible tools; correlated model-controlled use; hook capability;
observation consent; accepted observation evidence; service/provider readiness; privacy receipt;
workflow receipt. A later facet never backfills an earlier one.

## Exact local cells

The current implementation pins are Cursor IDE `3.17.8` build `3.17.8` and Cursor Agent CLI
`2026.07.09-a3815c0`. Record the executable digest, OS, architecture, scope, and activation
source. The retained `@cursor/sdk==1.0.23`, `cursor-sdk==1.0.24`, and bridge `sdk.v1` values are
metadata-only fixture pins for future design work; they are not supported compatibility cells.
Cursor's Python package deliberately has no `1.0.23` release; it aligned with the shared SDK
release line at `1.0.24`. Nearby versions are untested, not implicitly compatible.

## Preview and install

Use an explicit isolated root; never point a test at regular `~/.cursor`. The Cursor plugin
command surface is `preview`, `install`, `status`, and `remove` (`--action replace` previews a
replacement); the generic `update`, `enable`, `disable`, and `export` commands listed by the shared
group are Claude Code lifecycles and refuse for Cursor with
`cursor_plugin_command_unsupported:<command> supported=preview,install,status,remove` (exit 2).

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

The preview output includes an exact argv-shaped `authorization.prepare_command`. Run it without
editing the digest:

```text
yoetz consent prepare plugin_artifact_apply --target-digest <preview_digest> --json
```

Then apply with the same request and digest plus `--accept`. `--accept` binds the digest you
reviewed; it is not authority. On the pinned macOS cell, apply presents a fresh Apple
LocalAuthentication device-owner prompt that names the exact operation, full preview digest, and
pending review ID. Successful authentication consumes that pending once before install, replace,
or remove. Cancellation, unavailable policy, timeout, stale/reused/mismatched pending, non-macOS
hosts, TTY-only input, or `--accept` alone fails before mutation. This proof is installation
authority only; it does not prove discovery, activation, skill delivery, MCP runtime/model use,
hooks, observation, semantic review, or workflow completion.

Replaying the same request and digest after a committed install or remove whose result was lost
reconciles at the already-selected state without mutating bytes or spending a second review. Pass
`--action install` on an install replay: the tree now exists, so the inferred default becomes
`replace`, and a replace replay reports `preview_stale` because the accepted digest bound the tree
the commit already destroyed. Reconcile a wedged replace through `status`, never by re-applying.

Portable uses root `plugin.json`; native uses `.cursor-plugin/plugin.json`. One installed tree may
contain only one of those manifests.

For local IDE development the explicit user root resolves to `plugins/local/yoetz` below the named
Cursor configuration root. File install is not live MCP runtime. `Developer: Reload Window` can
leave a shared `mcp-process` helper on the previous route; fully quit that exact Cursor app, verify
its processes exited, and relaunch with the same isolated profile. `yoetz integrate cursor plugin
status` reports `mcp.runtime.activation` as `matched` or `full_restart_required` when a live scan
is available. That is activation work, not installation proof. For CLI use the exact installed tree with `--plugin-dir`. Do not copy
the skill to `.cursor/skills`, add a rule, or rely on `.agents/skills` as fallback evidence.

## MCP ownership and source precedence

Ownership mode is exactly `external_registration` or `plugin_managed`. Observed state is exactly
`absent|external|plugin|dual|foreign|ambiguous`. Configuration source is plugin, project, user,
inline-create, or inline-send. Preserve duplicate and foreign same-name entries.

The future SDK design records this precedence in metadata fixtures only; it is not an operational
SDK support claim:

1. per-send inline servers (replace creation-time servers);
2. creation-time inline servers;
3. plugin servers when `plugins` is selected;
4. project `.cursor/mcp.json` when `project` is selected;
5. user `~/.cursor/mcp.json` when `user` is selected.

An inline/project/user `yoetz` server must not create a plugin-managed pass. Prove negative controls
for each source, duplicate exact entries, a foreign same-name route, and the alternate strict/policy
route. The exact plugin-managed routes are `yoetz mcp serve` (policy) and
`yoetz mcp serve --semantic off` (strict) for the byte-identical portable carrier. The native
Cursor target adds `--host cursor` (policy: `yoetz mcp serve --host cursor`; strict:
`yoetz mcp serve --host cursor --semantic off`). That profile retains `structuredContent` and
also repeats the exact canonical JSON body in text `content`, because pinned Cursor `3.17.x` can
otherwise hide structured results from the model. It adds no environment or secret field and does
not widen the service route. Raw initialize and tools/list prove only runtime registration.
Require a correlated model-controlled `start` or `status` call for use.

## Upgrading Yoetz under a running service

The local-control handshake pins the exact schema-manifest digest, so after installing a new Yoetz
build the previous build's service still owning the endpoint refuses the new bridge and CLI. The
first plugin tool call (on-demand startup) replaces that service automatically: it asks the stale
holder to shut down through its ordinary bounded path and starts this installation's service inside
the same 30-second budget. If that cannot complete, the tool returns `SERVICE_UNAVAILABLE` with
`reason_code: service_incompatible` (or `protocol_mismatch` when the refusal is a protocol
generation mismatch) and the repair command; run it on a local terminal:

```text
yoetz service restart
```

`yoetz service status` names the incompatible holder's pid, version, and manifest digest. Other
hosts' sessions still running the previous build's bridge are refused after the switch until they
restart; that is the intended outcome of an upgrade, not a defect.

## SDK TypeScript and Python (deferred)

Operational local SDK support is deferred for the planned `0.2` readiness slice. The TypeScript and
Python fixture rows retain package, bridge, setting-source, and precedence metadata only; they are
marked `metadata_only` and `not_a_support_claim`. No SDK import or execution, bridge start,
activation, model-controlled Yoetz call, or SDK hook capability is currently advertised or proven.

Promotion requires a new design-gated issue and independently reviewed proof for each binding:
package/bridge identity, explicit `local.settingSources` or `local.setting_sources`, source-winning
negative controls, model-visible Yoetz operations, one correlated model-controlled call, and an
independent final result row. Until that work lands, use only the IDE/CLI implementation paths above
and keep each support claim bounded by its actual proof facets.

## Hooks and observation

The native IDE plugin advertises only `sessionStart`, `sessionEnd`, `afterMCPExecution`,
`afterFileEdit`, and `stop` for the pinned local profile. It intentionally excludes
`afterAgentThought`. The portable CLI artifact advertises no hooks. SDK fixture metadata advertises
no hook capability; the SDKs' file-based hook contract is not execution evidence. Hooks call
`yoetz hooks cursor-observe`, are fail-open, and never enforce Cursor work.

Native hook artifacts resolve the invoking `yoetz` launcher to an exact command at render time. A
console-script invocation resolves to that absolute executable; the documented `python -m yoetz`
entrypoint (ADR-007) is preserved as an equivalent module invocation of the same interpreter.
Explicit absolute and relative invocations retain their path intent and never fall back to
an ambient `PATH` entry; only a bare `yoetz` name uses `PATH`. The resolved launcher command is
recorded in native marker schema `/2`; an explicit invocation does not silently bind a
different ambient-PATH installation, and a malformed `/2` launcher invalidates the marker. Portable
markers
remain `/1`. A valid legacy native `/1` marker is recognized as managed-but-modified so users can
perform one exact previewed replacement (or safe removal) instead of being stranded. The rendered
timeouts are 10 seconds for `sessionStart`/`stop`, 5 seconds for
`afterFileEdit`/`afterMCPExecution`, and 3 seconds for `sessionEnd`; `failClosed` remains false.
`sessionStart` uses Cursor's documented `session_id`/`conversation_id` conversation identity and
persists the validated pair as a bounded local alias, so later events carrying only
`conversation_id` resolve to the same Yoetz session; an event whose pair contradicts the validated
alias is rejected as `cursor_session_ambiguous` rather than splitting one conversation across
sessions. Cursor's hooks reference (re-read 2026-08-28) describes `sessionStart`'s `session_id` as
"the same as `conversation_id`", so one conversation maps to one Yoetz session by the host's own
contract and the alias is a defensive bound. Local rendering and integration tests cannot prove
live host session binding.

Advice uses Cursor's native output contract rather than the Codex/Claude Code envelope.
`sessionStart` may emit `additional_context`. `stop` does not emit `followup_message` because Cursor
would auto-submit it as a new user message. `afterFileEdit`, `afterMCPExecution`, and `sessionEnd`
have no advice output channel and emit `{}`. Only a successfully written, nonempty `sessionStart`
object commits advice delivery; output-less events do not acquire the delivery lease or consume a
frontier-motion notice.

Workspace binding does not trust plugin-hook CWD. It selects a single `workspace_roots` entry first,
then `CURSOR_PROJECT_DIR`, then the explicit `--workspace` value. A multiroot workspace selects the
deepest root containing `CURSOR_PROJECT_DIR` or refuses. The reusable git-root helper walks safe
ancestors for the nearest `.git` directory or worktree file, without running Git, and refuses
symlinked ancestors, root/home locators, unsafe markers, or unbounded/control-bearing values.
`workspace_unresolvable` and `workspace_unconsented` remain distinct payload-free diagnostics
(with `paused` for a paused grant), recorded by the shared ingress for every host.

The host-neutral `observe status` boundary also keeps storage layers distinct: unsafe state/lock
paths report `storage_unsafe`, bounded open/permission/read-only/missing-parent/lock-acquisition
failures report `storage_unavailable`, invalid stored data reports `storage_corrupt`, and other
defects retain the internal-error boundary. Fixed remediation omits the absolute state path. A
sandboxed Cursor-agent result does not establish unrestricted Cursor-terminal behavior; record
those proof cells separately.

Measured on 2026-08-28 with Cursor Agent CLI `2026.08.25-3e8eec8` (payload `cursor_version`;
`cursor-agent --version` printed `2026.08.11-e8db854`) loading the native plugin through
`--plugin-dir` in an isolated cell: the plugin-sourced `sessionStart` hook ran with `$PWD` equal to
the **plugin directory** (`<cursor-config-root>/plugins/local/yoetz`), not the project, while
`CURSOR_PROJECT_DIR` and `CLAUDE_PROJECT_DIR` both named the project root and `workspace_roots`
was that single root. A bare `--workspace .` would therefore bind the wrong directory; the
`workspace_roots` → `CURSOR_PROJECT_DIR` → explicit order is what makes the rendered command
correct. The same payload carried identical `session_id` and `conversation_id` values. In that
cell, with observation consent granted, the model quoted the `sessionStart` `additional_context`
verbatim and `yoetz observe status` reported `source_coverage.cursor_hook: true`; with consent
revoked, the hook emitted `{}` and recorded `workspace_unconsented`. The IDE cell (3.17.x) was
not measured in that run.

Use the same selected path for `yoetz observe grant|status|pause|resume|revoke`: operator controls
and setup probes apply the identical nearest-safe-Git-root normalization as hook ingress. A legacy
grant made against an exact Git subdirectory is intentionally not searched as an ancestor fallback;
run `yoetz observe grant --workspace <subdirectory>` once after upgrade to record the canonical root.

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

When the `yoetz` runtime itself is upgraded, stop the running Yoetz service with the old runtime
before replacing it, then let the installed bridge start the matching successor. A service that
survives a schema-manifest-changing upgrade must fail the new client handshake; restart that exact
service through the user-selected supervisor before retrying Cursor. Runtime replacement, service
restart, and Cursor/plugin activation are separate proof facets.

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
| SDK fixture is present | metadata-only experimental scaffolding; no SDK activation or model-use claim exists |
| Model sees only a compact sentence and loses structured fields | the native plugin is stale or a portable/external route won; verify the winning source includes `--host cursor`, reload the isolated app, and retry |
| Installed MCP executable changed but Cursor still shows the old tool inventory | fully quit that exact Cursor testing app; Reload Window is not enough if a shared MCP helper survived. Verify its process exited, relaunch it with the same isolated profile, and re-prove discovery plus `tools/list` before claiming activation. `mcp.runtime.activation=full_restart_required` is this state. |
| `semantic_required` returns `route_semantic_ceiling` while plugin status says route `policy` | activation mismatch, not an owner privacy decision; inspect `mcp.runtime`, fully quit the host, and do not mint a fresh semantic check against the stale process |
| MCP resources load but every workflow call fails after a runtime upgrade | a pre-upgrade Yoetz service may still own the fixed endpoint; restart that exact service through the user-selected supervisor, then retry and require a returned task/session before claiming use |
| Hook fires but status stays published-only | configuration/trigger is not accepted observation evidence |
| Strict route has no semantic review | expected route ceiling; authorize a separate policy route when intended |
| Modified plugin cannot remove | preserved local change; inspect and resolve manually |
| Install refuses `authority_required` after `--accept` | no `plugin_artifact_apply` review is prepared for that exact digest |
| Install refuses `human_authority_unavailable` | LocalAuthentication was cancelled, unavailable, timed out, or the host is outside the pinned macOS authority cell; no mutation occurred |
| Install replay reports `preview_stale` | the inferred action became `replace`; replay with `--action install` |
| MCP entry looks right but reads `foreign` | route recognition is key-set exact; an extra `env`/`cwd` key is a foreign entry |

Always report what is proven and the remaining cells/gaps. A clean local test never proves Cursor
Cloud, a neighboring version, regular-profile isolation without a before/after check, provider
dispatch without provenance, or workflow completion without a current receipt.
