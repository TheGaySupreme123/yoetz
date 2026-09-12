# Cursor local integration runbook

## Conditional agent guidance

The skill keeps its activation boundary, core workflow, and safety floor in the entrypoint.
Read workflow guidance before `start`, publication policy before `publish_work`, and coverage
and receipts before `check`. Setup/consent, vault/credential operations, transcript import, and
recommendation decisions route to the corresponding sections of request templates only when
needed. Already-read guidance need not be fetched again while present in context.

Use `semantic_if_configured` only when semantic review is known to be optional; omit `mode` when
relying on the configured default. Select `semantic_required` for an explicit user requirement,
effective policy, or named acceptance criterion requiring independent semantic judgment. Preserve
required review and all host/disclosure approval boundaries. Installed guidance bytes alone prove
neither activation nor semantic dispatch.

Both Cursor renderers select `skills/cursor/yoetz/SKILL.md`; the standalone generic portable export
retains its neutral entrypoint. Cursor's entrypoint respects the active Plan/Ask/Agent mode, separates
a Cursor plan from a published Yoetz plan, and names distinct IDE and CLI tool-discovery surfaces.
Full application restart remains conditional on the documented activation mismatch, without
changing privacy authority.

A new Cursor session reads guidance and discovers tool schemas before calling MCP `start` as
its first workflow operation, before substantive research, commands, edits, or delegation.
Guidance reads (including `read_guidance`), discovery commands, and necessary bootstrap clarification
remain permitted. Hook auto-attachment is a cue, not proof of a current-scope plan: mapped
SessionStart context directs cooperative `start mode=attach` before `status` with the returned
ids; unmapped context directs `start` before material work. Same-session compaction uses held
current ids for `status`. Both startup messages route failures through exact continuations,
same-request recovery, and a named one-time repair before a blocked-startup user handoff.
A first non-retryable failure alone does not permit continuing without Yoetz; see
[startup failure precedence](../../guidance/coverage-and-receipts.md#startup-failure-precedence).
Cursor has no demonstrated equivalent of Claude's PreToolUse deny gate in this integration;
instruction delivery is not enforcement (#692).

Design basis, checked 2026-09-09: Cursor's [skills guidance](https://cursor.com/docs/skills)
uses descriptions for relevance and loads references progressively. Yoetz therefore keeps the
material-work trigger in metadata and the long procedures in shared references. Cursor's
[customization guidance](https://cursor.com/docs/customize-cursor) distinguishes skills for
specialized workflows from persistent rules; no always-on rule or new plan-approval step is added.
The [CLI mode guidance](https://cursor.com/docs/cli/using) informs the active-mode boundary;
the [MCP guide](https://cursor.com/docs/mcp) informs tool discovery and host approval. These design
choices do not promote an untested IDE/CLI version or establish native behavioral acceptance.

Cursor's own [create-learning-path skill](https://github.com/cursor/plugins/blob/main/teaching/skills/create-learning-path/SKILL.md)
and [compatibility skill](https://github.com/cursor/plugins/blob/main/agent-compatibility/skills/check-agent-compatibility/SKILL.md)
in its official plugin repository use direct triggers, short workflows, relevant guardrails, and
an explicit output contract. The Cursor Yoetz skill follows that structure. It keeps automatic
selection enabled; examples of explicitly invoked orchestration skills do not justify making
ordinary Yoetz activation manual-only.


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

Untested is not disabled (issue #656). Hook ingress maps only the IDE cell `3.17.8` to its
reviewed profile; a supplied unknown version — including the Agent CLI build — is admitted as
`untested` and its compatible events ingest under the conservative paired contract, while an
omitted version takes the legacy post-only carrier. Neither branch is promoted to the reviewed
profile, and neither loses ordinary hook observation. This decision was recorded without a new
live Cursor upgrade run; distinguishing the IDE from the Agent CLI capability scope in a
disposable instance remains the acceptance evidence for a promotion.

A pinned test instance (ADR-028, issue #604) adds a second guard beneath the artifact binding:
when the native MCP entry and hook commands name the absolute launcher of a runtime created with
`yoetz instance create --bind-runtime`, that launcher resolves its own root even if Cursor drops the
`env` block or a hook runs without it, and a different `YOETZ_ISOLATED_ROOT` is refused as
`isolation_root_conflict`. The artifact-level `isolation_binding` status is unchanged and still
required; the pin does not replace it, and it does not prove isolated plugin discovery either.
The everyday Cursor profile must keep naming the everyday launcher by absolute path. See
[`test-instances.md`](test-instances.md).

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
its native plugin projection: when `YOETZ_ISOLATED_ROOT` is set, the exact validated root is
rendered into the plugin-managed `mcp.json` environment and every native hook command, and is
bound by that artifact's preview and marker digests. Cursor uses its own
`isolation_binding` status field; it does not consume the Codex `mcp add --env` path.
The status comparison selects the hook set from the installed profile: ordinary artifacts use
`postToolUse`, `postToolUseFailure`, and `preToolUse` alongside lifecycle hooks, while structural
artifacts use `afterFileEdit` and `afterMCPExecution`. An exact MCP binding alone is insufficient;
a root drift in any expected hook surface reports `isolation_binding: different`.

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
is available. That is activation work, not installation proof. For CLI use the exact installed tree
with `--plugin-dir`. Cursor also supports `.cursor/skills`, `.agents/skills`, and compatible host
skill directories. Those are separate delivery paths: a copy or a project rule cannot serve as
evidence that this plugin was discovered. Record the actual loaded source before claiming delivery.

The current Cursor desktop host can still discover a user-local plugin under the regular shared
`~/.cursor/plugins/local/yoetz` when launched with another user-data directory. Treat that as a
host discovery limitation: an isolated user-data directory does not by itself prove isolated
plugin discovery. Keep the explicit plugin root, Yoetz root binding, and discovery observation as
separate proof facets; a clean cell must verify which plugin directory Cursor actually loaded.

## MCP ownership and source precedence

### Project registration and root selection

A user-local plugin can be discovered and its MCP tools can be connected while a workspace
operation returns `SESSION_CONFLICT` with `repository_identity_required`. The reviewed Cursor
implementation can emit absolute filesystem paths in `roots/list.uri` and include multiple open
projects in the shared MCP process's root inventory. Yoetz accepts that strict local path shape
through its Cursor adapter and safely canonicalizes every root. An explicit project registration
selects the intended repository from the host's inventory. Hook `workspace_roots` and an agent's
terminal working directory remain separate facts and cannot replace the MCP client's roots.

For this host behavior, use an explicit project registration and an external-registration plugin
for the skill and hooks. First remove the existing plugin-managed artifact through its normal
preview, consent, and authenticated remove lifecycle. There must be only one `yoetz` MCP owner.
Then invoke the exact intended Yoetz launcher to preview and install the project entry:

```text
/absolute/yoetz integrate cursor project-mcp preview \
  --project-root /exact/project --cursor-config-root /exact/cursor/config \
  --route-profile policy --json
/absolute/yoetz integrate cursor project-mcp install \
  --project-root /exact/project --cursor-config-root /exact/cursor/config \
  --route-profile policy --accept --preview-digest <preview_digest> --json
```

Install the native plugin with `--mcp-ownership external-registration` through the authenticated
plugin lifecycle. Its launcher and isolated root must match the project entry. The project entry
is `.cursor/mcp.json`, has the pinned launcher plus
`mcp serve --host cursor --project-root ${workspaceFolder}`, and carries
only the validated `YOETZ_ISOLATED_ROOT` environment binding when isolated. Strict mode appends
`--semantic off`; omitting `--route-profile` preserves an existing owned route. The project
registration command never launches a service and never grants semantic egress or host trust.
The startup selector is validated against the exact owned project entry, launcher, route, and
directory/configuration identity. It must match a repository in the active client's validated
root inventory; empty or unsupported roots, malformed responses, timeouts, and mismatches still
fail. A changed registration or selected repository retires the MCP session. Use the existing
project admission commands for an authorized policy route.

`project-mcp status` reports configuration ownership separately from unknown host trust and
unobserved runtime binding. Open that project in Cursor, activate the changed server through
Cursor's MCP controls, and use a fresh agent conversation. After changing the entry, disable and
enable its project source to create a new connection, then verify the active command: Reload can
retain an older process or cached definition. Verify a model-controlled `start`, native hook
evidence, captured content, and authorized semantic dispatch separately. Multiple distinct roots
still require a validated selector that matches one host-reported repository. Codex and Claude
Code retain their own existing registration paths.

Reverse the registration with `project-mcp preview-remove`, then `project-mcp remove` using the
exact preview digest and `--accept`. Removal and strict registration sweep only Yoetz-owned
project admission entries and report that result, including an already-absent registration.
Unrelated MCP servers and top-level JSON values are preserved; changed files are serialized as
canonical JSON. Foreign, duplicate, malformed, oversized, hard-linked, or symlinked configuration
is refused. This explicit writer currently requires POSIX descriptor-relative filesystem APIs.

The preview binds both target directories, all three known source preimages, the launcher, the
isolated root, and the before/after project configuration digests. Apply rereads those facts and
atomically replaces the project file through a pinned directory. Cursor does not share a lock
with this writer, so `host_config_not_compare_and_swap` discloses the remaining cross-process
race; this is not a multi-file transaction. After a lost result, inspect `status` and obtain a
fresh preview before retrying. No automatic migration or overwrite of another source occurs.

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
same launcher the native hooks use and the `/3` marker records. In an isolated artifact the MCP
entry also carries exactly `env: {YOETZ_ISOLATED_ROOT: <validated-root>}`; arbitrary environment
keys remain foreign. Cursor's MCP reference resolves a
bare `command` through the desktop app's sanitized PATH, which in the 2026-08-29 dogfood launched
an older ambient runtime (control schema 2.1.0) behind a marker-valid then-current plugin (2.3.0); the
bound entry removes PATH from the runtime choice (issue #468). That profile retains
`structuredContent` and also repeats the exact canonical JSON body in text `content`, because
pinned Cursor `3.17.x` can otherwise hide structured results from the model. It adds no
environment or secret beyond the exact isolated-root binding and does not widen the service
route. Decision for Cursor (issue
#579): supported here — the `--host cursor` text channel is the exact canonical JSON wire body,
so `safe_details.reason_code` and `safe_details.field` are already model-visible for
`EVENT_INVALID`. The generic bounded `Reason:` summary is the Claude Code path; Cursor tests lock
that the JSON copy still carries those tokens and is not reduced to the weaker summary. Route recognition accepts a
hand-written bare `yoetz` (external registrations) or a known launcher (this runtime's or the
installed marker's) with the exact serve arguments, optionally with exactly the validated
`YOETZ_ISOLATED_ROOT` environment binding. Route-shape recognition can describe a structurally
valid binding, but lifecycle ownership requires the artifact's exact root; a different valid root
is `foreign` and cannot count as owned or admitted. Any other command, prefix, or key set is
`foreign`. Raw initialize and
tools/list prove only runtime registration. Require a correlated model-controlled `start` or
`status` call for use.

`yoetz integrate cursor plugin status` reports the state-root binding as `isolation_binding`
(`ambient|isolated_exact|missing|different|unobserved`). `ambient` and `isolated_exact` require
the marker and every root-bearing MCP/hook member to agree; a marker alone does not prove the
child launch binding. `different` covers a changed root or a root-bearing member whose binding
drifted. It reports the executable binding under
`launcher`: `installed` and
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
at this time. The plugin-managed `mcp.json` entry already binds the route profile and declares
`--host cursor` (the exact serve arguments, including `--semantic off` for strict), and the `/3`
marker records the same launcher the native hooks use; the live binding and launcher read-backs
above remain the authority for which route this host serves. The explicit Cursor identity also
prevents the Codex-only applied-route drift comparison from being applied to this host. A stale
serving process shows as `executable_mismatch` / `full_restart_required`, not as
applied-vs-serving drift. If a ceiling check ever needs a Cursor-specific applied-route record,
that is a separate design-gated change.

## Auto-review and host admission

Cursor's Auto-review run mode sends non-allowlisted MCP calls to a classifier that may allow,
redirect, or ask; Ask Every Time was removed in 3.5. Its inputs are undocumented and it "is not
a security boundary" (`cursor.com/docs/agent/security/run-modes`, `/reference/permissions`,
`/cli/reference/permissions`, re-read 2026-08-30). The levers are `mcpAllowlist` (`server:tool`,
case-insensitive, `~/.cursor/permissions.json` and `<workspace>/.cursor/permissions.json`
concatenate; no deny list exists) and the Agent CLI's `permissions.allow` `Mcp(server:tool)` in
`<project>/.cursor/cli.json` (deny wins over allow). Cursor does not document whether the
classifier sees MCP initialize `instructions`, so the policy-route destination disclosure of
issue #479 is not relied on on this host; `mcpAllowlist` remains the lever.

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

The other workflow calls have a local effect: `start`, `publish_work`, `respond`, and `receipt`
append or read records in the local Yoetz ledger, while `status` and `read_guidance` read local or
packaged state. None of these calls publishes to GitHub or invokes a semantic provider. This
describes the effect after Yoetz receives the call; it does not predict Cursor's admission decision.
Cursor Auto-review may still hold a non-allowlisted local call as a shared-state or external-workflow
action, and Cursor provides no hook event for that classifier decision.

For a held local call, use Cursor's visible approval control for that exact call if the workflow is
authorized to continue. A hold before invocation is not a Yoetz result and creates no operation,
semantic status, provider attempt, or receipt. After approval, let Cursor execute the exact held
call and continue from its returned result. If Cursor requires resubmission or the response is
missing, retry the same request body and `request_id`. If the call may have started but its result
is ambiguous, use `status view=operation` with the original request identity or replay that same
request according to the operation's recovery instructions. Do not mint a replacement request or
duplicate event identity. This recovery procedure does not change Cursor's Auto-review settings or
prove that a future call will be admitted.

A mutating preview warns `host_config_not_compare_and_swap`; keep Cursor and other settings writers
quiescent during apply. Yoetz rechecks each exact preimage immediately before its atomic mutation
and verifies the combined result, but ordinary files cannot exclude a non-cooperating same-UID
writer in the final syscall window. If the second surface drifts after the first changed, the
operation reports `write_failed` rather than claiming a transaction-wide rollback.

Reverse: `admission revoke`; `plugin remove` and an install/replace onto the strict route sweep
the entry when `--project-root` is given and report `admission_cleanup`; a privacy commit that
stops external review sweeps it; `provider status` reports `host_admission_drift`. That report
walks from the launch directory to the repository root, so a subdirectory cwd does not read as
`absent`. Cursor publishes no hook for a classifier denial. A held `check` is visible only through
the #187 pause/approval flow, while a held local call has no Yoetz-side denial diagnostic; that
gap is documented, not diagnosed.

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
evidence. The ordinary-work profile below is a separately acknowledged capability/privacy
expansion; the default structural profile retains the boundary above.

The default structural artifact remains unchanged. An explicitly rendered ordinary-work artifact
uses `cursor-ordinary-observation-v1` and subscribes to Cursor's generic `preToolUse`,
`postToolUse`, and `postToolUseFailure` events plus lifecycle signals. `afterMCPExecution` is
subscribed only to bind an exact Yoetz-owned successful `start`; it emits no observation,
content, or advice in this profile. It leaves `beforeShellExecution` and `afterFileEdit` out until a
deduplication contract proves they are distinct from the generic stream. The hook command carries
the exact profile id with `--observation-profile`; the id records the normalization contract and
does not certify the installed Cursor build.

The ordinary `postToolUse` path emits queued advice through the documented
[`additional_context` output](https://cursor.com/docs/hooks#posttooluse), including when a
command's exit is unknown or nonzero. Advice is marked delivered only after successful stdout
emission. `postToolUseFailure` has no consumable output, so its advice remains pending for a later
supported event. The rendered ordinary hooks use a lightweight entrypoint that avoids loading the
full CLI application graph. Ordinary events have a five-second budget, `sessionStart` and `stop`
ten seconds, and `sessionEnd` three seconds. Local structural capture, pairing, and the outbox
intent close before the bounded service drain; advice-bearing events remain synchronous so
`additional_context` stays on the current hook response. Legacy edit/MCP hooks keep their existing
output behavior, and automatic Stop follow-up messages remain disabled. Hook success never
substitutes for an explicit command/test exit fact.
For ordinary MCP tool events, `tool_output` contains tool-domain data. Only the outer MCP
`isError`/`is_error` signal contributes execution status; nested `status`, `outcome`, `success`,
and exit-like fields do not describe the host execution. Built-in shell outcomes retain their
separate exit-status handling.

Select these hooks with `--observation-profile ordinary` on the existing native Cursor plugin
preview/install/status commands. Repeat the same profile when applying an exact preview. To
return to structural hooks, preview a replacement with `--observation-profile structural` and
apply that exact preview. Portable artifacts reject ordinary observation; selecting a native
artifact does not grant content capture.
Preview names the selected profile. Status reports the requested profile and confirms an installed
profile only when its verified marker and artifact digest match; otherwise that installed value is
unknown rather than inferred from the request.

Native content is a second, per-host consent arm. After granting structural observation, enable or
revoke it with:

```text
yoetz observe content-enable --workspace /exact/project \
  --profile cursor-ordinary-observation-v1
yoetz observe content-status --workspace /exact/project --json
yoetz observe content-disable --workspace /exact/project \
  --profile cursor-ordinary-observation-v1
```

The service accepts Cursor chunks only when that exact profile is active in local consent and in
the mapped task grant. A missing or mismatched profile drops plaintext chunks and records
`content_capture_unavailable`; chunks are not written to the structural outbox. Authorized native
hooks reserve the workspace drain before enqueueing the structural row, keeping a background sweep
from consuming it before the foreground content attempt. The nonblocking reservation is released
on cancellation or after the bounded drain; contention can still leave a content gap.
Content-bearing ordinary-profile native passes have a one-second drain window. Contentless
structural rows, including Yoetz-owned MCP mutations whose result is already durable elsewhere,
defer service delivery. When chunks exist, the pass prioritizes the current event after its
same-session FIFO prefix, within that one-second drain and sixteen-row bound. Teardown
keeps its host-clamped three-second hook, records local lifecycle/outbox intent, and defers service
delivery to a later hook or the sweeper (a ready service's idle sweep interval is 60 seconds);
it skips local advice construction because the closing host
cannot receive it. A stale or blocked
backlog produces the explicit gap when the hook completes.
A hard process kill or service failure before authenticated service-side staging completes may
lose transient content without a durable gap marker; there is no plaintext local spool or offline
acceptance guarantee. For this ordinary Cursor profile, the capture-only service request can commit
encrypted objects, manifests, and a metadata-only capture ticket before the structural FIFO ingest.
After that boundary, a retry revalidates the original host/source and content-authority generations,
requires the complete expected group/part set, and reuses the ticket rather than reminting content.
The bounded staging handoff can therefore survive a service restart, while a revoked or incomplete
ticket remains an honest content gap. The installed Cursor IDE `3.19.7` fact is a candidate local
host observation while this runbook's pinned compatibility cells remain unchanged; it cannot
certify the ordinary profile without an exact isolated fixture and receipt evidence for native hook
delivery, accepted content, semantic selection, and influence.
Native semantic selection uses the accepted tool event's durable session route and does not
require an approved-check policy. Local capture consent and repository disclosure permission
remain separate requirements.

### Smart observation selection (issue #687)

Cursor's shared hook ingress applies the selector to the generic tool stream only when the exact
ordinary profile `cursor-ordinary-observation-v1` is installed and rendered. The structural
profile remains its existing post-only/native event contract; selecting a retention mode does not
widen it. With the ordinary profile, **Focused/standard (512)** is the default. A paired, proven
successful routine read, search, or inventory call may be represented by a bounded summary in
Focused mode. Detailed keeps eligible routine calls as individual records while pressure is
healthy. Capacity is independent of detail: `standard` (512), `larger` (2,048), and `largest`
(8,192) can each be selected with either mode. The larger profiles have finite provisional budgets
and do not certify an IDE cell or improve sustained throughput.

Failures, denials, cancellation, interruption, partial, unknown or conflicting outcomes, edits and side
effects, declared checks/negative verification, and reads protected for an obligation, claim, or
finding remain individual records in both modes. A pre-event keeps its native tool identity until
a post event proves success. Ambiguous shell composition and caller-supplied routine labels do not
make an event eligible for summarization. Cursor's `afterMCPExecution` binding event remains
binding-only for the ordinary profile; the structural profile's `afterFileEdit` remains edit
evidence. Neither path is turned into a routine read by selection.

Apply an owner choice from an exact preview:

```text
yoetz observe selection-preview --workspace /exact/project \
  --detail detailed --capacity larger --session-id <cursor-session-id> --json
yoetz observe selection-apply --workspace /exact/project \
  --detail detailed --capacity larger --session-id <cursor-session-id> \
  --accept --preview-digest <preview-digest> --json
```

This is a temporary session override unless `--persist` is supplied on both preview and apply
without `--session-id`; `--persist` is the explicit workspace choice. An optional RFC3339 UTC
`--expires-at` bounds either setting. `selection-status` reports selected and effective mode and
capacity; `selection-revoke` restores the safe fallback at the matching scope. These controls
affect future retention only. They do not rewrite accepted rows, extend Cursor's ten-second
session/stop, five-second ordinary event, or three-second teardown budgets, or change content,
privacy, provider, or network authority. Pressure can make a selected Detailed session effectively
Focused until the bounded recovery policy returns it to its still-valid selection.

Use `protect-read` before an upcoming read when a later claim needs its individual identity. The
reference must be an `obl_`, `clm_`, or `fnd_` identifier; at most 32 logical reads are outstanding,
and the protection expires after ten minutes by default (an explicit expiry cannot exceed that
bound). It narrows retention under existing observation consent and grants no content or
disclosure authority. `promote` can only promote an exact native identity while it remains in the
bounded buffer. After delivery it reports `promotion_window_closed` and `not_retained`; rerun the
current read as a new observation if historical bytes are required, and do not use that rerun to
prove the earlier state.

Cursor ordinary content capture remains a separate consent arm selected by the exact profile. Its
workspace-wide capture lane allows at most 512 staging/pending tickets and 128 MiB of captured
content, independently of structural capacity. The one-second native content drain window and
host hook deadlines still apply. A timeout, cancellation, incomplete content group, or service
failure leaves partial/unknown coverage or `content_capture_unavailable` where the boundary
permits; it is never silently converted to a successful routine summary. Capture status proves
configuration only, not accepted bytes, semantic selection, or receipt coverage.

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

### Delegate identity and file overlap (#508, #509)

The current exact local capability cell is IDE `3.17.8` and Agent CLI
`2026.07.09-a3815c0`. A read-only inspection of the installed app found Cursor `3.19.7`; its
resolver contains `SubagentStartRequestQuery` / `SubagentStopRequestQuery` fields such as
`subagent_id`, `parent_conversation_id`, and `tool_call_id`. Those shipped type definitions are
artifact evidence only and do not prove that the pinned 3.17.8 IDE or CLI emits, forwards, or
binds them at runtime.

The decision is **not supported here** for native subagent observation on both surfaces. The IDE
profile advertises only the five hooks above, and the CLI profile has no admitted hook or SDK child
signal. An inherited MCP session may carry an attach handle only through cooperative prompt
delivery; no separate child session identity is currently proven. A child `afterFileEdit` has only
the one-way changed-path digest, so #503 file-overlap attribution is `not observable for a
delegate` unless the child explicitly registers and supplies its own task/session. Such activity
is recorded as an attribution gap and never silently assigned to the parent.

If a future exact cell proves a child signal, the service may stamp one `host_observed` pending
annotation from `subagent_id` plus parent conversation/tool correlation. An accepted parent-minted
delegate or cooperative self-registration then binds that annotation; host metadata alone never
creates a child task. The #509 row stays evidence-gated until an isolated cell reports child start,
publication, observation, advice isolation, and receipt separately. Cursor Cloud/Cloud Agents and
the portable CLI artifact remain separate unsupported surfaces.

Cursor's installed hook profile is post-only. `generation_id` identifies the
host turn/conversation and remains metadata; it is never used as a tool-call
identity or to synthesize a missing `PreToolUse`. A future paired Cursor
profile must be an exact capability-profile table entry and declare a real
tool-call identity before pairing is enabled.

Native hook artifacts and the plugin-owned `mcp.json` resolve the invoking `yoetz` launcher to
one exact command at render time. A
console-script invocation resolves to that absolute executable; the documented `python -m yoetz`
entrypoint (ADR-007) is preserved as an equivalent module invocation of the same interpreter.
Explicit absolute and relative invocations retain their path intent and never fall back to
an ambient `PATH` entry; only a bare `yoetz` name uses `PATH`. The resolved launcher command is
recorded in native marker schema `/3`; an explicit invocation does not silently bind a
different ambient-PATH installation, and a malformed `/2` or `/3` launcher invalidates the marker.
The `/3` marker also records the exact isolated root or null for ambient mode. Portable markers
remain `/1`. A valid legacy native `/1` or `/2` marker is recognized as managed-but-modified so users can
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
resolved workspace root as `workspace_ref` with `cursor-session:<session_id>` as `external_ref`.
Before automatic new-pair admission, it checks private persisted mappings from eligible ended Cursor
sessions. Eligibility requires a received `sessionEnd`, every other bound session ended, and a
candidate bound only to this consented workspace. A unique eligible mapping is selected first: the
hook holds the workspace and lifecycle locks, revalidates ownership and state, and sends one
`mode=attach` request carrying that selector plus the new pair. The catalog requires one mapped
task, the selector still active, no sibling task,
the matching repository-privacy binding, and no start already pending for that route. Recovery
revalidates unmapped sessions, cross-workspace ownership, mapping identity, and mapping recency; a
busy workspace reservation defers with `auto_attach_recovery_busy`, while candidate-lock contention
or changed state returns the closed `auto_attach_recovery_busy` boundary rather than creating work
from an unstable selector. A successful recovery rewrites every ended same-host predecessor mapping
for that task to
the rotated session and writer and drains pending rows on the successor route
(`session_superseded` is followed, not quarantined as `ledger_rejected`). With no usable persisted
selector, automatic `create_or_attach` admits the new pair as independent work, including beside a
dormant task. `workspace_task_exists` identifies only explicit `mode=create` colliding with an
identical pair; workspace membership never selects a task. Age alone never proves a host session
ended. A failed attempt records its typed cause (`auto_attach_workspace_unbound`,
`auto_attach_request_invalid`, `auto_attach_binding_ambiguous`, `auto_attach_conflict`,
`auto_attach_refused`,
`auto_attach_result_invalid`, `auto_attach_mapping_write_failed`, `privacy_authority_required`,
`service_unavailable`, `vault_locked`, `timeout`, `storage_unsafe`, or `storage_corrupt`) in the
same diagnostics file, and the session keeps an observation-only binding until a retry or an
explicit `start` maps it. For `vault_locked` on a never-initialized install, that explicit
`start` returns the typed `vault_initialization_required` continuation (see Troubleshooting)
rather than a dead end.

Busy host lifecycle changes are durable local work. State schema `/11` adds bounded pending
session-lifecycle intents, and a READY or hook drain reconciles them under the workspace and
session reservations before routing their rows; busy mapping writes use an atomic per-session
handoff. Upgrade this state quiescently: stop the older Yoetz service and Cursor hooks, install
the new runtime, then restart the service and all Cursor integrations before writing `/11` state.
Mixed old and new writers are unsupported because a `/10` writer ignores the new pairing fields and
can erase a deferred intent when it saves.

The native Cursor MCP bridge has a separate workspace binding. It does not use the helper's process
CWD, because a Cursor MCP child can be launched from the user home directory. On the first workflow
tool call, the `--host cursor` bridge asks the active MCP client for `roots/list`. Its Cursor adapter
accepts safe local file URIs and strict absolute local paths. They must canonicalize to one repository,
or the validated owned project selector must match one repository in the returned inventory.
An unavailable, remote, malformed, nonmatching, or unresolved multi-repository response returns
`SESSION_CONFLICT` with
`safe_details.reason_code: repository_identity_required` before any local-service call. The bridge
retains and revalidates that binding before each workflow call; a changed or unusable root result
retires the session and its local client. A `notifications/roots/list_changed` message does the same,
so fully quit and relaunch Cursor or start a fresh MCP process before another workflow call.
The reviewed Cursor 3.19.7 host bundle advertises `roots.listChanged: false`, so the per-call
revalidation remains required even when no roots-change notification is sent.
`read_guidance`, tools discovery, and resource reads remain available without a project root. The
public workflow `workspace_ref` is never consulted for this binding. Keep that field equal to the
open repository root when using create/attach selectors, as the agent convention and guidance
require; a mismatch does not override or invalidate the trusted roots binding, but normal task
lookup can still follow the mismatching public ref pair. Auto-attach, queue admission, and mapping
diagnostics are separate hook lifecycle behavior and are not repaired by this MCP binding.

The `sessionStart` status probe for an already-mapped session connects with the resolved workspace
root as its repository locator, so a live mapping answers `active` and the `additional_context`
names the task, frontier, mapped `session_id` and `writer_id`, and the `start mode=attach`
continuation by that session id (issues #578, #580). A daemon fence refusal records
`status_workspace_unbound` / `status_workspace_mismatch` and keeps the mapping; only a replaced
session records `mapping_stale`. Cursor resolves its root from the host payload's
`workspace_roots` before the rendered `--workspace .`, so the Codex-specific issue #659 failure
(a probe with no usable repository context) does not reproduce here; an unresolvable root is the
typed `workspace_unresolvable` failure before any probe, and a fence refusal records the companion
`locator_source_explicit` diagnostic row.

A successful explicit Yoetz `start` now binds the canonical Cursor session before normal
observation handling (issue #661). The adapter transiently decodes `result_json` on
`afterMCPExecution`, or `tool_output` on an exactly server-scoped `postToolUse`, then passes the
result to the existing lifecycle binder. Only validated task/session/writer IDs and an optional
frontier token enter mapping storage. Task switching and same-task session replacement use those
returned IDs; the workspace ambiguity guard is unchanged and never guesses a task.

[Cursor's hooks reference](https://cursor.com/docs/hooks), checked 2026-09-08, documents
`mcp_server_name` on `afterMCPExecution`. That hook admits bare `start` only for exact `yoetz` or
`plugin-yoetz-yoetz` server keys. The adapter also accepts the existing fully scoped
`mcp__yoetz__start`, `mcp__plugin_yoetz_yoetz__start`, `yoetz:start`, and
`plugin-yoetz-yoetz:start` forms, rejecting a conflicting server field. Generic `MCP:start` alone
does not identify an owner and cannot bind. The ordinary profile therefore uses the MCP-specific
hook only for binding and keeps the generic hook as its sole tool-observation stream. Failed
results, foreign tools, malformed IDs, and contradictory session aliases cannot replace a map.
Unparsed or invalid admitted results and failed/deferred writes report the existing closed
`start_bind_unparsed`, `start_bind_invalid_ids`, `start_bind_write_failed`, or
`start_bind_deferred` diagnostics. Deferred writes follow the same lifecycle lock as recovery.

After upgrading, preview and apply the ordinary native plugin update so the binding-only hook is
installed, reconnect the MCP server with the intended project open, and call `start mode=attach`
using the known session ID. Check `observe status` for mapping and delivery separately: successful
MCP attachment alone is not observation recovery. Binding permits subsequent consented content
handoff; it cannot reconstruct content that an earlier unmapped call never delivered.

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
(issue #576). During a compatible 0.2-to-0.3 package update, after old writers are stopped, the
fresh service runs its backup-first bundle migration before READY; no per-task migration ceremony
is required. The ordinary writer still refuses an unmigrated bundle before observation ingestion,
and an unsupported or ambiguous startup result remains fail-closed with the [migration and rollback
procedure](migration-rollback.md). Migration allows valid pending envelopes to store unchanged, but
delivery still requires a usable session mapping; it does not itself repair a retired session route
or replay quarantined rows. An
idempotent repeat of a committed envelope (lost acknowledgement, service restart, or a workflow
reattach that rotates the mapped Yoetz session) is resolved task-wide and acknowledged, never
quarantined. A pending row from an ended host session whose task was recovered by a successor
session is delivered on the successor route (`session_superseded` is followed). A successor
binding that cannot be followed quarantines that row as `session_superseded`, not
`ledger_rejected` or `mapping_missing`.
A non-retryable `SESSION_CONFLICT` while acquiring the task runtime reports `mapping_missing`,
keeping the envelope pending for a later drain after its lifecycle mapping is repaired. The route must still
pass its ownership checks. Non-retryable conflicts after runtime acquisition remain
`ledger_rejected` and enter quarantine. Retryable route conflicts report `service_unavailable`
and stay pending.
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
the authority on its outcome. The shared advice guard recognizes both Cursor server spellings, so
a self-owned hook without an explicit failure does not lease pending frontier or recommendation
context for the call being observed; explicit failures remain eligible for pending advice.
This does not change local retention or explicit failure delivery. Cursor reports
`duration` as a finite decimal number of milliseconds
for MCP executions, while the canonical structural field is the bounded integer `duration_ms`.
Cursor ingress truncates that vendor value to whole milliseconds before structural filtering; the canonical parser
continues to reject floats on every ledger and non-Cursor host surface. Decimal values in discarded
vendor fields are replaced transiently and never reach the structural envelope. Execution and file
edit payloads use `model`, while lifecycle payloads may also provide `model_id`; a valid
`model_id` takes precedence when both spellings are present, and `model` is its bounded fallback.
Malformed or unsafe Cursor envelopes remain fail-open but record `cursor_payload_invalid` as a
payload-free hook diagnostic. `afterFileEdit` and lifecycle events are otherwise unchanged.

Cursor's currently reviewed native profile is post-only: `afterMCPExecution` and `afterFileEdit`
do not imply a missing `PreToolUse`, so accepted observations carry no synthetic `unpaired_event`
gap. Their `generation_id` is retained as bounded host metadata and is never used as a tool-call
identity; the materializer records metadata-only evidence instead of fabricating an action/result
pair. Codex's paired hook profile keeps its source/session/generation-scoped orphan diagnostics.
The historical 0.3 cell used control 2.5, which admits `pairing_mode`, `correlation_kind`, and
`generation_id` on structural observation payloads. Current control 2.7 retains those fields and
main's control 2.6 observation-selection projection alongside the 0.3 coordination surface; both
peers must use the same current manifest. An older development artifact omitted these fields from its
closed schema, so the client refused a valid Cursor-shaped frame before sending it and the hook
layer reported `ledger_rejected`. The frozen control 2.4 schema remains available for historical
validation; metadata is not stripped to disguise an incompatible contract.

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
durable. Modified plugin bytes or recovery residue, including an isolated-root drift, are preserved
for review until an explicit replacement or removal preview is accepted. Foreign, dual, or
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
| MCP entry looks right but reads `foreign` | route recognition is key-set exact; an extra key such as `cwd`, or an `env` object other than exactly `YOETZ_ISOLATED_ROOT=<absolute printable root>`, is foreign, and an absolute `command` that is neither this runtime's launcher nor the installed marker's is another installation |
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

Fallback endpoint pairing (issue #582) is host-independent: whether the evaluator or a paired
API provider serves a given attempt is a service-side dispatch decision recorded in provenance
(`fallback_from`), with no Cursor-specific behaviour, plugin, or route input — the route ceiling
applies to dispatch authority regardless of which endpoint serves.


### Large tasks and semantic failure recovery (#674–#676)

This host uses the shared service status snapshot cache and bounded semantic reference selection.
A reduced reference scope reports `semantic_reference_scope_reduced`; it is not full-task semantic
coverage. `failed/case_capacity_exceeded` and `semantic_case_capacity_exceeded` mean the required
packet could not fit before any provider attempt. Select a smaller claim/obligation scope for a new
check. Shorter prose alone need not fix structural capacity.

For `coordinator_failure`, use the check's original request ID with
`yoetz service diagnostics --request-id req_…` to read bounded failure stages. Dispatch entry can
have an unknown outcome, even with null provenance. A missing record is a diagnostic coverage gap,
not proof of non-dispatch. Preserve the original request/attempt identity and existing retry rules.
These shared-path regressions do not certify a fresh installed native-host session.


### Evidence-first closure

The shared guidance instructs this host to paginate `status view=evidence` before publishing
replacement evidence, match state and source identity, and reuse only suitable observed IDs.
Digest-only, unavailable, unselected and clipped items remain separate limits. Evidence discovery
and reuse do not prove native capture coverage or command success. Read `command_attempts` on
obligation rows: this profile may omit command text, in which case reconciliation is `unknown`.
Only service-stamped, explicitly linked observations can support a match or mismatch. The optional
CLI closure composer uses the same projected status inputs; it does not grant capture or egress.
These shared regressions are synthetic contract evidence, not live-host certification.

### Bounded workflow recovery examples (#613)

These examples use the existing MCP operations on the pinned local Cursor IDE or Agent CLI cell.
Replace placeholders with values returned by the current call or `sessionStart` context; do not
reconstruct them from memory, a remote URL, or the live store.

- **Read retry.** If a `status`, diagnostics, or `status view=operation` read times out, repeat the
  same read intent with a new read `request_id`. Preserve its view, operation filter, cursor, and
  limit. An unreadable read does not establish that an operation or task is absent.
- **Ambiguous write.** If a `start` response is lost before session/writer IDs are returned,
  replay the exact original `start` body once with the same request ID. Otherwise read
  `status view=operation` with `filter.operation_request_id` set to the original write ID:
  replay only `absent`; use stored `complete`; follow an exact typed continuation and required
  approval before replaying `pending`. Retain and report pending without a continuation,
  `quarantined`, or unknown. Never invent session/writer IDs or create a task to escape a write.
- **Exact-session attach.** When `sessionStart` or recovery context provides a held `session_id`,
  use that exact value as the `mode=attach` selector. Cursor's canonical workspace fence comes
  from `workspace_roots`/`CURSOR_PROJECT_DIR`, not the plugin directory in `$PWD`; if the request
  carries identity refs, include the canonical `workspace_ref` + `external_ref` pair together,
  never `workspace_ref` alone. Use the returned successor `session_id` and `writer_id`, read
  `status`, and continue only from that binding. A bare `task_id` is not an attach selector.
- **Same-pair fresh conversation.** With no held session, call `start mode=create_or_attach` using
  the exact canonical project root as `workspace_ref` and the same stable `external_ref` pair, with
  no `session_id`. A remote URL is not a workspace identity, and a fresh Cursor conversation is
  not an implicit second task.
- **Explicit sibling handoff.** Use `start mode=create` only after same-task pair/session recovery
  is exhausted, every earlier write has a known terminal outcome, the Cursor binding is healthy and
  authorized, and the user has declared one bounded remaining or repaired verification scope. Keep
  the canonical project root as `workspace_ref`, choose a different stable `external_ref` such as
  `<work-item>-recovery-v1`, and bind the native mapping from the exact returned `start` result via
  `afterMCPExecution`/`postToolUse`. Verify `mapping_present` and the returned task/session/writer,
  then publish fresh plan, evidence, checks, and receipt for that scope. State that the predecessor
  receipt, findings, obligations, evidence IDs, and unresolved status remain separate; the sibling
  cannot make the predecessor look resolved. If no new scope exists, keep the old receipt and stop
  instead of creating another sibling.


## Observation recovery diagnostics

Cursor uses the shared selected-admission and outbox store. New native input obeys current hard
pressure and the complete projected buffer/outbox size; accepted replay work remains drainable.
Hard pressure can recover to high before optional detail recovers. A generation-fenced session
end retires pressure scheduling without deleting pending work or historical losses.

Unknown capture inventory can reject new input even when the outbox is empty. The READY
service now schedules a bounded inventory recovery pass independently of the next hook (#695),
using an existing unambiguous mapping and the authoritative catalog/bundle inventory. An unreadable
route or missing mapping stays blocked rather than being assumed empty; recovery retries after
it becomes readable without requiring a fresh event. Do not reset local state, enlarge capacity,
or toggle consent to manufacture a healthy status. Real hard limits continue to apply after
inventory recovery, and previous loss counts and identities remain unchanged.

Recovery emits fixed `capture_inventory_*` reason counts in its internal maintenance summary;
these are not ledger receipts or a new hook diagnostic format. Historical local selection losses
still need a separately attributed task/check propagation path when no later envelope is admitted.
Host-shaped regression tests, including interleaved parent/worker routes and encrypted readback,
are not a version-pinned acceptance run inside the installed vendor application.

Hook and manual drain control failures have `control_` reasons. A protocol error is not a
`ledger_rejected` result. A control failure stops further calls on that connection; the next
hook, manual drain or service sweep can attempt the retained identity. Oversized or locally
invalid requests are terminal. Transport/protocol retries are bounded to 128 consecutive
same-cause attempts; owner-action conditions such as a forbidden method or incompatible
protocol stay pending until corrected. Retrying does not establish whether an earlier request
committed; exact source/cursor identity supplies replay deduplication.

`yoetz observe status --workspace . --json` includes `hook_diagnostics.drain_failures`: up to
32 retained causal records with source/session commitments, generation and position, original
control reason and retryability, adapter stage, final disposition and correlation ID when supplied.
A `control` stage does not identify which side of the socket failed; `request_encode` and
`response_decode` identify locally observed boundaries. Missing correlation remains null. Typed ingest refusals have stage
`typed_ingest` and null control fields.
Diagnostic files rotate at 64 KiB with one backup; this is explicitly incomplete retained history.
A failed diagnostic append records `drain_diagnostic_unavailable` when store bookkeeping succeeds.
No command, prompt, path, raw session ID or raw source ID is included. Existing quarantine is
not automatically replayed or reclaimed, and new diagnostics cannot explain old entries.

Pre-tool hooks reuse advice at its recorded frontier; outcome hooks, lifecycle hooks and service
ingest refresh it. Once structural ingress is durable, ordinary hooks that spent the local
one-second allowance defer optional follow-up with `hook_followup_deferred`. Transient native
content and lifecycle hooks do not take that deferral. Drain snapshot, connect, RPC and local
bookkeeping share one elapsed drain budget; synchronous local writes cannot be interrupted safely.

For shared-store measurements and the remaining native-host coverage boundary, see
[the performance runbook](observation-selection-performance.md).

## Release notifications and upgrade choices

SessionStart can deliver one cached package-update recommendation in this host's context format.
The service checks on READY and at most hourly thereafter, under the existing update-check policy
and 24-hour PyPI cache. Hooks never access the network. Advice remains available when workspace
observation consent is absent or observation is disabled; it grants no observation authority.
Task/receipt advice can occupy the same context slot and defer the recommendation.

Use the exact advertised accept/decline command, including `--release-version`. A new decline skips
that release; older permanent declines remain respected. Acceptance only supplies the upgrade
instructions, and execution requires the user's explicit upgrade request. Package replacement does
not itself prove host activation; a compatible data migration is performed by the fresh service
before READY and must be verified separately. Preserve the existing host roots, ownership and
privacy choices; new settings such as Expanded review require a separate exact approval.
