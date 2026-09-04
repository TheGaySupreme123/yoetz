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

Use an explicit isolated root; never point a test at regular `~/.cursor`. An isolated Cursor
home isolates only Cursor: an isolated-Yoetz test cell must also export `YOETZ_ISOLATED_ROOT`
(ADR-026) into the cell's `mcp.json` `env` and hook commands, or the plugin's Yoetz children
resolve the live service singleton and state; prove the mode with
`yoetz service isolation --json` from the cell environment. The Cursor plugin
command surface is `preview`, `install`, `status`, and `remove` (`--action replace` previews a
replacement); the generic `update`, `enable`, `disable`, and `export` commands listed by the shared
group are Claude Code lifecycles and refuse for Cursor with
`cursor_plugin_command_unsupported:<command> supported=preview,install,status,remove` (exit 2).

Issue #561 changes only Yoetz-owned external Codex registration. Cursor remains supported through
its native plugin projection: the exact isolated root is rendered into the test cell's `mcp.json`
and hook commands and is bound by that artifact's preview digest. Cursor does not consume the Codex
`mcp add --env` path or its `isolation_binding` status field.

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
Cursor target adds `--host cursor` and binds the exact launcher: its `mcp.json` entry is
`command: <absolute yoetz executable>` (or the interpreter, with `-m yoetz` leading `args`) followed
by `mcp serve --host cursor` (policy) or `mcp serve --host cursor --semantic off` (strict) — the
same launcher the native hooks use and the `/2` marker records. Cursor's MCP reference resolves a
bare `command` through the desktop app's sanitized PATH, which in the 2026-08-29 dogfood launched
an older ambient runtime (control schema 2.1.0) behind a marker-valid then-current plugin (2.3.0); the
bound entry removes PATH from the runtime choice (issue #468). That profile retains
`structuredContent` and also repeats the exact canonical JSON body in text `content`, because
pinned Cursor `3.17.x` can otherwise hide structured results from the model. It adds no
environment or secret field and does not widen the service route. Route recognition accepts a
hand-written bare `yoetz` (external registrations) or a known launcher (this runtime's or the
installed marker's) with the exact serve arguments; anything else is `foreign`. Raw initialize and
tools/list prove only runtime registration. Require a correlated model-controlled `start` or
`status` call for use.

`yoetz integrate cursor plugin status` reports the binding under `launcher`: `installed` and
`artifact` launchers; `executable` (`matched` — same launcher and it exists; `drifted` — the
installed tree binds another installation than the one reading status; `missing` — the bound
executable is gone; `unbound` — portable or legacy `/1` marker; `unobserved`); `mcp_binding`
(`exact_launcher`, legacy `ambient_path`, `absent` for external registration, `foreign`); and
`identity`, probed by running the installed launcher's read-only `version --json` and comparing
`package_version`, the `control-result` schema version, and `resource_manifest_digest` with the
runtime reading status (`observed: false` when it cannot answer). `mcp.runtime.executable_activation`
compares live Cursor-helper children with the installed launcher: `executable_mismatch` forces
`activation: full_restart_required`. A tree rendered before issue #468 stays marker-valid and shows
`state: modified` with `mcp_binding: ambient_path`; perform one exact previewed replace, then
fully quit Cursor.

### Applied-route drift decision (issue #537)

Decision for Cursor: not supported here — no additional state-root applied-route record
at this time. The plugin-managed `mcp.json` entry already binds the route profile (the exact
serve arguments, including `--semantic off` for strict) and the `/2` marker records the same
launcher the native hooks use; the live binding and launcher read-backs above remain the
authority for which route this host serves. A stale serving process shows as
`executable_mismatch` / `full_restart_required`, not as applied-vs-serving drift. If a
ceiling check ever needs that distinction on this host, that is a separate design-gated
change.

## Auto-review and host admission

Cursor's Auto-review run mode sends non-allowlisted MCP calls to a classifier that may allow,
redirect, or ask; Ask Every Time was removed in 3.5. Its inputs are undocumented and it "is not
a security boundary" (`cursor.com/docs/agent/security/run-modes`, `/reference/permissions`,
`/cli/reference/permissions`, re-read 2026-08-30). The levers are `mcpAllowlist` (`server:tool`,
case-insensitive, `~/.cursor/permissions.json` and `<workspace>/.cursor/permissions.json`
concatenate; no deny list exists) and the Agent CLI's `permissions.allow` `Mcp(server:tool)` in
`<project>/.cursor/cli.json` (deny wins over allow).

Host admission (issue #467) writes both project-scoped entries for exactly `check`:

```text
yoetz integrate cursor admission preview --project-root <project> --cursor-config-root <root> --mcp-ownership plugin-managed --json
yoetz integrate cursor admission grant --project-root <project> --cursor-config-root <root> ... --accept --preview-digest <digest>
```

`.cursor/permissions.json` receives `yoetz:check` (the docs name the server by its `mcp.json`
key, which is `yoetz` for every Yoetz route). `.cursor/cli.json` receives `Mcp(yoetz:check)`
for an external registration or `Mcp(plugin-yoetz-yoetz:check)` for the plugin-managed server,
following the exclusively observed owner: the CLI names a plugin-bundled server
`plugin-<plugin>-<server>` (live-verified 2026-08-29). Whether the IDE names a plugin-bundled
server the same way is undocumented and unverified; the acceptance cell in issue #467 is open.
`status` reports `partial` when only one of the two files carries the entry. A wildcard
(`yoetz:*`, `*:*`, `Mcp(*:*)`) or a CLI deny rule is `foreign` and never edited.

A mutating preview warns `host_config_not_compare_and_swap`; keep Cursor and other settings writers
quiescent during apply. Yoetz rechecks each exact preimage immediately before its atomic mutation
and verifies the combined result, but ordinary files cannot exclude a non-cooperating same-UID
writer in the final syscall window. If the second surface drifts after the first changed, the
operation reports `write_failed` rather than claiming a transaction-wide rollback.

Reverse: `admission revoke`; `plugin remove` and an install/replace onto the strict route sweep
the entry when `--project-root` is given and report `admission_cleanup`; a privacy commit that
stops external review sweeps it; `provider status` reports `host_admission_drift`. That report
walks from the launch directory to the repository root, so a subdirectory cwd does not read as
`absent`. Cursor
publishes no hook for a classifier denial, so a held check is visible only through the #187
pause/approval flow; that gap is documented, not diagnosed.

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

Claim correction is carried by the shared `publish_work` descriptor at
`publish-work-request/1.1.0`; Cursor hooks and subagent inheritance do not synthesize a replacement
claim. The matching local-control schema is 2.4.0; manifest mismatch stops an older 2.3.0 helper
before its frozen opaque branch can classify the new pair. Fully restart the exact Cursor profile
and re-prove descriptor plus correlated model use after upgrading before reporting the capability
as active.

## Multitask delegation after an outage

Cursor subagents inherit the parent's MCP tools, so delegated workers reach the same `yoetz`
bridge process. The bridge latches the first availability failure of that binding
(`service_unavailable`, `service_incompatible`, `protocol_mismatch`, `endpoint_unsafe`,
`peer_untrusted`): the parent's error carries `safe_details.availability: terminal_unavailable`
with `host_profile`/`route_profile`, and every later call under a new `request_id` — any tool,
any worker — returns the same `correlation_id` with `availability_inherited: true` and mints no
new diagnostic, startup, or supersede. Parallel first calls (ordinary host behaviour) share that
same single attempt: they do not each mint a diagnostic before the latch exists (issue #476). The
latch clears when the original `request_id` replays after the
named repair, when `yoetz service run|restart|stop` changes the stamped holder, or (retryable
classes only) when one quiet handshake succeeds. The skill tells the coordinator to carry a
bounded `yoetz_availability` block into each assignment and tells delegates that inherit it to
make no Yoetz call and publish nothing; lifecycle commands are never a response to
`INTERNAL_ERROR`. In the 2026-08-29 dogfood (issue #469) the initial outage was the ambient-runtime
mismatch above; the delegates only amplified it. Report those two facts separately, and never
claim delegate publications or attribution without a task and session.

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

Cursor remains structural-only for issue #302: its native hooks retain digests and allowlisted
outcome metadata but no captured content object, so they do not mint `observation_captured`
evidence. Adding Cursor content capture requires a separately acknowledged capability/privacy
expansion and exact host fixtures.

Cursor has no `codex exec --json` import surface. Issue #301's bounded import authorization makes
no Cursor adapter change; Cursor evidence continues through cooperative MCP and native
hook/observation paths.

The native IDE plugin advertises only `sessionStart`, `sessionEnd`, `afterMCPExecution`,
`afterFileEdit`, and `stop` for the pinned local profile. It intentionally excludes
`afterAgentThought`. Cursor also supports Agent Plugins: Yoetz's portable artifact supplies the
standardized skills and MCP components there, while hooks remain a Cursor-native plugin capability;
the portable CLI artifact therefore advertises no hooks. SDK fixture metadata advertises no hook
capability; the SDKs' file-based hook contract is not execution evidence. Hooks call
`yoetz hooks cursor-observe`, are fail-open, and never enforce Cursor work.

Native hook artifacts and the plugin-owned `mcp.json` resolve the invoking `yoetz` launcher to
one exact command at render time. A
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
(with `paused` for a paused grant), recorded by the shared ingress for every host. A consented
`sessionStart` auto-attaches through the shared `start mode=create_or_attach` request, pairing the
resolved workspace root as `workspace_ref` with `cursor-session:<session_id>` as `external_ref`;
an exact `workspace_task_exists` conflict gets one `mode=attach` recovery only when the private
local store already holds a valid mapping from an earlier Cursor session whose `sessionEnd` was
received, every other bound session is ended, and the candidate is bound only to this consented
workspace. The catalog also requires one mapped task, the selector still active, no sibling task,
the matching repository-privacy binding, and no start already pending for that route. The conflict
reveals no selector, and a hard crash without `sessionEnd` remains fail-closed rather than being
guessed from age. A successful recovery also rewrites every ended same-host predecessor mapping for that task to
the rotated session and writer so pending predecessor rows drain on the successor route
(`session_superseded` is followed, not quarantined as `ledger_rejected`). A failed attempt records its typed cause (`auto_attach_workspace_unbound`,
`auto_attach_request_invalid`, `auto_attach_conflict`, `auto_attach_refused`,
`auto_attach_result_invalid`, `auto_attach_mapping_write_failed`, `privacy_authority_required`,
`service_unavailable`, `vault_locked`, `timeout`, `storage_unsafe`, or `storage_corrupt`) in the
same diagnostics file, and the session keeps an observation-only binding until a retry or an
explicit `start` maps it. For `vault_locked` on a never-initialized install, that explicit
`start` returns the typed `vault_initialization_required` continuation (see Troubleshooting)
rather than a dead end.

Cursor's hooks reference (re-read 2026-09-03) calls local `sessionStart` fire-and-forget: the hook
process can complete this mapping and drain, but the agent loop does not wait for it. Therefore a
rendered hook or passing local handler test does not prove the mapping existed before Cursor's first
agent action; verify eventual `mapping_present`, accepted envelopes, and drain separately. Cursor
cloud agents do not run `sessionStart` or `sessionEnd`, so this recovery is not claimed for that
surface.

The host-neutral `observe status` boundary also keeps storage layers distinct: unsafe state/lock
paths report `storage_unsafe`, bounded open/permission/read-only/missing-parent/lock-acquisition
failures report `storage_unavailable`, invalid stored data reports `storage_corrupt`, and other
defects retain the internal-error boundary. Fixed remediation omits the absolute state path. A
sandboxed Cursor-agent result does not establish unrestricted Cursor-terminal behavior; record
those proof cells separately.

Shared drain terminalization is host-neutral: `ledger_rejected` means the ready service rejected
one envelope non-retryably, so that row is retained in quarantine and later rows proceed. A task
bundle at schema 9 (bundle migration `0009`) stores `cursor_hook` rows; schema 8's source CHECK
refused them. The SQLite store now classifies deterministic constraint failures as `ledger_rejected`
(issue #576). Existing task bundles require the explicit [migration procedure](migration-rollback.md);
upgrading or restarting the service alone does not migrate them, and the new writer refuses an
unmigrated bundle before observation ingestion. Migration allows valid pending envelopes to store
unchanged, but delivery still requires a usable session mapping; it does not itself repair a retired
session route or replay quarantined rows. An
idempotent repeat of a committed envelope (lost acknowledgement, service restart, or a workflow
reattach that rotates the mapped Yoetz session) is resolved task-wide and acknowledged, never
quarantined. A pending row from an ended host session whose task was recovered by a successor
session is delivered on the successor route (`session_superseded` is followed). A successor
binding that cannot be followed quarantines that row as `session_superseded`, not
`ledger_rejected` or `mapping_missing`.
A row
also enters quarantine after 128 consecutive rejections with the same retryable reason, except for
designed back-pressure and workspace-global pause/vault/disabled gates. Both cases remain visible
in `quarantine_causes`, aggregate `delivery_causes`, and gaps;
`pending_delivery_causes` names only pending rows. A hook-driven drain also writes
`hook_diagnostics`, while manual and supervisor drains remain visible through status. Neither case
is repaired by restarting a service that already reports ready.

`afterMCPExecution` of a Yoetz-owned tool follows the shared self-observation policy (issue
#564): an execution of `status`, `receipt`, or `read_guidance` under any Yoetz server spelling
(`mcp__yoetz__*`, `yoetz:*`, `plugin-yoetz-yoetz:*`) is ingested into the bounded local store but
not enqueued for delivery, while `start`, `publish_work`, `check`, and `respond` enqueue one row
each. Cursor's hook payload states no outcome fact for MCP executions, so a failed Yoetz call is
indistinguishable from a successful one at this ingress; the service's own record of the call is
the authority on its outcome. `afterFileEdit` and lifecycle events are unchanged.

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

Measured on 2026-08-29 (issue #468) with the same Cursor Agent CLI build, loading a native
`plugin_managed`/`policy` tree rendered by a development checkout through `--plugin-dir` from an
isolated cell (`HOME` injected into the cell's `mcp.json` `env` and hook commands, which makes the
cell entry read `foreign` to status — read status before injecting): with `PATH` sanitized to
`/usr/bin:/bin` and a foreign `yoetz` shim placed *first* on `PATH`, the plugin's MCP child ran
exactly `<checkout>/.venv/bin/python <checkout>/.venv/bin/yoetz mcp serve --host cursor`, that
bridge spawned `<checkout>/.venv/bin/python -m yoetz service run` from the same installation, the
foreign shim was never invoked, and a model-controlled `start` (`Mcp(plugin-yoetz-yoetz:*)`
allowed in `<project>/.cursor/cli.json`; the CLI names the plugin server `plugin-yoetz-yoetz`)
returned typed `VAULT_LOCKED` with a resolvable `correlation_id` — not `INTERNAL_ERROR` and not
`service_incompatible`. On the same machine the regular Cursor IDE's helper child was running the
maintainer's uv-tool channel (`~/.local/bin/yoetz`, a shebang-expanded argv), and `status` for the
checkout's tree therefore reported `mcp.runtime.executable_activation: executable_mismatch` with
`activation: full_restart_required`, while `launcher.executable`, `mcp_binding`, and
`identity.matched` were `matched` / `exact_launcher` / `true` for the tree itself. The CLI's own
process is not a `Cursor`/`mcp-process` helper, so a CLI-only cell leaves `mcp.runtime` at
`unobserved` for its own child; the IDE cell's live executable match is the remaining
unmeasured facet.

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

Cursor is not an allowlisted `yoetz consent authorize` attestation client in v0.1. It may show the
agent-safe pending status and direct the user to a supported Codex attestation or local trusted
command, but it must not emulate `vault_initialize` or `vault_passphrase_rotate` authorization.
It still guides setup, installation, and settings choices in normal conversation and leaves each
supported product choice with the user. When semantic review is the stated goal, it recommends
Expanded first and explains Assisted as the lower-disclosure semantic option. It may show the full
v6 repository privacy preview, but its missing chat-authority capability is a technical boundary:
give the shortest exact trusted-local continuation and never silently downgrade the chosen recipe,
provider, or model.

First-run start continuation (issue #512): on a never-initialized install, the agent's first MCP
`start` returns non-retryable `VAULT_LOCKED` carrying
`safe_details.continuation: vault_initialization_required`. The `--host cursor` bridge profile
omits `authorize_command` entirely, so the continuation is trusted-local by construction: the
agent runs `yoetz consent prepare vault_initialize`, shows the returned danger text, directs the
user to run `yoetz consent review` on a local terminal, waits for the ceremony's terminal result,
then replays the exact original `start` request ID and body once. On denial or expiry the agent
states the boundary and continues without Yoetz. This supersedes the bare `VAULT_LOCKED` dead end
the 2026-08-29 measurement above recorded for a locked cell.

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
| MCP entry looks right but reads `foreign` | route recognition is key-set exact; an extra `env`/`cwd` key is a foreign entry, and an absolute `command` that is neither this runtime's launcher nor the installed marker's is another installation |
| Hooks observe but a model-controlled `start` returns `SERVICE_UNAVAILABLE` / `service_incompatible` right after install | the plugin's MCP process is another Yoetz installation; read `launcher.executable`, `launcher.mcp_binding`, `launcher.identity`, and `mcp.runtime.executable_activation`, replace a legacy `ambient_path` tree, then fully quit Cursor |
| `launcher.executable` is `drifted` or `missing` | the installed tree binds a launcher that is not this runtime's or no longer exists; one exact previewed replace re-binds hooks and MCP together |
| Delegated workers each report the same `correlation_id` with `availability_inherited: true` | expected: the bridge latched the parent's outage; repair once, replay the original `request_id`, and do not read those as fresh failures |

Always report what is proven and the remaining cells/gaps. A clean local test never proves Cursor
Cloud, a neighboring version, regular-profile isolation without a before/after check, provider
dispatch without provenance, or workflow completion without a current receipt.

## Codex subscription evaluator from Cursor

A Cursor policy route may request the service-owned `codex-chatgpt-subscription@1` evaluator, but
Cursor receives no Codex OAuth credential, home, app-server handle, or evaluator tool authority.
Cursor plugin/MCP/hook activation and Cursor model use do not prove that semantic dispatch happened.
A strict Cursor route must return `route_semantic_ceiling` with zero child launch. Follow the
[subscription evaluator runbook](codex-subscription-evaluator.md) and keep host activation,
accepted observation, runtime evidence, privacy receipt, corrective influence, and workflow receipt
as separate cells.
